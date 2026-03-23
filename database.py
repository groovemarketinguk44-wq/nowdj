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
                # ── Tenants (must be first — all other tables FK to this)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS tenants (
                        id            SERIAL PRIMARY KEY,
                        name          TEXT NOT NULL,
                        slug          TEXT UNIQUE NOT NULL,
                        email         TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        plan          TEXT NOT NULL DEFAULT 'starter',
                        custom_domain TEXT UNIQUE DEFAULT NULL,
                        created_at    TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS custom_domain TEXT UNIQUE DEFAULT NULL")

                # ── Quotes
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS quotes (
                        id             SERIAL PRIMARY KEY,
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
                        created_at     TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("ALTER TABLE quotes ADD COLUMN IF NOT EXISTS tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE")

                # ── Email templates
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS email_templates (
                        id         SERIAL PRIMARY KEY,
                        tenant_id  INTEGER REFERENCES tenants(id) ON DELETE CASCADE,
                        name       TEXT    NOT NULL,
                        subject    TEXT    NOT NULL DEFAULT '',
                        body       TEXT    NOT NULL DEFAULT '',
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        updated_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("ALTER TABLE email_templates ADD COLUMN IF NOT EXISTS tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE")

                # ── Settings (plain key-value; tenant keys are prefixed t{id}:key)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                        key   TEXT PRIMARY KEY,
                        value TEXT NOT NULL DEFAULT ''
                    )
                """)

                # ── Users (customers)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id            SERIAL PRIMARY KEY,
                        tenant_id     INTEGER REFERENCES tenants(id) ON DELETE CASCADE,
                        name          TEXT NOT NULL,
                        email         TEXT NOT NULL,
                        password_hash TEXT NOT NULL,
                        created_at    TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE")
                # Email uniqueness is per-tenant, not global
                cur.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_key")
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_tenant_email ON users(tenant_id, email) WHERE tenant_id IS NOT NULL")

                # ── Staff members
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS staff_members (
                        id            SERIAL PRIMARY KEY,
                        tenant_id     INTEGER REFERENCES tenants(id) ON DELETE CASCADE,
                        name          TEXT NOT NULL,
                        email         TEXT NOT NULL,
                        password_hash TEXT NOT NULL,
                        created_at    TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute("ALTER TABLE staff_members ADD COLUMN IF NOT EXISTS tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE")
                cur.execute("ALTER TABLE staff_members DROP CONSTRAINT IF EXISTS staff_members_email_key")
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS staff_tenant_email ON staff_members(tenant_id, email) WHERE tenant_id IS NOT NULL")

                # ── Bookings
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS bookings (
                        id          SERIAL PRIMARY KEY,
                        tenant_id   INTEGER REFERENCES tenants(id) ON DELETE CASCADE,
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
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE")
    finally:
        conn.close()


def migrate_null_tenant_ids(tenant_id: int) -> None:
    """Assign any legacy rows with NULL tenant_id to the given tenant. Safe to call repeatedly."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE quotes SET tenant_id = %s WHERE tenant_id IS NULL", (tenant_id,))
                cur.execute("UPDATE email_templates SET tenant_id = %s WHERE tenant_id IS NULL", (tenant_id,))
                cur.execute("UPDATE users SET tenant_id = %s WHERE tenant_id IS NULL", (tenant_id,))
                cur.execute("UPDATE staff_members SET tenant_id = %s WHERE tenant_id IS NULL", (tenant_id,))
                cur.execute("UPDATE bookings SET tenant_id = %s WHERE tenant_id IS NULL", (tenant_id,))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Settings (key-value store)
# Tenant-specific keys are prefixed with t{tenant_id}:
# Super-admin / global keys have no prefix.
# ---------------------------------------------------------------------------

def _skey(key: str, tenant_id: int | None = None) -> str:
    return f"t{tenant_id}:{key}" if tenant_id is not None else key


