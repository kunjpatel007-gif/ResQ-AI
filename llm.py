import json
import re
import time
import os
import db
from dataclasses import dataclass, field, asdict
from typing import Optional

import openai
import fewshot

client = openai.OpenAI(
    api_key=os.getenv("HF_API_KEY"),
    base_url="https://router.huggingface.co/v1"
)

MODEL_NAME = "google/gemma-2-9b-it"

URGENCY_RANK = {"high": 0, "medium": 1, "low": 2}
REQUIRED_KEYS = [
    "emergency_type", "urgency", "people_affected", 
    "action_required", "recommended_resources", "location"
]
VALID_TYPES = [
    "FIRE", "FLOOD", "MEDICAL", "HAZMAT", "STRUCTURAL_COLLAPSE",
    "ANIMAL_RESCUE", "WATER_RESCUE", "INFRASTRUCTURE", "PROPERTY_DAMAGE", "STATUS_UPDATE", "OTHER"
]

@dataclass
class EmergencyRecord:
    raw_message: str
    emergency_type: str = "Unknown"
    is_emergency: bool = True
    urgency: str = "Unknown"
    people_affected: str = "Unknown"
    action_required: str = "Unknown"
    recommended_resources: str = "Unknown"
    location: str = "Unknown"
    confidence: float = 95.0
    source: str = "manual"
    sender: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    parse_error: Optional[str] = None
    latency_sec: Optional[float] = None

    def urgency_rank(self) -> int:
        return URGENCY_RANK.get(str(self.urgency).strip().lower(), 99)

    def to_dict(self):
        return asdict(self)

def _extract_json_block(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text.strip(), flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text.strip()).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text

def analyze_emergency_message(
    message_text: str,
    source: str = "manual",
    sender: Optional[str] = None,
    model_name: str = MODEL_NAME,
    timeout_retries: int = 1,
    use_few_shot: bool = True,
) -> EmergencyRecord:
    quota = db.get_quota_status()
    if quota["exhausted"]:
        return EmergencyRecord(
            raw_message=message_text, source=source, sender=sender,
            emergency_type="QUOTA_EXCEEDED", urgency="Unknown",
            action_required=f"Daily free-tier limit ({quota['limit']}) reached. Wait for reset or upgrade billing.",
            confidence=0.0, parse_error="Local quota guard tripped before calling API",
        )
    
    prompt = fewshot.build_prompt(message_text) if use_few_shot else f"Analyze this message:\n{message_text}"
    
    # Instruct the model to return JSON and handle spelling
    valid_types_str = "[" + ", ".join(VALID_TYPES) + "]"
    full_prompt = (
        "You are a disaster response AI. You must ALWAYS reply with a valid JSON object. "
        "Ignore spelling mistakes in the input message. Correct them silently. "
        "First, decide: is this message describing an active emergency that requires a "
        "response, or is it non-urgent/informational? Set 'is_emergency' to true or false accordingly. "
        f"You MUST set 'emergency_type' to the most appropriate category from this exact list: {valid_types_str}. "
        "IMPORTANT: For ANY animal-related incidents (e.g. cat stuck, dog missing), you MUST use 'ANIMAL_RESCUE' even if is_emergency is false. "
        "For 'location', extract any mentioned address, sector, or landmark (or 'Unknown').\n\n"
    ) + prompt

    started = time.time()
    last_error = None
    
    for attempt in range(timeout_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "user" , "content": full_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1
            )
            
            db.log_api_call(model_name, success=True)
            content = response.choices[0].message.content.strip()
            content = _extract_json_block(content)
            print("RAW LLM OUTPUT:", content)
            
            # Ensure enums
            parsed = json.loads(content)
            db.log_api_call(model_name, success=True)
            raw_flag = parsed.get("is_emergency", True)
            if isinstance(raw_flag, str):
                is_emergency = raw_flag.strip().lower() in ("true", "yes", "1")
            else:
                is_emergency = bool(raw_flag)

            # Ensure enums
            raw_type = str(parsed.get("emergency_type", "OTHER")).upper().replace(" ", "_")
            if raw_type not in VALID_TYPES:
                raw_type = "OTHER"
            parsed["emergency_type"] = raw_type

            record = EmergencyRecord(
                raw_message=message_text, source=source, 
                sender=sender, latency_sec=round(time.time() - started, 2),
                is_emergency=is_emergency,
            )
            
            missing_count = 0
            for key in REQUIRED_KEYS:
                value = parsed.get(key)
                if value is not None and str(value).strip() != "" and str(value).strip().lower() != "unknown":
                    setattr(record, key, str(value))
                else:
                    if key != 'location': # location can legitimately be unknown
                        missing_count += 1
                    if value is not None and str(value).strip().lower() == "unknown":
                        setattr(record, key, str(value))
                    else:
                        record.parse_error = (record.parse_error or "") + f"missing:{key} "
                        
            # Dynamic confidence
            record.confidence = max(0.0, 95.0 - (missing_count * 15.0))
            return record

        except json.JSONDecodeError as e:
            db.log_api_call(model_name, success=False)
            last_error = f"JSON decode error: {e}"
        except Exception as e:
            db.log_api_call(model_name, success=False)
            last_error = f"API error: {e}"

    return EmergencyRecord(
        raw_message=message_text, source=source, sender=sender,
        emergency_type="PARSE_ERROR", urgency="Unknown", people_affected="Unknown",
        action_required="Review manually — model output could not be parsed",
        recommended_resources="Unknown", location="Unknown", confidence=0.0, parse_error=last_error,
        latency_sec=round(time.time() - started, 2),
    )
