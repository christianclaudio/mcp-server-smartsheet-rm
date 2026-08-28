---
name: smartsheet-rm
description: Enterprise Agent Skill for orchestrating Resource Management by Smartsheet (10,000ft API) — manage weekly timesheets, reconciliation, project staffing, assignments, capacity planning, and user availability.
---

# Smartsheet Resource Management (`mcp-server-smartsheet-rm`) Agent Skill

This skill provides expert instructions, architectural workflows, and safety protocols for AI agents (Claude, Antigravity, Cortex, VS Code) orchestrating **Resource Management by Smartsheet** (10,000ft API) via `mcp-server-smartsheet-rm`.

---

## 🎯 Core Agent Workflows

### 1. Weekly Timesheet Filling & Auto-Suggestions Workflow
- **Step 1: Discover Active Assignments** — Call `rm_list_assignments(user_id=..., from_date="YYYY-MM-DD", to_date="YYYY-MM-DD")` to discover active projects and phases.
- **Step 2: Inspect Scheduled Suggestions** — Call `rm_list_user_suggestions(user_id=..., from_date="YYYY-MM-DD", to_date="YYYY-MM-DD")` to preview scheduled hours vs logged time.
- **Step 3: Auto-Confirm or Batch Log** — Use `rm_confirm_suggested_hours(user_id=..., from_date=..., to_date=...)` to convert schedule suggestions into confirmed time entries, or `rm_fill_weekly_timesheet(user_id=..., start_date="YYYY-MM-DD", daily_hours=8.0)` to log 8h/day (Mon–Fri) across project assignments in 1 call.

### 2. Timesheet Reconciliation, Approvals & Month-End Lock
- **Step 1: Audit Capacity Variance** — Call `rm_reconcile_and_submit_week(user_id=..., start_date="YYYY-MM-DD", target_hours=40.0, auto_submit=False)` to check logged hours against the 40-hour standard baseline.
- **Step 2: Manager Approvals** — Call `rm_update_time_approval_status(user_id=..., entry_ids=[...], status="approved", approver_notes="Approved weekly time")`.
- **Step 3: Lock Timesheets** — Use `rm_lock_timesheet(user_id=..., lock_date="YYYY-MM-DD", unlock=False)` to prevent retroactive edits after billing closes.

### 3. Project Staffing, Phases & Schedule Cloning
- **Template Duplication** — Call `rm_clone_project_schedule(source_project_id=..., target_project_name="Client Rollout", new_start_date="YYYY-MM-DD")` to duplicate project budget settings, phase milestones, and staffing allocations in 1 call.
- **Phase Milestones** — Create and maintain project milestones with `rm_create_project_phase` and `rm_update_project_phase`.
- **Resource Allocation** — Assign users to phases using `rm_create_assignment` with `allocation_mode="percent"` or `hours_per_day`.

### 4. Capacity Planning & Utilization Analysis
- **Availability Matrix** — Query scheduled vs available capacity via `rm_get_user_availability(user_id=..., from_date="YYYY-MM-DD", to_date="YYYY-MM-DD")`.
- **Billable Utilization** — Query billable vs non-billable utilization percentages with `rm_get_user_utilization(user_id=..., from_date="YYYY-MM-DD", to_date="YYYY-MM-DD")`.
- **Role & Discipline Scaling** — Manage staffing tiers with `rm_list_roles`, `rm_list_disciplines`, and `rm_create_user_bill_rate`.

---

## 🛡️ Safety & Execution Rules for AI Agents

1. **Confirmation Gating on Destructive Tools**:
   All destructive deletion tools **MUST** explicitly receive `confirm=True` to execute. Calls with `confirm=False` (default) are automatically rejected:
   - `rm_delete_time_entry`
   - `rm_delete_project`, `rm_delete_project_phase`
   - `rm_delete_assignment`
   - `rm_delete_user`, `rm_delete_role`, `rm_delete_discipline`
   - `rm_delete_client`, `rm_delete_client_contact`
   - `rm_delete_leave_type`, `rm_delete_holiday`
   - `rm_delete_expense`, `rm_delete_expense_category`
   - `rm_delete_tag`, `rm_delete_custom_field`
   - `rm_delete_approval`, `rm_delete_placeholder_resource`
   - `rm_delete_assignment_subtask`, `rm_delete_webhook`

2. **Bulk-Destructive Safety Gating**:
   `rm_bulk_delete_time_entries` and `rm_bulk_delete_assignments` require **both**:
   - Environment variable `SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1` set at server startup
   - Parameter `confirm=True` on invocation

3. **Secret Protection & Redaction**:
   - Never log, echo, or store `SMARTSHEET_RM_API_TOKEN` or raw authorization headers. All errors and output automatically scrub credentials.

4. **Profile Selection**:
   Minimize token context in LLM prompts by setting `SMARTSHEET_RM_PROFILE`:
   - `time`: Time tracking, PTO, suggestions, approvals, and timesheet recipes.
   - `projects`: Projects, phases, milestones, assignments, and schedule cloning.
   - `admin`: Users, roles, disciplines, clients, expenses, tags, custom fields.
   - `full`: Complete 80+ tool surface.

---

## 📚 Resources & Guided Prompts

### Resources
- `rm://capabilities` — Full documentation of server metadata, supported domains, authentication, and base URL.
- `rm://quickstart` — Quickstart reference guide and common orchestration recipes.

### Prompts
- `timesheet_reconciliation(user_id=..., week_start_date=...)` — Step-by-step assistant guide for auditing and balancing weekly logged time against 40-hour capacity targets.
- `project_staffing_plan(project_id=...)` — Checklist for analyzing project phases, allocations, and discipline bottlenecks.
