"""
Auto-import website enquiries from Outlook.

Website contact forms (e.g. Hertfordshire Photo Booth Hire) email a submission
notification to the connected inbox. This module reads those emails via
Microsoft Graph, parses the submitted fields out of the email body, and creates
a matching enquiry (quote) so it lands at the top of the admin Enquiries list.

Design notes:
  * Trigger is a fixed, reliable subject line + expected sender (both configurable
    via settings, with sensible defaults).
  * We parse the *email body*, not the attached PDF — the body already contains
    every field in a predictable label/value layout, so PDF extraction is
    unnecessary and less robust.
  * Dedup is by Graph message id, stored in a per-tenant setting, so an email is
    never imported twice regardless of its read/unread state.
"""

import html
import json
import re
import time
from typing import Optional

import email_manager as em
from database import get_setting, set_setting, save_quote

# ---------------------------------------------------------------------------
# Configuration (per-tenant, stored in settings; defaults match the live setup)
# ---------------------------------------------------------------------------

DEFAULT_SUBJECT = "Contact got a new submission"
DEFAULT_SENDER  = "ben@groovemarketing.co.uk"

_SEEN_KEY   = "email_import_seen"     # JSON list of processed Graph message ids
_SEEN_MAX   = 300                     # cap stored ids so the setting stays small

# In-memory throttle so rapid page loads don't hammer Graph. Resets on restart.
_last_run: dict[int, float] = {}
_MIN_INTERVAL = 60.0                  # seconds between auto-runs per tenant


def is_enabled(tenant_id: int) -> bool:
    return get_setting("auto_import_enabled", "", tenant_id=tenant_id) == "1"


def set_enabled(tenant_id: int, enabled: bool) -> None:
    set_setting("auto_import_enabled", "1" if enabled else "", tenant_id=tenant_id)


def _match_subject(tenant_id: int) -> str:
    return get_setting("auto_import_subject", DEFAULT_SUBJECT, tenant_id=tenant_id) or DEFAULT_SUBJECT


def _match_sender(tenant_id: int) -> str:
    return (get_setting("auto_import_sender", DEFAULT_SENDER, tenant_id=tenant_id) or "").strip().lower()


def _load_seen(tenant_id: int) -> list[str]:
    raw = get_setting(_SEEN_KEY, "", tenant_id=tenant_id)
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return []


def _save_seen(tenant_id: int, seen: list[str]) -> None:
    set_setting(_SEEN_KEY, json.dumps(seen[-_SEEN_MAX:]), tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# HTML → text
# ---------------------------------------------------------------------------

def _html_to_text(content: str) -> str:
    """Flatten an HTML email body to plain text, preserving line breaks."""
    if not content:
        return ""
    text = content
    # Turn block-level boundaries into newlines so labels/values stay separated.
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|tr|li|h[1-6]|table)\s*>", "\n", text)
    # Table cells → space so "Label</td><td>Value" doesn't glue together.
    text = re.sub(r"(?i)</\s*(td|th)\s*>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)        # drop remaining tags
    text = html.unescape(text)
    # Normalise whitespace: trim each line, drop blank runs.
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Field parsing
# ---------------------------------------------------------------------------

# Labels as they may appear in the submission email. Order here doesn't matter —
# each is located by its position in the text, then the value runs to the next label.
# Patterns are matched at the START OF A LINE so a stray word inside a message
# (e.g. "...call my phone") can't be mistaken for a field label.
_LABELS = [
    ("name",    r"Full Name|Name"),
    ("phone",   r"Phone(?:\s*Number)?|Telephone|Mobile|Contact Number"),
    ("email",   r"Email(?:\s*Address)?"),
    ("service", r"What service do you need\??|Service"),
    ("message", r"How can we help\??|Message|Comments|Enquiry"),
]

# Anything at/after these markers is footer noise, not the enquiry.
_FOOTER_MARKERS = [
    "View Submissions",
    "This message was sent",
    "Powered by",
    "Manage your",
    "Unsubscribe",
]


def parse_submission(text: str) -> Optional[dict]:
    """Extract enquiry fields from the flattened email text. None if unparseable."""
    if not text:
        return None

    # Cut off any trailing footer content.
    for marker in _FOOTER_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]

    # Find the character position where each label's value starts. Labels are
    # anchored to the start of a line (MULTILINE) so text inside a message body
    # can't be misread as a field label. First match per label wins.
    positions = []
    for key, pat in _LABELS:
        m = re.search(r"(?im)^\s*(?:" + pat + r")\s*:\s*", text)
        if m:
            positions.append((key, m.end(), m.start()))

    if not positions:
        return None

    # Value for each label runs until the next label begins (by text position).
    positions.sort(key=lambda p: p[2])
    fields: dict = {}
    for i, (key, value_start, _label_start) in enumerate(positions):
        end = positions[i + 1][2] if i + 1 < len(positions) else len(text)
        fields[key] = text[value_start:end].strip(" \n\t:-")

    name  = fields.get("name", "").strip()
    email = fields.get("email", "").strip()
    if not name or not email or "@" not in email:
        return None

    return {
        "name":       name,
        "email":      email,
        "phone":      fields.get("phone", "").strip(),
        "event_type": fields.get("service", "").strip(),
        "message":    fields.get("message", "").strip(),
    }


