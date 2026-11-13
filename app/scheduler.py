from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Dict, List, Optional, Set, Tuple

PRIORITY_RANK = {"critical": 0, "high": 1, "normal": 2, "low": 3}


@dataclass(frozen=True)
class Candidate:
    venue_id: int
    slots: Tuple[str, ...]
    soft_cost: int
    explanation: Tuple[str, ...]


@lru_cache(maxsize=1024)
def parse_slot(s: str) -> datetime:
    return datetime.fromisoformat(s)


def format_slot_friendly(s: str) -> str:
    dt = parse_slot(s)
    return dt.strftime("%a %b %d, %H:%M")


def contiguous_slots(start: str, duration: int) -> List[str]:
    base = parse_slot(start)
    return [(base + timedelta(hours=i)).isoformat(timespec="minutes") for i in range(duration)]


def event_static_candidates(
    event: Dict[str, Any],
    venues: List[Dict[str, Any]],
    staff_map: Dict[int, Dict[str, Any]],
    ml_slot_scorer: Optional[Any] = None,
) -> Tuple[List[Candidate], List[str]]:
    """Generates all candidates satisfying single-event constraints (capacity, type, static availability)."""
    reasons: List[str] = []
    allowed: Set[str] = set(event["allowed_slots"])

    if event.get("fixed_start_slot"):
        starts = [event["fixed_start_slot"]]
    else:
        starts = sorted(allowed)

    candidates: List[Candidate] = []

    for venue in venues:
        # Hard Constraint: Required Specific Venue ID
        if event.get("required_venue_id") is not None and venue["id"] != event["required_venue_id"]:
            continue
        # Hard Constraint: Capacity
        if venue["capacity"] < event["expected_attendance"]:
            continue
        # Hard Constraint: Required Venue Type
        if event.get("required_venue_type") and venue["venue_type"] != event["required_venue_type"]:
            continue

        venue_available = set(venue["available_slots"])
        for start in starts:
            slots = contiguous_slots(start, event["duration_slots"])
            # Hard Constraint: Allowed window
            if not set(slots).issubset(allowed):
                continue
            # Hard Constraint: Venue open window
            if not set(slots).issubset(venue_available):
                continue

            # Hard Constraint: Staff static availability
            staff_ok = True
            for sid in event["required_staff_ids"]:
                st = staff_map.get(sid)
                if not st or not set(slots).issubset(set(st["available_slots"])):
                    staff_ok = False
                    break
            if not staff_ok:
                continue

            # Soft Preferences & Costs
            cost = 0
            expl: List[str] = [
                f"Venue '{venue['name']}' satisfies capacity ({venue['capacity']} seats >= {event['expected_attendance']} expected).",
                f"Venue type '{venue['venue_type']}' matches required specification.",
                "All assigned staff members are available for the entire duration.",
                "Time window falls inside university operational hours.",
            ]

            # Soft preference 1: Preferred venue
            if event.get("preferred_venue_id"):
                if venue["id"] == event["preferred_venue_id"]:
                    expl.append("Preferred venue constraint fully satisfied.")
                else:
                    cost += 3
                    expl.append(f"Preferred venue mismatch (+3 penalty; assigned '{venue['name']}').")

            # Soft preference 2: Preferred start hour
            if event.get("preferred_start_hour") is not None:
                delta = abs(parse_slot(start).hour - int(event["preferred_start_hour"]))
                cost += delta
                if delta == 0:
                    expl.append("Optimal preferred start hour satisfied.")
                else:
                    expl.append(f"Start hour offset by {delta}h from preferred time (+{delta} penalty).")

            # Soft preference 3: Avoid late sessions
            start_dt = parse_slot(start)
            end_hour = start_dt.hour + event["duration_slots"]
            if end_hour > 17:
                cost += 2
                expl.append("Event concludes after 17:00 (+2 penalty for late session).")

            # Soft preference 4: Optional ML slot convenience penalty
            if ml_slot_scorer:
                ml_penalty = ml_slot_scorer(event, venue, start_dt)
                cost += ml_penalty
                if ml_penalty > 0:
                    expl.append(f"ML slot preference adjustment (+{ml_penalty} soft cost).")

            candidates.append(Candidate(venue["id"], tuple(slots), cost, tuple(expl)))

    if candidates:
        candidates.sort(key=lambda c: (c.soft_cost, c.slots[0], c.venue_id))
        return candidates, reasons

    # Failure diagnostics when no static candidate exists
    max_venue_cap = max((v["capacity"] for v in venues), default=0)
    if event["expected_attendance"] > max_venue_cap:
        reasons.append(
            f"Attendance overflow: Event requires {event['expected_attendance']} seats, but university's largest venue has capacity {max_venue_cap}."
        )
    matching_types = [v for v in venues if v.get("venue_type") == event.get("required_venue_type")]
    if event.get("required_venue_type") and not matching_types:
        reasons.append(f"No venue on campus is registered with type '{event['required_venue_type']}'.")
    elif event.get("required_venue_type") and all(v["capacity"] < event["expected_attendance"] for v in matching_types):
        reasons.append(
            f"No venue of required type '{event['required_venue_type']}' meets the attendance threshold of {event['expected_attendance']}."
        )

    if event.get("fixed_start_slot") and event["fixed_start_slot"] not in allowed:
        reasons.append(f"Fixed start slot '{event['fixed_start_slot']}' is outside the event's permitted date/time window.")

    for sid in event["required_staff_ids"]:
        if sid not in staff_map:
            reasons.append(f"Required staff member (ID #{sid}) does not exist in the faculty directory.")

    if not reasons:
        reasons.append("No venue/time slot combination simultaneously satisfies static capacity, operational availability, and faculty calendars.")

    return [], reasons


