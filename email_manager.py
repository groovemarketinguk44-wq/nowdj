"""
Microsoft Graph API / Outlook integration.
OAuth2 flow:  admin → /api/email/auth/connect → Microsoft login → callback → done.
Requires:     pip install httpx
Azure setup:  portal.azure.com → Azure AD → App registrations → New registration
              Redirect URI:  <your-app-url>/api/email/auth/callback
              API permissions: Mail.Read  Mail.Send  Mail.ReadWrite  User.Read  offline_access
"""

import json
import time
import urllib.parse
from typing import Optional

from database import get_setting, set_setting

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

SCOPES = [
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/User.Read",
    "offline_access",
]

DEFAULT_CONFIG: dict = {
    "client_id":     "",
    "client_secret": "",
    "tenant_id":     "common",  # Azure AD tenant (not our app tenant)
    "redirect_uri":  "",
}


# ── Config ─────────────────────────────────────────────────────────────────

def load_config(app_tenant_id: int) -> dict:
    raw = get_setting("email_config", tenant_id=app_tenant_id)
    if raw:
        try:
            return {**DEFAULT_CONFIG, **json.loads(raw)}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(data: dict, app_tenant_id: int) -> None:
    set_setting("email_config", json.dumps(data, ensure_ascii=False), tenant_id=app_tenant_id)


# ── Tokens ─────────────────────────────────────────────────────────────────

def load_tokens(app_tenant_id: int) -> dict:
    raw = get_setting("email_tokens", tenant_id=app_tenant_id)
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return {}


def save_tokens(tokens: dict, app_tenant_id: int) -> None:
    if "expires_in" in tokens:
        tokens = {**tokens, "expires_at": time.time() + int(tokens["expires_in"]) - 60}
    set_setting("email_tokens", json.dumps(tokens), tenant_id=app_tenant_id)


def clear_tokens(app_tenant_id: int) -> None:
    set_setting("email_tokens", "", tenant_id=app_tenant_id)


def is_configured(app_tenant_id: int, config: Optional[dict] = None) -> bool:
    cfg = config or load_config(app_tenant_id)
    return bool(cfg.get("client_id") and cfg.get("client_secret"))


def is_authenticated(app_tenant_id: int) -> bool:
    t = load_tokens(app_tenant_id)
    return bool(t.get("refresh_token") or t.get("access_token"))


# ── OAuth2 ─────────────────────────────────────────────────────────────────

def get_auth_url(app_tenant_id: int) -> str:
    cfg = load_config(app_tenant_id)
    params = {
        "client_id":     cfg["client_id"],
        "response_type": "code",
        "redirect_uri":  cfg.get("redirect_uri", ""),
        "scope":         " ".join(SCOPES),
        "response_mode": "query",
        "prompt":        "select_account",
    }
    azure_tenant = cfg.get("tenant_id") or "common"
    return (
        f"https://login.microsoftonline.com/{azure_tenant}/oauth2/v2.0/authorize?"
        + urllib.parse.urlencode(params)
    )


async def _token_post(data: dict, app_tenant_id: int) -> dict:
    try:
        import httpx
    except ImportError:
        raise RuntimeError("httpx is required: pip install httpx")

    cfg         = load_config(app_tenant_id)
    azure_tenant = cfg.get("tenant_id") or "common"
    url          = f"https://login.microsoftonline.com/{azure_tenant}/oauth2/v2.0/token"

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, data=data)
        resp.raise_for_status()
        return resp.json()


async def exchange_code(code: str, app_tenant_id: int) -> dict:
    cfg    = load_config(app_tenant_id)
    tokens = await _token_post({
        "client_id":     cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "code":          code,
        "redirect_uri":  cfg.get("redirect_uri", ""),
        "grant_type":    "authorization_code",
    }, app_tenant_id)
    save_tokens(tokens, app_tenant_id)
    return tokens


