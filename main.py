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
    get_all_quotes, get_quote_by_id, init_db, save_quote, update_quote_status,
    get_all_templates, get_template_by_id, create_template, update_template, delete_template,
    get_user_by_email, get_user_by_id, get_all_users, create_user, delete_user, get_quotes_by_email,
    get_all_staff, get_staff_by_email, get_staff_by_id, create_staff_member, delete_staff_member,
    get_all_bookings, get_booking_by_id, get_bookings_for_user, get_bookings_for_staff,
    create_booking, update_booking, delete_booking,
)
from models import QuoteRequest, StatusUpdate
from auth import hash_password, verify_password, create_token, require_customer, require_staff
import email_manager as em


DEFAULT_PW = "     "  # 5 spaces

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _seed_defaults()
    yield


def _seed_defaults():
    from database import get_setting, set_setting
    # Admin credentials
    if not get_setting("admin_email"):
        set_setting("admin_email", "ben@groovemarketing.co.uk")
        set_setting("admin_pw_hash", hash_password(DEFAULT_PW))
    # Default staff account
    if not get_staff_by_email("staffmember@dj.co.uk"):
        create_staff_member("Staff Member", "staffmember@dj.co.uk", hash_password(DEFAULT_PW))
    # Default customer account
    if not get_user_by_email("client@dj.co.uk"):
        create_user("Test Client", "client@dj.co.uk", hash_password(DEFAULT_PW))


app = FastAPI(title="NowDJ", version="1.0.0", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def builder_page(request: Request):
    branding = load_branding_config()
    return templates.TemplateResponse("builder.html", {
        "request": request,
        "catalog": load_catalog(),
        "config": load_site_config(),
        "branding": branding,
        "branding_style": build_branding_style(branding),
    })


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    branding = load_branding_config()
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "branding": branding,
        "branding_style": build_branding_style(branding),
    })


@app.get("/portal", response_class=HTMLResponse)
async def portal_page(request: Request):
    branding = load_branding_config()
    return templates.TemplateResponse("portal.html", {
        "request": request,
        "branding": branding,
        "branding_style": build_branding_style(branding),
    })


@app.get("/staff-portal", response_class=HTMLResponse)
async def staff_portal_page(request: Request):
    branding = load_branding_config()
    return templates.TemplateResponse("staff.html", {
        "request": request,
        "branding": branding,
        "branding_style": build_branding_style(branding),
    })


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    branding = load_branding_config()
    return templates.TemplateResponse("login.html", {
        "request": request,
        "branding": branding,
        "branding_style": build_branding_style(branding),
    })


# ---------------------------------------------------------------------------
# Catalog & site config API
# ---------------------------------------------------------------------------

@app.get("/api/catalog")
async def api_get_catalog():
    return load_catalog()


@app.post("/api/catalog")
async def api_save_catalog(catalog: dict):
    save_catalog(catalog)
    return {"success": True}


@app.post("/api/catalog/reset")
async def api_reset_catalog():
    from pathlib import Path
    override = Path(__file__).parent / "catalog_override.json"
    if override.exists():
        override.unlink()
    return {"success": True}


@app.get("/api/site-config")
async def api_get_site_config():
    return load_site_config()


@app.post("/api/site-config")
async def api_save_site_config(config: dict):
    save_site_config(config)
    return {"success": True}


@app.get("/api/branding")
async def api_get_branding():
    return load_branding_config()


@app.post("/api/branding")
async def api_save_branding(config: dict):
    save_branding_config(config)
    return {"success": True}


# ---------------------------------------------------------------------------
# Quote submission
# ---------------------------------------------------------------------------

@app.post("/submit-quote")
async def submit_quote(quote: QuoteRequest):
    if not quote.selected_items:
        raise HTTPException(status_code=400, detail="Please select at least one item.")

    catalog = load_catalog()
    prices = get_prices(catalog)

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

        # pricing_type: "fixed" | "hourly" | "daily" | "tbc"
        # backward-compat: old `hourly: true` flag treated as "hourly"
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
    })

    return {
        "success": True,
        "quote_id": quote_id,
        "total_price": total,
        "message": f"Quote #{quote_id} submitted! We'll be in touch shortly.",
    }


# ---------------------------------------------------------------------------
# Quotes API
# ---------------------------------------------------------------------------

@app.get("/quotes")
async def list_quotes():
    return get_all_quotes()


