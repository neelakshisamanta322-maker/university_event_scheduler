from __future__ import annotations

import csv
import random
from pathlib import Path

DEPARTMENTS = ["Computer Science", "Electronics & Comm.", "Business & Management", "Biotechnology", "Mechanical Engineering", "Civil Engineering"]
EVENT_TYPES = ["symposium", "workshop", "seminar", "lecture", "orientation", "demonstration", "exam"]
PRIORITIES = ["critical", "high", "normal", "low"]
VENUE_TYPES = ["auditorium", "seminar", "lab", "general"]


def generate_synthetic_dataset(num_samples: int = 1200, output_path: Path | None = None) -> Path:
    """
    Generates a realistic synthetic historical dataset representing academic events at LPU.
    NOTE: As stated in the system documentation, this dataset is synthetically generated
    to train predictive models for attendance forecasting, conflict-risk scoring, and
    convenience profiling without exposing private student records.
    """
    if output_path is None:
        data_dir = Path(__file__).resolve().parent.parent.parent / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        output_path = data_dir / "synthetic_training.csv"
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    random.seed(42)
    rows = []

    for i in range(num_samples):
        dept = random.choice(DEPARTMENTS)
        ev_type = random.choice(EVENT_TYPES)
        priority = random.choice(PRIORITIES)
        venue_type = random.choice(VENUE_TYPES)
        day_of_week = random.randint(0, 4)  # Mon-Fri
        start_hour = random.randint(9, 17)
        duration = random.choices([1, 2, 3, 4], weights=[0.45, 0.35, 0.15, 0.05])[0]

        # Base expected attendance by event type
        base_attend = {
            "symposium": random.randint(120, 280),
            "workshop": random.randint(30, 60),
            "seminar": random.randint(50, 110),
            "lecture": random.randint(40, 90),
            "orientation": random.randint(180, 450),
            "demonstration": random.randint(25, 55),
            "exam": random.randint(80, 200),
        }[ev_type]

        staff_count = random.choices([0, 1, 2, 3], weights=[0.2, 0.5, 0.25, 0.05])[0]
        eq_count = random.choices([0, 1, 2, 3], weights=[0.3, 0.45, 0.2, 0.05])[0]

        # Attendance variance influenced by priority and day of week
        prio_boost = {"critical": 1.15, "high": 1.05, "normal": 0.95, "low": 0.85}[priority]
        time_factor = 1.1 if 10 <= start_hour <= 14 else 0.88
        actual_attendance = max(10, int(base_attend * prio_boost * time_factor * random.uniform(0.9, 1.12)))

        # Conflict risk heuristics
        risk_score = 0
        if actual_attendance > 150:
            risk_score += 2
        if staff_count >= 2:
            risk_score += 2
        if eq_count >= 2:
            risk_score += 2
        if 10 <= start_hour <= 12:
            risk_score += 1
        if duration >= 2:
            risk_score += 1

        if risk_score >= 5:
            risk_label = "High"
        elif risk_score >= 3:
            risk_label = "Medium"
        else:
            risk_label = "Low"

        # Convenience score (1 to 10): early afternoon on Tue/Wed is highest, 17:00 is lowest
        convenience = 8.0
        if start_hour >= 16:
            convenience -= 3.0
        elif 10 <= start_hour <= 13:
            convenience += 1.5
        if day_of_week in (1, 2):  # Tue, Wed
            convenience += 1.0
        elif day_of_week == 4:  # Fri late
            convenience -= 1.5
        convenience = round(max(1.0, min(10.0, convenience + random.gauss(0, 0.5))), 2)

        rows.append({
            "sample_id": i + 1,
            "department": dept,
            "event_type": ev_type,
            "priority": priority,
            "venue_type": venue_type,
            "day_of_week": day_of_week,
            "start_hour": start_hour,
            "duration_slots": duration,
            "expected_attendance": base_attend,
            "staff_count": staff_count,
            "equipment_count": eq_count,
            "actual_attendance": actual_attendance,
            "convenience_score": convenience,
            "risk_label": risk_label,
        })

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    return output_path


if __name__ == "__main__":
    p = generate_synthetic_dataset()
    print(f"Synthetic dataset created at: {p}")
