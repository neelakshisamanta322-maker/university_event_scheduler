from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Core Entity Models
# ---------------------------------------------------------------------------

class DepartmentBase(BaseModel):
    name: str
    code: Optional[str] = None


class DepartmentIn(DepartmentBase):
    pass


class DepartmentOut(DepartmentBase):
    id: int


class VenueBase(BaseModel):
    name: str
    capacity: int = Field(gt=0, description="Maximum seated audience capacity")
    venue_type: str = Field(default="general", description="auditorium, seminar, lab, classroom, or general")
    location: Optional[str] = Field(default="Academic Block", description="Campus building/location")
    available_slots: List[str] = Field(default_factory=list, description="ISO slot timestamps")


class VenueIn(VenueBase):
    pass


class VenueOut(VenueBase):
    id: int


class EquipmentBase(BaseModel):
    name: str
    quantity: int = Field(ge=0, description="Total units in stock across university")
    category: Optional[str] = Field(default="AV", description="AV, Computing, Lab Hardware")


class EquipmentIn(EquipmentBase):
    pass


class EquipmentOut(EquipmentBase):
    id: int


class StaffBase(BaseModel):
    name: str
    department_id: Optional[int] = None
    role: Optional[str] = Field(default="Faculty Coordinator", description="Role or title")
    skills: List[str] = Field(default_factory=list, description="List of expertise tags")
    available_slots: List[str] = Field(default_factory=list, description="ISO slot timestamps")


class StaffIn(StaffBase):
    pass


class StaffOut(StaffBase):
    id: int


class EventBase(BaseModel):
    name: str
    department_id: int
    duration_slots: int = Field(default=1, ge=1, le=8, description="Duration in hours (slots)")
    expected_attendance: int = Field(default=1, ge=1)
    required_venue_type: Optional[str] = Field(default=None, description="Required venue type or null for any")
    required_staff_ids: List[int] = Field(default_factory=list, description="IDs of staff required to attend")
    required_equipment: Dict[int, int] = Field(default_factory=dict, description="{equipment_id: quantity_needed}")
    allowed_slots: List[str] = Field(default_factory=list, description="Eligible ISO timestamps")
    preferred_venue_id: Optional[int] = None
    preferred_start_hour: Optional[int] = Field(default=None, ge=0, le=23)
    priority: str = Field(default="normal", description="critical, high, normal, low")
    fixed_start_slot: Optional[str] = Field(default=None, description="Enforces strict start slot if provided")
    buffer_slots: int = Field(default=0, ge=0, description="Buffer slots required after event in same venue")


class EventIn(EventBase):
    pass


class EventOut(EventBase):
    id: int
    ml_conflict_risk: Optional[str] = None
    ml_predicted_attendance: Optional[int] = None


# ---------------------------------------------------------------------------
# Scheduling & Solution Models
# ---------------------------------------------------------------------------

class ScheduleRequest(BaseModel):
    max_nodes: int = Field(default=100000, ge=100, le=1000000)
    use_ortools: bool = Field(default=False, description="Run OR-Tools CP-SAT in addition to CSP")
    buffer_slots: int = Field(default=0, ge=0, description="Global minimum buffer between consecutive events")


class AlternativeSuggestion(BaseModel):
    suggested_venue_id: Optional[int] = None
    suggested_venue_name: Optional[str] = None
    suggested_slots: List[str] = Field(default_factory=list)
    reason: str


class Assignment(BaseModel):
    event_id: int
    event_name: str
    department_name: Optional[str] = None
    venue_id: int
    venue_name: str
    slots: List[str]
    soft_cost: int
    explanation: List[str]
    ml_risk_score: Optional[str] = None
    ml_predicted_attendance: Optional[int] = None


class UnresolvedEvent(BaseModel):
    event_id: int
    event_name: str
    department_name: Optional[str] = None
    priority: str
    reasons: List[str]
    suggestion: Optional[AlternativeSuggestion] = None


class SolverComparison(BaseModel):
    csp_nodes: int
    csp_time_ms: float
    ortools_status: Optional[str] = None
    ortools_time_ms: Optional[float] = None
    match_percentage: Optional[float] = None