@app.get("/quotes/{quote_id}")
async def get_quote(quote_id: int):
    q = get_quote_by_id(quote_id)
    if not q:
        raise HTTPException(status_code=404, detail="Quote not found")
    return q


@app.patch("/quotes/{quote_id}/status")
async def patch_status(quote_id: int, payload: StatusUpdate):
    q = get_quote_by_id(quote_id)
    if not q:
        raise HTTPException(status_code=404, detail="Quote not found")
    update_quote_status(quote_id, payload.status)
    return {"success": True, "quote_id": quote_id, "status": payload.status}


# ---------------------------------------------------------------------------
# Email — config & auth
# ---------------------------------------------------------------------------

@app.get("/api/email/status")
async def email_status():
    configured = em.is_configured()
    authenticated = em.is_authenticated() if configured else False
    user: dict = {}
    if authenticated:
        try:
            user = await em.get_me()
        except Exception:
            authenticated = False
    return {"configured": configured, "authenticated": authenticated, "user": user}


@app.get("/api/email/config")
async def email_get_config():
    cfg = em.load_config()
    # Never expose the secret
    return {k: (v if k != "client_secret" else ("***" if v else "")) for k, v in cfg.items()}


@app.post("/api/email/config")
async def email_save_config(payload: dict):
    existing = em.load_config()
    # Keep existing secret if placeholder sent
    if payload.get("client_secret") == "***":
        payload["client_secret"] = existing.get("client_secret", "")
    em.save_config({**existing, **payload})
    return {"success": True}


@app.get("/api/email/auth/connect")
async def email_auth_connect():
    if not em.is_configured():
        raise HTTPException(status_code=400, detail="Email not configured. Add client_id and client_secret first.")
    return RedirectResponse(em.get_auth_url())


@app.get("/api/email/auth/callback")
async def email_auth_callback(code: str = "", error: str = ""):
    if error:
        return HTMLResponse(f"<script>window.location='/admin#email';alert('Auth error: {error}');</script>")
    if not code:
        raise HTTPException(status_code=400, detail="No code provided")
    try:
        await em.exchange_code(code)
    except Exception as exc:
        return HTMLResponse(f"<script>window.location='/admin#email';alert('Auth failed: {exc}');</script>")
    return HTMLResponse("<script>window.opener&&window.opener.postMessage('email_auth_ok','*');window.close();if(!window.opener)window.location='/admin#email';</script>")


@app.post("/api/email/auth/disconnect")
async def email_auth_disconnect():
    em.clear_tokens()
    return {"success": True}


# ---------------------------------------------------------------------------
# Email — mailbox operations
# ---------------------------------------------------------------------------

@app.get("/api/email/inbox")
async def email_inbox(top: int = 40):
    try:
        return await em.get_inbox(top)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/email/sent")
async def email_sent(top: int = 20):
    try:
        return await em.get_sent(top)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/email/messages/{msg_id:path}")
async def email_get_message(msg_id: str):
    try:
        msg = await em.get_message(msg_id)
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
async def email_mark_read(msg_id: str):
    try:
        ok = await em.mark_read(msg_id)
        return {"success": ok}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.delete("/api/email/messages/{msg_id:path}")
async def email_delete_message(msg_id: str):
    try:
        ok = await em.delete_message(msg_id)
        return {"success": ok}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.post("/api/email/send")
async def email_send(payload: dict):
    to_email = payload.get("to_email", "")
    to_name  = payload.get("to_name", "")
    subject  = payload.get("subject", "")
    body     = payload.get("body", "")
    if not to_email or not subject:
        raise HTTPException(status_code=400, detail="to_email and subject are required")
    try:
        ok = await em.send_email(to_email, to_name, subject, body)
        return {"success": ok}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/email/reply/{msg_id:path}")
async def email_reply(msg_id: str, payload: dict):
    body = payload.get("body", "")
    try:
        ok = await em.reply_email(msg_id, body)
        return {"success": ok}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Email — templates CRUD
# ---------------------------------------------------------------------------

@app.get("/api/email/templates")
async def list_email_templates():
    return get_all_templates()


@app.get("/api/email/templates/{tid}")
async def get_email_template(tid: int):
    t = get_template_by_id(tid)
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    return t