def get_setting(key: str, default: str = "", tenant_id: int | None = None) -> str:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key = %s", (_skey(key, tenant_id),))
            row = cur.fetchone()
            return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str, tenant_id: int | None = None) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO settings (key, value) VALUES (%s, %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """, (_skey(key, tenant_id), value))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tenants
# ---------------------------------------------------------------------------

def create_tenant(name: str, slug: str, email: str, password_hash: str, plan: str = "starter") -> int:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO tenants (name, slug, email, password_hash, plan) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (name, slug, email, password_hash, plan),
                )
                return cur.fetchone()["id"]
    finally:
        conn.close()


def get_tenant_by_slug(slug: str) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM tenants WHERE slug = %s", (slug,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def get_tenant_by_id(tid: int) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, slug, email, plan, custom_domain, created_at FROM tenants WHERE id = %s", (tid,))
            row = cur.fetchone()
            if not row:
                return None
            r = dict(row)
            if r.get("created_at"):
                r["created_at"] = r["created_at"].isoformat()
            return r
    finally:
        conn.close()


def get_tenant_by_email(email: str) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM tenants WHERE email = %s", (email,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def get_tenant_by_custom_domain(domain: str) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM tenants WHERE lower(custom_domain) = %s", (domain.lower(),))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def get_all_tenants() -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, slug, email, plan, custom_domain, created_at FROM tenants ORDER BY created_at ASC")
            result = []
            for row in cur.fetchall():
                r = dict(row)
                if r.get("created_at"):
                    r["created_at"] = r["created_at"].isoformat()
                result.append(r)
            return result
    finally:
        conn.close()


def update_tenant(tid: int, name: str, email: str, plan: str, password_hash: str | None = None, custom_domain: str | None = ...) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                # custom_domain=... (Ellipsis) means "don't touch it"; None means clear it
                if custom_domain is ...:
                    if password_hash:
                        cur.execute("UPDATE tenants SET name=%s, email=%s, plan=%s, password_hash=%s WHERE id=%s", (name, email, plan, password_hash, tid))
                    else:
                        cur.execute("UPDATE tenants SET name=%s, email=%s, plan=%s WHERE id=%s", (name, email, plan, tid))
                else:
                    cd = custom_domain.strip().lower() if custom_domain else None
                    if password_hash:
                        cur.execute("UPDATE tenants SET name=%s, email=%s, plan=%s, password_hash=%s, custom_domain=%s WHERE id=%s", (name, email, plan, password_hash, cd, tid))
                    else:
                        cur.execute("UPDATE tenants SET name=%s, email=%s, plan=%s, custom_domain=%s WHERE id=%s", (name, email, plan, cd, tid))
    finally:
        conn.close()


def delete_tenant(tid: int) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM tenants WHERE id = %s", (tid,))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------

def save_quote(data: dict, tenant_id: int) -> int:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO quotes
                        (tenant_id, name, email, phone, event_date, location, event_type,
                         selected_items, total_price, message)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
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


def get_all_quotes(tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM quotes WHERE tenant_id = %s ORDER BY created_at DESC", (tenant_id,))
            return [_parse_quote(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_quote_by_id(quote_id: int, tenant_id: int) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM quotes WHERE id = %s AND tenant_id = %s", (quote_id, tenant_id))
            row = cur.fetchone()
            return _parse_quote(row) if row else None
    finally:
        conn.close()


def update_quote_status(quote_id: int, status: str, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE quotes SET status = %s WHERE id = %s AND tenant_id = %s",
                    (status, quote_id, tenant_id),
                )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------

def seed_templates_for_tenant(tenant_id: int) -> None:
    """Insert default email templates for a newly created tenant."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM email_templates WHERE tenant_id = %s", (tenant_id,))
                if cur.fetchone()["c"] == 0:
                    for t in DEFAULT_TEMPLATES:
                        cur.execute(
                            "INSERT INTO email_templates (tenant_id, name, subject, body) VALUES (%s, %s, %s, %s)",
                            (tenant_id, t["name"], t["subject"], t["body"]),
                        )
    finally:
        conn.close()