class ScheduleResult(BaseModel):
    run_id: Optional[int] = None
    created_at: Optional[str] = None
    assignments: List[Assignment]
    unresolved: List[UnresolvedEvent]
    total_soft_cost: int
    explored_nodes: int
    solver_time_ms: float
    solver_type: str = "CSP-Heuristic"
    complete: bool
    comparison: Optional[SolverComparison] = None


# ---------------------------------------------------------------------------
# Explainable AI & What-If Simulation Models
# ---------------------------------------------------------------------------

class EventExplanation(BaseModel):
    event_id: int
    event_name: str
    department_name: Optional[str] = None
    status: str  # "Scheduled" or "Unresolved"
    priority: str
    venue_assigned: Optional[str] = None
    time_assigned: Optional[List[str]] = None
    hard_constraints_satisfied: List[str]
    soft_preferences_satisfied: List[str]
    soft_preferences_sacrificed: List[str]
    conflict_details: List[str]
    alternative_options: Optional[AlternativeSuggestion] = None


class WhatIfRequest(BaseModel):
    event_overrides: Dict[int, Dict[str, Any]] = Field(default_factory=dict, description="{event_id: {priority: 'critical', ...}}")
    venue_overrides: Dict[int, Dict[str, Any]] = Field(default_factory=dict, description="{venue_id: {capacity: 350, ...}}")
    unavailable_venue_id: Optional[int] = Field(default=None, description="Venue ID marked temporarily unavailable")
    unavailability_reason: Optional[str] = Field(default="Maintenance", description="Reason for venue unavailability")
    target_time_shift_event_id: Optional[int] = Field(default=None, description="Event ID for target time shift override")
    simulated_start_hour: Optional[int] = Field(default=None, description="Simulated start hour (0-23)")
    preserve_original_time: bool = Field(default=False)
    preserve_original_venue: bool = Field(default=False)
    allow_time_shift: bool = Field(default=True)
    allow_venue_shift: bool = Field(default=True)
    allow_cascade: bool = Field(default=True)
    max_allowed_soft_cost: Optional[int] = Field(default=None)
    max_cascade_depth: int = Field(default=10)
    priority_handling: str = Field(default="Priority Aware")
    conflict_resolution: str = Field(default="Minimize Total Soft Cost")
    max_nodes: int = Field(default=100000)


class DiffItem(BaseModel):
    event_id: int
    event_name: str
    change_type: str  # "UNCHANGED", "VENUE SHIFT", "TIME SHIFT", "TIME + VENUE SHIFT", "CAPACITY CONFLICT", "RESOURCE CONFLICT", "UNSOLVED"
    before_status: str
    after_status: str
    before_venue: Optional[str] = None
    after_venue: Optional[str] = None
    before_slots: Optional[List[str]] = None
    after_slots: Optional[List[str]] = None
    details: str
    before_cost: int = 0
    after_cost: int = 0
    cost_change: int = 0


class WhatIfResult(BaseModel):
    baseline_run_id: Optional[int] = None
    modified_schedule: ScheduleResult
    differences: List[DiffItem]
    schedule_impact: str = "None"
    venue_impact: str = "None"
    time_impact: str = "None"
    cost_impact: str = "None"
    total_changes: int = 0
    original_total_cost: int = 0
    new_total_cost: int = 0
    additional_soft_cost: int = 0
    dependency_chain: List[str] = Field(default_factory=list)
    scenario_id: Optional[str] = None
    # New impact summary fields (additive — defaults preserve backward compatibility)
    changed_events: int = 0
    time_shifts: int = 0
    venue_shifts: int = 0
    unresolved_events: int = 0
    hard_constraint_violations: int = 0
    feasibility_status: str = "FINAL FEASIBLE SCHEDULE"


# ---------------------------------------------------------------------------
# Analytics Models
# ---------------------------------------------------------------------------

class VenueUtilization(BaseModel):
    venue_id: int
    venue_name: str
    capacity: int
    total_slots_available: int
    booked_slots: int
    utilization_rate_pct: float


