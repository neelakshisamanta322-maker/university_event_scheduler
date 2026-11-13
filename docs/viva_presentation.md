# Viva & Presentation Guide: LPU Event Scheduling & Resource Conflict Resolution System

## 1. Problem Statement
At large universities like Lovely Professional University (LPU), multiple academic departments (CSE, ECE, MBA, Biotechnology) concurrently compete for shared resources:
- Auditoriums, specialized labs, seminar rooms, and classrooms
- Limited faculty coordinators and specialized AV technicians
- Pooled hardware equipment (4K projectors, VR headsets, PA sound systems)
- Preferred timing windows and student convenience hours

Manual scheduling leads to double bookings, equipment shortages during peak hours, and frustration. This project delivers an automated, constraint-satisfaction based scheduling engine that guarantees 100% hard constraint satisfaction, optimizes soft preferences, and provides plain-English explainability for every assignment and rejection.

---

## 2. Technical Approach
1. **CSP Formulation**: Models the scheduling matrix as a discrete Constraint Satisfaction Problem with backtracking, forward checking, and branch-and-bound pruning.
2. **Search Heuristics**: Combines Priority Tiering (Critical > High > Normal > Low), Minimum Remaining Values (MRV), and the Degree Heuristic to prune search branches early.
3. **Hybrid Cross-Check**: Includes a parallel Google OR-Tools CP-SAT solver to cross-verify assignment quality and runtime performance.
4. **Machine Learning Pipeline (Scikit-Learn)**:
   - Attendance regressor flags venue overflow risks.
   - Conflict-risk classifier scores events before scheduling.
   - Learned slot-preference model incorporates historical student/faculty convenience into soft costs.
5. **Explainable AI Engine**: Diagnoses every resource bottleneck in plain English (e.g. who won the conflict, which rules were checked, and what alternative slots are recommended).
6. **What-If Scenario Simulator**: Allows coordinators to simulate priority or capacity changes and view side-by-side schedule diffs in real-time.

---

## 3. Why CSP + Heuristics? (Key Justifications)

| Approach | Advantages | Drawbacks | Why CSP was Chosen |
|---|---|---|---|
| **Greedy Heuristics** | Extremely fast ($O(N)$) | Gets stuck in deadlocks; cannot backtrack when conflicts arise | **Rejected**: Too brittle for high-contention university schedules. |
| **Genetic Algorithms** | Good for open-ended optimization | Non-deterministic; cannot guarantee 100% hard constraint satisfaction; slow convergence | **Rejected**: University exam and symposium schedules cannot tolerate random overlaps. |
| **Integer Linear Programming (ILP)** | Globally optimal | Difficult to generate per-event explainable justifications and human-readable counterfactuals | **Hybrid**: Used Google OR-Tools CP-SAT as a cross-check benchmark. |
| **CSP with Backtracking + MRV + Degree** | Deterministic, guarantees zero hard constraint violations, natural explainability generation, fast pruning | Exponential worst-case if unguided | **CHOSEN**: Guided by MRV and Priority Ordering, worst-case branches are pruned immediately ($< 20$ms execution). |

---

## 4. Key Demo Scenarios & Boundary Conflict Proof

### Conflict 1: Resource Contention (AI Symposium vs Startup Pitch Day)
- **Situation**: Both events request Day 0 at 10:00 AM.
  - Event 1: `AI/ML Research Symposium` (CSE Department) — Priority: **Critical**, needs Dr. Alice, Mr. Bob, 1 Projector, 1 PA System.
  - Event 3: `Startup Pitch Day` (MBA Department) — Priority: **High**, needs Mr. Bob, 1 Projector, 1 PA System.
- **Contention**: Mr. Bob cannot be in two places at once, and the university only has 1 Portable PA System.
- **Resolution**:
  - The solver's Priority Ordering selects the Critical event first, securing Central Auditorium, Mr. Bob, and the PA System.
  - Startup Pitch Day is safely rescheduled to 14:00 (or reported as conflicted) with complete plain-English attribution:
    > *"Won by AI/ML Research Symposium due to priority precedence. Mr. Bob and Portable PA System already reserved."*

### Conflict 2: Physical Venue Capacity Overflow (Mega Orientation)
- **Situation**: `500-Seat Mega Orientation` requests an audience of 500.
- **Contention**: The largest venue on campus is Central Auditorium (capacity: 300).
- **Resolution**:
  - Static pruning immediately detects impossibility before entering dynamic search:
    > *"Attendance overflow: Event requires 500 seats, but university's largest venue has capacity 300."*

---

## 5. Potential Viva / Interview Questions & Model Answers

### Q1: How does your system guarantee no two events share the same venue or staff?
**Answer**: In `Scheduler.dynamic_ok()`, every potential assignment is checked against active assignments. If $\text{cand.venue\_id} == \text{other.venue\_id}$ and time intervals overlap, or if $\text{set}(\text{required\_staff}) \cap \text{set}(\text{other.required\_staff}) \ne \emptyset$, the candidate is rejected. The test suite includes explicit tests for venue and staff non-overlap (`test_hard_constraint_venue_no_double_booking` and `test_hard_constraint_staff_availability_and_non_overlap`), both passing.

### Q2: What happens when the problem is mathematically unsatisfiable?
**Answer**: Rather than crashing or hanging, the scheduler employs a **skip-branch**. It finds the maximum feasible subset of events, schedules all possible events, and routes unsatisfiable events to the `unresolved` list. Each unresolved event is analyzed by `unresolved_reasons_and_attribution()` to identify the exact blocking constraint and recommend nearest alternative slots.

### Q3: What is the role of Machine Learning in your scheduling system?
**Answer**: The system uses three Scikit-Learn models trained on historical/synthetic university records:
1. `RandomForestRegressor` estimates actual turnout based on department and event type, warning coordinators if an auditorium is too small or oversized.
2. `RandomForestClassifier` scores pre-scheduling conflict risk (`Low`, `Medium`, `High`) based on resource scarcity index.
3. `Ridge` regression predicts student/faculty convenience scores across 24h intervals, integrating learned temporal preferences into the soft-cost objective function.

### Q4: How is Explainable AI achieved?
**Answer**: For each scheduled assignment, the engine compiles a structured trace of verified hard constraints (capacity, type, non-overlap, equipment stock) and soft costs incurred (preferred hour offset, late session penalties). For rejected events, it logs the specific winning event ID, the contested resource (venue, staff, or equipment), and the priority tie-breaking rule that determined the outcome.
