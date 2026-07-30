"""
webhook.py
----------
A lightweight local HTTP endpoint that receives forwarded SMS/WhatsApp
text. It runs entirely on the laptop, with no internet or cloud API
(no Twilio) — an Android phone on the same local Wi-Fi hotspot posts to
this instead.

Pairing suggestion for the demo:
- Turn on the laptop's mobile hotspot (or point both devices at any
  local router/hotspot with no internet uplink).
- On the phone, install a free "SMS to HTTP/Webhook forwarder" app
  (several exist on F-Droid / Play that need no internet, just LAN).
- Point that app's webhook URL at: http://<laptop-lan-ip>:8000/sms
- Body should be JSON: {"message": "...", "sender": "+1555..."}

Run with:
    uvicorn webhook:app --host 0.0.0.0 --port 8000

Find your laptop's LAN IP on Windows with `ipconfig` (look for the
IPv4 address on the Wi-Fi adapter once the hotspot is active).
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from llm import analyze_emergency_message
import db

app = FastAPI(title="Offline Disaster Triage Webhook")


class IncomingMessage(BaseModel):
    message: str
    sender: str | None = None
    source: str | None = "sms_gateway"


@app.get("/health")
def health():
    """Quick check the judges (or your phone app) can hit to confirm the laptop is reachable."""
    return {"status": "ok"}


@app.post("/sms")
def receive_sms(payload: IncomingMessage):
    if not payload.message or not payload.message.strip():
        raise HTTPException(status_code=400, detail="message field is empty")

    record = analyze_emergency_message(
        payload.message,
        source=payload.source or "sms_gateway",
        sender=payload.sender,
    )
    incident_id = db.insert_record(record)

    return {
        "status": "processed",
        "incident_id": incident_id,
        "urgency": record.urgency,
        "emergency_type": record.emergency_type,
        "parse_error": record.parse_error,
    }


@app.get("/incidents")
def list_incidents():
    """Optional: lets the phone itself poll back the current triage queue."""
    return db.fetch_all()
