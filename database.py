import json
import os
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")

DEFAULT_TEMPLATES = [
    {
        "name": "Quote Received",
        "subject": "Thanks for your enquiry, {{name}}!",
        "body": """<p>Hi {{name}},</p>
<p>Thanks for getting in touch! We've received your enquiry and will be in touch very shortly with a full quote.</p>
<p><strong>Your event details:</strong><br>
Date: {{event_date}}<br>
Type: {{event_type}}<br>
Location: {{location}}</p>
<p>In the meantime, if you have any questions feel free to reply to this email.</p>
<p>Speak soon,<br>The NowDJ Team</p>""",
    },
    {
        "name": "Booking Confirmed",
        "subject": "Your booking is confirmed! 🎉 — {{name}}",
        "body": """<p>Hi {{name}},</p>
<p>Great news — your booking is confirmed! We're really looking forward to being part of your event.</p>
<p><strong>Booking summary:</strong><br>
Quote #{{quote_id}}<br>
Date: {{event_date}}<br>
Location: {{location}}<br>
Total: {{total}}</p>
<p>We'll be in touch closer to the date to go over any final details.</p>
<p>Thanks again,<br>The NowDJ Team</p>""",
    },
    {
        "name": "Following Up",
        "subject": "Just checking in — {{name}}",
        "body": """<p>Hi {{name}},</p>
<p>We sent over a quote a little while ago and just wanted to check in to see if you have any questions or if there's anything we can help with.</p>
<p>We'd love to be part of your {{event_type}} and are happy to chat through any details.</p>
<p>Just reply to this email or give us a call.</p>
<p>Thanks,<br>The NowDJ Team</p>""",
    },
]


def _conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def init_db() -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS quotes (
                        id            SERIAL PRIMARY KEY,
                        name          TEXT    NOT NULL,
                        email         TEXT    NOT NULL,
                        phone         TEXT    DEFAULT '',
                        event_date    TEXT    DEFAULT '',
                        location      TEXT    DEFAULT '',
                        event_type    TEXT    DEFAULT '',
                        selected_items TEXT   DEFAULT '[]',
                        total_price   REAL    DEFAULT 0,
                        message       TEXT    DEFAULT '',
                        status        TEXT    DEFAULT 'new',
                        created_at    TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS email_templates (
                        id         SERIAL PRIMARY KEY,
                        name       TEXT    NOT NULL,
                        subject    TEXT    NOT NULL DEFAULT '',
                        body       TEXT    NOT NULL DEFAULT '',
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        updated_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                        key   TEXT PRIMARY KEY,
                        value TEXT NOT NULL DEFAULT ''
                    )
                """)
                cur.execute("SELECT COUNT(*) AS c FROM email_templates")
                if cur.fetchone()["c"] == 0:
                    for t in DEFAULT_TEMPLATES:
                        cur.execute(
                            "INSERT INTO email_templates (name, subject, body) VALUES (%s, %s, %s)",
                            (t["name"], t["subject"], t["body"]),
                        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Settings (key-value store — replaces JSON config files)
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
            row = cur.fetchone()
            return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO settings (key, value) VALUES (%s, %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """, (key, value))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------

def save_quote(data: dict) -> int:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO quotes
                        (name, email, phone, event_date, location, event_type,
                         selected_items, total_price, message)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    data["name"],
                    data["email"],
                    data.get("phone", ""),
                    data.get("event_date", ""),
                    data.get("location", ""),
                    data.get("event_type", ""),
                    json.dumps(data.get("selected_items", [])),
                    data.get("total_price", 0),
                    data.get("message", ""),
                ))
                return cur.fetchone()["id"]
    finally:
        conn.close()


def _parse_quote(row: dict) -> dict:
    q = dict(row)
    if isinstance(q.get("selected_items"), str):
        try:
            q["selected_items"] = json.loads(q["selected_items"])
        except Exception:
            q["selected_items"] = []
    if q.get("created_at"):
        q["created_at"] = q["created_at"].isoformat()
    return q


def get_all_quotes() -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM quotes ORDER BY created_at DESC")
            return [_parse_quote(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_quote_by_id(quote_id: int) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM quotes WHERE id = %s", (quote_id,))
            row = cur.fetchone()
            return _parse_quote(row) if row else None
    finally:
        conn.close()


def update_quote_status(quote_id: int, status: str) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE quotes SET status = %s WHERE id = %s", (status, quote_id))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------

def get_all_templates() -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM email_templates ORDER BY id ASC")
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_template_by_id(tid: int) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM email_templates WHERE id = %s", (tid,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def create_template(name: str, subject: str, body: str) -> int:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO email_templates (name, subject, body) VALUES (%s, %s, %s) RETURNING id",
                    (name, subject, body),
                )
                return cur.fetchone()["id"]
    finally:
        conn.close()


def update_template(tid: int, name: str, subject: str, body: str) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE email_templates
                    SET name=%s, subject=%s, body=%s, updated_at=NOW()
                    WHERE id=%s
                """, (name, subject, body, tid))
    finally:
        conn.close()


def delete_template(tid: int) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM email_templates WHERE id = %s", (tid,))
    finally:
        conn.close()
