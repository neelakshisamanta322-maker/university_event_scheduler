from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

try:
    from ortools.sat.python import cp_model
    _ORTOOLS_AVAILABLE = True
except ImportError:  # pragma: no cover
    cp_model = None  # type: ignore
    _ORTOOLS_AVAILABLE = False


def parse_slot(s: str) -> datetime:
    return datetime.fromisoformat(s)


def solve_with_ortools(state: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """
    Cross-checks scheduling problem with Google OR-Tools CP-SAT.
    Maps discrete time slots to integer time points and solves using SAT constraints.
    """
    if not _ORTOOLS_AVAILABLE:
        return {
            "status": "UNAVAILABLE",
            "assignments": {},
            "unscheduled": [],
            "solver_time_ms": 0,
            "error": "google-ortools package is not installed. Install it with: pip install ortools",
        }
    start_time = time.perf_counter()
    model = cp_model.CpModel()

    events = state["events"]
    venues = state["venues"]
    staff_map = {s["id"]: s for s in state["staff"]}
    equipment_map = {e["id"]: e for e in state["equipment"]}

    # Collect all unique time slots across venues and events
    all_slots = set()
    for v in venues:
        all_slots.update(v["available_slots"])
    for e in events:
        all_slots.update(e["allowed_slots"])

    sorted_slots = sorted(list(all_slots))
    slot_to_idx = {slot: i for i, slot in enumerate(sorted_slots)}
    num_slots = len(sorted_slots)

    # Variables: is_scheduled[e] (0/1), event_venue[e, v] (0/1), event_slot[e, s] (0/1)
    # Plus combined assignment variable: x[e, v, s]
    x = {}
    event_scheduled = {}

    for ev in events:
        e_id = ev["id"]
        event_scheduled[e_id] = model.NewBoolVar(f"scheduled_{e_id}")
        dur = ev["duration_slots"]
        allowed = set(ev["allowed_slots"])
        valid_triplets = []

        for v in venues:
            v_id = v["id"]
            # Hard constraint: Capacity
            if v["capacity"] < ev["expected_attendance"]:
                continue
            # Hard constraint: Venue type
            if ev.get("required_venue_type") and v["venue_type"] != ev["required_venue_type"]:
                continue

            venue_avail = set(v["available_slots"])

            for s_str in allowed:
                if ev.get("fixed_start_slot") and s_str != ev["fixed_start_slot"]:
                    continue

                s_idx = slot_to_idx.get(s_str)
                if s_idx is None or s_idx + dur > num_slots:
                    continue

                # Check contiguous slots are all available for venue and event
                event_slots = [sorted_slots[s_idx + i] for i in range(dur)]
                if not set(event_slots).issubset(allowed) or not set(event_slots).issubset(venue_avail):
                    continue

                # Check required staff available
                staff_ok = True
                for sid in ev["required_staff_ids"]:
                    st = staff_map.get(sid)
                    if not st or not set(event_slots).issubset(set(st["available_slots"])):
                        staff_ok = False
                        break
                if not staff_ok:
                    continue

                var = model.NewBoolVar(f"x_{e_id}_{v_id}_{s_idx}")
                x[(e_id, v_id, s_idx)] = var
                valid_triplets.append(var)

        # An event is scheduled if and only if exactly one valid (v, s) is chosen
        if valid_triplets:
            model.Add(sum(valid_triplets) == event_scheduled[e_id])
        else:
            model.Add(event_scheduled[e_id] == 0)

    # 1. Hard constraint: Venue non-overlap
    for v in venues:
        v_id = v["id"]
        for t in range(num_slots):
            overlapping_vars = []
            for ev in events:
                e_id = ev["id"]
                dur = ev["duration_slots"]
                # Any start slot s such that s <= t < s + dur
                for s_start in range(max(0, t - dur + 1), t + 1):
                    if (e_id, v_id, s_start) in x:
                        overlapping_vars.append(x[(e_id, v_id, s_start)])
            if overlapping_vars:
                model.Add(sum(overlapping_vars) <= 1)

    # 2. Hard constraint: Staff non-overlap
    for sid in staff_map:
        for t in range(num_slots):
            overlapping_vars = []
            for ev in events:
                if sid in ev["required_staff_ids"]:
                    e_id = ev["id"]
                    dur = ev["duration_slots"]
                    for s_start in range(max(0, t - dur + 1), t + 1):
                        for v in venues:
                            if (e_id, v["id"], s_start) in x:
                                overlapping_vars.append(x[(e_id, v["id"], s_start)])
            if overlapping_vars:
                model.Add(sum(overlapping_vars) <= 1)

    # 3. Hard constraint: Equipment global pooling
    for eq_id, eq in equipment_map.items():
        cap = eq["quantity"]
        for t in range(num_slots):
            eq_usage = []
            for ev in events:
                qty_needed = int(ev["required_equipment"].get(eq_id, 0))
                if qty_needed > 0:
                    e_id = ev["id"]
                    dur = ev["duration_slots"]
                    for s_start in range(max(0, t - dur + 1), t + 1):
                        for v in venues:
                            if (e_id, v["id"], s_start) in x:
                                eq_usage.append(x[(e_id, v["id"], s_start)] * qty_needed)
            if eq_usage:
                model.Add(sum(eq_usage) <= cap)

    # Objective: Maximize scheduled priority-weighted events, minimize soft cost
    priority_weights = {"critical": 10000, "high": 5000, "normal": 1000, "low": 200}
    obj_terms = []

    for ev in events:
        e_id = ev["id"]
        p_weight = priority_weights.get(ev.get("priority", "normal"), 1000)
        obj_terms.append(event_scheduled[e_id] * p_weight)

        # Soft cost penalties for preferred venue and start hour
        for (eid, vid, s_idx), var in x.items():
            if eid == e_id:
                penalty = 0
                if ev.get("preferred_venue_id") and vid != ev["preferred_venue_id"]:
                    penalty += 3
                if ev.get("preferred_start_hour") is not None:
                    slot_hour = parse_slot(sorted_slots[s_idx]).hour
                    penalty += abs(slot_hour - ev["preferred_start_hour"])
                if penalty > 0:
                    obj_terms.append(var * (-penalty * 10))

    model.Maximize(sum(obj_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5.0
    status = solver.Solve(model)
    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

    status_name = solver.StatusName(status)
    ortools_assignments = {}

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for (eid, vid, s_idx), var in x.items():
            if solver.Value(var) == 1:
                ev = next(e for e in events if e["id"] == eid)
                slots = [sorted_slots[s_idx + i] for i in range(ev["duration_slots"])]
                ortools_assignments[eid] = {
                    "event_id": eid,
                    "venue_id": vid,
                    "slots": slots,
                }

    return {
        "status": status_name,
        "solver_time_ms": elapsed_ms,
        "scheduled_count": len(ortools_assignments),
        "assignments": ortools_assignments,
    }


def compare_csp_and_ortools(csp_result: Dict[str, Any], state: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Compares the custom CSP solver results with Google OR-Tools CP-SAT."""
    ortools_res = solve_with_ortools(state)
    csp_assignments = {a["event_id"]: a for a in csp_result["assignments"]}
    ot_assignments = ortools_res["assignments"]

    total_events = len(state["events"])
    matching_events = 0

    for eid in range(1, total_events + 1):
        in_csp = eid in csp_assignments
        in_ot = eid in ot_assignments
        if in_csp and in_ot:
            if csp_assignments[eid]["venue_id"] == ot_assignments[eid]["venue_id"]:
                matching_events += 1
        elif not in_csp and not in_ot:
            # Both solvers correctly concluded event is unplaceable
            matching_events += 1

    match_pct = round((matching_events / max(1, total_events)) * 100, 1)

    return {
        "csp_nodes": csp_result["explored_nodes"],
        "csp_time_ms": csp_result["solver_time_ms"],
        "ortools_status": ortools_res["status"],
        "ortools_time_ms": ortools_res["solver_time_ms"],
        "match_percentage": match_pct,
    }
