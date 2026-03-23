"""
Microsoft Graph / Outlook OAuth2 integration.

Credentials are stored once in environment variables (set by the app owner).
Each tenant gets their own access/refresh tokens, stored in the settings table.

Flow:
  1. Tenant admin clicks "Connect Outlook"
  2. Browser → GET /api/email/connect  → 302 to Microsoft login
  3. Microsoft → GET /api/email/auth/callback?code=X&state=Y
  4. Server exchanges code for tokens, stores per-tenant, redirects back to admin
"""

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse
from typing import Optional

from database import get_setting, set_setting

# ---------------------------------------------------------------------------
# App-level credentials (set these in your .env / Render env vars)
# ---------------------------------------------------------------------------

CLIENT_ID     = os.environ.get("MICROSOFT_CLIENT_ID",     "")
CLIENT_SECRET = os.environ.get("MICROSOFT_CLIENT_SECRET", "")
TENANT        = os.environ.get("MICROSOFT_TENANT",        "common")
REDIRECT_URI  = os.environ.get("MICROSOFT_REDIRECT_URI",  "")

# Used to sign the state parameter so it can't be forged
_STATE_SECRET = os.environ.get("SECRET_KEY", "nowdj-dev-secret-change-me-in-production")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

SCOPES = [
    "offline_access",
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/User.Read",
]


# ---------------------------------------------------------------------------
# State parameter — encodes tenant_id, expires in 15 min, HMAC-signed
# ---------------------------------------------------------------------------

def make_state(tenant_id: int) -> str:
    """Return a URL-safe state string encoding tenant_id + expiry + signature."""
    payload = f"{tenant_id}:{int(time.time()) + 900}"          # 15-min window
    sig = hmac.new(
        _STATE_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()[:16]
    raw = f"{payload}:{sig}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def verify_state(state: str) -> Optional[int]:
    """Return tenant_id if state is valid and unexpired, else None."""
    try:
        raw     = base64.urlsafe_b64decode(state.encode()).decode()
        parts   = raw.rsplit(":", 1)
        if len(parts) != 2:
            return None
        body, sig = parts
        expected = hmac.new(
            _STATE_SECRET.encode(), body.encode(), hashlib.sha256
        ).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            return None
        tenant_id_str, expiry_str = body.split(":", 1)
        if int(expiry_str) < time.time():
            return None
        return int(tenant_id_str)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Token storage — per-tenant in settings table
# ---------------------------------------------------------------------------

def load_tokens(tenant_id: int) -> dict:
    raw = get_setting("email_tokens", tenant_id=tenant_id)
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return {}


def save_tokens(tokens: dict, tenant_id: int) -> None:
    if "expires_in" in tokens and "expires_at" not in tokens:
        tokens = {**tokens, "expires_at": time.time() + int(tokens["expires_in"]) - 60}
    set_setting("email_tokens", json.dumps(tokens), tenant_id=tenant_id)


def clear_tokens(tenant_id: int) -> None:
    set_setting("email_tokens", "", tenant_id=tenant_id)


def is_connected(tenant_id: int) -> bool:
    """True if the tenant has a stored access or refresh token."""
    t = load_tokens(tenant_id)
    return bool(t.get("refresh_token") or t.get("access_token"))


# Keep old name as alias so nothing else breaks
def is_authenticated(tenant_id: int) -> bool:
    return is_connected(tenant_id)


# ---------------------------------------------------------------------------
# OAuth2 — build auth URL and exchange code for tokens
# ---------------------------------------------------------------------------

def get_auth_url(state: str) -> str:
    """Build the Microsoft OAuth2 authorisation URL."""
    params = {
        "client_id":     CLIENT_ID,
        "response_type": "code",
        "redirect_uri":  REDIRECT_URI,
        "scope":         " ".join(SCOPES),
        "response_mode": "query",
        "state":         state,
        "prompt":        "select_account",
    }
    return (
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize?"
        + urllib.parse.urlencode(params)
    )


async def _token_post(data: dict) -> dict:
    try:
        import httpx
    except ImportError:
        raise RuntimeError("httpx is required: pip install httpx")
    url = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, data=data)
        resp.raise_for_status()
        return resp.json()


async def exchange_code(code: str, tenant_id: int) -> dict:
    """Exchange an authorisation code for tokens and save them."""
    tokens = await _token_post({
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code":          code,
        "redirect_uri":  REDIRECT_URI,
        "grant_type":    "authorization_code",
    })
    save_tokens(tokens, tenant_id)
    return tokens


