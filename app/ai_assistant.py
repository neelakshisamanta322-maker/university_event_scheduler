from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import httpx

from . import db

SUGGESTED_PROMPTS = [
    "Show today's scheduled events.",
    "Which venues are currently available?",
    "Find all scheduling conflicts.",
    "Show this week's event schedule.",
    "Suggest an alternative venue for an event.",
    "Explain unresolved resource conflicts.",
    "Summarize the latest What-If simulation.",
]

DEFAULT_MODELS = {
    "gemini": "gemini-2.0-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-20241022",
    "ollama": "llama3",
}

SYSTEM_INSTRUCTION = (
    "You are the Lovely Professional University (LPU) AI Event Scheduling & Resource Optimization Assistant.\n"
    "Your role is to help university faculty, coordinators, and students understand university events, venue allocations, equipment constraints, and schedule conflicts.\n"
    "\nCRITICAL AI SAFETY & GROUNDING RULES:\n"
    "1. GROUNDING: Base all answers strictly on the actual live university schedule data provided in the context.\n"
    "2. NO INVENTED FACTS: Never invent events, venues, faculty, or dates not in the context.\n"
    "3. NO FALSE AVAILABILITY: Never claim a venue is available without verifying against the schedule assignments.\n"
    "4. RECOMMENDATIONS VS CONFIRMED DATA: Clearly state that suggestions or recommendations are analytical advice and NOT confirmed changes.\n"
    "5. READ-ONLY GUARANTEE: Inform users that you cannot modify the master schedule directly through chat and changes must be made via the dashboard by an authorized Coordinator or Admin.\n"
    "6. DATA PRIVACY: Never ask for or output passwords, session tokens, or personal identifiers.\n"
    "7. STYLE: Keep responses structured, professional, polite, and helpful using GitHub-flavored markdown with emojis and bullet points."
)


def get_sanitized_system_context() -> Dict[str, Any]:
    """Retrieves live university scheduling data while strictly excluding any credentials or user tokens."""
    state = db.get_state()
    schedule = db.latest_schedule() or {}
    conflict_logs = db.get_conflict_logs()[:20]

    # Department lookup
    dept_map = {d["id"]: d["name"] for d in state.get("departments", [])}

    # Format events cleanly
    events_summary = []
    for ev in state.get("events", []):
        events_summary.append({
            "id": ev["id"],
            "name": ev["name"],
            "department": dept_map.get(ev["department_id"], "General"),
            "expected_attendance": ev["expected_attendance"],
            "duration_slots": ev["duration_slots"],
            "priority": ev.get("priority", "normal"),
            "required_venue_type": ev.get("required_venue_type"),
            "allowed_slots": ev.get("allowed_slots", []),
            "buffer_slots": ev.get("buffer_slots", 0),
        })

    # Format venues cleanly
    venues_summary = []
    for v in state.get("venues", []):
        venues_summary.append({
            "id": v["id"],
            "name": v["name"],
            "capacity": v["capacity"],
            "venue_type": v["venue_type"],
            "location": v.get("location", "Academic Block"),
            "available_slots_count": len(v.get("available_slots", [])),
        })

    # Format assignments
    assignments = schedule.get("assignments", [])
    unresolved = schedule.get("unresolved", [])

    return {
        "departments": state.get("departments", []),
        "venues": venues_summary,
        "equipment": state.get("equipment", []),
        "staff": [
            {"id": s["id"], "name": s["name"], "role": s.get("role", "Faculty")}
            for s in state.get("staff", [])
        ],
        "events": events_summary,
        "schedule": {
            "solver_type": schedule.get("solver_type", "CSP-Heuristic"),
            "created_at": schedule.get("created_at"),
            "scheduled_count": len(assignments),
            "unresolved_count": len(unresolved),
            "assignments": assignments,
            "unresolved": unresolved,
        },
        "conflict_logs": conflict_logs,
    }


# ---------------------------------------------------------------------------
# LLM Providers
# ---------------------------------------------------------------------------

