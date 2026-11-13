"""
What-If Scenario Simulator — Dedicated Regression & Acceptance Tests
Tests A–L covering the approved test plan.

All tests use TestClient (in-process) — no live server required.
The existing tests in tests/test_scheduler.py, tests/test_api.py, tests/test_ml.py
remain as regression tests and are NOT replaced here.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import db
from app.scheduler import validate_schedule, PRIORITY_RANK

client = TestClient(app)
ADMIN = {"X-User-Role": "Admin"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def reset_and_generate() -> Dict[str, Any]:
    """Reset demo data and generate a fresh baseline schedule."""
    r = client.post("/api/demo/reset", headers=ADMIN)
    assert r.status_code == 200, f"reset failed: {r.text}"
    r2 = client.post("/api/schedule/generate", json={}, headers=ADMIN)
    assert r2.status_code == 200, f"generate failed: {r2.text}"
    return r2.json()


def get_schedule() -> Dict[str, Any]:
    r = client.get("/api/schedule", headers=ADMIN)
    assert r.status_code == 200
    return r.json()


def run_whatif(payload: Dict[str, Any]) -> Dict[str, Any]:
    r = client.post("/api/whatif/simulate", json=payload, headers=ADMIN)
    assert r.status_code == 200, f"whatif failed ({r.status_code}): {r.text}"
    return r.json()


def find_assignment_in_schedule(schedule: Dict[str, Any], event_id: int) -> Optional[Dict[str, Any]]:
    """Return the assignment dict for a given event_id in a schedule, or None."""
    for a in schedule.get("assignments", []):
        if a["event_id"] == event_id:
            return a
    return None


def find_assignment_in_result(result: Dict[str, Any], event_id: int) -> Optional[Dict[str, Any]]:
    """Return the assignment dict from a whatif result's modified_schedule."""
    return find_assignment_in_schedule(result["modified_schedule"], event_id)


def slots_overlap(slots_a: List[str], slots_b: List[str]) -> bool:
    """Return True if the half-open intervals [start_a, end_a) and [start_b, end_b) overlap."""
    if not slots_a or not slots_b:
        return False
    start_a = datetime.fromisoformat(slots_a[0])
    end_a = datetime.fromisoformat(slots_a[-1]) + timedelta(hours=1)
    start_b = datetime.fromisoformat(slots_b[0])
    end_b = datetime.fromisoformat(slots_b[-1]) + timedelta(hours=1)
    return not (end_a <= start_b or end_b <= start_a)


