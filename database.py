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
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id            SERIAL PRIMARY KEY,
                        name          TEXT NOT NULL,
                        email         TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        created_at    TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS staff_members (
                        id            SERIAL PRIMARY KEY,
                        name          TEXT NOT NULL,
                        email         TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        created_at    TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS bookings (
                        id          SERIAL PRIMARY KEY,
                        quote_id    INTEGER REFERENCES quotes(id) ON DELETE SET NULL,
                        user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
                        staff_id    INTEGER REFERENCES staff_members(id) ON DELETE SET NULL,
                        title       TEXT DEFAULT '',
                        event_date  TEXT DEFAULT '',
                        event_type  TEXT DEFAULT '',
                        location    TEXT DEFAULT '',
                        notes       TEXT DEFAULT '',
                        total_price REAL DEFAULT 0,
                        status      TEXT DEFAULT 'confirmed',
                        created_at  TIMESTAMPTZ DEFAULT NOW()
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


# ---------------------------------------------------------------------------
# Users (customers)
# ---------------------------------------------------------------------------

def get_user_by_email(email: str) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(uid: int) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, email, created_at FROM users WHERE id = %s", (uid,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def create_user(name: str, email: str, password_hash: str) -> int:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (name, email, password_hash) VALUES (%s, %s, %s) RETURNING id",
                    (name, email, password_hash),
                )
                return cur.fetchone()["id"]
    finally:
        conn.close()


def get_quotes_by_email(email: str) -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM quotes WHERE LOWER(email) = LOWER(%s) ORDER BY created_at DESC",
                (email,),
            )
            return [_parse_quote(row) for row in cur.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Staff members
# ---------------------------------------------------------------------------

def get_all_staff() -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, email, created_at FROM staff_members ORDER BY name ASC")
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_staff_by_email(email: str) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM staff_members WHERE email = %s", (email,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def get_staff_by_id(sid: int) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, email, created_at FROM staff_members WHERE id = %s", (sid,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def create_staff_member(name: str, email: str, password_hash: str) -> int:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO staff_members (name, email, password_hash) VALUES (%s, %s, %s) RETURNING id",
                    (name, email, password_hash),
                )
                return cur.fetchone()["id"]
    finally:
        conn.close()


def delete_staff_member(sid: int) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM staff_members WHERE id = %s", (sid,))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------

def _parse_booking(row: dict) -> dict:
    b = dict(row)
    if b.get("created_at"):
        b["created_at"] = b["created_at"].isoformat()
    return b


def get_all_bookings() -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT b.*, s.name AS staff_name, u.name AS customer_name, u.email AS customer_email
                FROM bookings b
                LEFT JOIN staff_members s ON b.staff_id = s.id
                LEFT JOIN users u ON b.user_id = u.id
                ORDER BY b.event_date ASC, b.created_at DESC
            """)
            return [_parse_booking(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_booking_by_id(bid: int) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT b.*, s.name AS staff_name, u.name AS customer_name, u.email AS customer_email
                FROM bookings b
                LEFT JOIN staff_members s ON b.staff_id = s.id
                LEFT JOIN users u ON b.user_id = u.id
                WHERE b.id = %s
            """, (bid,))
            row = cur.fetchone()
            return _parse_booking(row) if row else None
    finally:
        conn.close()


def get_bookings_for_user(user_id: int) -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT b.*, s.name AS staff_name
                FROM bookings b
                LEFT JOIN staff_members s ON b.staff_id = s.id
                WHERE b.user_id = %s
                ORDER BY b.event_date ASC
            """, (user_id,))
            return [_parse_booking(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_bookings_for_staff(staff_id: int) -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT b.*, u.name AS customer_name, u.email AS customer_email
                FROM bookings b
                LEFT JOIN users u ON b.user_id = u.id
                WHERE b.staff_id = %s
                ORDER BY b.event_date ASC
            """, (staff_id,))
            return [_parse_booking(row) for row in cur.fetchall()]
    finally:
        conn.close()


def create_booking(data: dict) -> int:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO bookings
                        (quote_id, user_id, staff_id, title, event_date, event_type,
                         location, notes, total_price, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    data.get("quote_id"),
                    data.get("user_id"),
                    data.get("staff_id"),
                    data.get("title", ""),
                    data.get("event_date", ""),
                    data.get("event_type", ""),
                    data.get("location", ""),
                    data.get("notes", ""),
                    data.get("total_price", 0),
                    data.get("status", "confirmed"),
                ))
                return cur.fetchone()["id"]
    finally:
        conn.close()


def update_booking(bid: int, data: dict) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE bookings SET
                        staff_id    = %s,
                        title       = %s,
                        event_date  = %s,
                        event_type  = %s,
                        location    = %s,
                        notes       = %s,
                        total_price = %s,
                        status      = %s
                    WHERE id = %s
                """, (
                    data.get("staff_id"),
                    data.get("title", ""),
                    data.get("event_date", ""),
                    data.get("event_type", ""),
                    data.get("location", ""),
                    data.get("notes", ""),
                    data.get("total_price", 0),
                    data.get("status", "confirmed"),
                    bid,
                ))
    finally:
        conn.close()


def delete_booking(bid: int) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM bookings WHERE id = %s", (bid,))
    finally:
        conn.close()
