"""
fewshot.py
----------
No GPU time for real fine-tuning today, so accuracy improvements come
from two things instead:

1. A curated bank of BASE_EXAMPLES covering the edge cases that trip up
   zero-shot prompting (ambiguous urgency, non-emergencies, multiple
   people, infrastructure hazards vs. direct danger).
2. A growing bank of operator CORRECTIONS (saved from the dashboard's
   "Fix this" form). Every time you correct a wrong triage, it's stored
   in SQLite and, from then on, the most relevant corrections are
   prepended to future prompts as extra few-shot examples.

This is in-context learning / retrieval-augmented prompting, not
weight updates — but on a CPU-only laptop with hours (not days) to
work with, it's the fastest way to visibly improve accuracy, and it
gives you a genuine "the system learns from operator feedback, fully
offline" story for judges.
"""

import re

import db

BASE_EXAMPLES = [
    {
        "message": "Whole family of 5 stuck on the roof, water is up to the second floor and still rising, no boat has come yet.",
        "is_emergency": True,
        "emergency_type": "FLOOD",
        "urgency": "High",
        "people_affected": "5",
        "action_required": "Dispatch boat/rescue team immediately",
        "recommended_resources": "Water rescue team, boat",
        "location": "Unknown"
    },
    {
        "message": "Strong gas smell near the school gym on 5th Ave, not sure if anyone is inside right now.",
        "is_emergency": True,
        "emergency_type": "HAZMAT",
        "urgency": "High",
        "people_affected": "Unknown, possible occupants",
        "action_required": "Evacuate area and dispatch hazmat/fire immediately",
        "recommended_resources": "Fire department, hazmat team",
        "location": "5th Ave"
    },
    {
        "message": "My elderly neighbor has a fever and is out of her blood pressure medication, roads are blocked so she can't get to a pharmacy.",
        "is_emergency": True,
        "emergency_type": "MEDICAL",
        "urgency": "Medium",
        "people_affected": "1",
        "action_required": "Arrange medication delivery or medical check within the day",
        "recommended_resources": "Mobile medical unit, pharmacy supply run",
        "location": "Unknown"
    },
    {
        "message": "Tree fell on our fence during the storm at Sector B, no one hurt, just wanted to report the damage.",
        "is_emergency": False,
        "emergency_type": "PROPERTY DAMAGE",
        "urgency": "Low",
        "people_affected": "0",
        "action_required": "Log for later cleanup, no immediate response needed",
        "recommended_resources": "None urgent",
        "location": "Sector B"
    },
    {
        "message": "My cat is stuck on the large oak tree in my backyard and won't come down.",
        "is_emergency": False,
        "emergency_type": "ANIMAL_RESCUE",
        "urgency": "Low",
        "people_affected": "0",
        "action_required": "Contact animal control or tree service",
        "recommended_resources": "Animal services",
        "location": "backyard"
    },
    {
        "message": "We made it to the shelter safely, just letting family know we are okay.",
        "is_emergency": False,
        "emergency_type": "STATUS UPDATE",
        "urgency": "Low",
        "people_affected": "0",
        "action_required": "No action required, informational only",
        "recommended_resources": "None",
        "location": "Unknown"
    },
    {
        "message": "Power line sparking over the flooded intersection on Main St, cars still trying to drive through.",
        "is_emergency": True,
        "emergency_type": "INFRASTRUCTURE",
        "urgency": "High",
        "people_affected": "Unknown, public at risk",
        "action_required": "Close road immediately and dispatch utility/emergency crew",
        "recommended_resources": "Utility crew, police to block road",
        "location": "Main St"
    },
]


def _tokenize(text: str):
    return set(re.findall(r"[a-zA-Z]{3,}", text.lower()))


def _most_relevant_corrections(message: str, k: int = 3):
    """Cheap keyword-overlap retrieval — no embeddings needed. Kept simple
    even on the faster Ryzen AI 350 since embedding lookups would need an
    extra model call for no real accuracy benefit at this dataset size."""
    corrections = db.fetch_corrections(limit=200)
    if not corrections:
        return []

    target_tokens = _tokenize(message)
    scored = []
    for c in corrections:
        overlap = len(target_tokens & _tokenize(c["raw_message"]))
        if overlap > 0:
            scored.append((overlap, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:k]]


def _format_example(ex: dict) -> str:
    return (
        f"Message: {ex['raw_message'] if 'raw_message' in ex else ex['message']}\n"
        "Output: {"
        f'"is_emergency": {str(ex.get("is_emergency", True)).lower()}, '
        f'"emergency_type": "{ex["emergency_type"]}", '
        f'"urgency": "{ex["urgency"]}", '
        f'"people_affected": "{ex["people_affected"]}", '
        f'"action_required": "{ex["action_required"]}", '
        f'"recommended_resources": "{ex["recommended_resources"]}", '
        f'"location": "{ex["location"]}"'
        "}"
    )


def build_prompt(message_text: str, use_corrections: bool = True) -> str:
    """
    Builds the full user-turn prompt: a handful of relevant examples
    followed by the actual message to analyze. The Modelfile's SYSTEM
    prompt still enforces the JSON schema — this just anchors the model
    on concrete input/output pairs, which measurably reduces schema
    drift and misjudged urgency on a base (non-fine-tuned) model.
    """
    examples = list(BASE_EXAMPLES[:4])  # a bit more headroom than the i3 build allowed
    if use_corrections:
        examples += _most_relevant_corrections(message_text, k=3)

    example_block = "\n\n".join(_format_example(ex) for ex in examples)

    return (
        "Here are some correctly triaged examples:\n\n"
        f"{example_block}\n\n"
        "Now analyze this new message the same way:\n"
        f"Message: {message_text}\n"
        "Output:"
    )
