# 📋 mcp-server-smartsheet-rm

[![CI](https://github.com/christianclaudio/mcp-server-smartsheet-rm/actions/workflows/ci.yml/badge.svg)](https://github.com/christianclaudio/mcp-server-smartsheet-rm/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mcp-server-smartsheet-rm)](https://pypi.org/project/mcp-server-smartsheet-rm/)
[![Python](https://img.shields.io/pypi/pyversions/mcp-server-smartsheet-rm)](https://pypi.org/project/mcp-server-smartsheet-rm/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](https://github.com/christianclaudio/mcp-server-smartsheet-rm)
[![CodeRabbit Reviews](https://img.shields.io/coderabbit/prs/github/christianclaudio/mcp-server-smartsheet-rm?labelColor=171717&color=FF570A&label=CodeRabbit+Reviews)](https://coderabbit.ai)

Enterprise Model Context Protocol (MCP) server for **Resource Management by Smartsheet** (10,000ft API).

Enables AI coding agents, planners, and assistants (Claude, Cortex, Antigravity, VS Code) to orchestrate the complete Smartsheet RM REST API surface: time tracking & timesheet reconciliation, resource scheduling & allocations, capacity planning, project & phase management, leaves/holidays, expense tracking, and custom fields.

---

## ⚡ Tool Surface Overview

The server exposes tools covering projects, resources, timesheets, and capacity:
- **Default Registration**: 98 tools (with bulk-destructive operations gated by default).
- **With Bulk Operations**: 100 total tools when `SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1`.
- **Read-Only Mode**: 39 tools (`readOnlyHint=true`).
- **Destructive Gates**: 21 tools requiring explicit `confirm=True` (19 standard + 2 bulk).
- **Idempotent Operations**: 4 tools with `idempotentHint=true`.

### Domain Breakdown
_Counts below include the 2 bulk-destructive tools (100 total)._
1. **Time Tracking & Approvals (8 tools)**: List/get/create/update/delete time entries, fetch suggestions, approve/reject hours, lock timesheets.
2. **Projects & Phases (11 tools)**: Full CRUD for projects, project users, milestones, budgets, and project phases.
3. **Assignments & Scheduling (5 tools)**: Full CRUD for resource assignments, percentages, fixed hours, and schedules.
4. **Users, Roles & Capacity (17 tools)**: User management, bill rate tiers, availability queries, utilization metrics, roles, and disciplines.
5. **Clients & Contacts (8 tools)**: Client organization CRUD and client contact records.
6. **Leaves & Holidays (10 tools)**: Vacation/PTO leave types, non-working days, and regional company holidays.
7. **Expense Tracking (8 tools)**: Expense submissions, category definitions, and billable tracking.
8. **Tags & Custom Fields (10 tools)**: Custom field definitions, field values, and tagging.
9. **Approvals, Statuses & Placeholders (9 tools)**: Organization approvals, user work status tracking (ITO/WFH/OOO/VAC), and placeholder resources.
10. **Subtasks, Reports & Webhooks (8 tools)**: Assignment task checklists, custom report generation (rows/totals), and event webhooks.
11. **Composite Workflow Recipes (6 tools)**: Bulk timesheet logging, auto-suggestion confirmation, 40h reconciliation audit, project schedule cloning, and bulk deletion.

---

## 🚀 Quickstart & Installation

### 1. Installation

```bash
# Using uv (recommended)
uv pip install mcp-server-smartsheet-rm

# Or standard pip
pip install mcp-server-smartsheet-rm
```

### 2. Environment Variables

| Variable | Description | Default |
| :--- | :--- | :--- |
| `SMARTSHEET_RM_API_TOKEN` | Smartsheet RM (10,000ft) API Token (**Required**) | - |
| `SMARTSHEET_RM_BASE_URL` | Base API URL | `https://api.rm.smartsheet.com/api/v1` |
| `SMARTSHEET_RM_PROFILE` | Tool profile subset: `time`, `projects`, `admin`, `full` | `full` |
| `SMARTSHEET_RM_READONLY` | Set to `1` to restrict server to read-only tools | `0` |
| `SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE` | Set to `1` to unlock bulk delete operations | `0` |
| `SMARTSHEET_RM_LOG_FORMAT` | Set to `json` for Datadog/CloudWatch structured logs | `text` |

---

## 💻 Client Configurations

### Google Antigravity (`~/.gemini/antigravity-cli/mcp_config.json`)

```json
{
  "mcpServers": {
    "smartsheet-rm": {
      "command": "uvx",
      "args": ["mcp-server-smartsheet-rm"],
      "env": {
        "SMARTSHEET_RM_API_TOKEN": "your-api-token"
      },
      "lazy": true
    }
  }
}
```

### Snowflake Cortex (`~/.snowflake/cortex/mcp.json`)

```json
{
  "servers": {
    "smartsheet-rm": {
      "command": "uvx",
      "args": ["mcp-server-smartsheet-rm"],
      "env": {
        "SMARTSHEET_RM_API_TOKEN": "your-api-token"
      }
    }
  }
}
```

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "smartsheet-rm": {
      "command": "uvx",
      "args": ["mcp-server-smartsheet-rm"],
      "env": {
        "SMARTSHEET_RM_API_TOKEN": "your-api-token"
      }
    }
  }
}
```

---

## 🛡️ Safety & Reliability

- **Secret Redaction**: API tokens, bearer headers, and sensitive keys are automatically scrubbed from errors and logs.
- **Destructive Gates**: Every deletion tool declares `confirm: bool = False` and rejects execution unless the caller explicitly passes `confirm=True`.
- **Profile Filtering**: Minimize token footprint by loading only relevant tool sets (`time`, `projects`, `admin`).
- **Resilience**: Exponential backoff with randomized jitter on HTTP 429 rate limits.

---

## 🧪 Testing & Validation

```bash
# Run tests with 100% coverage requirement
pytest --cov=src/smartsheet_rm_mcp --cov-fail-under=100 -v

# Run Tool Contract verification
python scripts/check_tool_contract.py

# Run OpenAPI Drift check
python scripts/check_openapi_drift.py
```

<!-- mcp-name: io.github.christianclaudio/smartsheet-rm -->
