# AGENTS.md

Instructions for AI coding agents (Claude Code, Copilot, Cursor, Antigravity, Windsurf) working on this repository.

## Project Overview

This is `mcp-server-smartsheet-rm` — an enterprise Python Model Context Protocol (MCP) server exposing 98 tools by default (100 with bulk-destructive operations enabled) covering the entire REST API surface for **Resource Management by Smartsheet** (10,000ft API).

## Architecture

```
src/smartsheet_rm_mcp/
├── server.py      # MCP tool definitions, @mcp.tool() handlers, prompts, resources
├── client.py      # Async HTTP client (auth header, 429 jitter backoff, retries)
├── errors.py      # Structured API errors and automated secret redaction
└── __init__.py    # Version only
```

## Key Patterns

- **Every tool** is an `async def` decorated with `@mcp.tool()` and `@rm_tool`
- `@rm_tool` decorator wraps tools with timing metrics, structured JSON logging, and exception handling
- **Destructive tools** require `confirm: bool = False` — reject if not `True`
- **Composite tools** (`rm_fill_weekly_timesheet`, `rm_confirm_suggested_hours`, `rm_reconcile_and_submit_week`, `rm_clone_project_schedule`) orchestrate multi-step workflows
- **Annotations** are applied post-registration via `mcp._tool_manager._tools`
- **Profile Filtering**: `SMARTSHEET_RM_PROFILE` (`time`, `projects`, `admin`, `full`)
- **Read-Only Mode**: `SMARTSHEET_RM_READONLY=1` filters out all non-read-only tools at startup
- **Bulk Gating**: `SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1` gates bulk deletion tools

## Development Commands

```bash
# Install editable in virtual environment
uv pip install -e ".[dev]"

# Lint + format check
ruff check . && ruff format --check .

# Type check
mypy --strict src/

# Tests (100% statement & branch coverage required)
pytest --cov=src/smartsheet_rm_mcp --cov-fail-under=100 -v

# Tool contract validation
python scripts/check_tool_contract.py

# OpenAPI drift check
python scripts/check_openapi_drift.py
```

## Adding a New Tool

1. Add the client method in `client.py` (typed, async).
2. Add the `@mcp.tool()` handler in `server.py` with docstring and `@rm_tool`.
3. Add tool name to `_DESTRUCTIVE_NAMES`, `_IDEMPOTENT_NAMES`, or let it default to read-only/write-safe.
4. Update `scripts/check_tool_contract.py` expected counts and `README.md`.
5. Maintain 100% test coverage in `tests/test_client.py` and `tests/test_server.py`.
