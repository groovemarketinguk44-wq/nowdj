import sqlite3
import json
import os
from pathlib import Path

_DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = _DATA_DIR / "nowdj.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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


def init_db() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS quotes (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
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
                created_at    TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS email_templates (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL,
                subject    TEXT    NOT NULL DEFAULT '',
                body       TEXT    NOT NULL DEFAULT '',
                created_at TEXT    DEFAULT (datetime('now')),
                updated_at TEXT    DEFAULT (datetime('now'))
            )
        """)
        # Seed default templates if table is empty
        count = conn.execute("SELECT COUNT(*) FROM email_templates").fetchone()[0]
        if count == 0:
            for t in DEFAULT_TEMPLATES:
                conn.execute(
                    "INSERT INTO email_templates (name, subject, body) VALUES (?, ?, ?)",
                    (t["name"], t["subject"], t["body"]),
                )
        conn.commit()


def save_quote(data: dict) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO quotes
                (name, email, phone, event_date, location, event_type,
                 selected_items, total_price, message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["name"],
                data["email"],
                data.get("phone", ""),
                data.get("event_date", ""),
                data.get("location", ""),
                data.get("event_type", ""),
                json.dumps(data.get("selected_items", [])),
                data.get("total_price", 0),
                data.get("message", ""),
            ),
        )
        conn.commit()
        return cursor.lastrowid  # type: ignore


def get_all_quotes() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM quotes ORDER BY created_at DESC"
        ).fetchall()
        result = []
        for row in rows:
            q = dict(row)
            if isinstance(q.get("selected_items"), str):
                try:
                    q["selected_items"] = json.loads(q["selected_items"])
                except Exception:
                    q["selected_items"] = []
            result.append(q)
        return result


def get_quote_by_id(quote_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM quotes WHERE id = ?", (quote_id,)
        ).fetchone()
        if not row:
            return None
        q = dict(row)
        if isinstance(q.get("selected_items"), str):
            try:
                q["selected_items"] = json.loads(q["selected_items"])
            except Exception:
                q["selected_items"] = []
        return q


def update_quote_status(quote_id: int, status: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE quotes SET status = ? WHERE id = ?", (status, quote_id)
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------

def get_all_templates() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM email_templates ORDER BY id ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_template_by_id(tid: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM email_templates WHERE id = ?", (tid,)
        ).fetchone()
        return dict(row) if row else None


def create_template(name: str, subject: str, body: str) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO email_templates (name, subject, body) VALUES (?, ?, ?)",
            (name, subject, body),
        )
        conn.commit()
        return cursor.lastrowid  # type: ignore


def update_template(tid: int, name: str, subject: str, body: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE email_templates
               SET name=?, subject=?, body=?, updated_at=datetime('now')
               WHERE id=?""",
            (name, subject, body, tid),
        )
        conn.commit()


def delete_template(tid: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM email_templates WHERE id = ?", (tid,))
        conn.commit()
