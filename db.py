"""
db.py
-----
A tiny SQLite layer that lets two separate local processes — the
Streamlit dashboard and the FastAPI webhook — share one triage queue
without any network service beyond a file on disk. SQLite's default
locking is more than enough for a single-laptop hackathon demo.
"""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "triage_queue.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    source TEXT,
    sender TEXT,
    raw_message TEXT NOT NULL,
    is_emergency INTEGER DEFAULT 1,
    emergency_type TEXT,
    urgency TEXT,
    urgency_rank INTEGER,
    people_affected TEXT,
    action_required TEXT,
    recommended_resources TEXT,
    location TEXT,
    confidence REAL,
    parse_error TEXT,
    latency_sec REAL
);

CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    raw_message TEXT NOT NULL,
    emergency_type TEXT,
    urgency TEXT,
    people_affected TEXT,
    action_required TEXT,
    recommended_resources TEXT,
    location TEXT,
    confidence REAL
);

CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_code TEXT NOT NULL,
    category TEXT NOT NULL,
    status TEXT NOT NULL,
    lat REAL,
    lng REAL,
    battery_fuel_pct REAL,
    personnel_count INTEGER,
    eta_minutes INTEGER,
    assigned_incident_id INTEGER
);

CREATE TABLE IF NOT EXISTS clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    impact_radius_miles REAL,
    estimated_civilians INTEGER,
    lat REAL,
    lng REAL,
    ai_damage_assessment TEXT,
    historical_context TEXT,
    risk_forecast TEXT
);
CREATE TABLE IF NOT EXISTS api_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    model TEXT,
    success INTEGER
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)  # idempotent safety net
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        # Seed resources if empty
        if conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO resources (unit_code, category, status, lat, lng, battery_fuel_pct, personnel_count, eta_minutes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("H-Alpha-01", "HELICOPTER", "STANDBY", 41.8781, -87.6298, 82.0, 6, 4),
                    ("M-Team-Delta", "MEDICAL_TEAM", "STANDBY", 41.8827, -87.6233, 95.0, 4, 0),
                    ("B-Swift-04", "RESCUE_BOAT", "STANDBY", 41.8885, -87.6355, 18.0, 5, 12),
                    ("H-Bravo-02", "HELICOPTER", "STANDBY", 41.8650, -87.6173, 100.0, 0, 0),
                    ("T-1", "RESCUE_BOAT", "STANDBY", 41.8902, -87.6410, 75.0, 3, 8),
                    ("R-4", "HELICOPTER", "STANDBY", 41.8500, -87.6100, 100.0, 4, 0)
                ]
            )
            
        # Seed clusters if empty
        if conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO clusters (name, risk_level, impact_radius_miles, estimated_civilians, lat, lng, ai_damage_assessment, historical_context, risk_forecast) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("Downtown Flood Zone (DEMO SEED)", "CRITICAL", 2.4, 1450, 41.8781, -87.6298, "Structural integrity of 4th St bridge compromised. High probability of flash flooding in residential sector B.", "3rd major flood in this zone since 2018.", "Water levels rising +2in/hr"),
                    ("Southside Power Failure (DEMO SEED)", "HIGH", 1.2, 850, 41.7943, -87.5907, "Grid failure leading to secondary infrastructure outages.", "Aging grid infrastructure.", "Expected restoration in 12hrs")
                ]
            )


def insert_record(record) -> int:
    """record: an llm.EmergencyRecord"""
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO incidents
                (timestamp, source, sender, raw_message, is_emergency, emergency_type,
                 urgency, urgency_rank, people_affected, action_required,
                 recommended_resources, location, confidence, parse_error, latency_sec)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.timestamp,
                record.source,
                record.sender,
                record.raw_message,
                int(record.is_emergency),
                record.emergency_type,
                record.urgency,
                record.urgency_rank(),
                record.people_affected,
                record.action_required,
                record.recommended_resources,
                record.location,
                record.confidence,
                record.parse_error,
                record.latency_sec,
            ),
        )
        return cur.lastrowid


def fetch_all(sort_by_urgency: bool = True):
    order_clause = "ORDER BY urgency_rank ASC, timestamp DESC" if sort_by_urgency else "ORDER BY timestamp DESC"
    with get_conn() as conn:
        rows = conn.execute(f"SELECT * FROM incidents {order_clause}").fetchall()
        return [dict(r) for r in rows]


def clear_all():
    with get_conn() as conn:
        conn.execute("DELETE FROM incidents")


# --- Corrections: operator-verified ground truth, used as live few-shot examples ---

def save_correction(raw_message, emergency_type, urgency, people_affected,
                     action_required, recommended_resources):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO corrections
                (timestamp, raw_message, emergency_type, urgency,
                 people_affected, action_required, recommended_resources)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (time.time(), raw_message, emergency_type, urgency,
             people_affected, action_required, recommended_resources),
        )


def fetch_corrections(limit: int = 200):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM corrections ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

# --- API quota tracking (local counter, since Gemini free tier gives no live quota endpoint) ---

DAILY_QUOTA_LIMIT = 14400  # Groq free tier limit

def log_api_call(model: str, success: bool):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO api_usage (timestamp, model, success) VALUES (?, ?, ?)",
            (time.time(), model, int(success)),
        )


def get_quota_status():
    """Counts calls made since midnight UTC (matches Google's daily reset window)."""
    import datetime
    midnight_utc = datetime.datetime.combine(
        datetime.datetime.utcnow().date(), datetime.time.min, tzinfo=datetime.timezone.utc
    ).timestamp()

    with get_conn() as conn:
        used = conn.execute(
            "SELECT COUNT(*) FROM api_usage WHERE timestamp >= ?", (midnight_utc,)
        ).fetchone()[0]

    remaining = max(0, DAILY_QUOTA_LIMIT - used)
    return {
        "used": used,
        "limit": DAILY_QUOTA_LIMIT,
        "remaining": remaining,
        "exhausted": remaining == 0,
    }
init_db()