def call_gemini_api(
    api_key: str,
    user_message: str,
    history: List[Dict[str, str]],
    context: Dict[str, Any],
    model: Optional[str] = None,
) -> str:
    """Invokes Google Gemini API with system instructions enforcing AI safety rules and live schedule context."""
    target_model = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODELS["gemini"]
    context_str = json.dumps(context, indent=2)

    contents = []
    for item in history[-6:]:
        role = "user" if item.get("role") == "user" else "model"
        contents.append({"role": role, "parts": [{"text": item.get("content", "")}]})

    prompt_with_context = (
        f"Live University Schedule Context:\n```json\n{context_str}\n```\n\n"
        f"User Query: {user_message}"
    )
    contents.append({"role": "user", "parts": [{"text": prompt_with_context}]})

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": contents,
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1200,
        },
    }

    models_to_try = [target_model]
    if target_model != "gemini-1.5-flash":
        models_to_try.append("gemini-1.5-flash")
    if target_model != "gemini-2.0-flash" and "gemini-2.0-flash" not in models_to_try:
        models_to_try.append("gemini-2.0-flash")

    last_error = None
    with httpx.Client(timeout=18.0) as client:
        for mod in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{mod}:generateContent?key={api_key}"
            try:
                res = client.post(url, json=payload, headers={"Content-Type": "application/json"})
                if res.status_code == 200:
                    data = res.json()
                    candidates = data.get("candidates", [])
                    if candidates and "content" in candidates[0]:
                        parts = candidates[0]["content"].get("parts", [])
                        if parts and "text" in parts[0]:
                            return parts[0]["text"]
                elif res.status_code == 404:
                    last_error = f"Model {mod} not found (404). Trying next fallback..."
                    continue
                else:
                    err_msg = res.text[:200]
                    last_error = f"Gemini API error ({res.status_code}): {err_msg}"
            except Exception as ex:
                last_error = str(ex)

    raise RuntimeError(last_error or "Empty or failed response from Gemini API.")