class DepartmentEquity(BaseModel):
    department_id: int
    department_name: str
    events_requested: int
    events_scheduled: int
    satisfaction_rate_pct: float


class AnalyticsResult(BaseModel):
    total_events: int
    scheduled_events: int
    unresolved_events: int
    schedule_success_rate_pct: float
    total_soft_cost: int
    average_soft_cost_per_event: float
    venue_utilization: List[VenueUtilization]
    department_equity: List[DepartmentEquity]
    conflicts_resolved_count: int
    solver_runtime_ms: float


# ---------------------------------------------------------------------------
# Authentication Models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: str = Field(..., description="University user email")
    password: str = Field(..., min_length=1, description="Password")
    remember_me: bool = Field(default=False, description="Extend session duration to 7 days")
    captcha_id: Optional[str] = Field(default=None, description="CAPTCHA challenge token")
    captcha_solution: Optional[str] = Field(default=None, description="CAPTCHA user answer")
    otp_code: Optional[str] = Field(default=None, description="Optional 2FA 6-digit verification code")


class TwoFactorVerifyRequest(BaseModel):
    ticket: str = Field(..., min_length=10, description="2FA verification ticket")
    otp_code: str = Field(..., min_length=6, max_length=6, description="6-digit verification code")
    remember_me: bool = Field(default=False, description="Extend session duration")


class TwoFactorResendRequest(BaseModel):
    ticket: str = Field(..., min_length=10, description="2FA verification ticket")


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., description="Institutional user email")
    captcha_id: Optional[str] = Field(default=None, description="CAPTCHA challenge token")
    captcha_solution: Optional[str] = Field(default=None, description="CAPTCHA user answer")


class ResetPasswordRequest(BaseModel):
    ticket: str = Field(..., min_length=10, description="Password reset ticket")
    code: str = Field(..., min_length=6, max_length=6, description="6-digit reset code")
    new_password: str = Field(..., description="New password with required complexity")


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., min_length=1, description="Current institutional password")
    new_password: str = Field(..., min_length=8, description="New institutional password with required complexity")


class RegisterRequest(BaseModel):
    email: str = Field(..., description="University user email")
    password: str = Field(..., min_length=8, description="Institutional password (minimum 8 characters)")
    confirm_password: Optional[str] = Field(default=None, description="Password confirmation")
    full_name: str = Field(..., min_length=2, description="User full name")
    role: Optional[str] = Field(default="Student", description="Role: Student (default), Admin, or Coordinator")
    department_id: Optional[int] = Field(default=None, description="Optional department affiliation")
    student_id: Optional[str] = Field(default=None, description="Optional university student ID")


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    department_id: Optional[int] = None
    two_factor_enabled: Optional[bool] = False



# ---------------------------------------------------------------------------
# AI Assistant Models
# ---------------------------------------------------------------------------

class AIChatMessage(BaseModel):
    role: str = Field(..., description="'user' or 'assistant'")
    content: str = Field(..., description="Message text")


class AIChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User query")
    history: List[AIChatMessage] = Field(default_factory=list, description="Recent conversation turns")
    provider: Optional[str] = Field(None, description="Preferred LLM provider: 'auto', 'gemini', 'openai', 'anthropic', 'ollama', 'local'")
    api_key: Optional[str] = Field(None, description="Optional override API key for this request")
    model: Optional[str] = Field(None, description="Optional override model name")
    base_url: Optional[str] = Field(None, description="Optional custom base URL for OpenAI-compatible or Ollama endpoints")


class AIChatResponse(BaseModel):
    ok: bool = True
    reply: str
    mode: str
    provider: str


class AILLMProviderInfo(BaseModel):
    id: str
    name: str
    description: str
    default_model: str
    requires_key: bool
    is_configured: bool


class AILLMConfig(BaseModel):
    active_provider: str
    active_model: str
    available_providers: List[AILLMProviderInfo]
    has_gemini_key: bool
    has_openai_key: bool
    has_anthropic_key: bool
    has_ollama: bool


class AITestConnectionRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None


class AITestConnectionResponse(BaseModel):
    ok: bool
    provider: str
    model: str
    message: str
    latency_ms: Optional[float] = None