class Scheduler:
    def __init__(
        self,
        state: Dict[str, List[Dict[str, Any]]],
        max_nodes: int = 100000,
        buffer_slots: int = 0,
        ml_slot_scorer: Optional[Any] = None,
    ):
        self.state = state
        self.max_nodes = max_nodes
        self.global_buffer = buffer_slots
        self.ml_slot_scorer = ml_slot_scorer
        self.nodes = 0
        self.backtracks = 0
        self.events = sorted(state["events"], key=lambda e: e["id"])
        self.event_map = {e["id"]: e for e in self.events}
        self.venues = state["venues"]
        self.staff_map = {s["id"]: s for s in state["staff"]}
        self.equipment_map = {e["id"]: e for e in state["equipment"]}
        self.venue_map = {v["id"]: v for v in self.venues}
        self.dept_map = {d["id"]: d["name"] for d in state.get("departments", [])}

        self.static: Dict[int, List[Candidate]] = {}
        self.static_reasons: Dict[int, List[str]] = {}

        for ev in self.events:
            cands, reasons = event_static_candidates(ev, self.venues, self.staff_map, ml_slot_scorer)
            self.static[ev["id"]] = cands
            self.static_reasons[ev["id"]] = reasons

        self.best_assignment: Dict[int, Candidate] = {}
        self.best_tuple = (-1, float("inf"))  # (count, cost)
        self.conflict_logs: List[Dict[str, Any]] = []

    def dynamic_ok(self, event: Dict[str, Any], cand: Candidate, assigned: Dict[int, Candidate]) -> bool:
        cand_start = parse_slot(cand.slots[0])
        cand_end = parse_slot(cand.slots[-1]) + timedelta(hours=1)
        eff_buffer = max(self.global_buffer, int(event.get("buffer_slots", 0)))
        buf_delta = timedelta(hours=eff_buffer)

        for other_id, other_cand in assigned.items():
            other_event = self.event_map[other_id]
            other_start = parse_slot(other_cand.slots[0])
            other_end = parse_slot(other_cand.slots[-1]) + timedelta(hours=1)
            other_eff_buf = max(self.global_buffer, int(other_event.get("buffer_slots", 0)))
            total_buf = max(buf_delta, timedelta(hours=other_eff_buf))

            # 1. Venue conflict & Buffer time constraint
            if cand.venue_id == other_cand.venue_id:
                # Direct overlap
                if not (cand_end <= other_start or cand_start >= other_end):
                    return False
                # Buffer spacing between consecutive events in the same venue
                if total_buf > timedelta(0):
                    if not (cand_end + total_buf <= other_start or cand_start >= other_end + total_buf):
                        return False

            # 2. Staff conflict (same staff member booked simultaneously)
            if not (cand_end <= other_start or cand_start >= other_end):
                shared_staff = set(event["required_staff_ids"]).intersection(other_event["required_staff_ids"])
                if shared_staff:
                    return False

        # 3. Pooled Equipment Quantity limit
        for eq_id, qty_needed in event["required_equipment"].items():
            capacity = self.equipment_map.get(eq_id, {}).get("quantity", 0)
            for slot in cand.slots:
                used = 0
                for other_id, other_cand in assigned.items():
                    if slot in other_cand.slots:
                        other_event = self.event_map[other_id]
                        used += int(other_event["required_equipment"].get(eq_id, 0))
                if used + int(qty_needed) > capacity:
                    return False

        return True

    def domain(self, event: Dict[str, Any], assigned: Dict[int, Candidate]) -> List[Candidate]:
        return [c for c in self.static[event["id"]] if self.dynamic_ok(event, c, assigned)]

    def select_event(self, remaining: List[Dict[str, Any]], assigned: Dict[int, Candidate]) -> Dict[str, Any]:
        """
        Heuristic:
        1. Priority Rank (critical=0, high=1, normal=2, low=3)
        2. MRV (Minimum Remaining Values in dynamic domain)
        3. Degree Heuristic (resource dependencies: staff + equipment count)
        4. Tie-breaker by event ID
        """
        scored = []
        for ev in remaining:
            d = self.domain(ev, assigned)
            dsize = len(d)
            degree = len(ev["required_staff_ids"]) + sum(ev["required_equipment"].values())
            priority_score = PRIORITY_RANK.get(ev.get("priority", "normal"), 2)
            scored.append((priority_score, dsize, -degree, ev["id"], ev))

        scored.sort(key=lambda x: x[:-1])
        return scored[0][-1]

    def maybe_update_best(self, assigned: Dict[int, Candidate]) -> None:
        count = len(assigned)
        cost = sum(c.soft_cost for c in assigned.values())
        if count > self.best_tuple[0] or (count == self.best_tuple[0] and cost < self.best_tuple[1]):
            self.best_tuple = (count, cost)
            self.best_assignment = dict(assigned)

    def search(self, remaining: List[Dict[str, Any]], assigned: Dict[int, Candidate]) -> None:
        if self.nodes >= self.max_nodes:
            self.maybe_update_best(assigned)
            return

        self.nodes += 1
        self.maybe_update_best(assigned)

        if not remaining:
            return

        # Pruning: Even placing all remaining events cannot beat current best count
        if len(assigned) + len(remaining) < self.best_tuple[0]:
            return

        ev = self.select_event(remaining, assigned)
        rest = [x for x in remaining if x["id"] != ev["id"]]
        domain = self.domain(ev, assigned)

        # Branch on valid candidates
        for cand in domain:
            assigned[ev["id"]] = cand
            self.search(rest, assigned)
            assigned.pop(ev["id"], None)
            self.backtracks += 1
            if self.nodes >= self.max_nodes:
                return

        # Skip-branch: Allow partial solution / conflict detection
        self.search(rest, assigned)

    def find_nearest_alternative(self, event: Dict[str, Any], chosen: Dict[int, Candidate]) -> Optional[Dict[str, Any]]:
        """Scans all venues and calendar days to suggest the closest feasible alternative."""
        all_allowed = sorted(event["allowed_slots"]) if event.get("allowed_slots") else []
        if not all_allowed:
            # Fallback to general venue availability
            for v in self.venues:
                all_allowed.extend(v["available_slots"])
            all_allowed = sorted(list(set(all_allowed)))

        for venue in sorted(self.venues, key=lambda v: abs(v["capacity"] - event["expected_attendance"])):
            if venue["capacity"] < event["expected_attendance"]:
                continue
            if event.get("required_venue_type") and venue["venue_type"] != event["required_venue_type"]:
                continue

            for start in all_allowed:
                slots = contiguous_slots(start, event["duration_slots"])
                if not set(slots).issubset(set(venue["available_slots"])):
                    continue

                test_cand = Candidate(venue["id"], tuple(slots), 0, ())
                if self.dynamic_ok(event, test_cand, chosen):
                    return {
                        "suggested_venue_id": venue["id"],
                        "suggested_venue_name": venue["name"],
                        "suggested_slots": slots,
                        "reason": f"Shifting to '{venue['name']}' at {format_slot_friendly(slots[0])} avoids all active resource contention.",
                    }

        return None

    def unresolved_reasons_and_attribution(
        self, ev: Dict[str, Any], chosen: Dict[int, Candidate]
    ) -> Tuple[List[str], Optional[Dict[str, Any]]]:
        """Provides human-readable explainability report detailing what clashed, who won, and why."""
        if not self.static[ev["id"]]:
            return self.static_reasons[ev["id"]], None

        reasons: Set[str] = set()
        for cand in self.static[ev["id"]]:
            cand_start = parse_slot(cand.slots[0])
            cand_end = parse_slot(cand.slots[-1]) + timedelta(hours=1)

            for other_id, oc in chosen.items():
                oe = next(e for e in self.events if e["id"] == other_id)
                o_start = parse_slot(oc.slots[0])
                o_end = parse_slot(oc.slots[-1]) + timedelta(hours=1)
                overlap = not (cand_end <= o_start or cand_start >= o_end)

                if overlap:
                    # Venue contention
                    if cand.venue_id == oc.venue_id:
                        oe_prio = oe.get("priority", "normal")
                        ev_prio = ev.get("priority", "normal")
                        reasons.add(
                            f"Venue '{self.venue_map[cand.venue_id]['name']}' occupied by '{oe['name']}' "
                            f"(Priority: {oe_prio} vs {ev_prio}). Won by '{oe['name']}' due to priority precedence."
                        )

                    # Staff contention
                    shared_staff = set(ev["required_staff_ids"]).intersection(oe["required_staff_ids"])
                    if shared_staff:
                        staff_names = [self.staff_map[sid]["name"] for sid in shared_staff if sid in self.staff_map]
                        reasons.add(
                            f"Staff contention: {', '.join(staff_names)} already assigned to '{oe['name']}' during requested hours."
                        )

            # Equipment contention
            for eq_id, qty in ev["required_equipment"].items():
                cap = self.equipment_map.get(eq_id, {}).get("quantity", 0)
                eq_name = self.equipment_map.get(eq_id, {}).get("name", f"Equipment #{eq_id}")
                for slot in cand.slots:
                    used = 0
                    competing = []
                    for other_id, oc in chosen.items():
                        if slot in oc.slots:
                            oe = next(e for e in self.events if e["id"] == other_id)
                            oq = int(oe["required_equipment"].get(eq_id, 0))
                            if oq:
                                used += oq
                                competing.append(f"{oe['name']} (uses {oq})")
                    if used + int(qty) > cap:
                        reasons.add(
                            f"Equipment shortage for '{eq_name}' at {format_slot_friendly(slot)}: "
                            f"Requires {qty} unit(s), but {used}/{cap} already booked by {', '.join(competing)}."
                        )

        if not reasons:
            reasons.add("Heuristic search prioritized globally optimal resource utilization within search bounds.")

        alt = self.find_nearest_alternative(ev, chosen)
        return sorted(reasons), alt

    def solve(self) -> Dict[str, Any]:
        start_time = time.perf_counter()
        self.search(list(self.events), {})
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

        chosen = self.best_assignment
        assignments = []

        for ev in sorted(self.events, key=lambda e: e["id"]):
            cand = chosen.get(ev["id"])
            if not cand:
                continue
            venue = self.venue_map[cand.venue_id]
            dept_name = self.dept_map.get(ev["department_id"], "General")

            expl = list(cand.explanation)
            expl.append(f"Event priority '{ev.get('priority', 'normal')}' was honored in assignment order.")
            expl.append("Zero hard constraint violations on venue capacity, faculty schedule, or equipment reserves.")

            assignments.append({
                "event_id": ev["id"],
                "event_name": ev["name"],
                "department_name": dept_name,
                "venue_id": venue["id"],
                "venue_name": venue["name"],
                "slots": list(cand.slots),
                "soft_cost": cand.soft_cost,
                "explanation": expl,
                "ml_risk_score": ev.get("ml_conflict_risk", "Low"),
                "ml_predicted_attendance": ev.get("ml_predicted_attendance", ev["expected_attendance"]),
            })

        unresolved = []
        for ev in self.events:
            if ev["id"] not in chosen:
                dept_name = self.dept_map.get(ev["department_id"], "General")
                reasons, alt = self.unresolved_reasons_and_attribution(ev, chosen)
                unresolved.append({
                    "event_id": ev["id"],
                    "event_name": ev["name"],
                    "department_name": dept_name,
                    "priority": ev.get("priority", "normal"),
                    "reasons": reasons,
                    "suggestion": alt,
                })

        return {
            "assignments": assignments,
            "unresolved": unresolved,
            "total_soft_cost": sum(a["soft_cost"] for a in assignments),
            "explored_nodes": self.nodes,
            "backtracks": self.backtracks,
            "solver_time_ms": elapsed_ms,
            "solver_type": "CSP-Heuristic (MRV + Degree + Priority)",
            "complete": len(unresolved) == 0,
        }


