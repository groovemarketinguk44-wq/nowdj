import json
import os
import sqlite3
from pathlib import Path

_RAW = os.environ.get("DATABASE_URL", "/data/nowdj.db")
DB_PATH = _RAW[len("sqlite:///"):] if _RAW.startswith("sqlite:///") else _RAW
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

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
<p>Speak soon,<br>The Team</p>""",
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
<p>Thanks again,<br>The Team</p>""",
    },
    {
        "name": "Following Up",
        "subject": "Just checking in — {{name}}",
        "body": """<p>Hi {{name}},</p>
<p>We sent over a quote a little while ago and just wanted to check in to see if you have any questions or if there's anything we can help with.</p>
<p>We'd love to be part of your {{event_type}} and are happy to chat through any details.</p>
<p>Just reply to this email or give us a call.</p>
<p>Thanks,<br>The Team</p>""",
    },
    {
        "name": "Thank You",
        "subject": "Thank you for booking with us, {{name}}!",
        "body": """<p>Hi {{name}},</p>
<p>Thank you so much for choosing us for your {{event_type}} — we're really excited to be part of your event!</p>
<p>If you have any questions in the lead up to the day, please don't hesitate to get in touch.</p>
<p>We'll be in touch closer to {{event_date}} to go over any final details.</p>
<p>Thanks again,<br>The Team</p>""",
    },
    {
        "name": "Review Request",
        "subject": "How did we do, {{name}}? 🌟",
        "body": """<p>Hi {{name}},</p>
<p>We hope your {{event_type}} went brilliantly!</p>
<p>We'd really appreciate it if you could take a moment to leave us a review — it helps us enormously and means the world to the team.</p>
<p>It only takes a minute and your feedback makes a huge difference.</p>
<p>Thanks so much,<br>The Team</p>""",
    },
]


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _add_col(cur: sqlite3.Cursor, table: str, col: str, defn: str) -> None:
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
    except sqlite3.OperationalError:
        pass


def init_db() -> None:
    conn = _conn()
    try:
        with conn:
            cur = conn.cursor()

            cur.execute("""
                CREATE TABLE IF NOT EXISTS tenants (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    name          TEXT NOT NULL,
                    slug          TEXT UNIQUE NOT NULL,
                    email         TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    plan          TEXT NOT NULL DEFAULT 'starter',
                    custom_domain TEXT UNIQUE DEFAULT NULL,
                    created_at    TEXT DEFAULT (datetime('now'))
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS quotes (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id      INTEGER REFERENCES tenants(id) ON DELETE CASCADE,
                    name           TEXT    NOT NULL,
                    email          TEXT    NOT NULL,
                    phone          TEXT    DEFAULT '',
                    event_date     TEXT    DEFAULT '',
                    location       TEXT    DEFAULT '',
                    event_type     TEXT    DEFAULT '',
                    selected_items TEXT    DEFAULT '[]',
                    total_price    REAL    DEFAULT 0,
                    message        TEXT    DEFAULT '',
                    status         TEXT    DEFAULT 'new',
                    created_at     TEXT    DEFAULT (datetime('now'))
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS email_templates (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id  INTEGER REFERENCES tenants(id) ON DELETE CASCADE,
                    name       TEXT    NOT NULL,
                    subject    TEXT    NOT NULL DEFAULT '',
                    body       TEXT    NOT NULL DEFAULT '',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT    DEFAULT (datetime('now')),
                    updated_at TEXT    DEFAULT (datetime('now'))
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
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id     INTEGER REFERENCES tenants(id) ON DELETE CASCADE,
                    name          TEXT NOT NULL,
                    email         TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at    TEXT DEFAULT (datetime('now'))
                )
            """)
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_tenant_email ON users(tenant_id, email) WHERE tenant_id IS NOT NULL")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS staff_members (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id     INTEGER REFERENCES tenants(id) ON DELETE CASCADE,
                    name          TEXT NOT NULL,
                    email         TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at    TEXT DEFAULT (datetime('now'))
                )
            """)
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS staff_tenant_email ON staff_members(tenant_id, email) WHERE tenant_id IS NOT NULL")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS bookings (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id      INTEGER REFERENCES tenants(id) ON DELETE CASCADE,
                    quote_id       INTEGER REFERENCES quotes(id) ON DELETE SET NULL,
                    user_id        INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    staff_id       INTEGER REFERENCES staff_members(id) ON DELETE SET NULL,
                    title          TEXT DEFAULT '',
                    event_date     TEXT DEFAULT '',
                    event_type     TEXT DEFAULT '',
                    location       TEXT DEFAULT '',
                    notes          TEXT DEFAULT '',
                    total_price    REAL DEFAULT 0,
                    status         TEXT DEFAULT 'confirmed',
                    staff_pay      REAL DEFAULT NULL,
                    staff_response TEXT DEFAULT NULL,
                    created_at     TEXT DEFAULT (datetime('now'))
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                    doc_type        TEXT NOT NULL DEFAULT 'quote',
                    doc_number      TEXT NOT NULL DEFAULT '',
                    status          TEXT NOT NULL DEFAULT 'draft',
                    client_name     TEXT DEFAULT '',
                    client_email    TEXT DEFAULT '',
                    client_phone    TEXT DEFAULT '',
                    client_address  TEXT DEFAULT '',
                    event_date      TEXT DEFAULT '',
                    event_type      TEXT DEFAULT '',
                    location        TEXT DEFAULT '',
                    line_items      TEXT DEFAULT '[]',
                    notes           TEXT DEFAULT '',
                    source_quote_id INTEGER,
                    sender          TEXT DEFAULT '{}',
                    discount_type   TEXT DEFAULT 'percent',
                    discount_value  REAL DEFAULT 0,
                    created_at      TEXT DEFAULT (datetime('now')),
                    updated_at      TEXT DEFAULT (datetime('now'))
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS email_automations (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id     INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                    name          TEXT NOT NULL,
                    trigger_event TEXT NOT NULL DEFAULT 'form_submission',
                    template_id   INTEGER REFERENCES email_templates(id) ON DELETE SET NULL,
                    send_to       TEXT NOT NULL DEFAULT 'custom',
                    send_to_email TEXT,
                    enabled       INTEGER NOT NULL DEFAULT 1,
                    created_at    TEXT DEFAULT (datetime('now'))
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS contract_templates (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id    INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                    name         TEXT NOT NULL,
                    html_content TEXT NOT NULL DEFAULT '',
                    created_at   TEXT DEFAULT (datetime('now')),
                    updated_at   TEXT DEFAULT (datetime('now'))
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS hire_contracts (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id         INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                    template_id       INTEGER REFERENCES contract_templates(id) ON DELETE SET NULL,
                    token             TEXT UNIQUE NOT NULL,
                    customer_name     TEXT DEFAULT '',
                    customer_email    TEXT DEFAULT '',
                    customer_phone    TEXT DEFAULT '',
                    customer_address  TEXT DEFAULT '',
                    agreement_date    TEXT DEFAULT '',
                    equipment_desc    TEXT DEFAULT '',
                    equipment_value   TEXT DEFAULT '',
                    hire_start        TEXT DEFAULT '',
                    hire_end          TEXT DEFAULT '',
                    rent_amount       TEXT DEFAULT '',
                    deposit_amount    TEXT DEFAULT '',
                    delivery_address  TEXT DEFAULT '',
                    pickup_address    TEXT DEFAULT '',
                    html_content      TEXT DEFAULT '',
                    status            TEXT DEFAULT 'sent',
                    signed_at         TEXT DEFAULT NULL,
                    signer_name       TEXT DEFAULT NULL,
                    signature_image   TEXT DEFAULT NULL,
                    ip_address        TEXT DEFAULT NULL,
                    created_at        TEXT DEFAULT (datetime('now'))
                )
            """)
    finally:
        conn.close()