def call_openai_api(
    api_key: str,
    user_message: str,
    history: List[Dict[str, str]],
    context: Dict[str, Any],
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    """Invokes OpenAI or OpenAI-compatible endpoint (Groq, DeepSeek, Together, vLLM, LM Studio)."""
    target_model = model or os.environ.get("OPENAI_MODEL") or DEFAULT_MODELS["openai"]
    raw_base = base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    endpoint = f"{raw_base.rstrip('/')}/chat/completions"

    context_str = json.dumps(context, indent=2)

    messages = [
        {"role": "system", "content": f"{SYSTEM_INSTRUCTION}\n\nLive University Schedule Context:\n```json\n{context_str}\n```"}
    ]
    for item in history[-6:]:
        messages.append({
            "role": "assistant" if item.get("role") == "assistant" else "user",
            "content": item.get("content", ""),
        })
    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": target_model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1200,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=20.0) as client:
        res = client.post(endpoint, json=payload, headers=headers)
        if res.status_code == 200:
            data = res.json()
            choices = data.get("choices", [])
            if choices and "message" in choices[0]:
                return choices[0]["message"].get("content", "")
        raise RuntimeError(f"OpenAI-compatible API error ({res.status_code}): {res.text[:200]}")


def call_anthropic_api(
    api_key: str,
    user_message: str,
    history: List[Dict[str, str]],
    context: Dict[str, Any],
    model: Optional[str] = None,
) -> str:
    """Invokes Anthropic Claude API."""
    target_model = model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODELS["anthropic"]
    endpoint = "https://api.anthropic.com/v1/messages"
    context_str = json.dumps(context, indent=2)

    formatted_messages = []
    for item in history[-6:]:
        formatted_messages.append({
            "role": "assistant" if item.get("role") == "assistant" else "user",
            "content": item.get("content", ""),
        })
    formatted_messages.append({"role": "user", "content": user_message})

    payload = {
        "model": target_model,
        "system": f"{SYSTEM_INSTRUCTION}\n\nLive University Schedule Context:\n```json\n{context_str}\n```",
        "messages": formatted_messages,
        "max_tokens": 1200,
        "temperature": 0.2,
    }

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    with httpx.Client(timeout=20.0) as client:
        res = client.post(endpoint, json=payload, headers=headers)
        if res.status_code == 200:
            data = res.json()
            content = data.get("content", [])
            if content and "text" in content[0]:
                return content[0]["text"]
        raise RuntimeError(f"Anthropic API error ({res.status_code}): {res.text[:200]}")


def call_ollama_api(
    user_message: str,
    history: List[Dict[str, str]],
    context: Dict[str, Any],
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    """Invokes local Ollama LLM server (offline, free, no API key)."""
    target_model = model or os.environ.get("OLLAMA_MODEL") or DEFAULT_MODELS["ollama"]
    raw_base = base_url or os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434"
    endpoint = f"{raw_base.rstrip('/')}/api/chat"

    context_str = json.dumps(context, indent=2)
    messages = [
        {"role": "system", "content": f"{SYSTEM_INSTRUCTION}\n\nLive University Schedule Context:\n```json\n{context_str}\n```"}
    ]
    for item in history[-6:]:
        messages.append({
            "role": "assistant" if item.get("role") == "assistant" else "user",
            "content": item.get("content", ""),
        })
    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": target_model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.2},
    }

    with httpx.Client(timeout=30.0) as client:
        res = client.post(endpoint, json=payload)
        if res.status_code == 200:
            data = res.json()
            msg = data.get("message", {})
            if "content" in msg:
                return msg["content"]
        raise RuntimeError(f"Ollama local LLM error ({res.status_code}): {res.text[:200]}")


# ---------------------------------------------------------------------------
# Smart Rule-Based Local Grounded Intelligence Engine (Fallback)
# ---------------------------------------------------------------------------

def run_rule_based_assistant(user_message: str, context: Dict[str, Any]) -> str:
    """High-fidelity local intelligence engine for LPU Event Scheduling with broad intent understanding."""
    msg = user_message.strip().lower()
    venues = context.get("venues", [])
    events = context.get("events", [])
    schedule = context.get("schedule", {})
    assignments = schedule.get("assignments", [])
    unresolved = schedule.get("unresolved", [])
    equipment = context.get("equipment", [])
    departments = context.get("departments", [])
    staff = context.get("staff", [])

    header = "🤖 **LPU Academic Intelligence Engine** *(Live Schedule Grounded)*\n\n"

    # 1. Today's / Daily scheduled events
    if any(k in msg for k in ["today", "daily", "happening today", "current events", "schedule for today", "todays"]):
        if not assignments:
            return header + "📅 **Today's Scheduled Events:**\n\nNo schedule has been generated yet. Please generate a schedule using the **⚡ Generate Schedule** button."

        all_dates = set()
        for a in assignments:
            for s in a.get("slots", []):
                all_dates.add(s.split("T")[0])
        sorted_dates = sorted(list(all_dates))
        target_date = sorted_dates[0] if sorted_dates else datetime.now().strftime("%Y-%m-%d")

        day_assignments = [
            a for a in assignments
            if any(s.startswith(target_date) for s in a.get("slots", []))
        ]

        if not day_assignments:
            return header + f"📅 **Events Scheduled for {target_date}:**\n\nNo university events are assigned for this date."

        lines = [f"📅 **Master Schedule for {target_date}** ({len(day_assignments)} events):", ""]
        for a in day_assignments:
            times = ", ".join(s.split("T")[1][:5] for s in a.get("slots", []))
            lines.append(f"- **{a['event_name']}**")
            lines.append(f"  • **Venue:** {a['venue_name']}")
            lines.append(f"  • **Department:** {a.get('department_name', 'University')}")
            lines.append(f"  • **Time Slots:** `{times}`")
            lines.append("")
        lines.append("💡 *Click any event card in the Timetable Grid for constraint breakdown and explainability.*")
        return header + "\n".join(lines)

    # 2. Available venues check
    if any(k in msg for k in ["available venue", "which venues", "free venue", "venue available", "check venue", "venues are available", "list venue", "all venue"]):
        lines = ["🏛️ **University Venue Availability & Capacity Matrix:**", ""]
        booked_venues = {}
        for a in assignments:
            v_name = a["venue_name"]
            slots = a.get("slots", [])
            booked_venues.setdefault(v_name, []).extend(slots)

        for v in venues:
            booked = booked_venues.get(v["name"], [])
            status = f"Occupied for {len(booked)} slot(s)" if booked else "Fully Available"
            lines.append(
                f"- **{v['name']}** (Capacity: **{v['capacity']}**, Type: `{v['venue_type']}`): **{status}**"
            )
            if booked:
                short_slots = ", ".join(s.split("T")[1][:5] for s in booked[:3])
                lines.append(f"  *Booked slots: {short_slots}{'...' if len(booked)>3 else ''}*")

        lines.append("")
        lines.append("📌 *Note: Venue capacity is a hard constraint. Events exceeding venue capacity cannot be scheduled.*")
        return header + "\n".join(lines)

    # 3. Scheduling conflicts / Unresolved events
    if any(k in msg for k in ["conflict", "unresolved", "overlap", "blocking", "why unresolved", "issues", "problem"]):
        if not unresolved:
            return header + (
                "✅ **Scheduling Conflict Analysis:**\n\n"
                "There are currently **0 unresolved conflicts** in the latest schedule run. "
                f"All {len(assignments)} eligible events were successfully scheduled without hard constraint violations."
            )

        lines = [f"⚠️ **Identified Scheduling Conflicts ({len(unresolved)} unresolved events):**", ""]
        for un in unresolved:
            lines.append(f"### • {un['event_name']}")
            lines.append(f"- **Event ID:** `#{un['event_id']}`")
            lines.append("- **Root Cause Reasons:**")
            for r in un.get("reasons", []):
                lines.append(f"  - ❌ {r}")

            sugg = un.get("suggestion")
            if sugg:
                lines.append(f"- **AI Resolution Suggestion:** {sugg.get('recommendation', 'Adjust venue or timing.')}")
                if sugg.get("closest_alternative_venue"):
                    lines.append(f"  • Recommended Alternative Venue: **{sugg['closest_alternative_venue']}**")
            lines.append("")

        lines.append("💡 *Safety Notice: These suggestions are advisory. To apply a resolution, use the What-If Simulator or edit event requirements.*")
        return header + "\n".join(lines)

    # 4. Weekly schedule summary
    if any(k in msg for k in ["week", "weekly", "full schedule", "master schedule", "timetable"]):
        if not assignments:
            return header + "🗓️ **Weekly Schedule Summary:**\n\nNo schedule has been generated yet. Please click **Generate Schedule**."

        lines = [f"🗓️ **University Weekly Master Schedule Overview ({len(assignments)} Assigned Events):**", ""]
        by_venue = {}
        for a in assignments:
            by_venue.setdefault(a["venue_name"], []).append(a)

        for v_name, ev_list in by_venue.items():
            lines.append(f"**{v_name}** ({len(ev_list)} events):")
            for ev in ev_list:
                times = ", ".join(s.split("T")[1][:5] for s in ev.get("slots", []))
                lines.append(f"  • `{times}` — **{ev['event_name']}** ({ev.get('department_name', 'Dept')})")
            lines.append("")

        if unresolved:
            lines.append(f"⚠️ **Note:** {len(unresolved)} event(s) remain unresolved due to resource contention.")
        return header + "\n".join(lines)

    # 5. Suggest an alternative venue for an event
    if any(k in msg for k in ["alternative venue", "suggest an alternative", "suggest venue", "different venue"]):
        matched_event = None
        for ev in events:
            if ev["name"].lower() in msg:
                matched_event = ev
                break

        if not matched_event and unresolved:
            un = unresolved[0]
            matched_event = next((e for e in events if e["id"] == un["event_id"]), None)

        if matched_event:
            att = matched_event["expected_attendance"]
            v_type = matched_event.get("required_venue_type")
            viable_venues = [
                v for v in venues
                if v["capacity"] >= att and (not v_type or v["venue_type"] == v_type)
            ]
            lines = [
                f"🏛️ **Alternative Venue Suggestions for '{matched_event['name']}':**",
                f"- **Expected Attendance:** {att} attendees",
                f"- **Required Venue Type:** `{v_type or 'Any'}`",
                "",
            ]
            if viable_venues:
                lines.append("Suitable venues capable of hosting this event:")
                for vv in viable_venues:
                    lines.append(f"- **{vv['name']}** (Capacity: {vv['capacity']}, Type: `{vv['venue_type']}`)")
                lines.append("")
                lines.append("💡 *To test assigning this venue without altering production data, try the What-If Simulator.*")
            else:
                lines.append(f"❌ No existing venue meets both the capacity ({att}) and type ({v_type}) constraints.")
                lines.append("Recommendation: Split the event into multiple sessions or adjust attendance.")
            return header + "\n".join(lines)
        else:
            return header + (
                "🏛️ **Alternative Venue Advisory:**\n\n"
                "To suggest an alternative venue, please specify the event name (e.g. *'Suggest alternative venue for Mega Orientation'*).\n"
                "Currently available venues across the university:\n" +
                "\n".join([f"- **{v['name']}** (Capacity: {v['capacity']})" for v in venues])
            )

    # 6. What-If simulation assistance
    if any(k in msg for k in ["what-if", "what if", "simulator", "simulation", "override", "capacity change"]):
        return header + (
            "⚡ **What-If Scenario Simulator Guide & Assistance:**\n\n"
            "The What-If Simulator allows coordinators and administrators to safely model dynamic campus changes:\n\n"
            "1. **Priority Escalations:** Bump normal events to Critical or High to observe how the CSP solver shifts lower-priority sessions.\n"
            "2. **Venue Capacity Overrides:** Test increasing auditorium seating or downscaling classrooms to see if unresolved events become feasible.\n"
            "3. **Emergency Venue Closures:** Mark a venue unavailable (e.g., maintenance) to trigger automated cascade rescheduling to backup halls.\n"
            "4. **Zero Master Mutation:** Simulations run on in-memory clones and never modify the master database until approved.\n\n"
            "To run a simulation, switch to the **⚡ What-If Simulator** tab in the dashboard navigation."
        )

    # 7. Resource & Equipment assistance
    if any(k in msg for k in ["resource", "equipment", "projector", "mic", "sound", "utilization"]):
        lines = ["🖥️ **University Pooled Equipment & Resource Status:**", ""]
        for eq in equipment:
            lines.append(f"- **{eq['name']}** ({eq.get('category', 'Hardware')}): **{eq['quantity']} total units** in stock")
        lines.append("")
        lines.append("Resource constraints are tracked globally: at any given hour, total concurrent event equipment usage cannot exceed university warehouse stock.")
        return header + "\n".join(lines)

    # 8. Department lookup
    for dept in departments:
        if dept["name"].lower() in msg or dept["code"].lower() in msg:
            dept_events = [e for e in events if e["department"].lower() == dept["name"].lower()]
            lines = [
                f"🏛️ **Department Overview: {dept['name']} ({dept['code']})**",
                f"- **Registered Events:** {len(dept_events)}",
            ]
            for de in dept_events:
                lines.append(f"  • **{de['name']}** (Attendance: {de['expected_attendance']}, Priority: `{de['priority']}`)")
            return header + "\n".join(lines)

    # 9. Specific Event lookup by name
    for ev in events:
        if ev["name"].lower() in msg or any(part in msg for part in ev["name"].lower().split() if len(part) > 3):
            asgn = next((a for a in assignments if a["event_id"] == ev["id"]), None)
            lines = [
                f"📋 **Event Information: {ev['name']}**",
                f"- **Department:** {ev['department']}",
                f"- **Expected Attendance:** {ev['expected_attendance']} participants",
                f"- **Duration:** {ev['duration_slots']} hour(s)",
                f"- **Priority Tier:** `{ev['priority']}`",
            ]
            if asgn:
                lines.append(f"- **Status:** Scheduled ✅")
                lines.append(f"- **Assigned Venue:** {asgn['venue_name']}")
                lines.append(f"- **Scheduled Time:** {', '.join(asgn['slots'])}")
            else:
                lines.append("- **Status:** Unresolved ⚠️")
                un = next((u for u in unresolved if u["event_id"] == ev["id"]), None)
                if un:
                    lines.append(f"- **Reason:** {', '.join(un.get('reasons', []))}")
            return header + "\n".join(lines)

    # 10. Specific Venue lookup by name
    for v in venues:
        if v["name"].lower() in msg:
            v_assignments = [a for a in assignments if a["venue_name"].lower() == v["name"].lower()]
            lines = [
                f"🏛️ **Venue Information: {v['name']}**",
                f"- **Capacity:** {v['capacity']} seats",
                f"- **Type:** `{v['venue_type']}`",
                f"- **Location:** {v['location']}",
                f"- **Scheduled Events:** {len(v_assignments)}",
            ]
            for a in v_assignments:
                lines.append(f"  • **{a['event_name']}** ({', '.join(a['slots'])})")
            return header + "\n".join(lines)

    # Default fallback overview
    return header + (
        f"Hello! I am your **LPU Event Scheduling & Resource AI Assistant**.\n\n"
        f"I have direct access to the live university scheduling database containing **{len(events)} events**, "
        f"**{len(venues)} venues**, **{len(equipment)} equipment types**, and **{len(assignments)} scheduled assignments**.\n\n"
        "Here are common inquiries I can assist you with:\n"
        "- 📅 *'Show today's scheduled events'* or *'Show this week's event schedule'*\n"
        "- 🏛️ *'Which venues are currently available?'* or *'Check Central Auditorium capacity'*\n"
        "- ⚠️ *'Find all scheduling conflicts'* or *'Why is Mega Orientation unresolved?'*\n"
        "- 💡 *'Suggest an alternative venue for AI/ML Symposium'*\n"
        "- ⚡ *'How does the What-If Simulator work?'*\n"
        "- 🖥️ *'Check equipment availability'* (projectors, sound systems)\n\n"
        "💡 *Tip: You can configure an external LLM (Gemini, OpenAI, Ollama) by clicking the ⚙️ Settings icon above for conversational reasoning!*"
    )


# ---------------------------------------------------------------------------
# Status, Config & Connection Testing
# ---------------------------------------------------------------------------

def get_llm_status() -> Dict[str, Any]:
    """Inspects environment and system state to determine configured LLM capabilities."""
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("AI_API_KEY")
    has_gemini = bool(gemini_key and not gemini_key.startswith("your_"))

    openai_key = os.environ.get("OPENAI_API_KEY")
    has_openai = bool(openai_key and not openai_key.startswith("your_"))

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    has_anthropic = bool(anthropic_key and not anthropic_key.startswith("your_"))

    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    # Fast microsecond socket probe for Ollama local service
    has_ollama = False
    try:
        parsed = urllib.parse.urlparse(ollama_url)
        host = parsed.hostname or "127.0.0.1"
        if host in ("localhost", "127.0.0.1"):
            host = "127.0.0.1"
        port = parsed.port or 11434
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.15)
            if sock.connect_ex((host, port)) == 0:
                has_ollama = True
    except Exception:
        has_ollama = False

    preferred = os.environ.get("LLM_PROVIDER", "auto").lower()

    if preferred == "gemini" and has_gemini:
        active_provider = "gemini"
        active_model = os.environ.get("GEMINI_MODEL", DEFAULT_MODELS["gemini"])
    elif preferred == "openai" and has_openai:
        active_provider = "openai"
        active_model = os.environ.get("OPENAI_MODEL", DEFAULT_MODELS["openai"])
    elif preferred == "anthropic" and has_anthropic:
        active_provider = "anthropic"
        active_model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODELS["anthropic"])
    elif preferred == "ollama" and has_ollama:
        active_provider = "ollama"
        active_model = os.environ.get("OLLAMA_MODEL", DEFAULT_MODELS["ollama"])
    elif has_gemini:
        active_provider = "gemini"
        active_model = os.environ.get("GEMINI_MODEL", DEFAULT_MODELS["gemini"])
    elif has_openai:
        active_provider = "openai"
        active_model = os.environ.get("OPENAI_MODEL", DEFAULT_MODELS["openai"])
    elif has_anthropic:
        active_provider = "anthropic"
        active_model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODELS["anthropic"])
    elif has_ollama:
        active_provider = "ollama"
        active_model = os.environ.get("OLLAMA_MODEL", DEFAULT_MODELS["ollama"])
    else:
        active_provider = "local"
        active_model = "Rule-Based Engine"

    available_providers = [
        {
            "id": "gemini",
            "name": "Google Gemini",
            "description": "Google's multi-modal fast LLM (Gemini 2.0 / 1.5 Flash).",
            "default_model": DEFAULT_MODELS["gemini"],
            "requires_key": True,
            "is_configured": has_gemini,
        },
        {
            "id": "openai",
            "name": "OpenAI / Compatible",
            "description": "OpenAI (GPT-4o mini) or compatible APIs like Groq, DeepSeek, vLLM.",
            "default_model": DEFAULT_MODELS["openai"],
            "requires_key": True,
            "is_configured": has_openai,
        },
        {
            "id": "ollama",
            "name": "Ollama (Local Offline LLM)",
            "description": "Free offline LLM running on your machine with zero API key (llama3, mistral).",
            "default_model": DEFAULT_MODELS["ollama"],
            "requires_key": False,
            "is_configured": has_ollama,
        },
        {
            "id": "anthropic",
            "name": "Anthropic Claude",
            "description": "Anthropic Claude 3.5 Haiku / Sonnet models.",
            "default_model": DEFAULT_MODELS["anthropic"],
            "requires_key": True,
            "is_configured": has_anthropic,
        },
        {
            "id": "local",
            "name": "LPU Academic Grounded Engine",
            "description": "Fast deterministic rules engine directly grounded in live SQLite state.",
            "default_model": "Live SQLite Grounding",
            "requires_key": False,
            "is_configured": True,
        },
    ]

    return {
        "active_provider": active_provider,
        "active_model": active_model,
        "available_providers": available_providers,
        "has_gemini_key": has_gemini,
        "has_openai_key": has_openai,
        "has_anthropic_key": has_anthropic,
        "has_ollama": has_ollama,
    }


