from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
_MODELS: Dict[str, Any] = {}
_SLOT_PENALTY_CACHE: Dict[tuple[int, int, int], int] = {}


def get_models() -> Dict[str, Any]:
    global _MODELS
    att_path = ARTIFACTS_DIR / "attendance_model.joblib"
    risk_path = ARTIFACTS_DIR / "conflict_risk_model.joblib"
    pref_path = ARTIFACTS_DIR / "slot_preference_model.joblib"

    if not att_path.exists() or not risk_path.exists() or not pref_path.exists():
        from .train import train_models
        train_models()

    if not _MODELS:
        import joblib
        _MODELS["attendance"] = joblib.load(att_path)
        _MODELS["risk"] = joblib.load(risk_path)
        _MODELS["preference"] = joblib.load(pref_path)

    return _MODELS


def predict_attendance(event: Dict[str, Any], dept_name: str = "Computer Science") -> int:
    """Predicts realistic expected turnout to flag venue overflow or under-allocation."""
    models = get_models()
    expected = event.get("expected_attendance", 50)
    prio = event.get("priority", "normal")
    dur = event.get("duration_slots", 1)
    hour = event.get("preferred_start_hour", 10) or 10

    # Guess event type from name
    name_lower = event.get("name", "").lower()
    ev_type = "lecture"
    for candidate_type in ["symposium", "workshop", "seminar", "orientation", "demonstration", "exam"]:
        if candidate_type in name_lower:
            ev_type = candidate_type
            break

    import pandas as pd
    df = pd.DataFrame([{
        "department": dept_name,
        "event_type": ev_type,
        "priority": prio,
        "expected_attendance": expected,
        "day_of_week": 1,
        "start_hour": hour,
        "duration_slots": dur,
    }])

    try:
        pred = models["attendance"].predict(df)[0]
        return max(10, int(round(pred)))
    except Exception:
        return expected


def predict_conflict_risk(event: Dict[str, Any]) -> str:
    """Classifies event conflict risk into 'Low', 'Medium', or 'High' before scheduling."""
    models = get_models()
    prio = event.get("priority", "normal")
    expected = event.get("expected_attendance", 50)
    dur = event.get("duration_slots", 1)
    staff_count = len(event.get("required_staff_ids", []))
    eq_count = sum(event.get("required_equipment", {}).values())
    hour = event.get("preferred_start_hour", 10) or 10

    import pandas as pd
    df = pd.DataFrame([{
        "priority": prio,
        "expected_attendance": expected,
        "duration_slots": dur,
        "staff_count": staff_count,
        "equipment_count": eq_count,
        "start_hour": hour,
    }])

    try:
        label = models["risk"].predict(df)[0]
        return str(label)
    except Exception:
        if staff_count >= 2 or eq_count >= 2 or expected > 150:
            return "High"
        elif staff_count == 1 or eq_count == 1:
            return "Medium"
        return "Low"


def get_slot_penalty(event: Dict[str, Any], venue: Dict[str, Any], start_dt: datetime) -> int:
    """
    Computes a data-driven soft penalty based on predicted student and faculty convenience.
    Lower convenience converts to a higher penalty cost (0 to 3).
    Results are cached in memory for optimal performance inside scheduling loops.
    """
    day_of_week = start_dt.weekday()
    start_hour = start_dt.hour
    dur = event.get("duration_slots", 1)
    cache_key = (day_of_week, start_hour, dur)

    if cache_key in _SLOT_PENALTY_CACHE:
        return _SLOT_PENALTY_CACHE[cache_key]

    models = get_models()

    import pandas as pd
    df = pd.DataFrame([{
        "day_of_week": day_of_week,
        "start_hour": start_hour,
        "duration_slots": dur,
    }])

    try:
        convenience = models["preference"].predict(df)[0]
        # Convenience is between 1 and 10. Higher convenience -> lower penalty.
        if convenience >= 8.5:
            penalty = 0
        elif convenience >= 6.5:
            penalty = 1
        elif convenience >= 4.5:
            penalty = 2
        else:
            penalty = 3
    except Exception:
        penalty = 0

    _SLOT_PENALTY_CACHE[cache_key] = penalty
    return penalty