def migrate_automations() -> None:
    pass  # handled in init_db


def migrate_null_tenant_ids(tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("UPDATE quotes SET tenant_id = ? WHERE tenant_id IS NULL", (tenant_id,))
            cur.execute("UPDATE email_templates SET tenant_id = ? WHERE tenant_id IS NULL", (tenant_id,))
            cur.execute("UPDATE users SET tenant_id = ? WHERE tenant_id IS NULL", (tenant_id,))
            cur.execute("UPDATE staff_members SET tenant_id = ? WHERE tenant_id IS NULL", (tenant_id,))
            cur.execute("UPDATE bookings SET tenant_id = ? WHERE tenant_id IS NULL", (tenant_id,))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def _skey(key: str, tenant_id: int | None = None) -> str:
    return f"t{tenant_id}:{key}" if tenant_id is not None else key


def get_setting(key: str, default: str = "", tenant_id: int | None = None) -> str:
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = ?", (_skey(key, tenant_id),))
        row = cur.fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str, tenant_id: int | None = None) -> None:
    conn = _conn()
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (_skey(key, tenant_id), value),
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tenants
# ---------------------------------------------------------------------------

def create_tenant(name: str, slug: str, email: str, password_hash: str, plan: str = "starter") -> int:
    conn = _conn()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO tenants (name, slug, email, password_hash, plan) VALUES (?, ?, ?, ?, ?)",
                (name, slug, email, password_hash, plan),
            )
            return cur.lastrowid
    finally:
        conn.close()


