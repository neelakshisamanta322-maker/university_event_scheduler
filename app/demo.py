from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from . import db


def slots_for_days(days: int = 3, hours=range(9, 18)):
    base = datetime(2026, 10, 5, 0, 0)
    return [
        (base + timedelta(days=d, hours=h)).isoformat(timespec="minutes")
        for d in range(days)
        for h in hours
    ]


def day_slots(day: int, hours=range(9, 18)):
    base = datetime(2026, 10, 5, 0, 0) + timedelta(days=day)
    return [(base + timedelta(hours=h)).isoformat(timespec="minutes") for h in hours]


def load_demo() -> dict:
    """Seeds the standard demo dataset: 3 departments, 7 events, 4 venues, 3 staff, 3 equipment."""
    db.init_db()
    db.reset_all()
    all_slots = slots_for_days()

    # 1. Departments (3)
    cse = db.add_department("Computer Science", "CSE")
    ece = db.add_department("Electronics & Comm.", "ECE")
    mba = db.add_department("Business & Management", "MBA")

    # 2. Venues (4)
    auditorium = db.add_venue({
        "name": "Central Auditorium",
        "capacity": 300,
        "venue_type": "auditorium",
        "location": "LPU Block 32 (Main Stage)",
        "available_slots": all_slots,
    })
    seminar = db.add_venue({
        "name": "Seminar Hall A",
        "capacity": 90,
        "venue_type": "seminar",
        "location": "LPU Block 34 (3rd Floor)",
        "available_slots": all_slots,
    })
    lab = db.add_venue({
        "name": "AI & Robotics Lab",
        "capacity": 45,
        "venue_type": "lab",
        "location": "Innovation Studio 104",
        "available_slots": day_slots(0) + day_slots(1),
    })
    classroom = db.add_venue({
        "name": "Classroom B201",
        "capacity": 70,
        "venue_type": "general",
        "location": "Uni-Mall Complex B",
        "available_slots": all_slots,
    })

    # 3. Equipment Types (3)
    projector = db.add_equipment({"name": "Portable 4K Projector", "quantity": 2, "category": "AV"})
    vr = db.add_equipment({"name": "HTC Vive VR Kit", "quantity": 1, "category": "Lab Hardware"})
    pa = db.add_equipment({"name": "Portable PA Sound System", "quantity": 1, "category": "Audio"})

    # 4. Staff Members (3)
    alice = db.add_staff({
        "name": "Dr. Alice (AI Faculty)",
        "department_id": cse,
        "role": "Associate Professor",
        "skills": ["Machine Learning", "Research Keynote", "Ethics"],
        "available_slots": all_slots,
    })
    bob = db.add_staff({
        "name": "Mr. Bob (AV Technician)",
        "department_id": cse,
        "role": "Lead AV Engineer",
        "skills": ["Acoustics", "Projection Systems", "Live Streaming"],
        "available_slots": day_slots(0) + day_slots(2),
    })
    carol = db.add_staff({
        "name": "Prof. Carol (Coordinator)",
        "department_id": ece,
        "role": "Academic Dean / Event Lead",
        "skills": ["Symposium Direction", "Lab Protocols", "Keynotes"],
        "available_slots": all_slots,
    })

    # 5. Events (7) with intentional conflict scenarios
    # Event 1: Critical priority academic symposium
    db.add_event({
        "name": "AI/ML Research Symposium",
        "department_id": cse,
        "duration_slots": 2,
        "expected_attendance": 180,
        "required_venue_type": "auditorium",
        "required_staff_ids": [alice, bob],
        "required_equipment": {projector: 1, pa: 1},
        "allowed_slots": day_slots(0),
        "preferred_venue_id": auditorium,
        "preferred_start_hour": 10,
        "priority": "critical",
        "buffer_slots": 1,
    })

    # Event 2: High priority technical workshop
    db.add_event({
        "name": "Embedded Systems Workshop",
        "department_id": ece,
        "duration_slots": 2,
        "expected_attendance": 40,
        "required_venue_type": "lab",
        "required_staff_ids": [carol],
        "required_equipment": {projector: 1},
        "allowed_slots": day_slots(0) + day_slots(1),
        "preferred_venue_id": lab,
        "preferred_start_hour": 11,
        "priority": "high",
        "buffer_slots": 0,
    })

    # Event 3: High priority pitch event contending with Event 1 for Bob and PA sound system on Day 0
    db.add_event({
        "name": "Startup Pitch Day",
        "department_id": mba,
        "duration_slots": 2,
        "expected_attendance": 80,
        "required_venue_type": "seminar",
        "required_staff_ids": [bob],
        "required_equipment": {projector: 1, pa: 1},
        "allowed_slots": day_slots(0),
        "preferred_venue_id": seminar,
        "preferred_start_hour": 10,
        "priority": "high",
        "buffer_slots": 0,
    })

    # Event 4: Normal priority tutorial
    db.add_event({
        "name": "Responsible AI Tutorial",
        "department_id": cse,
        "duration_slots": 1,
        "expected_attendance": 65,
        "required_venue_type": None,
        "required_staff_ids": [alice],
        "required_equipment": {projector: 1},
        "allowed_slots": day_slots(0) + day_slots(1),
        "preferred_venue_id": classroom,
        "preferred_start_hour": 14,
        "priority": "normal",
        "buffer_slots": 0,
    })

    # Event 5: Boundary case: exact venue capacity (90 on 90)
    db.add_event({
        "name": "ECE Student Seminar",
        "department_id": ece,
        "duration_slots": 1,
        "expected_attendance": 90,
        "required_venue_type": "seminar",
        "required_staff_ids": [],
        "required_equipment": {},
        "allowed_slots": day_slots(1),
        "preferred_venue_id": seminar,
        "preferred_start_hour": 12,
        "priority": "normal",
        "buffer_slots": 0,
    })

    # Event 6: Deliberately impossible capacity conflict: 500 attendees, max campus hall is 300
    db.add_event({
        "name": "500-Seat Mega Orientation",
        "department_id": mba,
        "duration_slots": 1,
        "expected_attendance": 500,
        "required_venue_type": "auditorium",
        "required_staff_ids": [],
        "required_equipment": {},
        "allowed_slots": day_slots(2),
        "preferred_venue_id": auditorium,
        "preferred_start_hour": 10,
        "priority": "low",
        "buffer_slots": 0,
    })

    # Event 7: Fixed start time contending for single VR Kit unit
    fixed_slot = day_slots(2)[2]  # Day 2, 11:00
    db.add_event({
        "name": "VR Demonstration",
        "department_id": cse,
        "duration_slots": 1,
        "expected_attendance": 35,
        "required_venue_type": "general",
        "required_staff_ids": [carol],
        "required_equipment": {vr: 1},
        "allowed_slots": day_slots(2),
        "preferred_venue_id": classroom,
        "preferred_start_hour": 11,
        "priority": "normal",
        "fixed_start_slot": fixed_slot,
        "buffer_slots": 0,
    })

    state = db.get_state()

    # Save to data/demo_data.json
    try:
        data_dir = Path(__file__).resolve().parent.parent / "data"
        data_dir.mkdir(exist_ok=True)
        with open(data_dir / "demo_data.json", "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass

    return state
