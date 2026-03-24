import base64
import re
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from catalog_store import (
    DEFAULT_BRANDING_CONFIG,
    build_branding_style,
    get_prices,
    load_branding_config,
    load_catalog,
    load_site_config,
    save_branding_config,
    save_catalog,
    save_site_config,
)
from database import (
    init_db, migrate_automations, migrate_null_tenant_ids,
    get_all_quotes, get_quote_by_id, save_quote, update_quote_status, delete_quote, update_quote_total,
    get_all_templates, get_template_by_id, create_template, update_template, delete_template, reorder_templates,
    seed_templates_for_tenant,
    get_all_automations, get_active_automations_for_trigger,
    create_automation, update_automation, delete_automation,
    get_user_by_email, get_user_by_id, get_all_users, create_user, delete_user,
    get_quotes_by_email, update_user,
    get_all_staff, get_staff_by_email, get_staff_by_id, create_staff_member,
    delete_staff_member, update_staff_member_info,
    get_all_bookings, get_booking_by_id, get_booking_by_quote_id, get_bookings_for_user, get_bookings_for_staff,
    create_booking, update_booking, delete_booking, sync_booking_date_fields,
    create_document, get_document, list_documents, update_document, delete_document,
    next_doc_number,
    get_setting, set_setting,
    create_tenant, get_tenant_by_slug, get_tenant_by_id, get_tenant_by_email,
    get_all_tenants, update_tenant, delete_tenant,
    find_staff_globally, find_user_globally,
    get_tenant_by_custom_domain,
)
from models import QuoteRequest, StatusUpdate
from auth import (
    hash_password, verify_password, create_token, decode_token,
    require_customer, require_staff, require_tenant_admin, require_super_admin,
)
import datetime
import json
import os
import uuid
import email_manager as em


DEFAULT_PW = "     "  # 5 spaces

# Subdomains reserved for platform use — never matched as tenant slugs
_RESERVED = {"admin", "www", "app", "api", "mail", "static"}

# Set BASE_DOMAIN in your environment so the app knows its root domain.
# e.g.  BASE_DOMAIN=nowdj.onrender.com   or   BASE_DOMAIN=nowdj.com
# Without it the app falls back to a heuristic that works for simple cases
# but breaks on multi-part provider domains like nowdj.onrender.com.
_BASE_DOMAIN = os.environ.get("BASE_DOMAIN", "").strip().lower()


def extract_subdomain(host: str) -> str | None:
    """
    Returns the subdomain slug, or None if this is the root/apex domain.

    With BASE_DOMAIN set (recommended):
        nowdj.onrender.com          → None   (apex)
        admin.nowdj.onrender.com    → "admin"
        ajax-dj.nowdj.onrender.com  → "ajax-dj"

    Without BASE_DOMAIN (local dev fallback):
        localhost                   → None
        admin.localhost             → "admin"
        ajax-dj.localhost           → "ajax-dj"
    """
    host = host.split(":")[0].lower()   # strip port

    if _BASE_DOMAIN:
        if host == _BASE_DOMAIN:
            return None                              # exact apex match
        if host.endswith("." + _BASE_DOMAIN):
            return host[: -(len(_BASE_DOMAIN) + 1)] # strip .base-domain
        return None                                  # unrelated host

    # ── Local dev fallback (no BASE_DOMAIN set) ──────────────────────────
    parts = host.split(".")
    if len(parts) == 1:
        return None          # bare "localhost"
    if parts[-1] == "localhost":
        return parts[0]      # slug.localhost
    # For production without BASE_DOMAIN, avoid misidentifying the service
    # name as a subdomain (e.g. nowdj.onrender.com → None, not "nowdj")
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    migrate_automations()
    _seed_defaults()
    yield


def _seed_defaults():
    # Super-admin credentials (global — no tenant prefix)
    if not get_setting("admin_email"):
        set_setting("admin_email", "ben@groovemarketing.co.uk")
        set_setting("admin_pw_hash", hash_password(DEFAULT_PW))

    # Migrate any legacy rows that pre-date multi-tenancy
    # (safe no-op if tenant 1 doesn't exist or all rows already have tenant_id)
    conn_check = get_setting("admin_email")  # quick DB connectivity check
    if conn_check:
        try:
            from database import _conn
            c = _conn()
            try:
                with c.cursor() as cur:
                    cur.execute("SELECT id FROM tenants ORDER BY id ASC LIMIT 1")
                    row = cur.fetchone()
                    if row:
                        migrate_null_tenant_ids(row["id"])
            finally:
                c.close()
        except Exception:
            pass


app = FastAPI(title="NowDJ", version="2.0.0", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# Subdomain / tenant middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def tenant_middleware(request: Request, call_next):
    host = request.headers.get("host", "")
    path = request.scope["path"]
    subdomain = extract_subdomain(host)

    request.state.subdomain = subdomain
    request.state.tenant = None
    request.state.is_admin_domain = subdomain in ("admin", None)

    # Custom domain: if the host matches a tenant's custom domain, load that tenant directly
    bare_host = host.split(":")[0].lower()
    tenant_by_domain = get_tenant_by_custom_domain(bare_host)
    if tenant_by_domain:
        request.state.tenant = tenant_by_domain
        request.state.is_admin_domain = False
        return await call_next(request)

    # Path-based tenant routing: /{slug}/... works on any domain (no wildcard DNS needed)
    # First path segment is treated as a tenant slug if it isn't a reserved platform path.
    _PATH_RESERVED = _RESERVED | {"login", "portal", "staff-portal"}
    parts = path.split("/", 2)  # ['', first_segment, rest...]
    first_segment = parts[1] if len(parts) > 1 else ""
    if first_segment and first_segment not in _PATH_RESERVED:
        tenant = get_tenant_by_slug(first_segment)
        if tenant:
            rest = "/" + parts[2] if len(parts) > 2 else "/"
            request.state.tenant = tenant
            request.state.is_admin_domain = False
            request.scope["path"] = rest
            request.scope["raw_path"] = rest.encode()
    elif subdomain and subdomain not in _RESERVED:
        tenant = get_tenant_by_slug(subdomain)
        request.state.tenant = tenant   # None if slug not found

    # Last resort: infer tenant from the Bearer JWT so JS API calls from
    # /t/{slug} pages work without needing to prefix every fetch URL.
    # Applies even on the admin domain so tenant API calls work on onrender.com.
    if not request.state.tenant:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            tok = decode_token(auth_header[7:])
            if tok and tok.get("tenant_id") and tok.get("role") != "super_admin":
                request.state.tenant = get_tenant_by_id(tok["tenant_id"])

    return await call_next(request)


def _get_tenant_or_404(request: Request) -> dict:
    tenant = getattr(request.state, "tenant", None)
    if not tenant:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return tenant


def _admin_url(request: Request, tenant_id: int) -> str:
    """Return the admin URL for a tenant, using custom domain if available."""
    t = get_tenant_by_id(tenant_id)
    if not t:
        return "/admin"
    if t.get("custom_domain"):
        return f"https://{t['custom_domain']}/admin"
    base = str(request.base_url).rstrip("/")
    return f"{base}/{t['slug']}/admin"


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def builder_page(request: Request):
    # Root domain with no tenant → redirect to admin panel
    if not request.state.tenant and getattr(request.state, "is_admin_domain", False):
        return RedirectResponse("/admin")
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]
    branding = load_branding_config(tid, tenant["name"])
    slug = tenant["slug"]
    custom_domain = tenant.get("custom_domain") or ""
    login_url = "/login" if custom_domain else f"/{slug}/login"
    return templates.TemplateResponse("builder.html", {
        "request": request,
        "catalog": load_catalog(tid),
        "config": load_site_config(tid),
        "branding": branding,
        "branding_style": build_branding_style(branding),
        "login_url": login_url,
    })


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if getattr(request.state, "is_admin_domain", False):
        return templates.TemplateResponse("super_admin.html", {"request": request})
    tenant = _get_tenant_or_404(request)
    branding = load_branding_config(tenant["id"], tenant["name"])
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "branding": branding,
        "branding_style": build_branding_style(branding),
        "tenant_email": tenant["email"],
    })


