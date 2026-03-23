from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from catalog_store import (
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
    init_db, migrate_null_tenant_ids,
    get_all_quotes, get_quote_by_id, save_quote, update_quote_status,
    get_all_templates, get_template_by_id, create_template, update_template, delete_template,
    seed_templates_for_tenant,
    get_user_by_email, get_user_by_id, get_all_users, create_user, delete_user,
    get_quotes_by_email, update_user,
    get_all_staff, get_staff_by_email, get_staff_by_id, create_staff_member,
    delete_staff_member, update_staff_member_info,
    get_all_bookings, get_booking_by_id, get_bookings_for_user, get_bookings_for_staff,
    create_booking, update_booking, delete_booking,
    get_setting, set_setting,
    create_tenant, get_tenant_by_slug, get_tenant_by_id, get_tenant_by_email,
    get_all_tenants, update_tenant, delete_tenant,
    find_staff_globally, find_user_globally,
)
from models import QuoteRequest, StatusUpdate
from auth import (
    hash_password, verify_password, create_token, decode_token,
    require_customer, require_staff, require_tenant_admin, require_super_admin,
)
import os
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
    return templates.TemplateResponse("builder.html", {
        "request": request,
        "catalog": load_catalog(tid),
        "config": load_site_config(tid),
        "branding": branding,
        "branding_style": build_branding_style(branding),
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
    tenant = _get_tenant_or_404(request)
    branding = load_branding_config(tenant["id"], tenant["name"])
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
        })
    tenant = _get_tenant_or_404(request)
    branding = load_branding_config(tenant["id"], tenant["name"])
    return templates.TemplateResponse("login.html", {
        "request": request,
        "branding": branding,
        "branding_style": build_branding_style(branding),
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
            qty = quote.item_quantities.get(item_id, 1)
            price = base_price * qty
            total += price
            unit = "hrs" if pt == "hourly" else "days"
            detail = {"id": item_id, "name": name, "price": price,
                      "base_price": base_price, "qty": qty, "unit": unit}
        else:
            price = base_price
            total += price
            detail = {"id": item_id, "name": name, "price": price}
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

    return {
        "success": True,
        "quote_id": quote_id,
        "total_price": total,
        "message": f"Quote #{quote_id} submitted! We'll be in touch shortly.",
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
    configured   = em.is_configured(tid)
    authenticated = em.is_authenticated(tid) if configured else False
    user: dict = {}
    if authenticated:
        try:
            user = await em.get_me(tid)
        except Exception:
            authenticated = False
    return {"configured": configured, "authenticated": authenticated, "user": user}


@app.get("/api/email/config")
async def email_get_config(
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    cfg = em.load_config(tenant["id"])
    return {k: (v if k != "client_secret" else ("***" if v else "")) for k, v in cfg.items()}


@app.post("/api/email/config")
async def email_save_config(
    payload: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]
    existing = em.load_config(tid)
    if payload.get("client_secret") == "***":
        payload["client_secret"] = existing.get("client_secret", "")
    em.save_config({**existing, **payload}, tid)
    return {"success": True}


@app.get("/api/email/auth/connect")
async def email_auth_connect(
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]
    if not em.is_configured(tid):
        raise HTTPException(status_code=400, detail="Email not configured. Add client_id and client_secret first.")
    return RedirectResponse(em.get_auth_url(tid))


@app.get("/api/email/auth/callback")
async def email_auth_callback(request: Request, code: str = "", error: str = ""):
    tenant = _get_tenant_or_404(request)
    tid = tenant["id"]
    if error:
        return HTMLResponse(f"<script>window.location='/admin#email';alert('Auth error: {error}');</script>")
    if not code:
        raise HTTPException(status_code=400, detail="No code provided")
    try:
        await em.exchange_code(code, tid)
    except Exception as exc:
        return HTMLResponse(f"<script>window.location='/admin#email';alert('Auth failed: {exc}');</script>")
    return HTMLResponse("<script>window.opener&&window.opener.postMessage('email_auth_ok','*');window.close();if(!window.opener)window.location='/admin#email';</script>")


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
    if not to_email or not subject:
        raise HTTPException(status_code=400, detail="to_email and subject are required")
    try:
        ok = await em.send_email(to_email, to_name, subject, body, tenant["id"])
        return {"success": ok}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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

    # ── If on a specific tenant (subdomain or /t/{slug} path), check only that tenant
    tenant = getattr(request.state, "tenant", None)
    if tenant:
        tid = tenant["id"]
        slug = tenant["slug"]
        if email == tenant["email"].lower() and verify_password(password, tenant["password_hash"]):
            token = create_token("tenant_admin", tenant["id"], tenant["email"], tenant["name"], tenant_id=tid)
            return {"role": "tenant_admin", "token": token, "name": tenant["name"], "email": tenant["email"], "slug": slug}
        member = get_staff_by_email(email, tid)
        if member and verify_password(password, member["password_hash"]):
            token = create_token("staff", member["id"], member["email"], member["name"], tenant_id=tid)
            return {"role": "staff", "token": token, "name": member["name"], "email": member["email"], "slug": slug}
        user = get_user_by_email(email, tid)
        if user and verify_password(password, user["password_hash"]):
            token = create_token("customer", user["id"], user["email"], user["name"], tenant_id=tid)
            return {"role": "customer", "token": token, "name": user["name"], "email": user["email"], "slug": slug}
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    # ── Admin domain with no tenant context — search globally across all workspaces
    tenant_row = get_tenant_by_email(email)
    if tenant_row and verify_password(password, tenant_row["password_hash"]):
        token = create_token("tenant_admin", tenant_row["id"], tenant_row["email"], tenant_row["name"], tenant_id=tenant_row["id"])
        return {"role": "tenant_admin", "token": token, "name": tenant_row["name"], "email": tenant_row["email"], "slug": tenant_row["slug"]}

    member = find_staff_globally(email)
    if member and verify_password(password, member["password_hash"]):
        token = create_token("staff", member["id"], member["email"], member["name"], tenant_id=member["tenant_id"])
        return {"role": "staff", "token": token, "name": member["name"], "email": member["email"], "slug": member["tenant_slug"]}

    user = find_user_globally(email)
    if user and verify_password(password, user["password_hash"]):
        token = create_token("customer", user["id"], user["email"], user["name"], tenant_id=user["tenant_id"])
        return {"role": "customer", "token": token, "name": user["name"], "email": user["email"], "slug": user["tenant_slug"]}

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
    return get_all_bookings(tenant["id"])


@app.post("/api/admin/bookings")
async def admin_create_booking(
    payload: dict,
    request: Request,
    _admin: Annotated[dict, Depends(require_tenant_admin)],
):
    tenant = _get_tenant_or_404(request)
    bid = create_booking(payload, tenant["id"])
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
    name     = payload.get("name", tenant["name"]).strip()
    email    = payload.get("email", tenant["email"]).strip().lower()
    plan     = payload.get("plan", tenant["plan"])
    new_pw   = payload.get("password", "")
    pw_hash  = hash_password(new_pw) if new_pw else None
    update_tenant(tid, name, email, plan, pw_hash)
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
