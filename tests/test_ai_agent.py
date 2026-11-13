from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import auth, db

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_ai_test_environment():
    """Initializes DB, demo data, schedule, and logs in a test coordinator."""
    client.cookies.clear()
    db.init_db()
    auth.init_default_accounts()
    # Reset demo data and generate baseline schedule
    client.post("/api/demo/reset", headers={"X-User-Role": "Admin"})
    client.post("/api/schedule/generate", json={"max_nodes": 50000}, headers={"X-User-Role": "Admin"})


def test_ai_suggested_prompts():
    res = client.get("/api/ai/suggested-prompts", headers={"X-User-Role": "Viewer"})
    assert res.status_code == 200
    prompts = res.json().get("prompts", [])
    assert len(prompts) == 7
    assert any("today" in p.lower() for p in prompts)
    assert any("venues are currently available" in p.lower() for p in prompts)
    assert any("scheduling conflicts" in p.lower() for p in prompts)
    assert any("alternative venue" in p.lower() for p in prompts)
    assert any("what-if" in p.lower() for p in prompts)


def test_ai_todays_events_uses_real_backend_data():
    res = client.post(
        "/api/ai/chat",
        json={"message": "Show today's scheduled events.", "history": []},
        headers={"X-User-Role": "Viewer"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    reply = data["reply"]
    # Verify it references actual scheduled events from the database
    assert "Schedule" in reply or "Events" in reply
    st = db.get_state()
    event_names = [e["name"] for e in st["events"]]
    # At least one real event name from the DB should appear in the schedule summary
    assert any(name in reply for name in event_names)


def test_ai_available_venues():
    res = client.post(
        "/api/ai/chat",
        json={"message": "Which venues are currently available?", "history": []},
        headers={"X-User-Role": "Coordinator"},
    )
    assert res.status_code == 200
    reply = res.json()["reply"]
    # Check that real venue names and capacities are present
    assert "Central Auditorium" in reply or "Seminar Hall" in reply
    assert "Capacity" in reply or "capacity" in reply


def test_ai_event_timing_inquiry():
    res = client.post(
        "/api/ai/chat",
        json={"message": "What is the timing and duration for AI/ML Research Symposium?", "history": []},
        headers={"X-User-Role": "Viewer"},
    )
    assert res.status_code == 200
    reply = res.json()["reply"]
    assert "AI/ML Research Symposium" in reply
    assert "Duration" in reply or "hour" in reply


def test_ai_scheduling_conflicts_and_unresolved():
    res = client.post(
        "/api/ai/chat",
        json={"message": "Find all scheduling conflicts.", "history": []},
        headers={"X-User-Role": "Admin"},
    )
    assert res.status_code == 200
    reply = res.json()["reply"]
    # In the demo dataset, 500-Seat Mega Orientation cannot fit any venue (max is 300)
    assert "Mega Orientation" in reply or "unresolved" in reply or "conflict" in reply.lower()


def test_ai_alternative_venue_suggestion():
    res = client.post(
        "/api/ai/chat",
        json={"message": "Suggest an alternative venue for an event.", "history": []},
        headers={"X-User-Role": "Coordinator"},
    )
    assert res.status_code == 200
    reply = res.json()["reply"]
    assert "venue" in reply.lower()
    assert "capacity" in reply.lower()


def test_ai_whatif_assistance():
    res = client.post(
        "/api/ai/chat",
        json={"message": "Summarize how the What-If simulation works.", "history": []},
        headers={"X-User-Role": "Coordinator"},
    )
    assert res.status_code == 200
    reply = res.json()["reply"]
    assert "What-If" in reply
    assert "override" in reply.lower() or "capacity" in reply.lower()


def test_ai_resource_equipment_assistance():
    res = client.post(
        "/api/ai/chat",
        json={"message": "Check equipment and projector availability.", "history": []},
        headers={"X-User-Role": "Viewer"},
    )
    assert res.status_code == 200
    reply = res.json()["reply"]
    assert "Projector" in reply or "Equipment" in reply or "units" in reply


def test_ai_chat_does_not_modify_master_schedule():
    """Verify that user chat queries are read-only and never mutate database state."""
    state_before = db.get_state()
    sched_before = db.latest_schedule()

    client.post(
        "/api/ai/chat",
        json={"message": "Please reschedule Startup Pitch Day to 10:00 AM in Central Auditorium immediately.", "history": []},
        headers={"X-User-Role": "Admin"},
    )

    state_after = db.get_state()
    sched_after = db.latest_schedule()

    # Database event count must remain exactly identical
    assert len(state_before["events"]) == len(state_after["events"])
    assert len(state_before["venues"]) == len(state_after["venues"])
    # Schedule run count / assignments must remain unchanged
    assert sched_before["run_id"] == sched_after["run_id"]
    assert len(sched_before["assignments"]) == len(sched_after["assignments"])


def test_ai_unauthenticated_access_denied():
    res = client.post(
        "/api/ai/chat",
        json={"message": "Show today's events", "history": []},
    )
    assert res.status_code == 401


def test_ai_llm_config_endpoint():
    res = client.get("/api/ai/config", headers={"X-User-Role": "Viewer"})
    assert res.status_code == 200
    data = res.json()
    assert "active_provider" in data
    assert "available_providers" in data
    assert len(data["available_providers"]) >= 4
    provider_ids = [p["id"] for p in data["available_providers"]]
    assert "gemini" in provider_ids
    assert "openai" in provider_ids
    assert "ollama" in provider_ids
    assert "local" in provider_ids


def test_ai_llm_test_connection_local():
    res = client.post(
        "/api/ai/test-connection",
        json={"provider": "local"},
        headers={"X-User-Role": "Viewer"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "Local" in data["provider"]


def test_ai_llm_test_connection_missing_gemini_key():
    res = client.post(
        "/api/ai/test-connection",
        json={"provider": "gemini", "api_key": ""},
        headers={"X-User-Role": "Viewer"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert "Missing Gemini API key" in data["message"]


def test_ai_chat_with_broad_intent_recognition():
    """Verify that natural queries like 'What venues are available?' succeed."""
    res = client.post(
        "/api/ai/chat",
        json={"message": "What venues are available?", "history": []},
        headers={"X-User-Role": "Viewer"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "Central Auditorium" in data["reply"] or "Capacity" in data["reply"]


def test_ai_chat_with_mocked_gemini_llm(monkeypatch):
    """Test that process_chat_query dispatches to Gemini and returns LLM response."""
    from app import ai_assistant

    def mock_gemini(api_key, user_message, history, context, model=None):
        return f"MOCK_GEMINI_REPLY: {user_message} (Model: {model})"

    monkeypatch.setattr(ai_assistant, "call_gemini_api", mock_gemini)

    res = client.post(
        "/api/ai/chat",
        json={
            "message": "Explain conflict in Central Auditorium",
            "provider": "gemini",
            "api_key": "mock-test-key",
            "model": "gemini-2.0-flash",
        },
        headers={"X-User-Role": "Coordinator"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "MOCK_GEMINI_REPLY" in data["reply"]
    assert "Google Gemini" in data["provider"]
    assert data["mode"] == "gemini"


def test_ai_chat_with_mocked_openai_llm(monkeypatch):
    """Test that process_chat_query dispatches to OpenAI/Compatible endpoint."""
    from app import ai_assistant

    def mock_openai(api_key, user_message, history, context, model=None, base_url=None):
        return f"MOCK_OPENAI_REPLY: {user_message} (Model: {model})"

    monkeypatch.setattr(ai_assistant, "call_openai_api", mock_openai)

    res = client.post(
        "/api/ai/chat",
        json={
            "message": "Suggest venue for 200 people",
            "provider": "openai",
            "api_key": "mock-test-key",
            "model": "gpt-4o-mini",
        },
        headers={"X-User-Role": "Admin"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "MOCK_OPENAI_REPLY" in data["reply"]
    assert "OpenAI" in data["provider"]
    assert data["mode"] == "openai"