@app.get("/portal", response_class=HTMLResponse)
async def portal_page(request: Request):
    tenant = _get_tenant_or_404(request)
    branding = load_branding_config(tenant["id"], tenant["name"])
    return templates.TemplateResponse("portal.html", {
        "request": request,
        "branding": branding,
        "branding_style": build_branding_style(branding),
    })


@app.get("/staff-portal", response_class=HTMLResponse)
async def staff_portal_page(request: Request):
    tenant = getattr(request.state, "tenant", None)
    if tenant:
        branding = load_branding_config(tenant["id"], tenant["name"])
    else:
        branding = DEFAULT_BRANDING_CONFIG.copy()
    return templates.TemplateResponse("staff.html", {
        "request": request,
        "branding": branding,
        "branding_style": build_branding_style(branding),
    })


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if getattr(request.state, "is_admin_domain", False):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "branding": {"company_name": "NowDJ", "logo_emoji": "🎧", "logo_image": "", "accent_color": "#fa854f"},
            "branding_style": "",
            "portal_url": "",
        })
    tenant = _get_tenant_or_404(request)
    branding = load_branding_config(tenant["id"], tenant["name"])
    slug = tenant["slug"]
    custom_domain = tenant.get("custom_domain") or ""
    portal_url = "/portal" if custom_domain else f"/{slug}/portal"
    return templates.TemplateResponse("login.html", {
        "request": request,
        "branding": branding,
        "branding_style": build_branding_style(branding),
        "portal_url": portal_url,
    })


# ---------------------------------------------------------------------------
# Catalog & site config API  (tenant-scoped, require tenant_admin)
# ---------------------------------------------------------------------------

@app.get("/api/catalog")
async def api_get_catalog(request: Request):
    tenant = _get_tenant_or_404(request)
    return load_catalog(tenant["id"])


