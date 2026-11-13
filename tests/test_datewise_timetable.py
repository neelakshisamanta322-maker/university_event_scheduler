"""
Date-Wise Interactive Timetable & Master Schedule Verification Tests
Tests covering:
1. Monday Oct 05, Tuesday Oct 06, Wednesday Oct 07 event dates & times
2. Date uniqueness (no event appears on another date)
3. Event durations (2-hour events have 2 contiguous slots, 1-hour have 1)
4. Unresolved events preserved in separate list
5. Filter contracts and UI elements in index.html
6. View mode elements (Day, Week, Month, Intervals) in index.html
7. Master schedule integrity and existing features preserved
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
ADMIN = {"X-User-Role": "Admin"}


def reset_and_generate() -> Dict[str, Any]:
    """Reset demo data and generate fresh schedule."""
    r1 = client.post("/api/demo/reset", headers=ADMIN)
    assert r1.status_code == 200
    r2 = client.post("/api/schedule/generate", json={"max_nodes": 100000}, headers=ADMIN)
    assert r2.status_code == 200
    return r2.json()


class TestDateWiseEventDistribution:
    """Verifies default demo events are assigned to their exact intended dates and times."""

    @pytest.fixture(autouse=True)
    def setup_schedule(self):
        self.result = reset_and_generate()
        self.assignments = self.result["assignments"]
        self.unresolved = self.result["unresolved"]

    def _get_event(self, name_part: str) -> Dict[str, Any]:
        for a in self.assignments:
            if name_part.lower() in a["event_name"].lower():
                return a
        raise AssertionError(f"Event matching '{name_part}' not found in assignments: {[a['event_name'] for a in self.assignments]}")

    def test_monday_oct_05_events(self):
        """
        Monday, October 05:
        - AI/ML Research Symposium: 10:00–12:00
        - Embedded Systems Workshop: 11:00–13:00
        - Startup Pitch Day: 12:00–14:00
        - Responsible AI Tutorial: 13:00–14:00 (or on Monday)
        """
        # 1. AI/ML Research Symposium
        aiml = self._get_event("AI/ML Research")
        assert len(aiml["slots"]) == 2, f"Expected 2 slots (2 hours), got {len(aiml['slots'])}"
        assert aiml["slots"][0].startswith("2026-10-05T10:00")
        assert aiml["slots"][1].startswith("2026-10-05T11:00")
        assert "Auditorium" in aiml["venue_name"]

        # 2. Embedded Systems Workshop
        embed = self._get_event("Embedded Systems")
        assert len(embed["slots"]) == 2, f"Expected 2 slots (2 hours), got {len(embed['slots'])}"
        assert embed["slots"][0].startswith("2026-10-05T11:00")
        assert embed["slots"][1].startswith("2026-10-05T12:00")

        # 3. Startup Pitch Day
        pitch = self._get_event("Startup Pitch")
        assert len(pitch["slots"]) == 2, f"Expected 2 slots (2 hours), got {len(pitch['slots'])}"
        assert pitch["slots"][0].startswith("2026-10-05T12:00")
        assert pitch["slots"][1].startswith("2026-10-05T13:00")

        # 4. Responsible AI Tutorial (Day 0)
        resp = self._get_event("Responsible AI")
        assert len(resp["slots"]) == 1, f"Expected 1 slot (1 hour), got {len(resp['slots'])}"
        assert resp["slots"][0].startswith("2026-10-05"), f"Responsible AI should be on 2026-10-05, got {resp['slots'][0]}"

    def test_tuesday_oct_06_events(self):
        """
        Tuesday, October 06:
        - ECE Student Seminar: 12:00–13:00
        """
        ece = self._get_event("ECE Student Seminar")
        assert len(ece["slots"]) == 1, f"Expected 1 slot (1 hour), got {len(ece['slots'])}"
        assert ece["slots"][0].startswith("2026-10-06T12:00"), f"Expected 2026-10-06T12:00, got {ece['slots'][0]}"

    def test_wednesday_oct_07_events(self):
        """
        Wednesday, October 07:
        - VR Demonstration: 11:00–12:00
        """
        vr = self._get_event("VR Demonstration")
        assert len(vr["slots"]) == 1, f"Expected 1 slot (1 hour), got {len(vr['slots'])}"
        assert vr["slots"][0].startswith("2026-10-07T11:00"), f"Expected 2026-10-07T11:00, got {vr['slots'][0]}"

    def test_date_uniqueness_no_cross_date_duplication(self):
        """Verify that each event appears on exactly one date and slots do not span across midnight."""
        seen_event_dates = {}
        for a in self.assignments:
            eid = a["event_id"]
            dates = {s.split("T")[0] for s in a["slots"]}
            assert len(dates) == 1, f"Event {a['event_name']} spans across multiple dates: {dates}"
            event_date = list(dates)[0]
            assert eid not in seen_event_dates, f"Event {eid} appears multiple times in assignments"
            seen_event_dates[eid] = event_date

    def test_unresolved_events_preserved(self):
        """500-Seat Mega Orientation must remain in unresolved list and not disappear."""
        assert len(self.unresolved) > 0
        unres_names = [u["event_name"] for u in self.unresolved]
        assert any("500" in name for name in unres_names), (
            f"Expected 500-Seat Mega Orientation in unresolved, got: {unres_names}"
        )


class TestTimetableUIStructure:
    """Verifies that index.html contains all required UI elements for Date-Wise Timetable."""

    @pytest.fixture(autouse=True)
    def fetch_index_html(self):
        r = client.get("/")
        assert r.status_code == 200
        self.html = r.text

    def test_view_mode_buttons_exist(self):
        """Verify Day View, Week View, Month View buttons exist."""
        assert 'id="viewDayBtn"' in self.html
        assert 'id="viewWeekBtn"' in self.html
        assert 'id="viewMonthBtn"' in self.html
        assert "Day View" in self.html
        assert "Week View" in self.html
        assert "Month View" in self.html

    def test_date_navigation_controls_exist(self):
        """Verify Previous Day, Next Day, Date Picker, and Heading exist."""
        assert 'id="prevDateBtn"' in self.html
        assert 'id="nextDateBtn"' in self.html
        assert 'id="todayDateBtn"' in self.html
        assert 'id="currentDateHeading"' in self.html
        assert 'id="timetableDatePicker"' in self.html

    def test_interval_selector_exists(self):
        """Verify interval selector exists with 15m, 30m, 60m options."""
        assert 'id="intervalSelect"' in self.html
        assert 'value="15"' in self.html
        assert 'value="30"' in self.html
        assert 'value="60"' in self.html

    def test_filter_controls_exist(self):
        """Verify Department, Venue, Priority filters, and Search input exist."""
        assert 'id="filterDeptSelect"' in self.html
        assert 'id="filterVenueSelect"' in self.html
        assert 'id="filterPrioritySelect"' in self.html
        assert 'id="filterSearchInput"' in self.html
        assert 'id="clearFiltersBtn"' in self.html

    def test_core_existing_components_preserved(self):
        """Verify heatmap toggle, stats bar, unresolved box, and tabs are preserved."""
        assert 'id="heatmapToggle"' in self.html
        assert 'id="timetableGrid"' in self.html
        assert 'id="unresolvedBox"' in self.html
        assert 'id="unresolvedList"' in self.html
        assert 'id="schedCount"' in self.html
        assert 'id="unresCount"' in self.html
