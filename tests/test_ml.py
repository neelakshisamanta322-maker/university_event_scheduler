from __future__ import annotations

from datetime import datetime
from app.ml.dataset import generate_synthetic_dataset
from app.ml.models import get_slot_penalty, predict_attendance, predict_conflict_risk


def test_synthetic_dataset_creation(tmp_path):
    out_file = tmp_path / "test_dataset.csv"
    res = generate_synthetic_dataset(num_samples=50, output_path=out_file)
    assert res.exists()
    assert res.stat().st_size > 0


def test_ml_attendance_prediction():
    sample_event = {
        "name": "Annual AI/ML Symposium",
        "expected_attendance": 200,
        "priority": "critical",
        "duration_slots": 2,
        "preferred_start_hour": 10,
    }
    pred = predict_attendance(sample_event, dept_name="Computer Science")
    assert isinstance(pred, int)
    assert pred > 0


def test_ml_conflict_risk_classifier():
    # Low risk event: small attendance, no staff or equipment contention
    low_event = {
        "priority": "normal",
        "expected_attendance": 25,
        "duration_slots": 1,
        "required_staff_ids": [],
        "required_equipment": {},
        "preferred_start_hour": 14,
    }
    risk_low = predict_conflict_risk(low_event)
    assert risk_low in ["Low", "Medium", "High"]

    # High risk event: large attendance, multiple staff, multiple equipment
    high_event = {
        "priority": "critical",
        "expected_attendance": 350,
        "duration_slots": 3,
        "required_staff_ids": [1, 2, 3],
        "required_equipment": {1: 2, 2: 1},
        "preferred_start_hour": 10,
    }
    risk_high = predict_conflict_risk(high_event)
    assert risk_high in ["Low", "Medium", "High"]


def test_ml_slot_preference_penalty():
    sample_event = {"duration_slots": 2}
    sample_venue = {"id": 1}
    # Midday slot (11:00 AM on Tuesday)
    dt_midday = datetime(2026, 10, 6, 11, 0)
    penalty_midday = get_slot_penalty(sample_event, sample_venue, dt_midday)
    assert penalty_midday >= 0

    # Late slot (17:00 on Friday)
    dt_late = datetime(2026, 10, 9, 17, 0)
    penalty_late = get_slot_penalty(sample_event, sample_venue, dt_late)
    assert penalty_late >= 0
