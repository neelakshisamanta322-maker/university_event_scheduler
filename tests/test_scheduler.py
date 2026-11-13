from __future__ import annotations

import pytest
from app.scheduler import solve_schedule


def base_test_state():
    slots = ["2026-10-05T09:00", "2026-10-05T10:00", "2026-10-05T11:00", "2026-10-05T12:00"]
    return {
        "departments": [{"id": 1, "name": "Computer Science"}],
        "venues": [
            {"id": 1, "name": "Seminar Hall A", "capacity": 100, "venue_type": "seminar", "available_slots": slots},
        ],
        "equipment": [{"id": 1, "name": "Projector", "quantity": 1}],
        "staff": [{"id": 1, "name": "Dr. Alice", "available_slots": slots}],
        "events": [],
    }


def make_event(
    eid: int,
    name: str,
    allowed: list,
    attendance: int = 50,
    venue_type: str = None,
    staff: list = None,
    eq: dict = None,
    priority: str = "normal",
    fixed_slot: str = None,
    buffer_slots: int = 0,
):
    return {
        "id": eid,
        "name": name,
        "department_id": 1,
        "duration_slots": 1,
        "expected_attendance": attendance,
        "required_venue_type": venue_type,
        "required_staff_ids": staff or [],
        "required_equipment": eq or {},
        "allowed_slots": allowed,
        "preferred_venue_id": 1,
        "preferred_start_hour": 9,
        "priority": priority,
        "fixed_start_slot": fixed_slot,
        "buffer_slots": buffer_slots,
    }


# ---------------------------------------------------------------------------
# Hard Constraint Tests
# ---------------------------------------------------------------------------

def test_hard_constraint_capacity_prevent_oversized():
    """Events exceeding venue capacity must not be scheduled in that venue."""
    s = base_test_state()
    slots = s["venues"][0]["available_slots"]
    s["events"] = [make_event(1, "Large Lecture", slots, attendance=150)]
    r = solve_schedule(s)
    assert len(r["assignments"]) == 0
    assert len(r["unresolved"]) == 1
    assert any("attendance" in x.lower() or "capacity" in x.lower() for x in r["unresolved"][0]["reasons"])


def test_hard_constraint_venue_type_mismatch():
    """Events requiring specific venue types must not be placed in mismatched venues."""
    s = base_test_state()
    slots = s["venues"][0]["available_slots"]
    s["events"] = [make_event(1, "Chemistry Lab", slots, venue_type="lab")]
    r = solve_schedule(s)
    assert len(r["assignments"]) == 0
    assert len(r["unresolved"]) == 1
    assert any("type" in x.lower() for x in r["unresolved"][0]["reasons"])


def test_hard_constraint_venue_no_double_booking():
    """Two events competing for the same venue at the same time must not overlap."""
    s = base_test_state()
    same_slot = ["2026-10-05T09:00"]
    s["events"] = [make_event(1, "Event A", same_slot), make_event(2, "Event B", same_slot)]
    r = solve_schedule(s)
    # Only one can be placed in that slot
    assert len(r["assignments"]) == 1
    assert len(r["unresolved"]) == 1


def test_hard_constraint_staff_availability_and_non_overlap():
    """Staff cannot be double-booked across different venues at the same time."""
    s = base_test_state()
    s["venues"].append({"id": 2, "name": "Room 2", "capacity": 100, "venue_type": "seminar", "available_slots": ["2026-10-05T09:00"]})
    same_slot = ["2026-10-05T09:00"]
    s["events"] = [
        make_event(1, "Event A", same_slot, staff=[1]),
        make_event(2, "Event B", same_slot, staff=[1]),
    ]
    r = solve_schedule(s)
    assert len(r["assignments"]) == 1
    assert len(r["unresolved"]) == 1


def test_hard_constraint_equipment_global_pooling_limit():
    """Equipment quantities across all venues must never exceed global stock."""
    s = base_test_state()
    s["venues"].append({"id": 2, "name": "Room 2", "capacity": 100, "venue_type": "seminar", "available_slots": ["2026-10-05T09:00"]})
    same_slot = ["2026-10-05T09:00"]
    # Total projector quantity in state is 1. Both events require 1.
    s["events"] = [
        make_event(1, "Event A", same_slot, eq={1: 1}),
        make_event(2, "Event B", same_slot, eq={1: 1}),
    ]
    r = solve_schedule(s)
    assert len(r["assignments"]) == 1
    assert len(r["unresolved"]) == 1


def test_hard_constraint_minimum_buffer_spacing():
    """Consecutive events in the same venue must respect buffer gaps."""
    s = base_test_state()
    # Consecutive slots: 09:00 and 10:00
    slots = ["2026-10-05T09:00", "2026-10-05T10:00"]
    # Event 1 is 1 hour, with a 1-slot buffer
    s["events"] = [
        make_event(1, "Event A", slots, buffer_slots=1),
        make_event(2, "Event B", slots, buffer_slots=0),
    ]
    r = solve_schedule(s, buffer_slots=1)
    # Because there are only 2 slots and a 1-hour buffer is needed between events in the same venue, only 1 fits!
    assert len(r["assignments"]) == 1
    assert len(r["unresolved"]) == 1


def test_hard_constraint_fixed_start_slot():
    """Fixed-time events must be scheduled exactly at their fixed start slot."""
    s = base_test_state()
    slots = s["venues"][0]["available_slots"]
    fixed = "2026-10-05T11:00"
    s["events"] = [make_event(1, "VR Demo", slots, fixed_slot=fixed)]
    r = solve_schedule(s)
    assert len(r["assignments"]) == 1
    assert r["assignments"][0]["slots"] == [fixed]


# ---------------------------------------------------------------------------
# Priority & Conflict Resolution Tests
# ---------------------------------------------------------------------------

def test_priority_resolution_critical_preempts_normal():
    """Critical priority event must win the resource over normal priority event."""
    s = base_test_state()
    same_slot = ["2026-10-05T09:00"]
    s["events"] = [
        make_event(1, "Regular Lecture", same_slot, priority="normal"),
        make_event(2, "Chancellor Address", same_slot, priority="critical"),
    ]
    r = solve_schedule(s)
    assert len(r["assignments"]) == 1
    assert r["assignments"][0]["event_id"] == 2
    assert len(r["unresolved"]) == 1
    assert r["unresolved"][0]["event_id"] == 1


def test_unsatisfiable_case_diagnostic_explanation():
    """Unsatisfiable event must yield concrete diagnostic explanation."""
    s = base_test_state()
    s["events"] = [make_event(1, "Stadium Concert", ["2026-10-05T09:00"], attendance=2000)]
    r = solve_schedule(s)
    assert len(r["unresolved"]) == 1
    reasons = r["unresolved"][0]["reasons"]
    assert any("overflow" in x.lower() or "capacity" in x.lower() for x in reasons)


def test_deterministic_reproducibility():
    """Repeated solver runs on identical state must produce 100% identical outputs."""
    from app.demo import load_demo
    state = load_demo()
    r1 = solve_schedule(state)
    r2 = solve_schedule(state)
    assert len(r1["assignments"]) == len(r2["assignments"])
    for a1, a2 in zip(r1["assignments"], r2["assignments"]):
        assert a1["event_id"] == a2["event_id"]
        assert a1["venue_id"] == a2["venue_id"]
        assert a1["slots"] == a2["slots"]
        assert a1["soft_cost"] == a2["soft_cost"]
