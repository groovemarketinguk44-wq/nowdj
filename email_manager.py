"""
Microsoft Graph API / Outlook integration.
OAuth2 flow:  admin → /api/email/auth/connect → Microsoft login → callback → done.
Requires:     pip install httpx
Azure setup:  portal.azure.com → Azure AD → App registrations → New registration
              Redirect URI:  http://localhost:8000/api/email/auth/callback
              API permissions: Mail.Read  Mail.Send  Mail.ReadWrite  User.Read  offline_access
"""

import json
import os
import time
import urllib.parse
from pathlib import Path
from typing import Optional

_DATA_DIR   = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
CONFIG_PATH  = _DATA_DIR / "email_config.json"
TOKENS_PATH  = _DATA_DIR / "email_tokens.json"
GRAPH_BASE   = "https://graph.microsoft.com/v1.0"
REDIRECT_URI = "http://localhost:8000/api/email/auth/callback"

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
    "tenant_id":     "common",
    "redirect_uri":  REDIRECT_URI,
}


# ── Config ─────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text())}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(data: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ── Tokens ─────────────────────────────────────────────────────────────────

def load_tokens() -> dict:
    if TOKENS_PATH.exists():
        try:
            return json.loads(TOKENS_PATH.read_text())
        except Exception:
            pass
    return {}


def save_tokens(tokens: dict) -> None:
    if "expires_in" in tokens:
        tokens = {**tokens, "expires_at": time.time() + int(tokens["expires_in"]) - 60}
    TOKENS_PATH.write_text(json.dumps(tokens, indent=2))


def clear_tokens() -> None:
    if TOKENS_PATH.exists():
        TOKENS_PATH.unlink()


def is_configured(config: Optional[dict] = None) -> bool:
    cfg = config or load_config()
    return bool(cfg.get("client_id") and cfg.get("client_secret"))


def is_authenticated() -> bool:
    t = load_tokens()
    return bool(t.get("refresh_token") or t.get("access_token"))


# ── OAuth2 ─────────────────────────────────────────────────────────────────

def get_auth_url() -> str:
    cfg = load_config()
    params = {
        "client_id":     cfg["client_id"],
        "response_type": "code",
        "redirect_uri":  cfg.get("redirect_uri", REDIRECT_URI),
        "scope":         " ".join(SCOPES),
        "response_mode": "query",
        "prompt":        "select_account",
    }
    tenant = cfg.get("tenant_id") or "common"
    return (
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?"
        + urllib.parse.urlencode(params)
    )


async def _token_post(data: dict) -> dict:
    try:
        import httpx
    except ImportError:
        raise RuntimeError("httpx is required: pip install httpx")

    cfg    = load_config()
    tenant = cfg.get("tenant_id") or "common"
    url    = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, data=data)
        resp.raise_for_status()
        return resp.json()


async def exchange_code(code: str) -> dict:
    cfg    = load_config()
    tokens = await _token_post({
        "client_id":     cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "code":          code,
        "redirect_uri":  cfg.get("redirect_uri", REDIRECT_URI),
        "grant_type":    "authorization_code",
    })
    save_tokens(tokens)
    return tokens


async def get_valid_token() -> str:
    tokens = load_tokens()
    if not tokens:
        raise ValueError("Not connected. Please link your Outlook account.")

    # Still valid?
    if tokens.get("access_token") and tokens.get("expires_at"):
        if time.time() < float(tokens["expires_at"]):
            return tokens["access_token"]

    # Refresh
    if tokens.get("refresh_token"):
        cfg = load_config()
        try:
            new_t = await _token_post({
                "client_id":     cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "refresh_token": tokens["refresh_token"],
                "grant_type":    "refresh_token",
            })
            if not new_t.get("refresh_token"):
                new_t["refresh_token"] = tokens["refresh_token"]
            save_tokens(new_t)
            return new_t["access_token"]
        except Exception:
            clear_tokens()
            raise ValueError("Session expired. Please reconnect your Outlook account.")

    if tokens.get("access_token"):
        return tokens["access_token"]

    raise ValueError("Not authenticated.")


# ── Graph helpers ───────────────────────────────────────────────────────────

async def _get(path: str, params: dict | None = None) -> Optional[dict]:
    try:
        import httpx
    except ImportError:
        raise RuntimeError("pip install httpx")

    token = await get_valid_token()
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"{GRAPH_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
        )
        return r.json() if r.status_code == 200 else None


async def _post(path: str, body: dict) -> tuple[int, dict]:
    try:
        import httpx
    except ImportError:
        raise RuntimeError("pip install httpx")

    token = await get_valid_token()
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{GRAPH_BASE}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
        )
        try:    data = r.json()
        except: data = {}
        return r.status_code, data


async def _patch(path: str, body: dict) -> int:
    try:
        import httpx
    except ImportError:
        raise RuntimeError("pip install httpx")

    token = await get_valid_token()
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.patch(
            f"{GRAPH_BASE}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
        )
        return r.status_code


async def _delete(path: str) -> int:
    try:
        import httpx
    except ImportError:
        raise RuntimeError("pip install httpx")

    token = await get_valid_token()
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.delete(
            f"{GRAPH_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
        )
        return r.status_code


# ── Graph operations ────────────────────────────────────────────────────────

async def get_me() -> dict:
    return await _get("/me", {"$select": "displayName,mail,userPrincipalName"}) or {}


async def get_inbox(top: int = 40) -> list:
    r = await _get("/me/mailFolders/inbox/messages", {
        "$top": top,
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,from,receivedDateTime,isRead,bodyPreview",
    })
    return (r or {}).get("value", [])


async def get_sent(top: int = 20) -> list:
    r = await _get("/me/mailFolders/sentitems/messages", {
        "$top": top,
        "$orderby": "sentDateTime desc",
        "$select": "id,subject,toRecipients,sentDateTime,bodyPreview",
    })
    return (r or {}).get("value", [])


async def get_message(msg_id: str) -> Optional[dict]:
    return await _get(f"/me/messages/{msg_id}", {
        "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,isRead,body,bodyPreview"
    })


async def mark_read(msg_id: str) -> bool:
    return await _patch(f"/me/messages/{msg_id}", {"isRead": True}) == 200


async def delete_message(msg_id: str) -> bool:
    return await _delete(f"/me/messages/{msg_id}") == 204


async def send_email(to_email: str, to_name: str, subject: str, body_html: str) -> bool:
    status, _ = await _post("/me/sendMail", {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body_html},
            "toRecipients": [
                {"emailAddress": {"address": to_email, "name": to_name or to_email}}
            ],
        },
        "saveToSentItems": True,
    })
    return status == 202


async def reply_email(msg_id: str, body_html: str) -> bool:
    status, _ = await _post(f"/me/messages/{msg_id}/reply", {
        "message": {"body": {"contentType": "HTML", "content": body_html}}
    })
    return status == 202
