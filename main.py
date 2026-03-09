from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from catalog_store import (
    get_prices,
    load_catalog,
    load_site_config,
    save_catalog,
    save_site_config,
)
from database import (
    get_all_quotes, get_quote_by_id, init_db, save_quote, update_quote_status,
    get_all_templates, get_template_by_id, create_template, update_template, delete_template,
)
from models import QuoteRequest, StatusUpdate
import email_manager as em


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="NowDJ", version="1.0.0", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def builder_page(request: Request):
    return templates.TemplateResponse("builder.html", {
        "request": request,
        "catalog": load_catalog(),
        "config": load_site_config(),
    })


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})


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