async def get_valid_token(app_tenant_id: int) -> str:
    tokens = load_tokens(app_tenant_id)
    if not tokens:
        raise ValueError("Not connected. Please link your Outlook account.")

    if tokens.get("access_token") and tokens.get("expires_at"):
        if time.time() < float(tokens["expires_at"]):
            return tokens["access_token"]

    if tokens.get("refresh_token"):
        cfg = load_config(app_tenant_id)
        try:
            new_t = await _token_post({
                "client_id":     cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "refresh_token": tokens["refresh_token"],
                "grant_type":    "refresh_token",
            }, app_tenant_id)
            if not new_t.get("refresh_token"):
                new_t["refresh_token"] = tokens["refresh_token"]
            save_tokens(new_t, app_tenant_id)
            return new_t["access_token"]
        except Exception:
            clear_tokens(app_tenant_id)
            raise ValueError("Session expired. Please reconnect your Outlook account.")

    if tokens.get("access_token"):
        return tokens["access_token"]

    raise ValueError("Not authenticated.")


# ── Graph helpers ───────────────────────────────────────────────────────────

async def _get(path: str, app_tenant_id: int, params: dict | None = None) -> Optional[dict]:
    try:
        import httpx
    except ImportError:
        raise RuntimeError("pip install httpx")

    token = await get_valid_token(app_tenant_id)
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"{GRAPH_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
        )
        return r.json() if r.status_code == 200 else None


async def _post(path: str, body: dict, app_tenant_id: int) -> tuple[int, dict]:
    try:
        import httpx
    except ImportError:
        raise RuntimeError("pip install httpx")

    token = await get_valid_token(app_tenant_id)
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{GRAPH_BASE}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
        )
        try:    data = r.json()
        except: data = {}
        return r.status_code, data


async def _patch(path: str, body: dict, app_tenant_id: int) -> int:
    try:
        import httpx
    except ImportError:
        raise RuntimeError("pip install httpx")

    token = await get_valid_token(app_tenant_id)
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.patch(
            f"{GRAPH_BASE}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
        )
        return r.status_code


async def _delete(path: str, app_tenant_id: int) -> int:
    try:
        import httpx
    except ImportError:
        raise RuntimeError("pip install httpx")

    token = await get_valid_token(app_tenant_id)
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.delete(
            f"{GRAPH_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
        )
        return r.status_code


# ── Graph operations ────────────────────────────────────────────────────────

async def get_me(app_tenant_id: int) -> dict:
    return await _get("/me", app_tenant_id, {"$select": "displayName,mail,userPrincipalName"}) or {}


async def get_inbox(app_tenant_id: int, top: int = 40) -> list:
    r = await _get("/me/mailFolders/inbox/messages", app_tenant_id, {
        "$top": top,
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,from,receivedDateTime,isRead,bodyPreview",
    })
    return (r or {}).get("value", [])


async def get_sent(app_tenant_id: int, top: int = 20) -> list:
    r = await _get("/me/mailFolders/sentitems/messages", app_tenant_id, {
        "$top": top,
        "$orderby": "sentDateTime desc",
        "$select": "id,subject,toRecipients,sentDateTime,bodyPreview",
    })
    return (r or {}).get("value", [])


async def get_message(msg_id: str, app_tenant_id: int) -> Optional[dict]:
    return await _get(f"/me/messages/{msg_id}", app_tenant_id, {
        "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,isRead,body,bodyPreview"
    })


async def mark_read(msg_id: str, app_tenant_id: int) -> bool:
    return await _patch(f"/me/messages/{msg_id}", {"isRead": True}, app_tenant_id) == 200


async def delete_message(msg_id: str, app_tenant_id: int) -> bool:
    return await _delete(f"/me/messages/{msg_id}", app_tenant_id) == 204


async def send_email(to_email: str, to_name: str, subject: str, body_html: str, app_tenant_id: int) -> bool:
    status, _ = await _post("/me/sendMail", {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body_html},
            "toRecipients": [
                {"emailAddress": {"address": to_email, "name": to_name or to_email}}
            ],
        },
        "saveToSentItems": True,
    }, app_tenant_id)
    return status == 202


async def reply_email(msg_id: str, body_html: str, app_tenant_id: int) -> bool:
    status, _ = await _post(f"/me/messages/{msg_id}/reply", {
        "message": {"body": {"contentType": "HTML", "content": body_html}}
    }, app_tenant_id)
    return status == 202