def test_llm_connection(
    provider: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Tests connection to an LLM provider with given or environment credentials."""
    t0 = time.time()
    prov = (provider or "auto").lower()

    if prov == "gemini":
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("AI_API_KEY")
        if not key or key.startswith("your_"):
            return {"ok": False, "provider": "gemini", "model": model or DEFAULT_MODELS["gemini"], "message": "Missing Gemini API key."}
        try:
            mod = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODELS["gemini"]
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{mod}:generateContent?key={key}"
            with httpx.Client(timeout=10.0) as client:
                res = client.post(
                    url,
                    json={"contents": [{"parts": [{"text": "Ping. Reply with 'OK'."}]}]},
                    headers={"Content-Type": "application/json"},
                )
                latency = round((time.time() - t0) * 1000, 2)
                if res.status_code == 200:
                    return {"ok": True, "provider": "Google Gemini", "model": mod, "message": "Connected successfully!", "latency_ms": latency}
                return {"ok": False, "provider": "Google Gemini", "model": mod, "message": f"HTTP {res.status_code}: {res.text[:150]}"}
        except Exception as e:
            return {"ok": False, "provider": "Google Gemini", "model": model or "gemini", "message": str(e)}

    elif prov == "openai":
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key or key.startswith("your_"):
            return {"ok": False, "provider": "openai", "model": model or DEFAULT_MODELS["openai"], "message": "Missing OpenAI API key."}
        try:
            mod = model or os.environ.get("OPENAI_MODEL") or DEFAULT_MODELS["openai"]
            raw_base = base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
            endpoint = f"{raw_base.rstrip('/')}/chat/completions"
            with httpx.Client(timeout=10.0) as client:
                res = client.post(
                    endpoint,
                    json={"model": mod, "messages": [{"role": "user", "content": "Ping"}]},
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                )
                latency = round((time.time() - t0) * 1000, 2)
                if res.status_code == 200:
                    return {"ok": True, "provider": "OpenAI / Compatible", "model": mod, "message": "Connected successfully!", "latency_ms": latency}
                return {"ok": False, "provider": "OpenAI / Compatible", "model": mod, "message": f"HTTP {res.status_code}: {res.text[:150]}"}
        except Exception as e:
            return {"ok": False, "provider": "OpenAI / Compatible", "model": model or "openai", "message": str(e)}

    elif prov == "ollama":
        try:
            mod = model or os.environ.get("OLLAMA_MODEL") or DEFAULT_MODELS["ollama"]
            raw_base = base_url or os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434"
            endpoint = f"{raw_base.rstrip('/')}/api/tags"
            with httpx.Client(timeout=5.0) as client:
                res = client.get(endpoint)
                latency = round((time.time() - t0) * 1000, 2)
                if res.status_code == 200:
                    return {"ok": True, "provider": "Ollama", "model": mod, "message": "Ollama server is active and reachable!", "latency_ms": latency}
                return {"ok": False, "provider": "Ollama", "model": mod, "message": f"HTTP {res.status_code}: {res.text[:150]}"}
        except Exception as e:
            return {"ok": False, "provider": "Ollama", "model": model or "llama3", "message": f"Could not reach Ollama: {str(e)}"}

    elif prov == "anthropic":
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return {"ok": False, "provider": "anthropic", "model": model or DEFAULT_MODELS["anthropic"], "message": "Missing Anthropic API key."}
        try:
            mod = model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODELS["anthropic"]
            with httpx.Client(timeout=10.0) as client:
                res = client.post(
                    "https://api.anthropic.com/v1/messages",
                    json={"model": mod, "messages": [{"role": "user", "content": "Ping"}], "max_tokens": 10},
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                )
                latency = round((time.time() - t0) * 1000, 2)
                if res.status_code == 200:
                    return {"ok": True, "provider": "Anthropic Claude", "model": mod, "message": "Connected successfully!", "latency_ms": latency}
                return {"ok": False, "provider": "Anthropic Claude", "model": mod, "message": f"HTTP {res.status_code}: {res.text[:150]}"}
        except Exception as e:
            return {"ok": False, "provider": "Anthropic Claude", "model": model or "claude", "message": str(e)}

    return {"ok": True, "provider": "Local Engine", "model": "Rule-Based", "message": "Local Grounded Engine active and operational."}


# ---------------------------------------------------------------------------
# Main AI Assistant Entrypoint
# ---------------------------------------------------------------------------

def process_chat_query(
    user_message: str,
    history: Optional[List[Dict[str, str]]] = None,
    provider_preference: Optional[str] = None,
    api_key_override: Optional[str] = None,
    model_override: Optional[str] = None,
    base_url_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Main AI Assistant entrypoint. Attempts requested or best available LLM provider,

    with seamless fallback to the live grounded local rule engine.
    """
    if not user_message or not user_message.strip():
        return {
            "reply": "Please enter a question regarding university events, venues, or schedules.",
            "mode": "validation",
            "provider": "local",
        }

    history = history or []
    context = get_sanitized_system_context()

    status = get_llm_status()
    prov = (provider_preference or status["active_provider"]).lower()

    # If explicit or auto-detected Gemini
    if prov in ("gemini", "auto") and (api_key_override or status["has_gemini_key"]):
        key = api_key_override or os.environ.get("GEMINI_API_KEY") or os.environ.get("AI_API_KEY")
        if key and not key.startswith("your_"):
            try:
                mod = model_override or os.environ.get("GEMINI_MODEL") or DEFAULT_MODELS["gemini"]
                reply = call_gemini_api(key, user_message, history, context, model=mod)
                return {
                    "reply": reply,
                    "mode": "gemini",
                    "provider": f"Google Gemini ({mod})",
                }
            except Exception as e:
                # If Gemini fails and user requested auto, try next or fallback
                if prov != "auto":
                    local_reply = run_rule_based_assistant(user_message, context)
                    return {
                        "reply": f"*(Notice: Gemini LLM query failed: {str(e)[:120]}. Showing local grounded analysis)*\n\n{local_reply}",
                        "mode": "local_fallback",
                        "provider": "LPU Knowledge Engine (Fallback)",
                    }

    # If explicit or auto-detected OpenAI / Compatible
    if prov in ("openai", "auto") and (api_key_override or status["has_openai_key"]):
        key = api_key_override or os.environ.get("OPENAI_API_KEY")
        if key and not key.startswith("your_"):
            try:
                mod = model_override or os.environ.get("OPENAI_MODEL") or DEFAULT_MODELS["openai"]
                reply = call_openai_api(key, user_message, history, context, model=mod, base_url=base_url_override)
                return {
                    "reply": reply,
                    "mode": "openai",
                    "provider": f"OpenAI / Compatible ({mod})",
                }
            except Exception as e:
                if prov != "auto":
                    local_reply = run_rule_based_assistant(user_message, context)
                    return {
                        "reply": f"*(Notice: OpenAI-compatible LLM query failed: {str(e)[:120]}. Showing local grounded analysis)*\n\n{local_reply}",
                        "mode": "local_fallback",
                        "provider": "LPU Knowledge Engine (Fallback)",
                    }

    # If explicit or auto-detected Anthropic
    if prov in ("anthropic", "auto") and (api_key_override or status["has_anthropic_key"]):
        key = api_key_override or os.environ.get("ANTHROPIC_API_KEY")
        if key and not key.startswith("your_"):
            try:
                mod = model_override or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODELS["anthropic"]
                reply = call_anthropic_api(key, user_message, history, context, model=mod)
                return {
                    "reply": reply,
                    "mode": "anthropic",
                    "provider": f"Anthropic Claude ({mod})",
                }
            except Exception as e:
                if prov != "auto":
                    local_reply = run_rule_based_assistant(user_message, context)
                    return {
                        "reply": f"*(Notice: Anthropic Claude LLM query failed: {str(e)[:120]}. Showing local grounded analysis)*\n\n{local_reply}",
                        "mode": "local_fallback",
                        "provider": "LPU Knowledge Engine (Fallback)",
                    }

    # If explicit or auto-detected Ollama
    if prov in ("ollama", "auto") and (status["has_ollama"] or base_url_override):
        try:
            mod = model_override or os.environ.get("OLLAMA_MODEL") or DEFAULT_MODELS["ollama"]
            reply = call_ollama_api(user_message, history, context, model=mod, base_url=base_url_override)
            return {
                "reply": reply,
                "mode": "ollama",
                "provider": f"Ollama Local ({mod})",
            }
        except Exception as e:
            if prov != "auto":
                local_reply = run_rule_based_assistant(user_message, context)
                return {
                    "reply": f"*(Notice: Ollama local LLM query failed: {str(e)[:120]}. Showing local grounded analysis)*\n\n{local_reply}",
                    "mode": "local_fallback",
                    "provider": "LPU Knowledge Engine (Fallback)",
                }

    # Deterministic local grounded intelligence engine
    local_reply = run_rule_based_assistant(user_message, context)
    return {
        "reply": local_reply,
        "mode": "local_engine",
        "provider": "LPU Knowledge Engine (Local Grounded)",
    }
