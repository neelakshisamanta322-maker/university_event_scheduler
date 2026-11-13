from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_get_index_html():
    res = client.get("/")
    assert res.status_code == 200
    assert "Lovely" in res.text
    assert "Professional" in res.text
    assert "Problem Model" in res.text
    assert "Hard constraints" in res.text
    assert "Soft preferences" in res.text


def test_demo_reset_and_state():
    res = client.post("/api/demo/reset", headers={"X-User-Role": "Admin"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True

    st = client.get("/api/state").json()
    assert len(st["departments"]) == 3
    assert len(st["events"]) == 7
    assert len(st["venues"]) == 4
    assert len(st["staff"]) == 3
    assert len(st["equipment"]) == 3


def test_generate_schedule():
    res = client.post("/api/schedule/generate", json={"max_nodes": 50000}, headers={"X-User-Role": "Admin"})
    assert res.status_code == 200
    data = res.json()
    assert "assignments" in data
    assert "unresolved" in data
    assert len(data["assignments"]) > 0
    # Boundary conflict: 500-seat mega orientation must be in unresolved
    assert any("500" in u["event_name"] for u in data["unresolved"])


def test_explain_endpoint():
    # Generate schedule first
    client.post("/api/schedule/generate", json={"max_nodes": 50000})
    # Explain event 1 (AI/ML Symposium)
    res = client.get("/api/explain/1")
    assert res.status_code == 200
    exp = res.json()
    assert exp["event_id"] == 1
    assert "hard_constraints_satisfied" in exp
    assert len(exp["hard_constraints_satisfied"]) > 0


def test_whatif_simulator():
    client.post("/api/demo/reset", headers={"X-User-Role": "Admin"})
    payload = {
        "event_overrides": {
            3: {"priority": "critical"}  # Bump Startup Pitch Day from high to critical
        },
        "venue_overrides": {},
        "max_nodes": 50000,
    }
    res = client.post("/api/whatif", json=payload, headers={"X-User-Role": "Admin"})
    assert res.status_code == 200
    data = res.json()
    assert "modified_schedule" in data
    assert "differences" in data
    assert len(data["differences"]) > 0


def test_analytics_endpoint():
    res = client.get("/api/analytics")
    assert res.status_code == 200
    data = res.json()
    assert "venue_utilization" in data
    assert "department_equity" in data
    assert data["total_events"] >= 7


def test_export_endpoints():
    # CSV
    csv_res = client.get("/api/export/csv")
    assert csv_res.status_code == 200
    assert "Event ID" in csv_res.text

    # PDF
    pdf_res = client.get("/api/export/pdf")
    assert pdf_res.status_code == 200
    assert pdf_res.headers["content-type"] == "application/pdf"
    assert len(pdf_res.content) > 1000

    # ICS
    ics_res = client.get("/api/export/ics")
    assert ics_res.status_code == 200
    assert "BEGIN:VCALENDAR" in ics_res.text


def test_role_based_permissions():
    # Viewer role attempting to reset demo should be forbidden (403)
    res = client.post("/api/demo/reset", headers={"X-User-Role": "Viewer"})
    assert res.status_code == 403

    # Coordinator can list and submit events
    coord_res = client.get("/api/events", headers={"X-User-Role": "Coordinator"})
    assert coord_res.status_code == 200


def test_whatif_advanced_features():
    client.post("/api/demo/reset", headers={"X-User-Role": "Admin"})
    client.post("/api/schedule/generate", json={"max_nodes": 50000}, headers={"X-User-Role": "Admin"})

    # TEST 1: Responsible AI Tutorial - sim capacity 60, expected attendance 65 -> VENUE SHIFT
    payload_cap = {
        "event_overrides": {4: {"expected_attendance": 65}},
        "venue_overrides": {4: {"capacity": 60}},
        "max_nodes": 50000,
    }
    res = client.post("/api/whatif", json=payload_cap, headers={"X-User-Role": "Admin"})
    assert res.status_code == 200
    data = res.json()
    diff4 = next(d for d in data["differences"] if d["event_id"] == 4)
    assert diff4["change_type"] == "VENUE SHIFT"
    assert diff4["before_venue"] == "Classroom B201"
    assert diff4["after_venue"] == "Central Auditorium"
    assert "insufficient" in diff4["details"].lower() or "capacity" in diff4["details"].lower()

    # TEST 2: Unavailable Venue Simulation
    payload_unavail = {
        "event_overrides": {},
        "venue_overrides": {},
        "unavailable_venue_id": 4,
        "unavailability_reason": "Maintenance",
        "max_nodes": 50000,
    }
    res_unavail = client.post("/api/whatif", json=payload_unavail, headers={"X-User-Role": "Admin"})
    assert res_unavail.status_code == 200
    data_unavail = res_unavail.json()
    assert "dependency_chain" in data_unavail
    assert len(data_unavail["dependency_chain"]) > 0
    assert "VENUE UNAVAILABLE" in data_unavail["dependency_chain"][0]

    # TEST 3: Remains Unsolved Check (Event 6: 500-Seat Mega Orientation)
    diff6 = next(d for d in data["differences"] if d["event_id"] == 6)
    assert diff6["change_type"] == "REMAINS UNSOLVED"
    assert "remained unresolved" in diff6["details"].lower()

    # TEST 4: Master Data Safety check (master state unchanged)
    st = client.get("/api/state").json()
    v4 = next(v for v in st["venues"] if v["id"] == 4)
    assert v4["capacity"] == 70
    assert len(v4["available_slots"]) > 0