@app.post("/api/email/templates")
async def create_email_template(payload: dict):
    name    = payload.get("name", "").strip()
    subject = payload.get("subject", "").strip()
    body    = payload.get("body", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    tid = create_template(name, subject, body)
    return {"success": True, "id": tid}


@app.put("/api/email/templates/{tid}")
async def update_email_template(tid: int, payload: dict):
    t = get_template_by_id(tid)
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    update_template(tid, payload.get("name", t["name"]), payload.get("subject", t["subject"]), payload.get("body", t["body"]))
    return {"success": True}


@app.delete("/api/email/templates/{tid}")
async def delete_email_template(tid: int):
    t = get_template_by_id(tid)
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    delete_template(tid)
    return {"success": True}


# ---------------------------------------------------------------------------
# Auth — customer
# ---------------------------------------------------------------------------

@app.post("/api/auth/customer/register")
async def customer_register(payload: dict):
    name     = payload.get("name", "").strip()
    email    = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    if not name or not email or not password:
        raise HTTPException(status_code=400, detail="name, email and password are required")
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if get_user_by_email(email):
        raise HTTPException(status_code=409, detail="An account with that email already exists")
    uid   = create_user(name, email, hash_password(password))
    token = create_token("customer", uid, email, name)
    return {"token": token, "name": name, "email": email}


@app.post("/api/auth/customer/login")
async def customer_login(payload: dict):
    email    = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    user     = get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token = create_token("customer", user["id"], user["email"], user["name"])
    return {"token": token, "name": user["name"], "email": user["email"]}


# ---------------------------------------------------------------------------
# Auth — staff
# ---------------------------------------------------------------------------

@app.post("/api/auth/staff/login")
async def staff_login(payload: dict):
    email    = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    member   = get_staff_by_email(email)
    if not member or not verify_password(password, member["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token = create_token("staff", member["id"], member["email"], member["name"])
    return {"token": token, "name": member["name"], "email": member["email"]}


# ---------------------------------------------------------------------------
# Customer portal API
# ---------------------------------------------------------------------------

@app.get("/api/customer/me")
async def customer_me(user: Annotated[dict, Depends(require_customer)]):
    return {"id": user["uid"], "name": user["name"], "email": user["email"]}


@app.get("/api/customer/quotes")
async def customer_quotes(user: Annotated[dict, Depends(require_customer)]):
    return get_quotes_by_email(user["email"])


@app.get("/api/customer/bookings")
async def customer_bookings(user: Annotated[dict, Depends(require_customer)]):
    return get_bookings_for_user(user["uid"])


# ---------------------------------------------------------------------------
# Staff portal API
# ---------------------------------------------------------------------------

@app.get("/api/staff/me")
async def staff_me(member: Annotated[dict, Depends(require_staff)]):
    return {"id": member["uid"], "name": member["name"], "email": member["email"]}


@app.get("/api/staff/bookings")
async def staff_bookings(member: Annotated[dict, Depends(require_staff)]):
    return get_bookings_for_staff(member["uid"])


# ---------------------------------------------------------------------------
# Admin — staff management
# ---------------------------------------------------------------------------

@app.get("/api/admin/staff")
async def admin_list_staff():
    return get_all_staff()


@app.post("/api/admin/staff")
async def admin_create_staff(payload: dict):
    name     = payload.get("name", "").strip()
    email    = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    if not name or not email or not password:
        raise HTTPException(status_code=400, detail="name, email and password required")
    if get_staff_by_email(email):
        raise HTTPException(status_code=409, detail="Staff member with that email already exists")
    sid = create_staff_member(name, email, hash_password(password))
    return {"success": True, "id": sid}


@app.delete("/api/admin/staff/{sid}")
async def admin_delete_staff(sid: int):
    if not get_staff_by_id(sid):
        raise HTTPException(status_code=404, detail="Staff member not found")
    delete_staff_member(sid)
    return {"success": True}


# ---------------------------------------------------------------------------
# Admin — bookings
# ---------------------------------------------------------------------------

@app.get("/api/admin/bookings")
async def admin_list_bookings():
    return get_all_bookings()


@app.post("/api/admin/bookings")
async def admin_create_booking(payload: dict):
    bid = create_booking(payload)
    return {"success": True, "id": bid}


@app.patch("/api/admin/bookings/{bid}")
async def admin_update_booking(bid: int, payload: dict):
    if not get_booking_by_id(bid):
        raise HTTPException(status_code=404, detail="Booking not found")
    update_booking(bid, payload)
    return {"success": True}


@app.delete("/api/admin/bookings/{bid}")
async def admin_delete_booking(bid: int):
    if not get_booking_by_id(bid):
        raise HTTPException(status_code=404, detail="Booking not found")
    delete_booking(bid)
    return {"success": True}


# ---------------------------------------------------------------------------
# Admin — user (client) management
# ---------------------------------------------------------------------------

@app.get("/api/admin/users")
async def admin_list_users():
    return get_all_users()


@app.patch("/api/admin/users/{uid}")
async def admin_update_user(uid: int, payload: dict):
    user = get_user_by_id(uid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    new_name  = payload.get("name", "").strip()
    new_email = payload.get("email", "").strip().lower()
    new_pw    = payload.get("password", "")
    if not new_name or not new_email:
        raise HTTPException(status_code=400, detail="name and email required")
    from database import update_user
    pw_hash = hash_password(new_pw) if new_pw else None
    update_user(uid, new_name, new_email, pw_hash)
    return {"success": True}


@app.delete("/api/admin/users/{uid}")
async def admin_delete_user(uid: int):
    if not get_user_by_id(uid):
        raise HTTPException(status_code=404, detail="User not found")
    delete_user(uid)
    return {"success": True}


# ---------------------------------------------------------------------------
# Unified login
# ---------------------------------------------------------------------------

@app.post("/api/auth/login")
async def unified_login(payload: dict):
    from database import get_setting
    email    = payload.get("email", "").strip().lower()
    password = payload.get("password", "")

    # Admin check
    admin_email   = get_setting("admin_email", "ben@groovemarketing.co.uk").lower()
    admin_pw_hash = get_setting("admin_pw_hash", "")
    if email == admin_email and admin_pw_hash and verify_password(password, admin_pw_hash):
        return {"role": "admin"}

    # Staff check
    member = get_staff_by_email(email)
    if member and verify_password(password, member["password_hash"]):
        token = create_token("staff", member["id"], member["email"], member["name"])
        return {"role": "staff", "token": token, "name": member["name"], "email": member["email"]}

    # Customer check
    user = get_user_by_email(email)
    if user and verify_password(password, user["password_hash"]):
        token = create_token("customer", user["id"], user["email"], user["name"])
        return {"role": "customer", "token": token, "name": user["name"], "email": user["email"]}

    raise HTTPException(status_code=401, detail="Incorrect email or password")


# ---------------------------------------------------------------------------
# Profile updates
# ---------------------------------------------------------------------------

@app.patch("/api/customer/profile")
async def update_customer_profile(
    payload: dict,
    user: Annotated[dict, Depends(require_customer)],
):
    from database import update_user
    new_name  = payload.get("name", "").strip()
    new_email = payload.get("email", "").strip().lower()
    new_pw    = payload.get("password", "")
    if not new_name or not new_email:
        raise HTTPException(status_code=400, detail="name and email required")
    pw_hash = hash_password(new_pw) if new_pw else None
    update_user(user["uid"], new_name, new_email, pw_hash)
    token = create_token("customer", user["uid"], new_email, new_name)
    return {"success": True, "token": token, "name": new_name, "email": new_email}


@app.patch("/api/staff/profile")
async def update_staff_profile(
    payload: dict,
    member: Annotated[dict, Depends(require_staff)],
):
    from database import update_staff_member_info
    new_name  = payload.get("name", "").strip()
    new_email = payload.get("email", "").strip().lower()
    new_pw    = payload.get("password", "")
    if not new_name or not new_email:
        raise HTTPException(status_code=400, detail="name and email required")
    pw_hash = hash_password(new_pw) if new_pw else None
    update_staff_member_info(member["uid"], new_name, new_email, pw_hash)
    token = create_token("staff", member["uid"], new_email, new_name)
    return {"success": True, "token": token, "name": new_name, "email": new_email}


@app.patch("/api/admin/credentials")
async def update_admin_credentials(payload: dict):
    from database import get_setting, set_setting
    new_email = payload.get("email", "").strip().lower()
    new_pw    = payload.get("password", "")
    if not new_email:
        raise HTTPException(status_code=400, detail="email required")
    set_setting("admin_email", new_email)
    if new_pw:
        set_setting("admin_pw_hash", hash_password(new_pw))
    return {"success": True}