@app.post("/api/catalog")
async def api_save_catalog(
    catalog: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    save_catalog(catalog, tenant["id"])
    return {"success": True}


@app.post("/api/catalog/reset")
async def api_reset_catalog(
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    from database import set_setting
    set_setting("catalog_override", "", tenant_id=tenant["id"])
    return {"success": True}


@app.get("/api/site-config")
async def api_get_site_config(request: Request):
    tenant = _get_tenant_or_404(request)
    return load_site_config(tenant["id"])


@app.post("/api/site-config")
async def api_save_site_config(
    config: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    save_site_config(config, tenant["id"])
    return {"success": True}


@app.get("/api/branding")
async def api_get_branding(request: Request):
    tenant = _get_tenant_or_404(request)
    return load_branding_config(tenant["id"], tenant["name"])


@app.post("/api/branding")
async def api_save_branding(
    config: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    save_branding_config(config, tenant["id"])
    return {"success": True}


# ---------------------------------------------------------------------------
# Quote submission (public)
# ---------------------------------------------------------------------------

@app.post("/submit-quote")
async def submit_quote(quote: QuoteRequest, request: Request):
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]

    if not quote.selected_items:
        raise HTTPException(status_code=400, detail="Please select at least one item.")

    catalog = load_catalog(tid)
    prices  = get_prices(catalog)

    total = 0.0
    item_details = []
    for item_id in quote.selected_items:
        base_price = prices.get(item_id, 0)
        name = item_id
        cat_item: dict = {}
        for cat in catalog.values():
            if item_id in cat["items"]:
                cat_item = cat["items"][item_id]
                name = cat_item["name"]
                break

        pt = cat_item.get("pricing_type") or ("hourly" if cat_item.get("hourly") else "fixed")

        if pt == "tbc":
            detail: dict = {"id": item_id, "name": name, "price": 0, "tbc": True}
        elif pt in ("hourly", "daily"):
            raw = quote.item_quantities.get(item_id, 1)
            # "qty:days" format sent when item has both allow_quantity and time-based pricing
            if isinstance(raw, str) and ":" in raw:
                parts = raw.split(":", 1)
                item_qty = int(parts[0]) if parts[0].isdigit() else 1
                item_days = int(parts[1]) if parts[1].isdigit() else 1
            else:
                item_qty = 1
                item_days = int(raw) if raw else 1
            qty = item_qty * item_days
            price = base_price * qty
            total += price
            unit = "hrs" if pt == "hourly" else "days"
            detail = {"id": item_id, "name": name, "price": price,
                      "base_price": base_price, "qty": qty, "unit": unit}
        else:
            qty = int(quote.item_quantities.get(item_id, 1) or 1)
            price = base_price * qty
            total += price
            detail = {"id": item_id, "name": name, "price": price, "qty": qty}
        item_details.append(detail)

    quote_id = save_quote({
        "name": quote.name,
        "email": quote.email,
        "phone": quote.phone,
        "event_date": quote.event_date,
        "location": quote.location,
        "event_type": quote.event_type,
        "selected_items": item_details,
        "total_price": total,
        "message": quote.message,
    }, tid)

    # Fire automations for form_submission trigger (best-effort, don't block response)
    try:
        automations = get_active_automations_for_trigger("form_submission", tid)
        for auto in automations:
            vars_map = _build_vars_map(
                name=quote.name or "",
                first_name=quote.first_name or "",
                last_name=quote.last_name or "",
                email=quote.email or "",
                phone=quote.phone or "",
                event_date=quote.event_date or "",
                event_type=quote.event_type or "",
                location=quote.location or "",
                total=f"£{total:,.2f}",
                message=quote.message or "",
                quote_id=str(quote_id),
            )
            subj = _render_template_vars(auto.get("subject") or "", vars_map)
            body = _render_template_vars(auto.get("body") or "", vars_map)
            if auto["send_to"] == "submitter":
                to_email = quote.email
                to_name  = quote.name or quote.email
            else:
                to_email = auto.get("send_to_email") or ""
                to_name  = to_email
            if to_email:
                await em.send_email(to_email, to_name, subj, body, tid)
    except Exception:
        pass  # Never let automation errors break form submission

    return {
        "success": True,
        "quote_id": quote_id,
        "total_price": total,
        "message": f"Quote #{quote_id} submitted! We'll be in touch shortly.",
    }


def _render_template_vars(text: str, vars_map: dict) -> str:
    """Replace {{key}} placeholders in template text."""
    for key, val in vars_map.items():
        text = text.replace("{{" + key + "}}", val)
    return text


def _event_month(event_date: str) -> str:
    """Return month name from YYYY-MM-DD string, e.g. '2025-03-15' → 'March'."""
    try:
        import datetime as _dt
        return _dt.date.fromisoformat(event_date).strftime("%B")
    except Exception:
        return ""


def _build_vars_map(name: str, email: str, phone: str, event_date: str,
                    event_type: str, location: str, total: str,
                    message: str, quote_id: str,
                    first_name: str = "", last_name: str = "") -> dict:
    """Build the standard template variable map."""
    # Derive first/last from full name if not provided
    if not first_name and name:
        parts = name.strip().split(" ", 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""
    return {
        "name":            name,
        "first_name":      first_name,
        "last_name":       last_name,
        "email":           email,
        "phone":           phone,
        "event_date":      event_date,
        "event_month":     _event_month(event_date),
        "event_type":      event_type,
        "event_type_lower": event_type.lower(),
        "location":        location,
        "total":           total,
        "message":         message,
        "quote_id":        quote_id,
    }


# ---------------------------------------------------------------------------
# Quotes API  (tenant admin)
# ---------------------------------------------------------------------------

@app.get("/quotes")
async def list_quotes(
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    return get_all_quotes(tenant["id"])


@app.get("/quotes/{quote_id}")
async def get_quote(
    quote_id: int,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    q = get_quote_by_id(quote_id, tenant["id"])
    if not q:
        raise HTTPException(status_code=404, detail="Quote not found")
    return q


@app.post("/quotes/{quote_id}/duplicate")
async def duplicate_quote_route(
    quote_id: int,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]
    original = get_quote_by_id(quote_id, tid)
    if not original:
        raise HTTPException(status_code=404, detail="Quote not found")
    new_id = save_quote({
        "name":           original["name"],
        "email":          original["email"],
        "phone":          original.get("phone", ""),
        "event_date":     original.get("event_date", ""),
        "location":       original.get("location", ""),
        "event_type":     original.get("event_type", ""),
        "selected_items": original.get("selected_items", []),
        "total_price":    original.get("total_price", 0),
        "message":        original.get("message", ""),
    }, tid)
    return {"success": True, "id": new_id}


@app.delete("/quotes/{quote_id}")
async def delete_quote_route(
    quote_id: int,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    deleted = delete_quote(quote_id, tenant["id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Quote not found")
    return {"success": True}


@app.patch("/quotes/{quote_id}/status")
async def patch_status(
    quote_id: int,
    payload: StatusUpdate,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    q = get_quote_by_id(quote_id, tenant["id"])
    if not q:
        raise HTTPException(status_code=404, detail="Quote not found")
    update_quote_status(quote_id, payload.status, tenant["id"])
    # Sync to bookings table for booked/attended/paid
    _booking_status = {"booked": "confirmed", "attended": "attended", "paid": "paid"}
    if payload.status in _booking_status:
        bk_status = _booking_status[payload.status]
        existing = get_booking_by_quote_id(quote_id, tenant["id"])
        if existing:
            update_booking(existing["id"], {**existing, "status": bk_status}, tenant["id"])
        else:
            create_booking({
                "quote_id":    quote_id,
                "title":       q.get("name", ""),
                "event_date":  q.get("event_date", ""),
                "event_type":  q.get("event_type", ""),
                "location":    q.get("location", ""),
                "total_price": q.get("total_price", 0),
                "status":      bk_status,
            }, tenant["id"])
    # Fire automations for status-based triggers
    _status_trigger_map = {
        "booked":      "status_booked",
        "new":         "status_new",
        "quoted":      "status_quoted",
        "followed_up": "status_followed_up",
        "attended":    "status_attended",
        "paid":        "status_paid",
    }
    auto_trigger = _status_trigger_map.get(payload.status)
    if auto_trigger:
        try:
            automations = get_active_automations_for_trigger(auto_trigger, tenant["id"])
            vars_map = _build_vars_map(
                name=q.get("name") or "",
                email=q.get("email") or "",
                phone=q.get("phone") or "",
                event_date=q.get("event_date") or "",
                event_type=q.get("event_type") or "",
                location=q.get("location") or "",
                total=f"£{q.get('total_price', 0):,.2f}",
                message=q.get("message") or "",
                quote_id=str(quote_id),
            )
            for auto in automations:
                subj = _render_template_vars(auto.get("subject") or "", vars_map)
                body = _render_template_vars(auto.get("body") or "", vars_map)
                to_email = q.get("email") if auto["send_to"] == "submitter" else auto.get("send_to_email") or ""
                to_name  = q.get("name") or to_email
                if to_email:
                    await em.send_email(to_email, to_name, subj, body, tenant["id"])
        except Exception:
            pass
    return {"success": True, "quote_id": quote_id, "status": payload.status}


# ---------------------------------------------------------------------------
# Email — config & auth  (tenant admin)
# ---------------------------------------------------------------------------

@app.get("/api/email/status")
async def email_status(
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]
    connected = em.is_connected(tid)
    user: dict = {}
    if connected:
        try:
            user = await em.get_me(tid)
        except Exception:
            connected = False
    return {"configured": True, "authenticated": connected, "connected": connected, "user": user}


@app.get("/api/email/connect")
async def email_connect(request: Request, token: str = ""):
    """Start the Outlook OAuth flow. Accepts JWT via ?token= query param for browser redirects."""
    # Resolve tenant from query token or from request state (set by middleware)
    tenant = getattr(request.state, "tenant", None)
    if not tenant and token:
        tok = decode_token(token)
        if tok and tok.get("tenant_id") and tok.get("role") in ("tenant_admin", "staff"):
            tenant = get_tenant_by_id(tok["tenant_id"])
    if not tenant:
        raise HTTPException(status_code=401, detail="Authentication required")
    state = em.make_state(tenant["id"])
    return RedirectResponse(em.get_auth_url(state))


@app.get("/api/email/auth/callback")
async def email_auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    # Determine tenant from the HMAC-signed state parameter
    tid = em.verify_state(state) if state else None
    if error:
        dest = f"/{_get_tenant_or_404(request)['slug']}/admin#email" if not tid else _admin_url(request, tid)
        return HTMLResponse(f"<script>window.location='{dest}';alert('Auth error: {error}');</script>")
    if tid is None:
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")
    if not code:
        raise HTTPException(status_code=400, detail="No code provided")
    try:
        await em.exchange_code(code, tid)
    except Exception as exc:
        return HTMLResponse(f"<script>window.location='{_admin_url(request, tid)}';alert('Auth failed: {exc}');</script>")
    return RedirectResponse(_admin_url(request, tid) + "#email")


@app.post("/api/email/auth/disconnect")
async def email_auth_disconnect(
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    em.clear_tokens(tenant["id"])
    return {"success": True}


# ---------------------------------------------------------------------------
# Email — mailbox operations  (tenant admin)
# ---------------------------------------------------------------------------

@app.get("/api/email/inbox")
async def email_inbox(
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
    top: int = 40,
):
    tenant = _get_tenant_or_404(request)
    try:
        return await em.get_inbox(tenant["id"], top)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/email/sent")
async def email_sent(
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
    top: int = 20,
):
    tenant = _get_tenant_or_404(request)
    try:
        return await em.get_sent(tenant["id"], top)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/email/messages/{msg_id:path}")
async def email_get_message(
    msg_id: str,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    try:
        msg = await em.get_message(msg_id, tenant["id"])
        if not msg:
            raise HTTPException(status_code=404, detail="Message not found")
        return msg
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/email/messages/{msg_id:path}/read")
async def email_mark_read(
    msg_id: str,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    try:
        ok = await em.mark_read(msg_id, tenant["id"])
        return {"success": ok}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.delete("/api/email/messages/{msg_id:path}")
async def email_delete_message(
    msg_id: str,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    try:
        ok = await em.delete_message(msg_id, tenant["id"])
        return {"success": ok}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.post("/api/email/send")
async def email_send(
    payload: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    to_email = payload.get("to_email", "")
    to_name  = payload.get("to_name", "")
    subject  = payload.get("subject", "")
    body     = payload.get("body", "")
    doc_id   = payload.get("doc_id")
    if not to_email or not subject:
        raise HTTPException(status_code=400, detail="to_email and subject are required")

    attachments = None
    if doc_id:
        doc = get_document(int(doc_id), tenant["id"])
        if doc:
            try:
                pdf_bytes = _render_doc_pdf_bytes(doc, tenant["id"])
                filename = f"{doc.get('doc_number', f'document-{doc_id}')}.pdf"
                attachments = [{
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": filename,
                    "contentType": "application/pdf",
                    "contentBytes": base64.b64encode(pdf_bytes).decode(),
                }]
            except Exception as pdf_err:
                # Don't block sending if PDF generation fails
                pass

    try:
        ok = await em.send_email(to_email, to_name, subject, body, tenant["id"], attachments)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Auto-advance status: new → contacted when an email is sent to an enquiry
    status_updated = False
    quote_id = payload.get("quote_id")
    if ok and quote_id:
        try:
            q = get_quote_by_id(int(quote_id), tenant["id"])
            if q and q.get("status") == "new":
                update_quote_status(int(quote_id), "contacted", tenant["id"])
                status_updated = True
        except Exception:
            pass  # never block the send response

    return {"success": ok, "status_updated": status_updated}


@app.post("/api/email/reply/{msg_id:path}")
async def email_reply(
    msg_id: str,
    payload: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    body = payload.get("body", "")
    try:
        ok = await em.reply_email(msg_id, body, tenant["id"])
        return {"success": ok}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Email — templates CRUD  (tenant admin)
# ---------------------------------------------------------------------------

@app.get("/api/email/templates")
async def list_email_templates(
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    return get_all_templates(tenant["id"])


@app.get("/api/email/templates/{tid}")
async def get_email_template(
    tid: int,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    t = get_template_by_id(tid, tenant["id"])
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    return t


@app.post("/api/email/templates")
async def create_email_template(
    payload: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    name    = payload.get("name", "").strip()
    subject = payload.get("subject", "").strip()
    body    = payload.get("body", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    tid = create_template(name, subject, body, tenant["id"])
    return {"success": True, "id": tid}


@app.put("/api/email/templates/{tid}")
async def update_email_template(
    tid: int,
    payload: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    t = get_template_by_id(tid, tenant["id"])
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    update_template(
        tid,
        payload.get("name", t["name"]),
        payload.get("subject", t["subject"]),
        payload.get("body", t["body"]),
        tenant["id"],
    )
    return {"success": True}


@app.delete("/api/email/templates/{tid}")
async def delete_email_template(
    tid: int,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    t = get_template_by_id(tid, tenant["id"])
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    delete_template(tid, tenant["id"])
    return {"success": True}


@app.post("/api/email/templates/reorder")
async def reorder_email_templates(
    payload: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    ids = payload.get("ids", [])
    reorder_templates([int(i) for i in ids], tenant["id"])
    return {"success": True}


# ---------------------------------------------------------------------------
# Email — automations  (tenant admin)
# ---------------------------------------------------------------------------

@app.get("/api/email/automations")
async def list_automations(
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    return get_all_automations(tenant["id"])


@app.post("/api/email/automations")
async def create_automation_route(
    payload: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    name = payload.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    import sys
    args = (
        tenant["id"],
        name,
        payload.get("trigger_event", "form_submission"),
        payload.get("template_id") or None,
        payload.get("send_to", "custom"),
        (payload.get("send_to_email") or "").strip() or None,
    )
    print(f"[automation] creating: {args}", file=sys.stderr)
    try:
        aid = create_automation(*args)
    except Exception as e:
        print(f"[automation] first attempt failed: {e}", file=sys.stderr)
        migrate_automations()
        try:
            aid = create_automation(*args)
        except Exception as e2:
            print(f"[automation] second attempt failed: {e2}", file=sys.stderr)
            raise HTTPException(status_code=500, detail=str(e2))
    print(f"[automation] created id={aid}", file=sys.stderr)
    return {"success": True, "id": aid}


@app.put("/api/email/automations/{aid}")
async def update_automation_route(
    aid: int,
    payload: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    name = payload.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    update_automation(
        aid,
        tenant["id"],
        name,
        payload.get("trigger_event", "form_submission"),
        payload.get("template_id") or None,
        payload.get("send_to", "custom"),
        payload.get("send_to_email", "").strip() or None,
        bool(payload.get("enabled", True)),
    )
    return {"success": True}


@app.delete("/api/email/automations/{aid}")
async def delete_automation_route(
    aid: int,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    delete_automation(aid, tenant["id"])
    return {"success": True}


# ---------------------------------------------------------------------------
# Email — signatures  (tenant admin)
# ---------------------------------------------------------------------------

@app.get("/api/email/signatures")
async def list_signatures(
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    raw = get_setting("email_signatures", tenant_id=tenant["id"])
    try:
        return json.loads(raw) if raw else []
    except Exception:
        return []


@app.post("/api/email/signatures")
async def save_signature(
    payload: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]
    raw = get_setting("email_signatures", tenant_id=tid)
    sigs = json.loads(raw) if raw else []

    sig_id = payload.get("id")
    if sig_id:
        found = False
        for i, s in enumerate(sigs):
            if s["id"] == sig_id:
                sigs[i] = {**s, **payload}
                found = True
                break
        if not found:
            sigs.append({**payload, "id": sig_id})
    else:
        sig_id = str(uuid.uuid4())[:8]
        sigs.append({**payload, "id": sig_id})

    # Only one default at a time
    if payload.get("is_default"):
        for s in sigs:
            if s["id"] != sig_id:
                s["is_default"] = False

    set_setting("email_signatures", json.dumps(sigs), tenant_id=tid)
    return {"success": True, "id": sig_id}


@app.delete("/api/email/signatures/{sig_id}")
async def delete_signature(
    sig_id: str,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]
    raw = get_setting("email_signatures", tenant_id=tid)
    sigs = [s for s in (json.loads(raw) if raw else []) if s["id"] != sig_id]
    set_setting("email_signatures", json.dumps(sigs), tenant_id=tid)
    return {"success": True}


# ---------------------------------------------------------------------------
# Auth — customer  (public, tenant-scoped)
# ---------------------------------------------------------------------------

@app.post("/api/auth/customer/register")
async def customer_register(payload: dict, request: Request):
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]
    name     = payload.get("name", "").strip()
    email    = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    if not name or not email or not password:
        raise HTTPException(status_code=400, detail="name, email and password are required")
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if get_user_by_email(email, tid):
        raise HTTPException(status_code=409, detail="An account with that email already exists")
    uid   = create_user(name, email, hash_password(password), tid)
    token = create_token("customer", uid, email, name, tenant_id=tid)
    return {"token": token, "name": name, "email": email}


@app.post("/api/auth/customer/login")
async def customer_login(payload: dict, request: Request):
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]
    email    = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    user     = get_user_by_email(email, tid)
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token = create_token("customer", user["id"], user["email"], user["name"], tenant_id=tid)
    return {"token": token, "name": user["name"], "email": user["email"]}


# ---------------------------------------------------------------------------
# Auth — staff  (public, tenant-scoped)
# ---------------------------------------------------------------------------

@app.post("/api/auth/staff/login")
async def staff_login(payload: dict, request: Request):
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]
    email    = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    member   = get_staff_by_email(email, tid)
    if not member or not verify_password(password, member["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token = create_token("staff", member["id"], member["email"], member["name"], tenant_id=tid)
    return {"token": token, "name": member["name"], "email": member["email"]}


# ---------------------------------------------------------------------------
# Unified login  (handles super_admin, tenant_admin, staff, customer)
# ---------------------------------------------------------------------------

@app.post("/api/auth/login")
async def unified_login(payload: dict, request: Request):
    email    = payload.get("email", "").strip().lower()
    password = payload.get("password", "")

    # ── Always try super admin first
    admin_email   = get_setting("admin_email", "").lower()
    admin_pw_hash = get_setting("admin_pw_hash", "")
    if email == admin_email and admin_pw_hash and verify_password(password, admin_pw_hash):
        token = create_token("super_admin", 0, email, "Super Admin")
        return {"role": "super_admin", "token": token}

    def _tenant_resp(role, uid, t_email, t_name, tid, slug, custom_domain, extra=None):
        token = create_token(role, uid, t_email, t_name, tenant_id=tid)
        r = {"role": role, "token": token, "name": t_name, "email": t_email,
             "slug": slug, "custom_domain": custom_domain or ""}
        if extra:
            r.update(extra)
        return r

    # ── If on a specific tenant (subdomain or /{slug} path), check only that tenant
    tenant = getattr(request.state, "tenant", None)
    if tenant:
        tid  = tenant["id"]
        slug = tenant["slug"]
        cd   = tenant.get("custom_domain") or ""
        if email == tenant["email"].lower() and verify_password(password, tenant["password_hash"]):
            return _tenant_resp("tenant_admin", tenant["id"], tenant["email"], tenant["name"], tid, slug, cd)
        member = get_staff_by_email(email, tid)
        if member and verify_password(password, member["password_hash"]):
            return _tenant_resp("staff", member["id"], member["email"], member["name"], tid, slug, cd)
        user = get_user_by_email(email, tid)
        if user and verify_password(password, user["password_hash"]):
            return _tenant_resp("customer", user["id"], user["email"], user["name"], tid, slug, cd)
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    # ── Admin domain — search globally across all workspaces
    tenant_row = get_tenant_by_email(email)
    if tenant_row and verify_password(password, tenant_row["password_hash"]):
        return _tenant_resp("tenant_admin", tenant_row["id"], tenant_row["email"], tenant_row["name"],
                            tenant_row["id"], tenant_row["slug"], tenant_row.get("custom_domain") or "")

    member = find_staff_globally(email)
    if member and verify_password(password, member["password_hash"]):
        return _tenant_resp("staff", member["id"], member["email"], member["name"],
                            member["tenant_id"], member["tenant_slug"], member.get("tenant_custom_domain") or "")

    user = find_user_globally(email)
    if user and verify_password(password, user["password_hash"]):
        return _tenant_resp("customer", user["id"], user["email"], user["name"],
                            user["tenant_id"], user["tenant_slug"], user.get("tenant_custom_domain") or "")

    raise HTTPException(status_code=401, detail="Incorrect email or password")


# ---------------------------------------------------------------------------
# Customer portal API
# ---------------------------------------------------------------------------

@app.get("/api/customer/me")
async def customer_me(user: Annotated[dict, Depends(require_customer)]):
    return {"id": user["uid"], "name": user["name"], "email": user["email"]}


@app.get("/api/customer/quotes")
async def customer_quotes(
    request: Request,
    user: Annotated[dict, Depends(require_customer)],
):
    tenant = _get_tenant_or_404(request)
    return get_quotes_by_email(user["email"], tenant["id"])


@app.get("/api/customer/bookings")
async def customer_bookings(
    request: Request,
    user: Annotated[dict, Depends(require_customer)],
):
    tenant = _get_tenant_or_404(request)
    return get_bookings_for_user(user["uid"], tenant["id"])


# ---------------------------------------------------------------------------
# Staff portal API
# ---------------------------------------------------------------------------

@app.get("/api/staff/me")
async def staff_me(member: Annotated[dict, Depends(require_staff)]):
    return {"id": member["uid"], "name": member["name"], "email": member["email"]}


@app.get("/api/staff/bookings")
async def staff_bookings(
    request: Request,
    member: Annotated[dict, Depends(require_staff)],
):
    tenant = _get_tenant_or_404(request)
    return get_bookings_for_staff(member["uid"], tenant["id"])


# ---------------------------------------------------------------------------
# Admin — staff management  (tenant admin)
# ---------------------------------------------------------------------------

@app.get("/api/admin/staff")
async def admin_list_staff(
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    return get_all_staff(tenant["id"])


@app.post("/api/admin/staff")
async def admin_create_staff(
    payload: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]
    name     = payload.get("name", "").strip()
    email    = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    if not name or not email or not password:
        raise HTTPException(status_code=400, detail="name, email and password required")
    if get_staff_by_email(email, tid):
        raise HTTPException(status_code=409, detail="Staff member with that email already exists")
    sid = create_staff_member(name, email, hash_password(password), tid)
    return {"success": True, "id": sid}


@app.delete("/api/admin/staff/{sid}")
async def admin_delete_staff(
    sid: int,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    if not get_staff_by_id(sid, tenant["id"]):
        raise HTTPException(status_code=404, detail="Staff member not found")
    delete_staff_member(sid, tenant["id"])
    return {"success": True}


# ---------------------------------------------------------------------------
# Admin — bookings  (tenant admin)
# ---------------------------------------------------------------------------

@app.get("/api/admin/bookings")
async def admin_list_bookings(
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]
    # Backfill: create booking records for any booked/attended/paid enquiries that don't have one yet
    _bk_map = {"booked": "confirmed", "attended": "attended", "paid": "paid"}
    for q in get_all_quotes(tid):
        if q.get("status") in _bk_map and not get_booking_by_quote_id(q["id"], tid):
            create_booking({
                "quote_id":    q["id"],
                "title":       q.get("name", ""),
                "event_date":  q.get("event_date", ""),
                "event_type":  q.get("event_type", ""),
                "location":    q.get("location", ""),
                "total_price": q.get("total_price", 0),
                "status":      _bk_map[q["status"]],
            }, tid)

    # Sync: for every booking linked to an enquiry, apply the most recent
    # linked document's date/location/type so everything stays in agreement.
    all_docs = list_documents(tid)
    # Build map: quote_id → most recently updated doc
    doc_by_quote: dict = {}
    for d in all_docs:
        qid = d.get("source_quote_id")
        if not qid:
            continue
        prev = doc_by_quote.get(qid)
        if prev is None or (d.get("updated_at") or d.get("created_at", "")) >= (prev.get("updated_at") or prev.get("created_at", "")):
            doc_by_quote[qid] = d

    for bk in get_all_bookings(tid):
        qid = bk.get("quote_id")
        if not qid:
            continue
        doc = doc_by_quote.get(qid)
        if not doc or not doc.get("event_date"):
            continue
        if (doc["event_date"] != bk.get("event_date") or
                doc.get("location", "") != bk.get("location", "") or
                doc.get("event_type", "") != bk.get("event_type", "")):
            sync_booking_date_fields(
                bk["id"],
                doc["event_date"],
                doc.get("event_type") or bk.get("event_type", ""),
                doc.get("location")   or bk.get("location", ""),
                tid,
            )

    return get_all_bookings(tid)


@app.post("/api/admin/bookings")
async def admin_create_booking(
    payload: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    bid = create_booking(payload, tenant["id"])
    try:
        automations = get_active_automations_for_trigger("booking_created", tenant["id"])
        vars_map = _build_vars_map(
            name=payload.get("title") or "",
            email="", phone="", message="", quote_id="",
            event_date=payload.get("event_date") or "",
            event_type=payload.get("event_type") or "",
            location=payload.get("location") or "",
            total=f"£{payload.get('total_price', 0):,.2f}",
        )
        for auto in automations:
            subj = _render_template_vars(auto.get("subject") or "", vars_map)
            body = _render_template_vars(auto.get("body") or "", vars_map)
            to_email = auto.get("send_to_email") or "" if auto["send_to"] != "submitter" else ""
            if to_email:
                await em.send_email(to_email, to_email, subj, body, tenant["id"])
    except Exception:
        pass
    return {"success": True, "id": bid}


@app.patch("/api/admin/bookings/{bid}")
async def admin_update_booking(
    bid: int,
    payload: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    if not get_booking_by_id(bid, tenant["id"]):
        raise HTTPException(status_code=404, detail="Booking not found")
    update_booking(bid, payload, tenant["id"])
    return {"success": True}


@app.delete("/api/admin/bookings/{bid}")
async def admin_delete_booking(
    bid: int,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    if not get_booking_by_id(bid, tenant["id"]):
        raise HTTPException(status_code=404, detail="Booking not found")
    delete_booking(bid, tenant["id"])
    return {"success": True}


# ── Calendar feed ──────────────────────────────────────────────────────────

def _get_or_create_cal_token(tenant_id: int) -> str:
    """Return the tenant's calendar token, creating one if it doesn't exist."""
    token = get_setting("cal_token", tenant_id=tenant_id)
    if not token:
        token = str(uuid.uuid4()).replace("-", "")
        set_setting("cal_token", token, tenant_id=tenant_id)
    return token


@app.get("/api/admin/calendar-token")
async def get_calendar_token(
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    token = _get_or_create_cal_token(tenant["id"])
    base = str(request.base_url).rstrip("/")
    return {"token": token, "url": f"{base}/calendar/bookings.ics?cal_token={token}"}


@app.post("/api/admin/calendar-token/regenerate")
async def regenerate_calendar_token(
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    token = str(uuid.uuid4()).replace("-", "")
    set_setting("cal_token", token, tenant_id=tenant["id"])
    base = str(request.base_url).rstrip("/")
    return {"token": token, "url": f"{base}/calendar/bookings.ics?cal_token={token}"}


@app.get("/calendar/bookings.ics")
async def bookings_ics(request: Request, cal_token: str = ""):
    """Public ICS feed — authenticated by per-tenant calendar token."""
    from fastapi.responses import Response
    if not cal_token:
        raise HTTPException(status_code=401, detail="Missing cal_token")

    # Find the tenant whose token matches
    tenant = getattr(request.state, "tenant", None)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tid = tenant["id"]

    stored = get_setting("cal_token", tenant_id=tid)
    if not stored or stored != cal_token:
        raise HTTPException(status_code=403, detail="Invalid calendar token")

    # Apply doc-sync in memory so the feed always has the latest dates
    # even if the DB hasn't been updated yet via the admin bookings endpoint
    all_docs = list_documents(tid)
    doc_by_quote: dict = {}
    for d in all_docs:
        qid = d.get("source_quote_id")
        if not qid:
            continue
        prev = doc_by_quote.get(qid)
        if prev is None or (d.get("updated_at") or d.get("created_at", "")) >= (prev.get("updated_at") or prev.get("created_at", "")):
            doc_by_quote[qid] = d

    raw_bookings = get_all_bookings(tid)
    bookings = []
    for bk in raw_bookings:
        bk = dict(bk)
        doc = doc_by_quote.get(bk.get("quote_id"))
        if doc and doc.get("event_date"):
            bk["event_date"]  = doc["event_date"]
            bk["event_type"]  = doc.get("event_type") or bk.get("event_type", "")
            bk["location"]    = doc.get("location")   or bk.get("location", "")
        bookings.append(bk)

    def ics_date(d: str) -> str:
        """Convert YYYY-MM-DD to YYYYMMDD for ICS VALUE=DATE."""
        return d.replace("-", "") if d else ""

    def ics_escape(s: str) -> str:
        return (s or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")

    now_stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//NowDJ//Bookings//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:NowDJ Bookings",
        "X-WR-TIMEZONE:Europe/London",
        "REFRESH-INTERVAL;VALUE=DURATION:PT15M",
        "X-PUBLISHED-TTL:PT15M",
    ]
    for b in bookings:
        if b.get("status") == "cancelled":
            continue
        dt = ics_date(b.get("event_date", ""))
        if not dt:
            continue
        title   = ics_escape(b.get("title") or b.get("event_type") or "Booking")
        loc     = ics_escape(b.get("location", ""))
        notes   = ics_escape(b.get("notes", ""))
        staff   = ics_escape(b.get("staff_name", ""))
        desc_parts = []
        if b.get("event_type"): desc_parts.append(f"Type: {b['event_type']}")
        if staff:               desc_parts.append(f"Staff: {staff}")
        if b.get("total_price"): desc_parts.append(f"Total: £{b['total_price']:.2f}")
        if notes:               desc_parts.append(notes)
        desc = ics_escape("\\n".join(desc_parts))
        uid = f"nowdj-booking-{b['id']}@nowdj"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_stamp}",
            f"DTSTART;VALUE=DATE:{dt}",
            f"DTEND;VALUE=DATE:{dt}",
            f"SUMMARY:{title}",
            f"LOCATION:{loc}",
            f"DESCRIPTION:{desc}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")

    ics_content = "\r\n".join(lines) + "\r\n"
    return Response(
        content=ics_content,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="bookings.ics"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )


# ---------------------------------------------------------------------------
# Admin — user (client) management  (tenant admin)
# ---------------------------------------------------------------------------

@app.get("/api/admin/users")
async def admin_list_users(
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    return get_all_users(tenant["id"])


@app.patch("/api/admin/users/{uid}")
async def admin_update_user(
    uid: int,
    payload: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]
    user = get_user_by_id(uid, tid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    new_name  = payload.get("name", "").strip()
    new_email = payload.get("email", "").strip().lower()
    new_pw    = payload.get("password", "")
    if not new_name or not new_email:
        raise HTTPException(status_code=400, detail="name and email required")
    pw_hash = hash_password(new_pw) if new_pw else None
    update_user(uid, new_name, new_email, tid, pw_hash)
    return {"success": True}


@app.delete("/api/admin/users/{uid}")
async def admin_delete_user(
    uid: int,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    if not get_user_by_id(uid, tenant["id"]):
        raise HTTPException(status_code=404, detail="User not found")
    delete_user(uid, tenant["id"])
    return {"success": True}


# ---------------------------------------------------------------------------
# Profile updates
# ---------------------------------------------------------------------------

@app.patch("/api/customer/profile")
async def update_customer_profile(
    payload: dict,
    request: Request,
    user: Annotated[dict, Depends(require_customer)],
):
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]
    new_name  = payload.get("name", "").strip()
    new_email = payload.get("email", "").strip().lower()
    new_pw    = payload.get("password", "")
    if not new_name or not new_email:
        raise HTTPException(status_code=400, detail="name and email required")
    pw_hash = hash_password(new_pw) if new_pw else None
    update_user(user["uid"], new_name, new_email, tid, pw_hash)
    token = create_token("customer", user["uid"], new_email, new_name, tenant_id=tid)
    return {"success": True, "token": token, "name": new_name, "email": new_email}


@app.patch("/api/staff/profile")
async def update_staff_profile(
    payload: dict,
    request: Request,
    member: Annotated[dict, Depends(require_staff)],
):
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]
    new_name  = payload.get("name", "").strip()
    new_email = payload.get("email", "").strip().lower()
    new_pw    = payload.get("password", "")
    if not new_name or not new_email:
        raise HTTPException(status_code=400, detail="name and email required")
    pw_hash = hash_password(new_pw) if new_pw else None
    update_staff_member_info(member["uid"], new_name, new_email, tid, pw_hash)
    token = create_token("staff", member["uid"], new_email, new_name, tenant_id=tid)
    return {"success": True, "token": token, "name": new_name, "email": new_email}


@app.patch("/api/admin/credentials")
async def update_tenant_admin_credentials(
    payload: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    """Tenant admin updates their own login email / password."""
    tenant = _get_tenant_or_404(request)
    new_email = payload.get("email", "").strip().lower()
    new_pw    = payload.get("password", "")
    if not new_email:
        raise HTTPException(status_code=400, detail="email required")
    pw_hash = hash_password(new_pw) if new_pw else None
    update_tenant(tenant["id"], tenant["name"], new_email, tenant["plan"], pw_hash)
    return {"success": True}


# ---------------------------------------------------------------------------
# Super admin — tenant management
# ---------------------------------------------------------------------------

@app.get("/api/super/tenants")
async def super_list_tenants(
    _admin: Annotated[dict, Depends(require_super_admin)],
):
    return get_all_tenants()


@app.post("/api/super/tenants")
async def super_create_tenant(
    payload: dict,
    _admin: Annotated[dict, Depends(require_super_admin)],
):
    name     = payload.get("name", "").strip()
    slug     = payload.get("slug", "").strip().lower()
    email    = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    plan     = payload.get("plan", "starter")

    if not name or not slug or not email or not password:
        raise HTTPException(status_code=400, detail="name, slug, email and password are required")
    if slug in _RESERVED:
        raise HTTPException(status_code=400, detail=f"'{slug}' is a reserved slug")
    if get_tenant_by_slug(slug):
        raise HTTPException(status_code=409, detail="A workspace with that slug already exists")
    if get_tenant_by_email(email):
        raise HTTPException(status_code=409, detail="A workspace with that email already exists")

    tid = create_tenant(name, slug, email, hash_password(password), plan)
    seed_templates_for_tenant(tid)
    return {"success": True, "id": tid, "slug": slug}


@app.patch("/api/super/tenants/{tid}")
async def super_update_tenant(
    tid: int,
    payload: dict,
    _admin: Annotated[dict, Depends(require_super_admin)],
):
    tenant = get_tenant_by_id(tid)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    name          = payload.get("name", tenant["name"]).strip()
    email         = payload.get("email", tenant["email"]).strip().lower()
    plan          = payload.get("plan", tenant["plan"])
    new_pw        = payload.get("password", "")
    pw_hash       = hash_password(new_pw) if new_pw else None
    custom_domain = payload.get("custom_domain", ...)  # Ellipsis = not provided
    update_tenant(tid, name, email, plan, pw_hash, custom_domain)
    return {"success": True}


@app.delete("/api/super/tenants/{tid}")
async def super_delete_tenant(
    tid: int,
    _admin: Annotated[dict, Depends(require_super_admin)],
):
    if not get_tenant_by_id(tid):
        raise HTTPException(status_code=404, detail="Tenant not found")
    delete_tenant(tid)
    return {"success": True}


# ---------------------------------------------------------------------------
# Documents — Quotes & Invoices  (tenant admin)
# ---------------------------------------------------------------------------

@app.get("/api/docs/settings")
async def doc_settings_get(
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    raw = get_setting("doc_settings", tenant_id=tenant["id"])
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


@app.put("/api/docs/settings")
async def doc_settings_put(
    payload: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    set_setting("doc_settings", json.dumps(payload), tenant_id=tenant["id"])
    return {"success": True}


@app.get("/api/docs")
async def docs_list(
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
    type: str | None = None,
):
    tenant = _get_tenant_or_404(request)
    return list_documents(tenant["id"], doc_type=type)


def _sync_booking_from_doc(payload: dict, doc: dict | None, tid: int) -> None:
    """Sync event_date/location/event_type to the linked booking when a doc is saved."""
    src = payload.get("source_quote_id") or (doc.get("source_quote_id") if doc else None)
    if not src:
        return
    event_date = payload.get("event_date") or (doc.get("event_date") if doc else "") or ""
    event_type = payload.get("event_type") or (doc.get("event_type") if doc else "") or ""
    location   = payload.get("location")   or (doc.get("location")   if doc else "") or ""
    if not event_date:
        return
    booking = get_booking_by_quote_id(int(src), tid)
    if not booking:
        return
    sync_booking_date_fields(booking["id"], event_date, event_type, location, tid)


def _doc_subtotal(line_items_json: str) -> float:
    """Sum of all line item totals (before discount)."""
    try:
        items = json.loads(line_items_json or "[]")
    except Exception:
        return 0.0
    total = 0.0
    for item in items:
        p = str(item.get("price", "")).strip()
        if p and p[0] in "£$€":
            p = p[1:]
        try:
            unit = float(p.replace(",", ""))
        except ValueError:
            continue
        qty_str = str(item.get("qty", "1"))
        m = re.match(r"[\d.,]+", qty_str.replace(",", ""))
        qty = float(m.group()) if m else 1.0
        total += unit * qty
    return total


def _doc_grand_total(line_items_json: str, discount_type: str = "percent", discount_value: float = 0.0) -> float:
    """Compute grand total after applying discount."""
    subtotal = _doc_subtotal(line_items_json)
    if discount_value > 0:
        if discount_type == "fixed":
            subtotal = max(0.0, subtotal - discount_value)
        else:
            subtotal = max(0.0, subtotal * (1 - discount_value / 100))
    return subtotal


@app.post("/api/docs")
async def docs_create(
    payload: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]
    doc_type = payload.get("doc_type", "quote")
    if doc_type not in ("quote", "invoice"):
        raise HTTPException(status_code=400, detail="doc_type must be 'quote' or 'invoice'")
    if not payload.get("client_name", "").strip():
        raise HTTPException(status_code=400, detail="client_name is required")
    doc_number = next_doc_number(tid, doc_type)
    doc_id = create_document(tid, doc_type, doc_number, payload)
    # Sync total back to linked enquiry if present
    src = payload.get("source_quote_id")
    if src:
        try:
            total = _doc_grand_total(
                payload.get("line_items", "[]"),
                payload.get("discount_type", "percent"),
                float(payload.get("discount_value") or 0),
            )
            update_quote_total(int(src), total, tid)
        except Exception:
            pass
    _sync_booking_from_doc(payload, None, tid)
    return {"success": True, "id": doc_id, "doc_number": doc_number}


@app.get("/api/docs/{doc_id}")
async def docs_get(
    doc_id: int,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    doc = get_document(doc_id, tenant["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@app.put("/api/docs/{doc_id}")
async def docs_update(
    doc_id: int,
    payload: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]
    doc = get_document(doc_id, tid)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    update_document(doc_id, tid, payload)
    # Sync total back to linked enquiry if line_items changed
    src = payload.get("source_quote_id") or doc.get("source_quote_id")
    if src and "line_items" in payload:
        try:
            disc_type = payload.get("discount_type") or doc.get("discount_type", "percent")
            disc_val = float(payload.get("discount_value") if "discount_value" in payload else (doc.get("discount_value") or 0))
            total = _doc_grand_total(payload["line_items"], disc_type, disc_val)
            update_quote_total(int(src), total, tid)
        except Exception:
            pass
    _sync_booking_from_doc(payload, doc, tid)
    return {"success": True}


@app.delete("/api/docs/{doc_id}")
async def docs_delete(
    doc_id: int,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    doc = get_document(doc_id, tenant["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    delete_document(doc_id, tenant["id"])
    return {"success": True}


def _build_doc_render_context(doc: dict, tenant_id: int) -> dict:
    """Build the Jinja2 template context for a document (shared by print + PDF)."""
    raw_settings = get_setting("doc_settings", tenant_id=tenant_id)
    try:
        settings = json.loads(raw_settings) if raw_settings else {}
    except Exception:
        settings = {}
    if not settings.get("accent"):
        branding = load_branding_config(tenant_id)
        settings["accent"] = branding.get("accent_color", "#fa854f")

    try:
        raw_items = json.loads(doc.get("line_items") or "[]")
    except Exception:
        raw_items = []

    def _parse_price(price_str: str):
        p = (price_str or "").strip()
        if not p or p == "—":
            return None, "£"
        currency = "£"
        if p and p[0] in "£$€":
            currency = p[0]; p = p[1:]
        try:
            return float(p.replace(",", "")), currency
        except ValueError:
            return None, currency

    line_items, grand_total, grand_currency, all_numeric = [], 0.0, "£", True
    for item in raw_items:
        unit_val, currency = _parse_price(item.get("price", ""))
        qty_raw = str(item.get("qty", "1"))
        m = re.match(r"[\d.,]+", qty_raw.replace(",", ""))
        qty = float(m.group()) if m else 1.0
        if unit_val is not None:
            line_total = unit_val * qty
            grand_total += line_total
            grand_currency = currency
            line_items.append({**item, "line_total": f"{currency}{line_total:,.2f}",
                                "unit_price_fmt": f"{currency}{unit_val:,.2f}", "qty_fmt": qty_raw})
        else:
            all_numeric = False
            line_items.append({**item, "line_total": "—",
                                "unit_price_fmt": item.get("price", "—"),
                                "qty_fmt": str(item.get("qty", "1"))})

    subtotal = grand_total  # line items sum before discount
    disc_type  = doc.get("discount_type", "percent") or "percent"
    disc_val   = float(doc.get("discount_value") or 0)
    disc_amount = 0.0
    has_discount = False
    if disc_val > 0 and all_numeric:
        has_discount = True
        if disc_type == "fixed":
            disc_amount = min(disc_val, subtotal)
        else:
            disc_amount = subtotal * disc_val / 100
        grand_total = max(0.0, subtotal - disc_amount)
    subtotal_fmt = f"{grand_currency}{subtotal:,.2f}" if all_numeric else "—"
    grand_total_fmt = f"{grand_currency}{grand_total:,.2f}" if all_numeric else "—"
    discount_fmt = f"{grand_currency}{disc_amount:,.2f}" if all_numeric else "—"
    discount_label = (f"Discount ({disc_val:g}%)" if disc_type == "percent" else "Discount")

    created = doc.get("created_at", "")
    try:
        doc_date = datetime.datetime.fromisoformat(created).strftime("%-d %B %Y")
    except Exception:
        doc_date = created[:10] if created else ""
    doc_type_label = "INVOICE" if doc.get("doc_type") == "invoice" else "QUOTE"

    return {"settings": settings, "line_items": line_items,
            "subtotal_fmt": subtotal_fmt, "grand_total": grand_total_fmt,
            "has_discount": has_discount, "discount_fmt": discount_fmt, "discount_label": discount_label,
            "doc_date": doc_date, "doc_type_label": doc_type_label}


def _render_doc_pdf_bytes(doc: dict, tenant_id: int) -> bytes:
    from weasyprint import HTML
    ctx = _build_doc_render_context(doc, tenant_id)
    html_str = templates.env.get_template("doc_print.html").render(doc=doc, request=None, **ctx)
    return HTML(string=html_str, base_url="https://fonts.googleapis.com").write_pdf()


@app.get("/api/docs/{doc_id}/print", response_class=HTMLResponse)
async def docs_print(
    doc_id: int,
    request: Request,
    token: str = "",
):
    """Serve print-optimised HTML. Auth via ?token= query param."""
    # Resolve tenant from middleware or from the token
    tenant = getattr(request.state, "tenant", None)
    if not tenant and token:
        tok = decode_token(token)
        if tok and tok.get("tenant_id"):
            tenant = get_tenant_by_id(tok["tenant_id"])
    if not tenant:
        # Last try: check token for tenant_id regardless of role
        if token:
            tok = decode_token(token)
            if tok and tok.get("tenant_id"):
                tenant = get_tenant_by_id(tok["tenant_id"])
    if not tenant:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Validate the token belongs to an admin or staff of this tenant
    if token:
        tok = decode_token(token)
        if not tok:
            raise HTTPException(status_code=401, detail="Invalid token")
        if tok.get("role") not in ("tenant_admin", "staff", "super_admin"):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        if tok.get("tenant_id") and tok["tenant_id"] != tenant["id"]:
            raise HTTPException(status_code=403, detail="Token does not match tenant")
    else:
        raise HTTPException(status_code=401, detail="Token required for print access")

    doc = get_document(doc_id, tenant["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    ctx = _build_doc_render_context(doc, tenant["id"])
    return templates.TemplateResponse("doc_print.html", {"request": request, "doc": doc, **ctx})
