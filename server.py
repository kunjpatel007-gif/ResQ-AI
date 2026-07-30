import asyncio
import json
import psutil
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
from llm import analyze_emergency_message

# Initialize DB (creates tables and seeds mock data if needed)
db.init_db()

app = FastAPI(title="GEMMA ECC Backend")

# We will serve the frontend files from the "frontend" directory
FRONTEND_DIR = Path(__file__).parent / "frontend"

@app.get("/")
async def root():
    """Serve the main index.html file."""
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Building Frontend...</h1>")


# ── REST API ENDPOINTS ──────────────────────────────────────────────────

@app.get("/api/incidents")
def get_incidents():
    """Fetch all incidents from the SQLite triage queue."""
    rows = db.fetch_all(sort_by_urgency=True)
    return {"incidents": rows}

@app.post("/api/incident/{incident_id}/resolve")
def resolve_incident(incident_id: int):
    """Dismisses an incident from the queue."""
    with db.get_conn() as conn:
        conn.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
    return {"success": True}

@app.get("/api/resources")
def get_resources():
    """Fetch all resource assets."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM resources").fetchall()
        return {"resources": [dict(r) for r in rows]}

@app.get("/api/clusters")
def get_clusters():
    """Fetch all map incident clusters."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM clusters").fetchall()
        return {"clusters": [dict(r) for r in rows]}

@app.get("/api/quota")
def get_quota():
    """Local tracker for the API free-tier daily request limit."""
    return db.get_quota_status()
    
@app.get("/api/telemetry")
def get_telemetry():
    """Fetch local hardware telemetry."""
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory()
    
    return {
        "cpu_percent": cpu,
        "ram_used_gb": round(ram.used / (1024**3), 1),
        "ram_total_gb": round(ram.total / (1024**3), 1),
        "gpu_percent": 80,  # Mocked GPU load for the dashboard
        "uptime_sec": int(time.time() - psutil.boot_time())
    }

@app.get("/api/system/config")
def get_system_config():
    import os
    provider = "Hugging Face" if os.getenv("HF_API_KEY") else "Local (Mock)"
    return {
        "provider": provider,
        "model": "google/gemma-2-9b-it"
    }

class StatusPayload(BaseModel):
    status: str

@app.post("/api/resource/{unit_code}/status")
def update_resource_status(unit_code: str, payload: StatusPayload):
    """Update a resource status in the database."""
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE resources SET status = ? WHERE unit_code = ?",
            (payload.status, unit_code)
        )
    return {"success": True, "unit_code": unit_code, "new_status": payload.status}

class DeployPayload(BaseModel):
    incident_id: int = None

@app.post("/api/deploy_next_available")
def deploy_next_available(payload: DeployPayload = None):
    """Finds the first STANDBY unit and deploys it to the requested incident."""
    with db.get_conn() as conn:
        unit = conn.execute("SELECT unit_code FROM resources WHERE status = 'STANDBY' LIMIT 1").fetchone()
        if unit:
            inc_id = payload.incident_id if payload else None
            conn.execute(
                "UPDATE resources SET status = 'EN_ROUTE', assigned_incident_id = ? WHERE unit_code = ?", 
                (inc_id, unit["unit_code"])
            )
            return {"success": True, "deployed_unit": unit["unit_code"], "incident_id": inc_id}
    return {"success": False, "error": "No units available on STANDBY."}

@app.get("/api/resources/recommendation")
def get_resource_recommendation():
    """Generates a dynamic Intel Action recommendation based on database state."""
    with db.get_conn() as conn:
        # Check if there are any high urgency incidents without a deployed unit
        unassigned = conn.execute(
            """SELECT id, emergency_type, urgency FROM incidents 
               WHERE urgency = 'High' AND id NOT IN (
                   SELECT assigned_incident_id FROM resources WHERE assigned_incident_id IS NOT NULL
               ) LIMIT 1"""
        ).fetchone()
        
        # Check available standby units
        standby_count = conn.execute("SELECT COUNT(*) FROM resources WHERE status = 'STANDBY'").fetchone()[0]
        
        if standby_count == 0:
            return {
                "recommendation": "No units available. All assets deployed.",
                "confidence": 100,
                "title": "CAPACITY ALERT",
                "actionable": False
            }
            
        if unassigned:
            return {
                "recommendation": f"Recommend deploying 1 standby unit to unassigned High-Priority {unassigned['emergency_type']} (INC-{unassigned['id']}).",
                "confidence": 92,
                "title": "URGENT DEPLOYMENT",
                "actionable": True,
                "incident_id": unassigned["id"]
            }
            
        return {
            "recommendation": "All high-priority incidents are currently handled. Keep remaining units on STANDBY.",
            "confidence": 98,
            "title": "STABLE",
            "actionable": False
        }

@app.post("/api/evac_order")
def issue_evac_order():
    """Simulates broadcasting an evacuation order to the sector."""
    # In a real app, this would trigger an SMS gateway or push notification system.
    return {"success": True, "message": "Evacuation order broadcasted to 1,450 civilians."}

class TestPayload(BaseModel):
    message: str

@app.post("/api/test-analyze")
def test_analyze(payload: TestPayload):
    """Endpoint to manually send a message for analysis."""
    msg = payload.message
    if not msg:
        return {"error": "No message provided"}
        
    # Runs inference via API
    record = analyze_emergency_message(msg, source="manual_ui")
    db.insert_record(record)
    
    return {"status": "success", "record": record.__dict__}


# ── WEBSOCKETS (Real-time feeds) ────────────────────────────────────────

@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    """Streams live hardware telemetry and inference logs to the frontend."""
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(get_telemetry())
            await asyncio.sleep(2)
    except Exception:
        pass

# Mount static files (CSS, JS, images)
app.mount("/static", StaticFiles(directory="frontend"), name="static")

import requests

geocode_cache = {}

@app.get("/api/geocode")
def geocode_location(q: str):
    """Server-side geocoding via Nominatim to respect usage policies and cache."""
    if not q or q == "Unknown" or q == "AUTO-LOCATE":
        return {"lat": None, "lng": None}
        
    if q in geocode_cache:
        return geocode_cache[q]
    
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search", 
            params={"q": q, "format": "json", "limit": 1},
            headers={"User-Agent": "GEMMA-ECC-Hackathon/1.0"},
            timeout=3.0
        )
        data = r.json()
        if data and len(data) > 0:
            res = {"lat": float(data[0]["lat"]), "lng": float(data[0]["lon"])}
            geocode_cache[q] = res
            return res
    except Exception as e:
        print(f"Geocode failed for {q}: {e}")
        
    return {"lat": None, "lng": None}

if __name__ == "__main__":
    import uvicorn
    # Run server on all interfaces so it can be accessed via phone/LAN
    print("🚀 Starting GEMMA ECC Server on http://localhost:8000")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