# ---------------------------------------------------------------------------
# Import runner
# ---------------------------------------------------------------------------

async def run_import(tenant_id: int, limit: int = 25) -> dict:
    """
    Scan recent inbox messages, create enquiries for any unseen matching ones.
    Returns {"imported": n, "quote_ids": [...]} (raises nothing on Graph errors).
    """
    if not em.is_connected(tenant_id):
        return {"imported": 0, "quote_ids": [], "error": "not_connected"}

    subject_match = _match_subject(tenant_id).strip().lower()
    sender_match  = _match_sender(tenant_id)

    try:
        messages = await em.get_inbox(tenant_id, top=limit)
    except Exception:
        return {"imported": 0, "quote_ids": [], "error": "inbox_failed"}

    seen = _load_seen(tenant_id)
    seen_set = set(seen)
    created: list = []
    parsed_data: list[dict] = []

    for msg in messages:
        mid = msg.get("id")
        if not mid or mid in seen_set:
            continue
        subject = (msg.get("subject") or "").strip().lower()
        if subject != subject_match:
            continue
        sender = ((msg.get("from") or {}).get("emailAddress") or {}).get("address", "").strip().lower()
        # Substring match: form mailers sometimes send "on behalf of" the address.
        if sender_match and sender_match not in sender:
            continue

        # Fetch the full body (inbox preview is truncated) and parse it.
        try:
            full = await em.get_message(mid, tenant_id)
        except Exception:
            full = None
        body = (full or {}).get("body", {}).get("content", "") if full else ""
        parsed = parse_submission(_html_to_text(body))
        # Fall back to the preview if the full body somehow didn't parse.
        if not parsed:
            parsed = parse_submission(_html_to_text(msg.get("bodyPreview", "")))

        # Mark as processed regardless, so a permanently-unparseable email
        # isn't retried forever.
        seen.append(mid)
        seen_set.add(mid)
        if not parsed:
            continue

        note = "— Imported automatically from website enquiry form"
        message = (parsed["message"] + "\n\n" + note).strip() if parsed["message"] else note
        qid = save_quote({
            "name":           parsed["name"],
            "email":          parsed["email"],
            "phone":          parsed.get("phone", ""),
            "event_date":     "",
            "location":       "",
            "event_type":     parsed["event_type"],
            "selected_items": [],
            "total_price":    0,
            "message":        message,
        }, tenant_id)
        created.append(qid)
        parsed_data.append({**parsed, "quote_id": qid})

    _save_seen(tenant_id, seen)
    return {"imported": len(created), "quote_ids": created, "enquiries": parsed_data}


async def maybe_run(tenant_id: int) -> None:
    """Throttled, best-effort auto-run used on page loads. Swallows all errors."""
    if not is_enabled(tenant_id):
        return
    now = time.time()
    if now - _last_run.get(tenant_id, 0) < _MIN_INTERVAL:
        return
    _last_run[tenant_id] = now
    try:
        await run_import(tenant_id)
    except Exception:
        pass
