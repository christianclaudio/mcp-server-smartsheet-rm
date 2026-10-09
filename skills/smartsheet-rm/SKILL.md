---
name: smartsheet-rm
description: Enterprise Agent Skill for orchestrating Resource Management by Smartsheet (10,000ft API) — manage weekly timesheets, reconciliation, project staffing, assignments, capacity planning, and user availability.
---

# Smartsheet Resource Management (`mcp-server-smartsheet-rm`) Agent Skill

This skill provides expert instructions, architectural workflows, and safety protocols for AI agents (Claude, Antigravity, Cortex, VS Code) orchestrating **Resource Management by Smartsheet** (10,000ft API) via `mcp-server-smartsheet-rm`.

---

## 🎯 Core Agent Workflows

### 1. Weekly Timesheet Filling & Auto-Suggestions Workflow
- **Step 1: Discover Active Assignments** — Call `projects_list_assignments(user_id=..., from_date="YYYY-MM-DD", to_date="YYYY-MM-DD")` to discover active projects and phases.
- **Step 2: Inspect Scheduled Suggestions** — Call `time_list_user_suggestions(user_id=..., from_date="YYYY-MM-DD", to_date="YYYY-MM-DD")` to preview scheduled hours vs logged time.
- **Step 3: Auto-Confirm or Batch Log** — Use `time_confirm_suggested_hours(user_id=..., from_date=..., to_date=...)` to convert schedule suggestions into confirmed time entries, or `time_fill_weekly_timesheet(user_id=..., start_date="YYYY-MM-DD", daily_hours=8.0)` to log 8h/day (Mon–Fri) across project assignments in 1 call.

### 2. Timesheet Reconciliation, Approvals & Month-End Lock
- **Step 1: Audit Capacity Variance** — Call `time_reconcile_and_submit_week(user_id=..., start_date="YYYY-MM-DD", target_hours=40.0, auto_submit=False)` to check logged hours against the 40-hour standard baseline.
- **Step 2: Manager Approvals** — Call `time_update_time_approval_status(user_id=..., entry_ids=[...], status="approved", approver_notes="Approved weekly time")`.
- **Step 3: Lock Timesheets** — Use `time_lock_timesheet(user_id=..., lock_date="YYYY-MM-DD", unlock=False)` to prevent retroactive edits after billing closes.

### 3. Project Staffing, Phases & Schedule Cloning
- **Template Duplication** — Call `projects_clone_project_schedule(source_project_id=..., target_project_name="Client Rollout", new_start_date="YYYY-MM-DD")` to duplicate project budget settings, phase milestones, and staffing allocations in 1 call.
- **Phase Milestones** — Create and maintain project milestones with `projects_create_project_phase` and `projects_update_project_phase`.
- **Resource Allocation** — Assign users to phases using `projects_create_assignment` with `allocation_mode="percent"` or `hours_per_day`.

### 4. Capacity Planning & Utilization Analysis
- **Availability Matrix** — Query scheduled vs available capacity via `admin_get_user_availability(user_id=..., from_date="YYYY-MM-DD", to_date="YYYY-MM-DD")`.
- **Billable Utilization** — Query billable vs non-billable utilization percentages with `admin_get_user_utilization(user_id=..., from_date="YYYY-MM-DD", to_date="YYYY-MM-DD")`.
- **Role & Discipline Scaling** — Manage staffing tiers with `admin_list_roles`, `admin_list_disciplines`, and `admin_create_user_bill_rate`.

---

## 🛡️ Safety & Execution Rules for AI Agents

1. **Confirmation Gating on Destructive Tools**:
   All destructive deletion tools **MUST** explicitly receive `confirm=True` to execute. Calls with `confirm=False` (default) are automatically rejected:
   - `time_delete_time_entry`
   - `projects_delete_project`, `projects_delete_project_phase`
   - `projects_delete_assignment`
   - `admin_delete_user`, `admin_delete_role`, `admin_delete_discipline`
   - `admin_delete_client`, `admin_delete_client_contact`
   - `admin_delete_leave_type`, `admin_delete_holiday`
   - `admin_delete_expense`, `admin_delete_expense_category`
   - `admin_delete_tag`, `admin_delete_custom_field`
   - `time_delete_approval`, `projects_delete_placeholder_resource`
   - `projects_delete_assignment_subtask`, `admin_delete_webhook`

2. **Bulk-Destructive Safety Gating**:
   `time_bulk_delete_time_entries` and `projects_bulk_delete_assignments` are listed in `full` but run only with **both**:
   - Environment variable `SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1` on the server (otherwise the call is refused with `isError: true`)
   - Parameter `confirm=True` on invocation

3. **Secret Protection & Redaction**:
   - Never log, echo, or store `SMARTSHEET_RM_API_TOKEN` or raw authorization headers. All errors and output automatically scrub credentials.