async def get_valid_token(tenant_id: int) -> str:
    """Return a valid access token, refreshing if necessary."""
    tokens = load_tokens(tenant_id)
    if not tokens:
        raise ValueError("Outlook not connected. Please click 'Connect Outlook'.")

    # Still valid
    if tokens.get("access_token") and tokens.get("expires_at"):
        if time.time() < float(tokens["expires_at"]):
            return tokens["access_token"]

    # Refresh
    if tokens.get("refresh_token"):
        try:
            new_t = await _token_post({
                "client_id":     CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "refresh_token": tokens["refresh_token"],
                "grant_type":    "refresh_token",
            })
            if not new_t.get("refresh_token"):
                new_t["refresh_token"] = tokens["refresh_token"]
            save_tokens(new_t, tenant_id)
            return new_t["access_token"]
        except Exception:
            clear_tokens(tenant_id)
            raise ValueError("Outlook session expired. Please reconnect.")

    if tokens.get("access_token"):
        return tokens["access_token"]

    raise ValueError("Outlook not connected.")


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------

async def _get(path: str, tenant_id: int, params: dict | None = None) -> Optional[dict]:
    try:
        import httpx
    except ImportError:
        raise RuntimeError("pip install httpx")
    token = await get_valid_token(tenant_id)
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"{GRAPH_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
        )
        return r.json() if r.status_code == 200 else None


async def _post(path: str, body: dict, tenant_id: int) -> tuple[int, dict]:
    try:
        import httpx
    except ImportError:
        raise RuntimeError("pip install httpx")
    token = await get_valid_token(tenant_id)
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{GRAPH_BASE}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
        )
        try:    data = r.json()
        except: data = {}
        return r.status_code, data


async def _patch(path: str, body: dict, tenant_id: int) -> int:
    try:
        import httpx
    except ImportError:
        raise RuntimeError("pip install httpx")
    token = await get_valid_token(tenant_id)
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.patch(
            f"{GRAPH_BASE}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
        )
        return r.status_code


async def _delete(path: str, tenant_id: int) -> int:
    try:
        import httpx
    except ImportError:
        raise RuntimeError("pip install httpx")
    token = await get_valid_token(tenant_id)
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.delete(
            f"{GRAPH_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
        )
        return r.status_code


# ---------------------------------------------------------------------------
# Graph operations
# ---------------------------------------------------------------------------

async def get_me(tenant_id: int) -> dict:
    return await _get("/me", tenant_id, {"$select": "displayName,mail,userPrincipalName"}) or {}


async def get_inbox(tenant_id: int, top: int = 40) -> list:
    r = await _get("/me/mailFolders/inbox/messages", tenant_id, {
        "$top": top,
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,from,receivedDateTime,isRead,bodyPreview",
    })
    return (r or {}).get("value", [])


async def get_sent(tenant_id: int, top: int = 20) -> list:
    r = await _get("/me/mailFolders/sentitems/messages", tenant_id, {
        "$top": top,
        "$orderby": "sentDateTime desc",
        "$select": "id,subject,toRecipients,sentDateTime,bodyPreview",
    })
    return (r or {}).get("value", [])


async def get_message(msg_id: str, tenant_id: int) -> Optional[dict]:
    return await _get(f"/me/messages/{msg_id}", tenant_id, {
        "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,isRead,body,bodyPreview"
    })


async def mark_read(msg_id: str, tenant_id: int) -> bool:
    return await _patch(f"/me/messages/{msg_id}", {"isRead": True}, tenant_id) == 200


async def delete_message(msg_id: str, tenant_id: int) -> bool:
    return await _delete(f"/me/messages/{msg_id}", tenant_id) == 204


async def send_email(to_email: str, to_name: str, subject: str, body_html: str, tenant_id: int, attachments: list | None = None) -> bool:
    message: dict = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": body_html},
        "toRecipients": [
            {"emailAddress": {"address": to_email, "name": to_name or to_email}}
        ],
    }
    if attachments:
        message["attachments"] = attachments
    status, _ = await _post("/me/sendMail", {"message": message, "saveToSentItems": True}, tenant_id)
    return status == 202


async def reply_email(msg_id: str, body_html: str, tenant_id: int) -> bool:
    status, _ = await _post(f"/me/messages/{msg_id}/reply", {
        "message": {"body": {"contentType": "HTML", "content": body_html}}
    }, tenant_id)
    return status == 202