def get_all_templates(tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM email_templates WHERE tenant_id = %s ORDER BY id ASC",
                (tenant_id,),
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_template_by_id(tid: int, tenant_id: int) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM email_templates WHERE id = %s AND tenant_id = %s",
                (tid, tenant_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def create_template(name: str, subject: str, body: str, tenant_id: int) -> int:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO email_templates (tenant_id, name, subject, body) VALUES (%s, %s, %s, %s) RETURNING id",
                    (tenant_id, name, subject, body),
                )
                return cur.fetchone()["id"]
    finally:
        conn.close()


def update_template(tid: int, name: str, subject: str, body: str, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE email_templates
                    SET name=%s, subject=%s, body=%s, updated_at=NOW()
                    WHERE id=%s AND tenant_id=%s
                """, (name, subject, body, tid, tenant_id))
    finally:
        conn.close()


def delete_template(tid: int, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM email_templates WHERE id = %s AND tenant_id = %s",
                    (tid, tenant_id),
                )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Users (customers)
# ---------------------------------------------------------------------------

def get_user_by_email(email: str, tenant_id: int) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM users WHERE email = %s AND tenant_id = %s",
                (email, tenant_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(uid: int, tenant_id: int) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, email, created_at FROM users WHERE id = %s AND tenant_id = %s",
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
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (tenant_id, name, email, password_hash) VALUES (%s, %s, %s, %s) RETURNING id",
                    (tenant_id, name, email, password_hash),
                )
                return cur.fetchone()["id"]
    finally:
        conn.close()


def get_all_users(tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, email, created_at FROM users WHERE tenant_id = %s ORDER BY created_at DESC",
                (tenant_id,),
            )
            result = []
            for row in cur.fetchall():
                r = dict(row)
                if r.get("created_at"):
                    r["created_at"] = r["created_at"].isoformat()
                result.append(r)
            return result
    finally:
        conn.close()


def delete_user(uid: int, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE id = %s AND tenant_id = %s", (uid, tenant_id))
    finally:
        conn.close()


def get_quotes_by_email(email: str, tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM quotes WHERE LOWER(email) = LOWER(%s) AND tenant_id = %s ORDER BY created_at DESC",
                (email, tenant_id),
            )
            return [_parse_quote(row) for row in cur.fetchall()]
    finally:
        conn.close()


def update_user(uid: int, name: str, email: str, tenant_id: int, password_hash: str | None = None) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                if password_hash:
                    cur.execute(
                        "UPDATE users SET name=%s, email=%s, password_hash=%s WHERE id=%s AND tenant_id=%s",
                        (name, email, password_hash, uid, tenant_id),
                    )
                else:
                    cur.execute(
                        "UPDATE users SET name=%s, email=%s WHERE id=%s AND tenant_id=%s",
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
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, email, created_at FROM staff_members WHERE tenant_id = %s ORDER BY name ASC",
                (tenant_id,),
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_staff_by_email(email: str, tenant_id: int) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM staff_members WHERE email = %s AND tenant_id = %s",
                (email, tenant_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def find_staff_globally(email: str) -> dict | None:
    """Find a staff member by email across all tenants; includes tenant_slug and tenant_custom_domain."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT sm.*, t.slug AS tenant_slug, t.custom_domain AS tenant_custom_domain
                   FROM staff_members sm
                   JOIN tenants t ON t.id = sm.tenant_id
                   WHERE sm.email = %s LIMIT 1""",
                (email,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def find_user_globally(email: str) -> dict | None:
    """Find a customer by email across all tenants; includes tenant_slug and tenant_custom_domain."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT u.*, t.slug AS tenant_slug, t.custom_domain AS tenant_custom_domain
                   FROM users u
                   JOIN tenants t ON t.id = u.tenant_id
                   WHERE u.email = %s LIMIT 1""",
                (email,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def get_staff_by_id(sid: int, tenant_id: int) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, email, created_at FROM staff_members WHERE id = %s AND tenant_id = %s",
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
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO staff_members (tenant_id, name, email, password_hash) VALUES (%s, %s, %s, %s) RETURNING id",
                    (tenant_id, name, email, password_hash),
                )
                return cur.fetchone()["id"]
    finally:
        conn.close()


def delete_staff_member(sid: int, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM staff_members WHERE id = %s AND tenant_id = %s",
                    (sid, tenant_id),
                )
    finally:
        conn.close()


def update_staff_member_info(sid: int, name: str, email: str, tenant_id: int, password_hash: str | None = None) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                if password_hash:
                    cur.execute(
                        "UPDATE staff_members SET name=%s, email=%s, password_hash=%s WHERE id=%s AND tenant_id=%s",
                        (name, email, password_hash, sid, tenant_id),
                    )
                else:
                    cur.execute(
                        "UPDATE staff_members SET name=%s, email=%s WHERE id=%s AND tenant_id=%s",
                        (name, email, sid, tenant_id),
                    )
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


def get_all_bookings(tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT b.*, s.name AS staff_name, u.name AS customer_name, u.email AS customer_email
                FROM bookings b
                LEFT JOIN staff_members s ON b.staff_id = s.id
                LEFT JOIN users u ON b.user_id = u.id
                WHERE b.tenant_id = %s
                ORDER BY b.event_date ASC, b.created_at DESC
            """, (tenant_id,))
            return [_parse_booking(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_booking_by_id(bid: int, tenant_id: int) -> dict | None:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT b.*, s.name AS staff_name, u.name AS customer_name, u.email AS customer_email
                FROM bookings b
                LEFT JOIN staff_members s ON b.staff_id = s.id
                LEFT JOIN users u ON b.user_id = u.id
                WHERE b.id = %s AND b.tenant_id = %s
            """, (bid, tenant_id))
            row = cur.fetchone()
            return _parse_booking(row) if row else None
    finally:
        conn.close()


def get_bookings_for_user(user_id: int, tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT b.*, s.name AS staff_name
                FROM bookings b
                LEFT JOIN staff_members s ON b.staff_id = s.id
                WHERE b.user_id = %s AND b.tenant_id = %s
                ORDER BY b.event_date ASC
            """, (user_id, tenant_id))
            return [_parse_booking(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_bookings_for_staff(staff_id: int, tenant_id: int) -> list[dict]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT b.*, u.name AS customer_name, u.email AS customer_email
                FROM bookings b
                LEFT JOIN users u ON b.user_id = u.id
                WHERE b.staff_id = %s AND b.tenant_id = %s
                ORDER BY b.event_date ASC
            """, (staff_id, tenant_id))
            return [_parse_booking(row) for row in cur.fetchall()]
    finally:
        conn.close()


def create_booking(data: dict, tenant_id: int) -> int:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO bookings
                        (tenant_id, quote_id, user_id, staff_id, title, event_date, event_type,
                         location, notes, total_price, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
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
                ))
                return cur.fetchone()["id"]
    finally:
        conn.close()


def update_booking(bid: int, data: dict, tenant_id: int) -> None:
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
                    WHERE id = %s AND tenant_id = %s
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
                    tenant_id,
                ))
    finally:
        conn.close()


def delete_booking(bid: int, tenant_id: int) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM bookings WHERE id = %s AND tenant_id = %s", (bid, tenant_id))
    finally:
        conn.close()