def get_tenant_by_slug(slug: str) -> dict | None:
    conn = _conn()
    try:
        cur = conn.execute("SELECT * FROM tenants WHERE slug = ?", (slug,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_tenant_by_id(tid: int) -> dict | None:
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT id, name, slug, email, plan, custom_domain, created_at FROM tenants WHERE id = ?", (tid,)
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_tenant_by_email(email: str) -> dict | None:
    conn = _conn()
    try:
        cur = conn.execute("SELECT * FROM tenants WHERE email = ?", (email,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_tenant_by_custom_domain(domain: str) -> dict | None:
    conn = _conn()
    try:
        cur = conn.execute("SELECT * FROM tenants WHERE lower(custom_domain) = ?", (domain.lower(),))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_tenants() -> list[dict]:
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT id, name, slug, email, plan, custom_domain, created_at FROM tenants ORDER BY created_at ASC"
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def update_tenant(tid: int, name: str, email: str, plan: str, password_hash: str | None = None, custom_domain: str | None = ...) -> None:
    conn = _conn()
    try:
        with conn:
            if custom_domain is ...:
                if password_hash:
                    conn.execute(
                        "UPDATE tenants SET name=?, email=?, plan=?, password_hash=? WHERE id=?",
                        (name, email, plan, password_hash, tid),
                    )
                else:
                    conn.execute(
                        "UPDATE tenants SET name=?, email=?, plan=? WHERE id=?",
                        (name, email, plan, tid),
                    )
            else:
                cd = custom_domain.strip().lower() if custom_domain else None
                if password_hash:
                    conn.execute(
                        "UPDATE tenants SET name=?, email=?, plan=?, password_hash=?, custom_domain=? WHERE id=?",
                        (name, email, plan, password_hash, cd, tid),
                    )
                else:
                    conn.execute(
                        "UPDATE tenants SET name=?, email=?, plan=?, custom_domain=? WHERE id=?",
                        (name, email, plan, cd, tid),
                    )
    finally:
        conn.close()


def delete_tenant(tid: int) -> None:
    conn = _conn()
    try:
        with conn:
            conn.execute("DELETE FROM tenants WHERE id = ?", (tid,))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------

def save_quote(data: dict, tenant_id: int) -> int:
    conn = _conn()
    try:
        with conn:
            cur = conn.execute("""
                INSERT INTO quotes
                    (tenant_id, name, email, phone, event_date, location, event_type,
                     selected_items, total_price, message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tenant_id,
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
            return cur.lastrowid
    finally:
        conn.close()


def _parse_quote(row) -> dict:
    q = dict(row)
    if isinstance(q.get("selected_items"), str):
        try:
            q["selected_items"] = json.loads(q["selected_items"])
        except Exception:
            q["selected_items"] = []
    return q


def get_all_quotes(tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        cur = conn.execute("""
            SELECT * FROM quotes WHERE tenant_id = ?
            ORDER BY
              CASE status
                WHEN 'new'       THEN 1
                WHEN 'contacted' THEN 2
                WHEN 'booked'    THEN 3
                WHEN 'attended'  THEN 4
                WHEN 'paid'      THEN 5
                ELSE 6
              END ASC,
              NULLIF(event_date, '') ASC NULLS LAST,
              created_at ASC
        """, (tenant_id,))
        return [_parse_quote(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_quote_by_id(quote_id: int, tenant_id: int) -> dict | None:
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT * FROM quotes WHERE id = ? AND tenant_id = ?", (quote_id, tenant_id)
        )
        row = cur.fetchone()
        return _parse_quote(row) if row else None
    finally:
        conn.close()


def update_quote_status(quote_id: int, status: str, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            conn.execute(
                "UPDATE quotes SET status = ? WHERE id = ? AND tenant_id = ?",
                (status, quote_id, tenant_id),
            )
    finally:
        conn.close()


def update_quote_total(quote_id: int, total_price: float, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            conn.execute(
                "UPDATE quotes SET total_price = ? WHERE id = ? AND tenant_id = ?",
                (total_price, quote_id, tenant_id),
            )
    finally:
        conn.close()


def delete_quote(quote_id: int, tenant_id: int) -> bool:
    conn = _conn()
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM quotes WHERE id = ? AND tenant_id = ?",
                (quote_id, tenant_id),
            )
            return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------

def seed_templates_for_tenant(tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            cur = conn.execute(
                "SELECT name FROM email_templates WHERE tenant_id = ?", (tenant_id,)
            )
            existing_names = {row["name"] for row in cur.fetchall()}
            for t in DEFAULT_TEMPLATES:
                if t["name"] not in existing_names:
                    conn.execute(
                        "INSERT INTO email_templates (tenant_id, name, subject, body) VALUES (?, ?, ?, ?)",
                        (tenant_id, t["name"], t["subject"], t["body"]),
                    )
    finally:
        conn.close()


def get_all_templates(tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT * FROM email_templates WHERE tenant_id = ? ORDER BY sort_order ASC, id ASC",
            (tenant_id,),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def reorder_templates(ordered_ids: list[int], tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            for pos, tid in enumerate(ordered_ids):
                conn.execute(
                    "UPDATE email_templates SET sort_order=? WHERE id=? AND tenant_id=?",
                    (pos, tid, tenant_id),
                )
    finally:
        conn.close()


def get_template_by_id(tid: int, tenant_id: int) -> dict | None:
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT * FROM email_templates WHERE id = ? AND tenant_id = ?", (tid, tenant_id)
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_template(name: str, subject: str, body: str, tenant_id: int) -> int:
    conn = _conn()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO email_templates (tenant_id, name, subject, body) VALUES (?, ?, ?, ?)",
                (tenant_id, name, subject, body),
            )
            return cur.lastrowid
    finally:
        conn.close()


def update_template(tid: int, name: str, subject: str, body: str, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            conn.execute("""
                UPDATE email_templates
                SET name=?, subject=?, body=?, updated_at=datetime('now')
                WHERE id=? AND tenant_id=?
            """, (name, subject, body, tid, tenant_id))
    finally:
        conn.close()


def delete_template(tid: int, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            conn.execute(
                "DELETE FROM email_templates WHERE id = ? AND tenant_id = ?", (tid, tenant_id)
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Email Automations
# ---------------------------------------------------------------------------

def get_all_automations(tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        cur = conn.execute("""
            SELECT a.*, t.name AS template_name
            FROM email_automations a
            LEFT JOIN email_templates t ON t.id = a.template_id
            WHERE a.tenant_id = ?
            ORDER BY a.id ASC
        """, (tenant_id,))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_active_automations_for_trigger(trigger_event: str, tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        cur = conn.execute("""
            SELECT a.*, t.subject, t.body
            FROM email_automations a
            LEFT JOIN email_templates t ON t.id = a.template_id
            WHERE a.tenant_id = ? AND a.trigger_event = ? AND a.enabled = 1
              AND a.template_id IS NOT NULL
        """, (tenant_id, trigger_event))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def create_automation(tenant_id: int, name: str, trigger_event: str, template_id: int | None,
                      send_to: str, send_to_email: str | None) -> int:
    conn = _conn()
    try:
        with conn:
            cur = conn.execute("""
                INSERT INTO email_automations
                    (tenant_id, name, trigger_event, template_id, send_to, send_to_email)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (tenant_id, name, trigger_event, template_id, send_to, send_to_email))
            return cur.lastrowid
    finally:
        conn.close()


def update_automation(aid: int, tenant_id: int, name: str, trigger_event: str, template_id: int | None,
                      send_to: str, send_to_email: str | None, enabled: bool) -> None:
    conn = _conn()
    try:
        with conn:
            conn.execute("""
                UPDATE email_automations
                SET name=?, trigger_event=?, template_id=?, send_to=?, send_to_email=?, enabled=?
                WHERE id=? AND tenant_id=?
            """, (name, trigger_event, template_id, send_to, send_to_email, 1 if enabled else 0, aid, tenant_id))
    finally:
        conn.close()


def delete_automation(aid: int, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            conn.execute("DELETE FROM email_automations WHERE id=? AND tenant_id=?", (aid, tenant_id))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Users (customers)
# ---------------------------------------------------------------------------

def get_user_by_email(email: str, tenant_id: int) -> dict | None:
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT * FROM users WHERE email = ? AND tenant_id = ?", (email, tenant_id)
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(uid: int, tenant_id: int) -> dict | None:
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT id, name, email, created_at FROM users WHERE id = ? AND tenant_id = ?",
            (uid, tenant_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_user(name: str, email: str, password_hash: str, tenant_id: int) -> int:
    conn = _conn()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO users (tenant_id, name, email, password_hash) VALUES (?, ?, ?, ?)",
                (tenant_id, name, email, password_hash),
            )
            return cur.lastrowid
    finally:
        conn.close()


def get_all_users(tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT id, name, email, created_at FROM users WHERE tenant_id = ? ORDER BY created_at DESC",
            (tenant_id,),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def delete_user(uid: int, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            conn.execute("DELETE FROM users WHERE id = ? AND tenant_id = ?", (uid, tenant_id))
    finally:
        conn.close()


def get_quotes_by_email(email: str, tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT * FROM quotes WHERE LOWER(email) = LOWER(?) AND tenant_id = ? ORDER BY created_at DESC",
            (email, tenant_id),
        )
        return [_parse_quote(row) for row in cur.fetchall()]
    finally:
        conn.close()


def update_user(uid: int, name: str, email: str, tenant_id: int, password_hash: str | None = None) -> None:
    conn = _conn()
    try:
        with conn:
            if password_hash:
                conn.execute(
                    "UPDATE users SET name=?, email=?, password_hash=? WHERE id=? AND tenant_id=?",
                    (name, email, password_hash, uid, tenant_id),
                )
            else:
                conn.execute(
                    "UPDATE users SET name=?, email=? WHERE id=? AND tenant_id=?",
                    (name, email, uid, tenant_id),
                )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Staff members
# ---------------------------------------------------------------------------

def get_all_staff(tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT id, name, email, created_at FROM staff_members WHERE tenant_id = ? ORDER BY name ASC",
            (tenant_id,),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_staff_by_email(email: str, tenant_id: int) -> dict | None:
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT * FROM staff_members WHERE email = ? AND tenant_id = ?", (email, tenant_id)
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def find_staff_globally(email: str) -> dict | None:
    conn = _conn()
    try:
        cur = conn.execute(
            """SELECT sm.*, t.slug AS tenant_slug, t.custom_domain AS tenant_custom_domain
               FROM staff_members sm
               JOIN tenants t ON t.id = sm.tenant_id
               WHERE sm.email = ? LIMIT 1""",
            (email,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def find_user_globally(email: str) -> dict | None:
    conn = _conn()
    try:
        cur = conn.execute(
            """SELECT u.*, t.slug AS tenant_slug, t.custom_domain AS tenant_custom_domain
               FROM users u
               JOIN tenants t ON t.id = u.tenant_id
               WHERE u.email = ? LIMIT 1""",
            (email,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_staff_by_id(sid: int, tenant_id: int) -> dict | None:
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT id, name, email, created_at FROM staff_members WHERE id = ? AND tenant_id = ?",
            (sid, tenant_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_staff_member(name: str, email: str, password_hash: str, tenant_id: int) -> int:
    conn = _conn()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO staff_members (tenant_id, name, email, password_hash) VALUES (?, ?, ?, ?)",
                (tenant_id, name, email, password_hash),
            )
            return cur.lastrowid
    finally:
        conn.close()


def delete_staff_member(sid: int, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            conn.execute(
                "DELETE FROM staff_members WHERE id = ? AND tenant_id = ?", (sid, tenant_id)
            )
    finally:
        conn.close()


def update_staff_member_info(sid: int, name: str, email: str, tenant_id: int, password_hash: str | None = None) -> None:
    conn = _conn()
    try:
        with conn:
            if password_hash:
                conn.execute(
                    "UPDATE staff_members SET name=?, email=?, password_hash=? WHERE id=? AND tenant_id=?",
                    (name, email, password_hash, sid, tenant_id),
                )
            else:
                conn.execute(
                    "UPDATE staff_members SET name=?, email=? WHERE id=? AND tenant_id=?",
                    (name, email, sid, tenant_id),
                )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------

def _parse_booking(row) -> dict:
    return dict(row)


def get_all_bookings(tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        cur = conn.execute("""
            SELECT b.*, s.name AS staff_name, u.name AS customer_name, u.email AS customer_email
            FROM bookings b
            LEFT JOIN staff_members s ON b.staff_id = s.id
            LEFT JOIN users u ON b.user_id = u.id
            WHERE b.tenant_id = ?
            ORDER BY b.event_date ASC, b.created_at DESC
        """, (tenant_id,))
        return [_parse_booking(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_booking_by_id(bid: int, tenant_id: int) -> dict | None:
    conn = _conn()
    try:
        cur = conn.execute("""
            SELECT b.*, s.name AS staff_name, u.name AS customer_name, u.email AS customer_email
            FROM bookings b
            LEFT JOIN staff_members s ON b.staff_id = s.id
            LEFT JOIN users u ON b.user_id = u.id
            WHERE b.id = ? AND b.tenant_id = ?
        """, (bid, tenant_id))
        row = cur.fetchone()
        return _parse_booking(row) if row else None
    finally:
        conn.close()


def get_bookings_for_user(user_id: int, tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        cur = conn.execute("""
            SELECT b.*, s.name AS staff_name
            FROM bookings b
            LEFT JOIN staff_members s ON b.staff_id = s.id
            WHERE b.user_id = ? AND b.tenant_id = ?
            ORDER BY b.event_date ASC
        """, (user_id, tenant_id))
        return [_parse_booking(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_bookings_for_staff(staff_id: int, tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        cur = conn.execute("""
            SELECT b.*, u.name AS customer_name, u.email AS customer_email
            FROM bookings b
            LEFT JOIN users u ON b.user_id = u.id
            WHERE b.staff_id = ? AND b.tenant_id = ?
            ORDER BY b.event_date ASC
        """, (staff_id, tenant_id))
        return [_parse_booking(row) for row in cur.fetchall()]
    finally:
        conn.close()


def update_staff_response(booking_id: int, staff_id: int, tenant_id: int, response: str) -> dict | None:
    conn = _conn()
    try:
        with conn:
            cur = conn.execute(
                "UPDATE bookings SET staff_response = ? WHERE id = ? AND staff_id = ? AND tenant_id = ?",
                (response, booking_id, staff_id, tenant_id),
            )
            if cur.rowcount == 0:
                return None
        return get_booking_by_id(booking_id, tenant_id)
    finally:
        conn.close()


def sync_booking_date_fields(bid: int, event_date: str, event_type: str, location: str, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            conn.execute(
                "UPDATE bookings SET event_date=?, event_type=?, location=? WHERE id=? AND tenant_id=?",
                (event_date, event_type, location, bid, tenant_id),
            )
    finally:
        conn.close()


def get_booking_by_quote_id(quote_id: int, tenant_id: int) -> dict | None:
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT * FROM bookings WHERE quote_id = ? AND tenant_id = ? LIMIT 1",
            (quote_id, tenant_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_booking(data: dict, tenant_id: int) -> int:
    conn = _conn()
    try:
        with conn:
            cur = conn.execute("""
                INSERT INTO bookings
                    (tenant_id, quote_id, user_id, staff_id, title, event_date, event_type,
                     location, notes, total_price, status, staff_pay)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tenant_id,
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
                data.get("staff_pay"),
            ))
            return cur.lastrowid
    finally:
        conn.close()


def update_booking(bid: int, data: dict, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            conn.execute("""
                UPDATE bookings SET
                    staff_id    = ?,
                    title       = ?,
                    event_date  = ?,
                    event_type  = ?,
                    location    = ?,
                    notes       = ?,
                    total_price = ?,
                    status      = ?,
                    staff_pay   = ?
                WHERE id = ? AND tenant_id = ?
            """, (
                data.get("staff_id"),
                data.get("title", ""),
                data.get("event_date", ""),
                data.get("event_type", ""),
                data.get("location", ""),
                data.get("notes", ""),
                data.get("total_price", 0),
                data.get("status", "confirmed"),
                data.get("staff_pay"),
                bid,
                tenant_id,
            ))
    finally:
        conn.close()


def delete_booking(bid: int, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            conn.execute("DELETE FROM bookings WHERE id = ? AND tenant_id = ?", (bid, tenant_id))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def _parse_document(row) -> dict:
    d = dict(row)
    if isinstance(d.get("sender"), str):
        try:
            d["sender"] = json.loads(d["sender"])
        except (ValueError, TypeError):
            d["sender"] = {}
    return d


def next_doc_number(tenant_id: int, doc_type: str) -> str:
    import datetime
    now = datetime.datetime.utcnow()
    month_start = f"{now.year}-{now.month:02d}-01"
    prefix = f"{now.month:02d}{str(now.year)[2:]}"
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT COUNT(*) AS cnt FROM documents WHERE tenant_id = ? AND doc_type = ? AND created_at >= ?",
            (tenant_id, doc_type, month_start),
        )
        row = cur.fetchone()
        count = row["cnt"] if row else 0
        return f"{prefix}{count + 1:03d}"
    finally:
        conn.close()


def create_document(tenant_id: int, doc_type: str, doc_number: str, data: dict) -> int:
    conn = _conn()
    try:
        with conn:
            cur = conn.execute("""
                INSERT INTO documents
                    (tenant_id, doc_type, doc_number, status,
                     client_name, client_email, client_phone, client_address,
                     event_date, event_type, location, line_items, notes, source_quote_id, sender,
                     discount_type, discount_value)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tenant_id,
                doc_type,
                doc_number,
                data.get("status", "draft"),
                data.get("client_name", ""),
                data.get("client_email", ""),
                data.get("client_phone", ""),
                data.get("client_address", ""),
                data.get("event_date", ""),
                data.get("event_type", ""),
                data.get("location", ""),
                data.get("line_items", "[]"),
                data.get("notes", ""),
                data.get("source_quote_id"),
                data.get("sender", "{}"),
                data.get("discount_type", "percent"),
                float(data.get("discount_value") or 0),
            ))
            return cur.lastrowid
    finally:
        conn.close()


def get_document(doc_id: int, tenant_id: int) -> dict | None:
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT * FROM documents WHERE id = ? AND tenant_id = ?", (doc_id, tenant_id)
        )
        row = cur.fetchone()
        return _parse_document(row) if row else None
    finally:
        conn.close()


def list_documents(tenant_id: int, doc_type: str | None = None) -> list:
    conn = _conn()
    try:
        if doc_type:
            cur = conn.execute(
                "SELECT * FROM documents WHERE tenant_id = ? AND doc_type = ? ORDER BY created_at DESC",
                (tenant_id, doc_type),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM documents WHERE tenant_id = ? ORDER BY created_at DESC",
                (tenant_id,),
            )
        return [_parse_document(row) for row in cur.fetchall()]
    finally:
        conn.close()


def update_document(doc_id: int, tenant_id: int, data: dict) -> None:
    allowed = {
        "status", "client_name", "client_email", "client_phone", "client_address",
        "event_date", "event_type", "location", "line_items", "notes", "doc_number", "sender",
        "discount_type", "discount_value",
    }
    fields = {k: v for k, v in data.items() if k in allowed}
    if not fields:
        return
    conn = _conn()
    try:
        with conn:
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            values = list(fields.values()) + [doc_id, tenant_id]
            conn.execute(
                f"UPDATE documents SET {set_clause}, updated_at = datetime('now') WHERE id = ? AND tenant_id = ?",
                values,
            )
    finally:
        conn.close()


def delete_document(doc_id: int, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            conn.execute(
                "DELETE FROM documents WHERE id = ? AND tenant_id = ?", (doc_id, tenant_id)
            )
    finally:
        conn.close()


# ── Contract templates ────────────────────────────────────────────────────────

DEFAULT_CONTRACT_HTML = """
<div class="ct-details-block">
  <h2 class="ct-main-title">EQUIPMENT HIRE AGREEMENT</h2>

  <p class="ct-intro">THIS EQUIPMENT HIRE AGREEMENT (this "Agreement") dated this {{AGREEMENT_DATE}}</p>

  <div class="ct-parties">
    <p><strong>BETWEEN:</strong></p>
    <p>Ben Henderson of 16 Hudson House, Bessemer Road, Welwyn Garden City, Herts, AL7 1GT<br><em>(the "Owner")</em><br>OF THE FIRST PART</p>
    <p style="text-align:center;font-weight:700;margin:12px 0">— AND —</p>
    <p><strong>{{HIRER_NAME}}</strong> of {{HIRER_ADDRESS}}<br><em>(the "Hirer")</em><br>OF THE SECOND PART <em>(the Owner and Hirer are collectively the "Parties")</em></p>
  </div>

  <p class="ct-recital">IN CONSIDERATION OF the mutual covenants and promises in this Agreement, the receipt and sufficiency of which consideration is hereby acknowledged, the Owner leases the Equipment to the Hirer, and the Hirer leases the Equipment from the Owner on the following terms:</p>

  <div class="ct-details-grid">
    <div class="ct-detail-row"><span class="ct-detail-lbl">Equipment</span><span class="ct-detail-val"><strong>{{EQUIPMENT_DESCRIPTION}}</strong> which has an approximate value of <strong>{{EQUIPMENT_VALUE}}</strong></span></div>
    <div class="ct-detail-row"><span class="ct-detail-lbl">Hire Period</span><span class="ct-detail-val"><strong>{{HIRE_START}}</strong> to <strong>{{HIRE_END}}</strong></span></div>
    <div class="ct-detail-row"><span class="ct-detail-lbl">Hire Fee</span><span class="ct-detail-val"><strong>{{RENT_AMOUNT}}</strong> (inclusive of VAT), payable prior to taking possession of the Equipment</span></div>
    <div class="ct-detail-row"><span class="ct-detail-lbl">Deposit</span><span class="ct-detail-val"><strong>{{DEPOSIT_AMOUNT}}</strong>, refundable at end of Term subject to return of Equipment in good condition</span></div>
    <div class="ct-detail-row"><span class="ct-detail-lbl">Delivery Address</span><span class="ct-detail-val">{{DELIVERY_ADDRESS}}</span></div>
    <div class="ct-detail-row"><span class="ct-detail-lbl">Return Address</span><span class="ct-detail-val">{{PICKUP_ADDRESS}}</span></div>
  </div>
</div>

<div class="ct-section">
  <h3>Definitions</h3>
  <p><strong>1.</strong> The following definitions are used but not otherwise defined in this Agreement:</p>
  <p><strong>a.</strong> "Casualty Value" means the market value of the Equipment at the end of the Term or when in relation to a Total Loss, the market value the Equipment would have had at the end of the Term but for the Total Loss. The Casualty Value may be less than but will not be more than the original purchase price of the Equipment.</p>
  <p><strong>b.</strong> "Equipment" means {{EQUIPMENT_DESCRIPTION}} which has an approximate value of {{EQUIPMENT_VALUE}}.</p>
  <p><strong>c.</strong> "Total Loss" means any loss or damage that is not repairable or that would cost more to repair than the market value of the Equipment.</p>
</div>

<div class="ct-section">
  <h3>Lease</h3>
  <p><strong>2.</strong> The Owner agrees to lease the Equipment to the Hirer, and the Hirer agrees to lease the Equipment from the Owner in accordance with the terms set out in this Agreement.</p>
</div>

<div class="ct-section">
  <h3>Term</h3>
  <p><strong>3.</strong> The Agreement commences on {{HIRE_START}} and will continue until {{HIRE_END}} (the "Term").</p>
</div>

<div class="ct-section">
  <h3>Rent and Deposit</h3>
  <p><strong>4.</strong> The rent for the Equipment, inclusive of VAT, will be {{RENT_AMOUNT}} (the "Rent") and the Rent will be paid prior to the Hirer taking possession of the Equipment.</p>
  <p><strong>5.</strong> The Hirer will pay a deposit of {{DEPOSIT_AMOUNT}} (the "Deposit") before taking possession of the Equipment. The Owner will refund the Deposit to the Hirer at the end of the Term provided that the Hirer has performed all of the Hirer's obligations under this Agreement.</p>
</div>

<div class="ct-section">
  <h3>Delivery of Equipment</h3>
  <p><strong>6.</strong> The Owner will, at the Owner's own expense and risk, deliver the Equipment to the Hirer at {{DELIVERY_ADDRESS}}.</p>
</div>

<div class="ct-section">
  <h3>Use of Equipment</h3>
  <p><strong>7.</strong> The Hirer will use the Equipment in a good and careful manner and will comply with all of the manufacturer's requirements and recommendations respecting the Equipment and with any applicable law, whether local, state or federal respecting the use of the Equipment.</p>
  <p><strong>8.</strong> The Hirer will use the Equipment for the purpose for which it was designed and not for any other purpose.</p>
  <p><strong>9.</strong> Unless the Hirer obtains the prior written consent of the Owner, the Hirer will not alter, modify or attach anything to the Equipment unless the alteration, modification or attachment is easily removable without damaging the functional capabilities or economic value of the Equipment.</p>
</div>

<div class="ct-section">
  <h3>Warranties</h3>
  <p><strong>10.</strong> The Equipment will be in good working order and good condition upon delivery.</p>
  <p><strong>11.</strong> The Equipment is of merchantable quality and is fit for the purposes it is ordinarily used.</p>
</div>

<div class="ct-section">
  <h3>Loss and Damage</h3>
  <p><strong>12.</strong> To the extent permitted by law, the Hirer will be responsible for risk of loss, theft, damage or destruction to the Equipment from any and every cause.</p>
  <p><strong>13.</strong> If the Equipment is lost or damaged, the Hirer will continue paying Rent, will provide the Owner with prompt written notice of such loss or damage and will, if the Equipment is repairable, put or cause the Equipment to be put in a state of good repair, appearance and condition.</p>
  <p><strong>14.</strong> In the event of Total Loss of the Equipment, the Hirer will provide the Owner with prompt written notice of such loss and will pay to the Owner all unpaid Rent for the Term plus the Casualty Value of the Equipment, at which point ownership of the Equipment passes to the Hirer.</p>
</div>

<div class="ct-section">
  <h3>Ownership, Right to Lease and Quiet Enjoyment</h3>
  <p><strong>15.</strong> The Equipment is the property of the Owner and will remain the property of the Owner.</p>
  <p><strong>16.</strong> The Hirer will not encumber the Equipment or allow the Equipment to be encumbered or pledge the Equipment as security in any manner.</p>
  <p><strong>17.</strong> The Owner warrants that the Owner has the right to lease the Equipment according to the terms in this Agreement.</p>
  <p><strong>18.</strong> The Owner warrants that as long as no Event of Default has occurred, the Owner will not disturb the Hirer's quiet and peaceful possession of the Equipment or the Hirer's unrestricted use of the Equipment for the purpose for which the Equipment was designed.</p>
</div>

<div class="ct-section">
  <h3>Surrender</h3>
  <p><strong>19.</strong> At the end of the Term or upon earlier termination of this Agreement, the Hirer will make the Equipment available for pick up at {{PICKUP_ADDRESS}}. If the Hirer fails to make the Equipment available for pick up, the Hirer will pay to the Owner any unpaid Rent for the Term plus the Casualty Value of the Equipment plus 10% of the Casualty Value, at which point ownership of the Equipment will pass to the Hirer.</p>
</div>

<div class="ct-section">
  <h3>Insurance</h3>
  <p><strong>20.</strong> No insurance coverage for the Equipment is required under this Agreement.</p>
</div>

<div class="ct-section">
  <h3>Indemnity</h3>
  <p><strong>21.</strong> The Hirer will indemnify and hold harmless the Owner against any and all claims, actions, suits, proceedings, costs, expenses, damages and liabilities, including attorney's fees and costs, arising out of or related to the Hirer's use of the Equipment.</p>
</div>

<div class="ct-section">
  <h3>Default</h3>
  <p><strong>22.</strong> The occurrence of any one or more of the following events will constitute an event of default ("Event of Default") under this Agreement:</p>
  <p><strong>a.</strong> The Hirer fails to pay any amount provided for in this Agreement when such amount is due or otherwise breaches the Hirer's obligations under this Agreement.</p>
  <p><strong>b.</strong> The Hirer becomes insolvent or makes an assignment of rights or property for the benefit of creditors or files for or has bankruptcy proceedings instituted against it under the bankruptcy law of the United Kingdom or another competent jurisdiction.</p>
  <p><strong>c.</strong> A writ of attachment or execution is levied on the Equipment and is not released or satisfied within 10 days.</p>
</div>

<div class="ct-section">
  <h3>Remedies</h3>
  <p><strong>23.</strong> On the occurrence of an Event of Default, the Owner will be entitled to pursue any one or more of the following remedies (the "Remedies"):</p>
  <p><strong>a.</strong> Declare the entire amount of the Rent for the Term immediately due and payable without notice or demand to the Hirer.</p>
  <p><strong>b.</strong> Apply the Deposit toward any amount owing to the Owner.</p>
  <p><strong>c.</strong> Commence legal proceedings to recover the Rent and other obligations accrued before and after the Event of Default.</p>
  <p><strong>d.</strong> Take possession of the Equipment, without demand or notice, wherever same may be located, without any court order or other process of law. The Hirer waives any and all damage occasioned by such taking of possession.</p>
  <p><strong>e.</strong> Terminate this Agreement immediately upon written notice to the Hirer.</p>
  <p><strong>f.</strong> Pursue any other remedy available in law or equity.</p>
  <p><strong>24.</strong> The Hirer is entitled to the protection and remedies available to them under the Consumer Credit Act 1974.</p>
</div>

<div class="ct-section">
  <h3>Assignment</h3>
  <p><strong>25.</strong> THE HIRER WILL NOT ASSIGN THIS AGREEMENT, THE HIRER'S INTEREST IN THIS AGREEMENT OR THE HIRER'S INTEREST IN THE EQUIPMENT WITHOUT THE PRIOR WRITTEN CONSENT OF THE OWNER.</p>
  <p><strong>26.</strong> If the Hirer assigns this Agreement without the prior written consent of the Owner, the Owner will have recourse to the Remedies and will be entitled to all damages caused by the assignment.</p>
</div>

<div class="ct-section">
  <h3>Entire Agreement</h3>
  <p><strong>27.</strong> This Agreement will constitute the entire agreement between the Parties. Any prior understanding or representation of any kind preceding the date of this Agreement will not be binding on either Party except to the extent incorporated in this Agreement.</p>
</div>

<div class="ct-section">
  <h3>Address for Notice</h3>
  <p><strong>28.</strong> Service of all notices under this Agreement will be delivered personally or sent by registered mail or courier to the following addresses:</p>
  <p><strong>Owner:</strong> Ben Henderson, 16 Hudson House, Bessemer Road, Welwyn Garden City, Herts, AL7 1GT</p>
  <p><strong>Hirer:</strong> {{HIRER_NAME}}, {{HIRER_ADDRESS}}</p>
</div>

<div class="ct-section">
  <h3>Payment</h3>
  <p><strong>29.</strong> All pound amounts in this agreement refer to pounds sterling, and all payments required to be paid under this Agreement will be paid in pound sterling unless the Parties agree otherwise.</p>
</div>

<div class="ct-section">
  <h3>Governing Law</h3>
  <p><strong>31.</strong> This Agreement will be construed in accordance with and governed by the laws of England and the Parties submit to the exclusive jurisdiction of the English courts.</p>
</div>

<div class="ct-section">
  <h3>Severability</h3>
  <p><strong>32.</strong> If there is a conflict between any provision of this Agreement and the applicable legislation of England, the Act will prevail and such provisions will be amended or deleted as necessary in order to comply with the Act.</p>
  <p><strong>33.</strong> In the event that any of the provisions of this Agreement are held to be invalid or unenforceable in whole or in part, all other provisions will nevertheless continue to be valid and enforceable.</p>
</div>

<div class="ct-section">
  <h3>General Terms</h3>
  <p><strong>34.</strong> This Agreement may be executed in counterparts. Facsimile signatures are binding and are considered to be original signatures.</p>
  <p><strong>35.</strong> Time is of the essence in this Agreement.</p>
  <p><strong>36.</strong> This Agreement will extend to and be binding upon the respective heirs, executors, administrators, successors and assigns of each Party.</p>
  <p><strong>37.</strong> Neither Party will be liable in damages or have the right to terminate this Agreement for any delay or default in performance caused by conditions beyond its control including Acts of God, Government restrictions, wars, insurrections, or natural disasters.</p>
</div>

<div class="ct-notice">
  <p><strong>NOTICE TO THE HIRER:</strong> This is a lease. You are not buying the Equipment. Do not sign this Agreement before you read it. You are entitled to a completed copy of this Agreement when you sign it.</p>
</div>

<div class="ct-witness">
  <p>IN WITNESS WHEREOF Ben Henderson and {{HIRER_NAME}} have duly affixed their signatures under hand and seal on this {{AGREEMENT_DATE}}.</p>
  <div class="ct-sig-row">
    <div class="ct-sig-block"><span class="ct-sig-name">Ben Henderson (Owner)</span></div>
    <div class="ct-sig-block"><span class="ct-sig-name">{{HIRER_NAME}} (Hirer)</span></div>
  </div>
</div>
"""


def seed_contract_template_for_tenant(tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM contract_templates WHERE tenant_id = ? LIMIT 1", (tenant_id,))
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO contract_templates (tenant_id, name, html_content) VALUES (?, ?, ?)",
                    (tenant_id, "Equipment Hire Agreement", DEFAULT_CONTRACT_HTML),
                )
    finally:
        conn.close()


def list_contract_templates(tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, name, created_at, updated_at FROM contract_templates WHERE tenant_id = ? ORDER BY created_at ASC", (tenant_id,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_contract_template(template_id: int, tenant_id: int) -> dict | None:
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM contract_templates WHERE id = ? AND tenant_id = ?", (template_id, tenant_id))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_contract_template(tenant_id: int, name: str, html_content: str) -> int:
    conn = _conn()
    try:
        with conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO contract_templates (tenant_id, name, html_content) VALUES (?, ?, ?)",
                (tenant_id, name, html_content),
            )
            return cur.lastrowid
    finally:
        conn.close()


def update_contract_template(template_id: int, tenant_id: int, name: str, html_content: str) -> None:
    conn = _conn()
    try:
        with conn:
            conn.execute(
                "UPDATE contract_templates SET name=?, html_content=?, updated_at=datetime('now') WHERE id=? AND tenant_id=?",
                (name, html_content, template_id, tenant_id),
            )
    finally:
        conn.close()


def delete_contract_template(template_id: int, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            conn.execute("DELETE FROM contract_templates WHERE id=? AND tenant_id=?", (template_id, tenant_id))
    finally:
        conn.close()


def list_hire_contracts(tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, token, customer_name, customer_email, customer_phone, agreement_date, hire_start, hire_end, rent_amount, status, signed_at, signer_name, created_at FROM hire_contracts WHERE tenant_id=? ORDER BY created_at DESC",
            (tenant_id,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_hire_contract(contract_id: int, tenant_id: int) -> dict | None:
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM hire_contracts WHERE id=? AND tenant_id=?", (contract_id, tenant_id))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_hire_contract_by_token(token: str) -> dict | None:
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM hire_contracts WHERE token=?", (token,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_hire_contract(tenant_id: int, template_id: int, fields: dict) -> tuple[int, str]:
    import uuid as _uuid
    template = None
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT html_content FROM contract_templates WHERE id=? AND tenant_id=?", (template_id, tenant_id))
        row = cur.fetchone()
        if row:
            template = row["html_content"]
    finally:
        conn.close()

    if not template:
        raise ValueError("Template not found")

    vars_map = {
        "HIRER_NAME": fields.get("customer_name", ""),
        "HIRER_ADDRESS": fields.get("customer_address", "").replace("\n", "<br>"),
        "AGREEMENT_DATE": fields.get("agreement_date", ""),
        "EQUIPMENT_DESCRIPTION": fields.get("equipment_desc", ""),
        "EQUIPMENT_VALUE": fields.get("equipment_value", ""),
        "HIRE_START": fields.get("hire_start", ""),
        "HIRE_END": fields.get("hire_end", ""),
        "RENT_AMOUNT": fields.get("rent_amount", ""),
        "DEPOSIT_AMOUNT": fields.get("deposit_amount", ""),
        "DELIVERY_ADDRESS": fields.get("delivery_address", "").replace("\n", "<br>"),
        "PICKUP_ADDRESS": fields.get("pickup_address", "").replace("\n", "<br>"),
    }
    html = template
    for k, v in vars_map.items():
        html = html.replace("{{" + k + "}}", v)

    token = str(_uuid.uuid4())
    conn2 = _conn()
    try:
        with conn2:
            cur2 = conn2.cursor()
            cur2.execute("""
                INSERT INTO hire_contracts
                  (tenant_id, template_id, token, customer_name, customer_email, customer_phone,
                   customer_address, agreement_date, equipment_desc, equipment_value,
                   hire_start, hire_end, rent_amount, deposit_amount,
                   delivery_address, pickup_address, html_content)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                tenant_id, template_id, token,
                fields.get("customer_name", ""), fields.get("customer_email", ""), fields.get("customer_phone", ""),
                fields.get("customer_address", ""), fields.get("agreement_date", ""),
                fields.get("equipment_desc", ""), fields.get("equipment_value", ""),
                fields.get("hire_start", ""), fields.get("hire_end", ""),
                fields.get("rent_amount", ""), fields.get("deposit_amount", ""),
                fields.get("delivery_address", ""), fields.get("pickup_address", ""),
                html,
            ))
            return cur2.lastrowid, token
    finally:
        conn2.close()


def sign_hire_contract(token: str, signer_name: str, signature_image: str, ip_address: str) -> None:
    conn = _conn()
    try:
        with conn:
            conn.execute(
                "UPDATE hire_contracts SET status='signed', signed_at=datetime('now'), signer_name=?, signature_image=?, ip_address=? WHERE token=? AND status='sent'",
                (signer_name, signature_image, ip_address, token),
            )
    finally:
        conn.close()


def delete_hire_contract(contract_id: int, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            conn.execute("DELETE FROM hire_contracts WHERE id=? AND tenant_id=?", (contract_id, tenant_id))
    finally:
        conn.close()