def first_scheduled_event(schedule: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the first assignment in a schedule (any scheduled event)."""
    assignments = schedule.get("assignments", [])
    return assignments[0] if assignments else None


def find_event_with_min_slots(schedule: Dict[str, Any], min_slots: int = 2) -> Optional[Dict[str, Any]]:
    """Return the first scheduled event that spans at least min_slots hours."""
    for a in schedule.get("assignments", []):
        if len(a.get("slots", [])) >= min_slots:
            return a
    return None


def find_good_target_hour(asgn: Dict[str, Any], target_delta: int = 4) -> int:
    """Pick a target hour that is 4h different from the current start (stays in 9-16 range)."""
    current_h = datetime.fromisoformat(asgn["slots"][0]).hour
    for delta in [target_delta, -target_delta, 3, -3, 2, -2, 5, -5]:
        candidate = current_h + delta
        if 9 <= candidate <= 16:  # ensure event can start at this hour safely
            return candidate
    return 9  # fallback


# ---------------------------------------------------------------------------
# Test A — Time override: move target event to a different start time
# ---------------------------------------------------------------------------

class TestA_TimeOverride:
    """Shift a scheduled event to a new hour; verify TIME SHIFT, TIME+VENUE SHIFT, or UNRESOLVED."""

    def test_time_override_different_hour(self):
        baseline = reset_and_generate()
        asgn = find_event_with_min_slots(baseline, min_slots=1)
        assert asgn, "No scheduled event in baseline"
        event_id = asgn["event_id"]
        duration = len(asgn["slots"])
        current_hour = datetime.fromisoformat(asgn["slots"][0]).hour
        # Pick a target hour clearly different from current
        target_hour = find_good_target_hour(asgn, target_delta=4)
        # Ensure we're not requesting the same hour (would be UNCHANGED)
        assert target_hour != current_hour, (
            f"Could not find a different valid target hour from {current_hour}"
        )

        result = run_whatif({
            "target_time_shift_event_id": event_id,
            "simulated_start_hour": target_hour,
            "allow_cascade": True,
        })

        diff = next((d for d in result["differences"] if d["event_id"] == event_id), None)
        assert diff is not None, "Target event not in differences"

        # Acceptable outcomes: moved to a new time, or UNRESOLVED if constraints prevent it
        assert diff["change_type"] in (
            "TIME SHIFT", "VENUE_AND_TIME_SHIFT", "TIME + VENUE SHIFT", "UNRESOLVED",
            "UNSOLVED", "CAPACITY CONFLICT",
        ), f"Unexpected change_type: {diff['change_type']}"

        # If it was scheduled, duration must be preserved
        if diff["after_status"] == "Scheduled":
            after_slots = diff.get("after_slots", [])
            assert len(after_slots) == duration, (
                f"Duration changed: expected {duration} slots, got {len(after_slots)}"
            )
            # Must start at target_hour
            start_h = datetime.fromisoformat(after_slots[0]).hour
            assert start_h == target_hour, f"Expected start hour {target_hour}, got {start_h}"

    def test_time_override_response_is_valid(self):
        """Smoke test: the endpoint must return 200 for any time shift request."""
        baseline = reset_and_generate()
        asgn = first_scheduled_event(baseline)
        if asgn is None:
            pytest.skip("No scheduled events in baseline.")
        result = run_whatif({
            "target_time_shift_event_id": asgn["event_id"],
            "simulated_start_hour": 14,
            "allow_cascade": True,
        })
        assert "differences" in result
        assert "modified_schedule" in result
        assert "feasibility_status" in result


# ---------------------------------------------------------------------------
# Test B — Same-time override returns UNCHANGED
# ---------------------------------------------------------------------------

class TestB_SameTimeUnchanged:
    """Requesting the original start time must return UNCHANGED for the target event."""

    def test_same_time_returns_unchanged(self):
        baseline = reset_and_generate()
        asgn = first_scheduled_event(baseline)
        assert asgn, "No scheduled event in baseline"
        event_id = asgn["event_id"]
        original_hour = datetime.fromisoformat(asgn["slots"][0]).hour

        result = run_whatif({
            "target_time_shift_event_id": event_id,
            "simulated_start_hour": original_hour,
            "allow_cascade": True,
        })

        diff = next((d for d in result["differences"] if d["event_id"] == event_id), None)
        assert diff is not None, "Target event not in differences"
        assert diff["change_type"] == "UNCHANGED", (
            f"Expected UNCHANGED for same-time override, got {diff['change_type']}"
        )
        assert result["total_changes"] == 0, (
            f"Expected 0 total_changes, got {result['total_changes']}"
        )


# ---------------------------------------------------------------------------
# Test C — Conflicting time override: attempt 12:00 for a 10:00–12:00 event
# ---------------------------------------------------------------------------

class TestC_ConflictingOverride:
    """
    Shift a 2-slot event at 10:00 to 12:00.
    The simulator must either cascade-resolve or mark unresolved.
    """

    def _find_event_starting_at(self, schedule: Dict[str, Any], hour: int) -> Optional[Dict[str, Any]]:
        for a in schedule.get("assignments", []):
            if datetime.fromisoformat(a["slots"][0]).hour == hour and len(a["slots"]) >= 2:
                return a
        return None

    def test_conflicting_override_has_valid_outcome(self):
        baseline = reset_and_generate()
        asgn = self._find_event_starting_at(baseline, 10)
        if asgn is None:
            pytest.skip("No 2-slot event starting at 10:00 in baseline; skipping Test C.")

        event_id = asgn["event_id"]
        result = run_whatif({
            "target_time_shift_event_id": event_id,
            "simulated_start_hour": 12,
            "allow_cascade": True,
        })

        # The target event must appear in differences
        diff = next((d for d in result["differences"] if d["event_id"] == event_id), None)
        assert diff is not None, "Target event missing from differences"

        # Valid outcomes: moved to 12:00, or unresolved due to hard constraints
        assert diff["change_type"] in (
            "TIME SHIFT", "VENUE_AND_TIME_SHIFT", "TIME + VENUE SHIFT",
            "UNRESOLVED", "UNSOLVED", "CAPACITY CONFLICT", "UNCHANGED",
        ), f"Unexpected change_type: {diff['change_type']}"

        # If it was scheduled at 12:00, verify no venue overlap exists
        if diff["after_status"] == "Scheduled":
            after_slots = diff.get("after_slots", [])
            if after_slots:
                start_h = datetime.fromisoformat(after_slots[0]).hour
                assert start_h == 12, f"Expected 12:00 start, got {start_h}"


# ---------------------------------------------------------------------------
# Test D — Venue-overlap regression (the named bug): no two events may share
#           a venue at overlapping times in ANY simulated result
# ---------------------------------------------------------------------------

class TestD_VenueOverlapRegression:
    """
    After any time shift (especially the 10:00→12:00 conflict case), no two
    events may be assigned to the same venue with overlapping time intervals.
    This is the core regression test for the original bug.
    """

    def _find_event_starting_at(self, schedule: Dict[str, Any], hour: int) -> Optional[Dict[str, Any]]:
        for a in schedule.get("assignments", []):
            if datetime.fromisoformat(a["slots"][0]).hour == hour and len(a["slots"]) >= 2:
                return a
        return None

    def _assert_no_venue_overlap(self, result: Dict[str, Any], context: str = ""):
        sim_assignments = result["modified_schedule"]["assignments"]
        by_venue: Dict[int, List[Dict]] = {}
        for a in sim_assignments:
            by_venue.setdefault(a["venue_id"], []).append(a)

        violations = []
        for vid, vas in by_venue.items():
            for i in range(len(vas)):
                for j in range(i + 1, len(vas)):
                    if slots_overlap(vas[i]["slots"], vas[j]["slots"]):
                        violations.append(
                            f"Venue {vid}: '{vas[i]['event_name']}' "
                            f"({vas[i]['slots']}) overlaps '{vas[j]['event_name']}' "
                            f"({vas[j]['slots']})"
                        )
        assert not violations, (
            f"Venue overlap detected{' (' + context + ')' if context else ''}:\n"
            + "\n".join(violations)
        )

    def test_no_venue_overlap_after_cascade_10_to_12(self):
        """Core bug regression: 10:00→12:00 shift must never produce overlapping assignments."""
        baseline = reset_and_generate()
        asgn = self._find_event_starting_at(baseline, 10)
        if asgn is None:
            pytest.skip("No 2-slot event at 10:00 in baseline; skipping Test D.")

        result = run_whatif({
            "target_time_shift_event_id": asgn["event_id"],
            "simulated_start_hour": 12,
            "allow_cascade": True,
        })
        self._assert_no_venue_overlap(result, context="10:00→12:00 cascade")

    def test_no_venue_overlap_any_shift(self):
        """General: any time shift must produce a venue-overlap-free result."""
        baseline = reset_and_generate()
        asgn = first_scheduled_event(baseline)
        if asgn is None:
            pytest.skip("No scheduled events in baseline.")

        target_hour = find_good_target_hour(asgn, target_delta=3)
        result = run_whatif({
            "target_time_shift_event_id": asgn["event_id"],
            "simulated_start_hour": target_hour,
            "allow_cascade": True,
        })
        self._assert_no_venue_overlap(result, context=f"shift to {target_hour}:00")

    def test_hard_violations_zero_after_eviction(self):
        """hard_constraint_violations in result must be 0 — eviction must clean them up."""
        baseline = reset_and_generate()
        asgn = self._find_event_starting_at(baseline, 10)
        if asgn is None:
            pytest.skip("No 2-slot event at 10:00 in baseline; skipping.")

        result = run_whatif({
            "target_time_shift_event_id": asgn["event_id"],
            "simulated_start_hour": 12,
            "allow_cascade": True,
        })
        assert result.get("hard_constraint_violations", 0) == 0, (
            f"Expected 0 hard violations after eviction, got {result['hard_constraint_violations']}"
        )


# ---------------------------------------------------------------------------
# Test E — Half-open interval validation (unit-level)
# ---------------------------------------------------------------------------

class TestE_HalfOpenIntervalValidation:
    """validate_schedule() must use half-open intervals [start, end)."""

    def _make_state(self, venues, events):
        return {
            "venues": venues,
            "events": events,
            "departments": [],
            "staff": [],
            "equipment": [],
        }

    def _make_asgn(self, event_id, event_name, venue_id, slots):
        return {
            "event_id": event_id,
            "event_name": event_name,
            "venue_id": venue_id,
            "slots": slots,
        }

    def _iso(self, hour: int) -> str:
        return datetime(2026, 10, 5, hour, 0).isoformat(timespec="minutes")

    def test_overlap_at_first_hour(self):
        """Two events sharing hour 10 in same venue → VENUE_OVERLAP."""
        venue = {"id": 1, "name": "Hall A", "capacity": 100}
        ev1 = {"id": 1, "expected_attendance": 50, "name": "E1"}
        ev2 = {"id": 2, "expected_attendance": 50, "name": "E2"}
        state = self._make_state([venue], [ev1, ev2])
        asgns = [
            self._make_asgn(1, "E1", 1, [self._iso(10), self._iso(11)]),  # 10:00–12:00
            self._make_asgn(2, "E2", 1, [self._iso(10)]),                  # 10:00–11:00
        ]
        violations = validate_schedule(state, asgns)
        overlap_violations = [v for v in violations if "VENUE_OVERLAP" in v]
        assert overlap_violations, "Expected VENUE_OVERLAP at hour 10, got none"

    def test_overlap_at_second_hour(self):
        """Event A covers 10–12, Event B starts at 11 → overlap at hour 11."""
        venue = {"id": 1, "name": "Hall A", "capacity": 100}
        ev1 = {"id": 1, "expected_attendance": 50, "name": "E1"}
        ev2 = {"id": 2, "expected_attendance": 50, "name": "E2"}
        state = self._make_state([venue], [ev1, ev2])
        asgns = [
            self._make_asgn(1, "E1", 1, [self._iso(10), self._iso(11)]),  # 10:00–12:00
            self._make_asgn(2, "E2", 1, [self._iso(11)]),                  # 11:00–12:00
        ]
        violations = validate_schedule(state, asgns)
        overlap_violations = [v for v in violations if "VENUE_OVERLAP" in v]
        assert overlap_violations, "Expected VENUE_OVERLAP at hour 11, got none"

    def test_adjacent_events_not_flagged(self):
        """Event A: 10:00–12:00 and Event B: 12:00–14:00 → no overlap (half-open)."""
        venue = {"id": 1, "name": "Hall A", "capacity": 100}
        ev1 = {"id": 1, "expected_attendance": 50, "name": "E1"}
        ev2 = {"id": 2, "expected_attendance": 50, "name": "E2"}
        state = self._make_state([venue], [ev1, ev2])
        asgns = [
            self._make_asgn(1, "E1", 1, [self._iso(10), self._iso(11)]),  # 10:00–12:00
            self._make_asgn(2, "E2", 1, [self._iso(12), self._iso(13)]),  # 12:00–14:00
        ]
        violations = validate_schedule(state, asgns)
        overlap_violations = [v for v in violations if "VENUE_OVERLAP" in v]
        assert not overlap_violations, (
            f"Adjacent events incorrectly flagged as overlap: {overlap_violations}"
        )


# ---------------------------------------------------------------------------
# Test F — Different venues may use the same time without conflict
# ---------------------------------------------------------------------------

class TestF_DifferentVenueSameTime:
    """Two events at the same time in different venues must not be flagged."""

    def _iso(self, hour: int) -> str:
        return datetime(2026, 10, 5, hour, 0).isoformat(timespec="minutes")

    def test_different_venues_same_time_no_conflict(self):
        venues = [
            {"id": 1, "name": "Hall A", "capacity": 100},
            {"id": 2, "name": "Hall B", "capacity": 100},
        ]
        events = [
            {"id": 1, "expected_attendance": 50, "name": "E1"},
            {"id": 2, "expected_attendance": 50, "name": "E2"},
        ]
        state = {"venues": venues, "events": events, "departments": [], "staff": [], "equipment": []}
        asgns = [
            {"event_id": 1, "event_name": "E1", "venue_id": 1,
             "slots": [self._iso(12), self._iso(13)]},
            {"event_id": 2, "event_name": "E2", "venue_id": 2,
             "slots": [self._iso(12), self._iso(13)]},
        ]
        violations = validate_schedule(state, asgns)
        overlap_violations = [v for v in violations if "VENUE_OVERLAP" in v]
        assert not overlap_violations, (
            f"Different venues should not flag overlap: {overlap_violations}"
        )


# ---------------------------------------------------------------------------
# Test G — Reduced venue capacity causes reassignment or unresolved
# ---------------------------------------------------------------------------

class TestG_VenueCapacityOverride:
    """Reduce the baseline venue's capacity below the event's attendance."""

    def test_capacity_override_triggers_move_or_unresolved(self):
        baseline = reset_and_generate()
        asgn = first_scheduled_event(baseline)
        assert asgn, "No scheduled event in baseline"

        event_id = asgn["event_id"]
        venue_id = asgn["venue_id"]

        # Get event attendance from state
        state = db.get_state()
        ev_obj = next((e for e in state["events"] if e["id"] == event_id), None)
        assert ev_obj, f"Event {event_id} not in state"
        attendance = ev_obj["expected_attendance"]

        # Set capacity to below the attendance value (minimum 1)
        reduced_cap = max(1, attendance - 1)

        result = run_whatif({
            "venue_overrides": {str(venue_id): {"capacity": reduced_cap}},
        })

        diff = next((d for d in result["differences"] if d["event_id"] == event_id), None)
        assert diff is not None, "Target event not in differences"

        # Must either move to another venue or become unresolved — not stay overloaded
        if diff["after_status"] == "Scheduled":
            after_asgn = find_assignment_in_result(result, event_id)
            assert after_asgn is not None
            if after_asgn["venue_id"] == venue_id:
                # Check the venue capacity in the result — if it's still reduced, that's a bug
                # (The simulated venue_override reduces it; if still assigned there, capacity check failed)
                pytest.fail(
                    f"Event {event_id} still assigned to venue {venue_id} "
                    f"whose simulated capacity ({reduced_cap}) < attendance ({attendance})"
                )
        else:
            # Unresolved is acceptable when no compatible venue exists
            assert diff["after_status"] == "Unresolved", (
                f"Unexpected after_status: {diff['after_status']}"
            )


# ---------------------------------------------------------------------------
# Test H — Unavailable venue receives no events
# ---------------------------------------------------------------------------

class TestH_UnavailableVenue:
    """Mark a venue unavailable; no event should be assigned to it in simulation."""

    def test_unavailable_venue_gets_no_assignments(self):
        baseline = reset_and_generate()
        # Find any venue that has at least one assignment
        venue_ids_used = {a["venue_id"] for a in baseline.get("assignments", [])}
        if not venue_ids_used:
            pytest.skip("No assignments in baseline; cannot test unavailable venue.")

        target_venue_id = next(iter(venue_ids_used))

        result = run_whatif({
            "unavailable_venue_id": target_venue_id,
            "unavailability_reason": "Maintenance",
            "allow_cascade": True,
        })

        sim_assignments = result["modified_schedule"]["assignments"]
        still_in_venue = [a for a in sim_assignments if a["venue_id"] == target_venue_id]
        assert not still_in_venue, (
            f"Venue {target_venue_id} marked unavailable but still has "
            f"{len(still_in_venue)} assignment(s): "
            + ", ".join(a["event_name"] for a in still_in_venue)
        )


# ---------------------------------------------------------------------------
# Test I — Cascade disabled does not reschedule unrelated events
# ---------------------------------------------------------------------------

class TestI_CascadeDisabled:
    """With allow_cascade=False, events that were scheduled in baseline must stay UNCHANGED."""

    def test_cascade_disabled_non_target_unchanged(self):
        baseline = reset_and_generate()
        # Find any scheduled event to shift
        asgn = first_scheduled_event(baseline)
        if asgn is None:
            pytest.skip("No scheduled event found; skipping Test I.")

        event_id = asgn["event_id"]
        original_hour = datetime.fromisoformat(asgn["slots"][0]).hour
        # Request a different hour to create a potential conflict
        new_hour = find_good_target_hour(asgn, target_delta=3)

        result = run_whatif({
            "target_time_shift_event_id": event_id,
            "simulated_start_hour": new_hour,
            "allow_cascade": False,
        })

        # Build set of event IDs that were SCHEDULED in baseline
        baseline_scheduled_ids = {a["event_id"] for a in baseline.get("assignments", [])}

        # All non-target events that were SCHEDULED in baseline must be UNCHANGED
        for diff in result["differences"]:
            if diff["event_id"] == event_id:
                continue  # skip target event
            if diff["event_id"] not in baseline_scheduled_ids:
                continue  # skip events that were already unresolved in baseline
            # Already-unresolved events appear as REMAINS UNSOLVED, not UNCHANGED — that's OK
            if diff["before_status"] == "Unresolved":
                continue
            assert diff["change_type"] == "UNCHANGED", (
                f"With cascade disabled, scheduled event '{diff['event_name']}' "
                f"(id={diff['event_id']}) changed: {diff['change_type']}"
            )


# ---------------------------------------------------------------------------
# Test J — Impossible events remain unresolved
# ---------------------------------------------------------------------------

class TestJ_ImpossibleEventUnresolved:
    """
    Any event that was unresolved in the baseline must NOT appear in the
    simulated assignments list (it must remain unresolved).
    """

    def test_impossible_event_stays_unresolved(self):
        baseline = reset_and_generate()
        unresolved_ids = {u["event_id"] for u in baseline.get("unresolved", [])}

        if not unresolved_ids:
            pytest.skip("Baseline has no unresolved events; cannot verify impossible-event behaviour.")

        result = run_whatif({})  # identity simulation — no overrides

        sim_assigned_ids = {a["event_id"] for a in result["modified_schedule"].get("assignments", [])}

        for uid in unresolved_ids:
            assert uid not in sim_assigned_ids, (
                f"Event {uid} was unresolved in baseline but appears scheduled in simulation!"
            )


# ---------------------------------------------------------------------------
# Test K — Feasibility status correctly reflects constraint state
# ---------------------------------------------------------------------------

class TestK_FeasibilityStatus:
    """feasibility_status must match actual schedule state."""

    def test_k1_feasibility_status_present(self):
        """feasibility_status field must always be present in the response."""
        reset_and_generate()
        result = run_whatif({})
        assert "feasibility_status" in result, "feasibility_status field missing from response"
        assert result["feasibility_status"] in (
            "FINAL FEASIBLE SCHEDULE",
            "PARTIALLY FEASIBLE SCHEDULE",
            "INFEASIBLE SCHEDULE",
        ), f"Unknown feasibility_status value: {result['feasibility_status']}"

    def test_k2_unresolved_events_means_not_fully_feasible(self):
        """If unresolved events exist, feasibility must NOT claim FINAL FEASIBLE SCHEDULE."""
        reset_and_generate()
        result = run_whatif({})
        sim_unresolved = result["modified_schedule"].get("unresolved", [])
        if sim_unresolved:
            assert result["feasibility_status"] != "FINAL FEASIBLE SCHEDULE", (
                "Claimed FINAL FEASIBLE but unresolved events exist: "
                + ", ".join(u["event_name"] for u in sim_unresolved)
            )

    def test_k3_no_unresolved_means_final_feasible(self):
        """If no unresolved events and no violations, status must be FINAL FEASIBLE SCHEDULE."""
        baseline = reset_and_generate()
        unresolved = baseline.get("unresolved", [])
        if unresolved:
            pytest.skip("Baseline has unresolved events; cannot test all-feasible case.")

        result = run_whatif({})  # identity simulation
        sim_unresolved = result["modified_schedule"].get("unresolved", [])
        if not sim_unresolved:
            assert result["feasibility_status"] == "FINAL FEASIBLE SCHEDULE", (
                f"Expected FINAL FEASIBLE SCHEDULE with zero unresolved, "
                f"got {result['feasibility_status']}"
            )

    def test_k4_new_impact_fields_present(self):
        """All new impact fields must be present and have correct types."""
        reset_and_generate()
        result = run_whatif({})
        for field in ("changed_events", "time_shifts", "venue_shifts",
                      "unresolved_events", "hard_constraint_violations"):
            assert field in result, f"Field '{field}' missing from response"
            assert isinstance(result[field], int), f"Field '{field}' must be int, got {type(result[field])}"
        assert isinstance(result["feasibility_status"], str)


# ---------------------------------------------------------------------------
# Test L — Master schedule data unchanged after simulation
# ---------------------------------------------------------------------------

class TestL_MasterScheduleIntegrity:
    """Simulation must never mutate the master schedule or event/venue data."""

    def test_master_schedule_unchanged_after_simulation(self):
        baseline_before = reset_and_generate()
        assignments_before = sorted(
            baseline_before.get("assignments", []), key=lambda a: a["event_id"]
        )

        # Run several different simulation scenarios
        asgn = first_scheduled_event(baseline_before)
        if asgn:
            run_whatif({
                "target_time_shift_event_id": asgn["event_id"],
                "simulated_start_hour": 15,
                "allow_cascade": True,
            })
            run_whatif({
                "venue_overrides": {str(asgn["venue_id"]): {"capacity": 1}},
            })

        # Fetch master schedule after simulations
        baseline_after = get_schedule()
        assignments_after = sorted(
            baseline_after.get("assignments", []), key=lambda a: a["event_id"]
        )

        assert len(assignments_before) == len(assignments_after), (
            f"Master schedule length changed: "
            f"{len(assignments_before)} → {len(assignments_after)}"
        )
        for before, after in zip(assignments_before, assignments_after):
            assert before["event_id"] == after["event_id"]
            assert before["venue_id"] == after["venue_id"], (
                f"Event {before['event_id']} venue changed in master schedule!"
            )
            assert before["slots"] == after["slots"], (
                f"Event {before['event_id']} slots changed in master schedule!"
            )

    def test_state_events_unchanged_after_simulation(self):
        """Event records in the state (attendance, priority) must be unchanged after simulation."""
        reset_and_generate()
        state_before = db.get_state()
        events_before = {e["id"]: copy.deepcopy(e) for e in state_before["events"]}

        # Run a simulation that attempts event overrides
        for ev_id in list(events_before.keys())[:1]:  # test just first event
            run_whatif({
                "event_overrides": {
                    str(ev_id): {"priority": "critical", "expected_attendance": 9999}
                },
            })

        state_after = db.get_state()
        for ev in state_after["events"]:
            original = events_before.get(ev["id"])
            if original is None:
                continue
            assert ev["expected_attendance"] == original["expected_attendance"], (
                f"Event {ev['id']} attendance mutated in state! "
                f"Before: {original['expected_attendance']}, After: {ev['expected_attendance']}"
            )
            assert ev["priority"] == original["priority"], (
                f"Event {ev['id']} priority mutated in state! "
                f"Before: {original['priority']}, After: {ev['priority']}"
            )

    def test_venue_state_unchanged_after_simulation(self):
        """Venue capacity must not be mutated in state after a venue_override simulation."""
        reset_and_generate()
        state_before = db.get_state()
        venues_before = {v["id"]: copy.deepcopy(v) for v in state_before["venues"]}

        # Run simulation with capacity override
        for vid in list(venues_before.keys())[:1]:
            run_whatif({
                "venue_overrides": {str(vid): {"capacity": 1}},
            })

        state_after = db.get_state()
        for venue in state_after["venues"]:
            original = venues_before.get(venue["id"])
            if original is None:
                continue
            assert venue["capacity"] == original["capacity"], (
                f"Venue {venue['id']} capacity mutated! "
                f"Before: {original['capacity']}, After: {venue['capacity']}"
            )
