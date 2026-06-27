"""
Copies all data from the Fly.io Postgres into a local SQLite file.
Run with the fly proxy active:
    fly proxy 5434:5433 --app nowdj-db &
    python migrate_pg_to_sqlite.py
"""
import json
import os
import sqlite3
import psycopg2
import psycopg2.extras

PG_URL = "postgresql://postgres:SKYj0pFbw7ZRYL6@localhost:5434/nowdj"
SQLITE_PATH = "/Users/benhenderson/nowdj_migrated.db"

os.environ["DATABASE_URL"] = f"sqlite:///{SQLITE_PATH}"

import database as db

print("Creating SQLite schema...")
db.init_db()

pg = psycopg2.connect(PG_URL, cursor_factory=psycopg2.extras.RealDictCursor)
sq = sqlite3.connect(SQLITE_PATH)
sq.row_factory = sqlite3.Row
sq.execute("PRAGMA foreign_keys=OFF")  # disable during bulk insert


def pg_rows(table, order="id"):
    with pg.cursor() as cur:
        cur.execute(f"SELECT * FROM {table} ORDER BY {order}")
        return [dict(r) for r in cur.fetchall()]


def ts(val):
    """Convert postgres datetime to ISO string for SQLite."""
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)


print("Migrating tenants...")
for r in pg_rows("tenants"):
    sq.execute(
        "INSERT OR IGNORE INTO tenants (id, name, slug, email, password_hash, plan, custom_domain, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (r["id"], r["name"], r["slug"], r["email"], r["password_hash"], r["plan"], r.get("custom_domain"), ts(r.get("created_at")))
    )

print("Migrating settings...")
for r in pg_rows("settings", order="key"):
    sq.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)",
        (r["key"], r["value"])
    )

print("Migrating quotes...")
for r in pg_rows("quotes"):
    sq.execute(
        "INSERT OR IGNORE INTO quotes (id, tenant_id, name, email, phone, event_date, location, event_type, selected_items, total_price, message, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (r["id"], r["tenant_id"], r["name"], r["email"], r.get("phone",""), r.get("event_date",""), r.get("location",""), r.get("event_type",""), r.get("selected_items","[]"), r.get("total_price",0), r.get("message",""), r.get("status","new"), ts(r.get("created_at")))
    )

print("Migrating email_templates...")
for r in pg_rows("email_templates"):
    sq.execute(
        "INSERT OR IGNORE INTO email_templates (id, tenant_id, name, subject, body, sort_order, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (r["id"], r["tenant_id"], r["name"], r.get("subject",""), r.get("body",""), r.get("sort_order",0), ts(r.get("created_at")), ts(r.get("updated_at")))
    )

print("Migrating users...")
for r in pg_rows("users"):
    sq.execute(
        "INSERT OR IGNORE INTO users (id, tenant_id, name, email, password_hash, created_at) VALUES (?,?,?,?,?,?)",
        (r["id"], r["tenant_id"], r["name"], r["email"], r["password_hash"], ts(r.get("created_at")))
    )

print("Migrating staff_members...")
for r in pg_rows("staff_members"):
    sq.execute(
        "INSERT OR IGNORE INTO staff_members (id, tenant_id, name, email, password_hash, created_at) VALUES (?,?,?,?,?,?)",
        (r["id"], r["tenant_id"], r["name"], r["email"], r["password_hash"], ts(r.get("created_at")))
    )

print("Migrating email_automations...")
for r in pg_rows("email_automations"):
    sq.execute(
        "INSERT OR IGNORE INTO email_automations (id, tenant_id, name, trigger_event, template_id, send_to, send_to_email, enabled, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (r["id"], r["tenant_id"], r["name"], r.get("trigger_event","form_submission"), r.get("template_id"), r.get("send_to","custom"), r.get("send_to_email"), 1 if r.get("enabled", True) else 0, ts(r.get("created_at")))
    )

print("Migrating bookings...")
for r in pg_rows("bookings"):
    sq.execute(
        "INSERT OR IGNORE INTO bookings (id, tenant_id, quote_id, user_id, staff_id, title, event_date, event_type, location, notes, total_price, status, staff_pay, staff_response, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (r["id"], r["tenant_id"], r.get("quote_id"), r.get("user_id"), r.get("staff_id"), r.get("title",""), r.get("event_date",""), r.get("event_type",""), r.get("location",""), r.get("notes",""), r.get("total_price",0), r.get("status","confirmed"), r.get("staff_pay"), r.get("staff_response"), ts(r.get("created_at")))
    )

print("Migrating documents...")
for r in pg_rows("documents"):
    sender = r.get("sender", "{}")
    if isinstance(sender, dict):
        sender = json.dumps(sender)
    sq.execute(
        "INSERT OR IGNORE INTO documents (id, tenant_id, doc_type, doc_number, status, client_name, client_email, client_phone, client_address, event_date, event_type, location, line_items, notes, source_quote_id, sender, discount_type, discount_value, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (r["id"], r["tenant_id"], r.get("doc_type","quote"), r.get("doc_number",""), r.get("status","draft"), r.get("client_name",""), r.get("client_email",""), r.get("client_phone",""), r.get("client_address",""), r.get("event_date",""), r.get("event_type",""), r.get("location",""), r.get("line_items","[]"), r.get("notes",""), r.get("source_quote_id"), sender, r.get("discount_type","percent"), float(r.get("discount_value") or 0), ts(r.get("created_at")), ts(r.get("updated_at")))
    )

sq.execute("PRAGMA foreign_keys=ON")
sq.commit()
pg.close()
sq.close()

print(f"\nDone. SQLite DB written to {SQLITE_PATH}")