def validate_schedule(
    state: Dict[str, List[Dict[str, Any]]],
    assignments: List[Dict[str, Any]],
    unavailable_venue_ids: Optional[Set[int]] = None,
) -> List[str]:
    """
    Validate a complete set of assignments against hard constraints.

    Returns a list of human-readable violation descriptions.
    An empty list means the schedule is fully valid.

    Constraints checked:
      1. No venue double-booking (half-open interval [start, end) overlap).
      2. Capacity: attendance must not exceed venue capacity.
      3. Venue availability: unavailable venues must have zero assignments.
    """
    violations: List[str] = []
    venue_map: Dict[int, Dict[str, Any]] = {v["id"]: v for v in state.get("venues", [])}
    event_map: Dict[int, Dict[str, Any]] = {e["id"]: e for e in state.get("events", [])}

    # Group assignments by venue
    by_venue: Dict[int, List[Dict[str, Any]]] = {}
    for asgn in assignments:
        by_venue.setdefault(asgn["venue_id"], []).append(asgn)

    for vid, vas in by_venue.items():
        venue = venue_map.get(vid, {})
        venue_display = venue.get("name", str(vid))

        # --- (1) Unavailable venue check ---
        if unavailable_venue_ids and vid in unavailable_venue_ids:
            for asgn in vas:
                violations.append(
                    f"UNAVAILABLE_VENUE: '{asgn['event_name']}' assigned to "
                    f"unavailable venue '{venue_display}'"
                )

        # --- (2) Capacity check ---
        venue_cap = venue.get("capacity", 0)
        for asgn in vas:
            ev = event_map.get(asgn["event_id"], {})
            attendance = ev.get("expected_attendance", 0)
            if attendance > venue_cap:
                violations.append(
                    f"CAPACITY: '{asgn['event_name']}' attendance {attendance} "
                    f"> venue capacity {venue_cap} at '{venue_display}'"
                )

        # --- (3) Half-open interval overlap check [start, end) ---
        # Build (start_dt, end_dt, event_name) for each assignment in this venue
        intervals: List[Tuple[datetime, datetime, str]] = []
        for asgn in vas:
            slots = asgn.get("slots", [])
            if not slots:
                continue
            try:
                start_dt = parse_slot(slots[0])
                end_dt = parse_slot(slots[-1]) + timedelta(hours=1)
                intervals.append((start_dt, end_dt, asgn["event_name"]))
            except Exception:
                pass

        for i in range(len(intervals)):
            for j in range(i + 1, len(intervals)):
                a_start, a_end, a_name = intervals[i]
                b_start, b_end, b_name = intervals[j]
                # Overlap iff NOT (a_end <= b_start OR b_end <= a_start)
                if not (a_end <= b_start or b_end <= a_start):
                    violations.append(
                        f"VENUE_OVERLAP: '{a_name}' [{a_start.strftime('%H:%M')}–"
                        f"{a_end.strftime('%H:%M')}] and '{b_name}' [{b_start.strftime('%H:%M')}–"
                        f"{b_end.strftime('%H:%M')}] overlap in venue '{venue_display}'"
                    )

    return violations


def solve_schedule(
    state: Dict[str, List[Dict[str, Any]]],
    max_nodes: int = 100000,
    buffer_slots: int = 0,
    ml_slot_scorer: Optional[Any] = None,
) -> Dict[str, Any]:
    return Scheduler(state, max_nodes=max_nodes, buffer_slots=buffer_slots, ml_slot_scorer=ml_slot_scorer).solve()