4. **Profile Selection**:
   Load only the tools the job needs by setting `SMARTSHEET_RM_PROFILE` or `--profile`. Prompts and resources stay available on every job profile and on `readonly`; the domain-mount profiles `time`, `projects` and `admin` carry only their own domain's prompts and resources.
   - Job profiles (tools across domains):
     - `timesheets` (22 tools): log, correct, submit, lock and approve weekly time against assignments.
     - `staffing` (25 tools): check availability and utilization; create or adjust assignments, placeholders and subtasks.
     - `org_setup` (35 tools): onboard people; maintain bill rates, roles, disciplines, leave types, holidays, custom-field definitions, user statuses and webhooks.
     - `portfolio` (33 tools): set up and maintain projects, phases, clients and expenses; read budget and report totals.
   - Domain profiles: `time` (15 tools), `projects` (25 tools), `admin` (60 tools).
   - `readonly` (39 tools): every tool annotated `readOnlyHint=true`, across all domains.
   - `full` (100 tools): the complete catalog. Seven tools are only here (or in their domain profile): the two bulk deletes, `admin_delete_client`, `admin_delete_client_contact`, `admin_create_expense_category`, `admin_delete_expense_category` and `admin_delete_tag`.

5. **Read-Only Mode**:
   `SMARTSHEET_RM_READONLY=1` keeps only the read-only tools of the active profile. Any other call is refused with `isError: true`. An unknown name returns `Unknown tool`, so check the spelling rather than assuming the tool is blocked.

6. **Tool Search and Code Mode (`full` only)**:
   On `full`, `--enable-tool-search` / `SMARTSHEET_RM_ENABLE_TOOL_SEARCH=1` replaces the list with `search_tools` and `call_tool` (`regex` or `bm25` backend). `--enable-code-mode` / `SMARTSHEET_RM_ENABLE_CODE_MODE=1` attaches experimental Code Mode instead; the two cannot be combined. Code Mode needs the `fastmcp[code-mode]` extra on the server (for example `uvx --with "fastmcp[code-mode]" mcp-server-smartsheet-rm --enable-code-mode`); without it the server logs a warning, skips Code Mode, and keeps the flat list, so `execute` is not listed. Under read-only, writes through `call_tool` and Code Mode `execute` are refused. On other profiles both flags are ignored with a warning.

---

## 📚 Resources & Guided Prompts

### Resources
- `rm://admin/capabilities` — Full documentation of server metadata, supported domains, authentication, and base URL.
- `rm://admin/quickstart` — Quickstart reference guide and common orchestration recipes.

### Prompts
- `time_timesheet_reconciliation(user_id=..., week_start_date=...)` — Step-by-step assistant guide for auditing and balancing weekly logged time against 40-hour capacity targets.
- `projects_project_staffing_plan(project_id=...)` — Checklist for analyzing project phases, allocations, and discipline bottlenecks.

---

## 🏗️ FastMCP 4 Architecture & Server Composition

The server is engineered as a modular, layered FastMCP 4 composition with domain sub-servers mounted onto a root FastMCP gateway with native domain namespaces:

```
FastMCP Gateway (create_server)
├── Global Middleware Pipeline
│   ├── ParentAuditMiddleware (timing, structured JSON logging, secret scrubbing)
│   └── ReadOnlyGateMiddleware (readOnlyHint-only gate; refuses writes, fails closed)
├── Mounted Domain Sub-Servers (Selective Namespace Mounting)
│   ├── Time Sub-Server (tools/time.py, namespace="time", 15 tools + time_timesheet_reconciliation prompt)
│   │   └── TimeDomainGuardMiddleware (hours range validation, bulk gate)
│   ├── Projects Sub-Server (tools/projects.py, namespace="projects", 25 tools + projects_project_staffing_plan prompt)
│   │   └── ProjectsDomainGuardMiddleware (non-empty naming validation, bulk gate)
│   └── Admin Sub-Server (tools/admin.py, namespace="admin", 60 tools + rm://admin/* resources)
│       └── AdminDomainGuardMiddleware (pagination batch cap validation)
└── Configuration & Safety Filtering
    ├── SmartsheetRMSettings (Pydantic BaseSettings binding SMARTSHEET_RM_*)
    ├── Profiles (profiles.py): domain mounts full/time/projects/admin/readonly + job allowlists timesheets/staffing/org_setup/portfolio
    ├── ReadOnlyToolFilter (readOnlyHint=True only, when readonly)
    ├── Bulk-Destructive Safety Gate (call time, SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1)
    └── Discovery on full only: Tool Search (regex/bm25) XOR experimental Code Mode
```

### Component Breakdown

| Layer | Component | Responsibility |
| :--- | :--- | :--- |
| **Settings** | `SmartsheetRMSettings` (`config.py`) | Strongly-typed configuration bound to `SMARTSHEET_RM_*` environment variables. |
| **Common** | `@rm_tool` & `get_client` (`common.py`) | Standardized execution wrapper, client caching, and fail-closed secret redaction. A failed call returns the redacted `{"error": ...}` JSON with `isError: true`. |
| **Middleware** | `ParentAuditMiddleware`, `ReadOnlyGateMiddleware`, Guards (`middleware.py`) | Hierarchical invocation logging, read-only gating, and domain argument validation. |
| **Domain Tools** | `time.py`, `projects.py`, `admin.py` (`tools/`) | Autonomous domain sub-servers with dedicated tools, prompts, and resources. |
| **Profiles** | `PROFILES`, `FULL_ONLY_TOOLS`, `ReadOnlyToolFilter` (`profiles.py`) | Domain-mount and job allowlist profiles; annotation-driven read-only filtering. |
| **Root Gateway** | `create_server()` (`server.py`) | Factory composing sub-servers via `mount(..., namespace="...")`, applying job allowlists, the read-only filter, and full-only discovery transforms. |

