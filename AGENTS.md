# AGENTS.md

Instructions for AI coding agents (Antigravity, Claude Code, Copilot, Cursor, Windsurf) working on this repository or integrating Smartsheet Resource Management capabilities.

---

## 🎯 Project Overview

This is `mcp-server-smartsheet-rm` — an enterprise Python Model Context Protocol (MCP) server exposing 98 tools by default (100 with bulk-destructive operations enabled) covering the entire REST API surface for **Resource Management by Smartsheet** (formerly 10,000ft API).

**Primary Purpose**:
Expose deep resource planning, allocation, time-tracking, project management, and budget telemetry to AI agents with strict enterprise safety gates, offline testing, and multi-tenant token isolation.

---

## 🏗️ Architecture Blueprint

```
mcp-server-smartsheet-rm/
├── src/smartsheet_rm_mcp/
│   ├── __init__.py               # Package version (__version__) and public exports
│   ├── server.py                 # FastMCP 4 root gateway, composition mounting, profiles, tool search
│   ├── middleware.py             # Gateway audit/readonly middleware & domain guardrails
│   ├── common.py                 # Client resolution, decorator (@rm_tool), secret redaction, structured logging
│   ├── config.py                 # Pydantic settings with SMARTSHEET_RM_* env bindings
│   ├── client.py                 # Async HTTP client (httpx.AsyncClient, retries, jitter, auth headers)
│   ├── errors.py                 # Structured API exceptions and regex secret redaction
│   └── tools/                    # Modular domain sub-servers
│       ├── __init__.py           # Sub-server and tool function re-exports
│       ├── time.py               # Time tracking, suggestions, approval, timesheets sub-server
│       ├── projects.py           # Projects, phases, assignments, placeholders sub-server
│       └── admin.py              # Users, roles, clients, expenses, tags, reports sub-server
├── scripts/
│   ├── check_conformance.sh      # MCP Protocol conformance suite verification script
│   ├── check_tool_contract.py    # AST/reflection contract testing total tool & annotation counts
│   ├── check_openapi_drift.py    # AST visitor checking client methods against upstream API routes
│   └── determine_bump.py         # SemVer release bump recommendation based on git log
├── tests/
│   ├── conftest.py               # Shared fixtures and mock HTTP transports (offline only)
│   ├── test_client.py            # Unit tests for HTTP client, retries, headers, and error handling
│   ├── test_determine_bump.py    # Unit tests for determine_bump.py SemVer calculation
│   ├── test_server.py            # Tests for tool execution, parameter validation, and confirmation gating
│   ├── test_layered.py           # FastMCP 4 composition, profiles, middleware, and domain guard tests
│   ├── test_errors.py            # Tests for error formatting and regex credential redaction
│   ├── test_scripts.py           # Unit tests for contract and drift validation scripts
│   └── test_protocol.py          # FastMCP 4 in-memory & stdio/streamable HTTP protocol verification
├── .github/workflows/
│   ├── ci.yml                    # Multi-job matrix: lint, py3.10-3.13 tests, contracts, CodeQL, docker build
│   ├── release.yml               # Automated release on v* tags: wheels, sdist, CycloneDX SBOM, GHCR docker
│   └── drift-monitor.yml         # Scheduled upstream schema drift check
├── Dockerfile                    # Multi-stage container build running as non-root USER mcp
├── fastmcp.json                  # FastMCP 4 deployment and execution manifest
├── conformance-baseline.yml      # Conformance suite baseline expected failures
├── server.json                   # MCP Registry catalog metadata (runtimeHint: uvx, stdio transport)
├── pyproject.toml                # Packaging metadata, entrypoint CLI, dependency pinning
├── AGENTS.md                     # Agent guidance map, gotchas, and conventions (this file)
└── README.md                     # User-facing installation, quickstart, and tool index
```

---

## ⚡ The Canonical Workflow: Building Tools from API / llm.txt

When translating an API documentation page or endpoint into an MCP tool, follow these 4 steps in exact order:

### 1. Client Method (`client.py`)
- Implement a dedicated `async def` method on `SmartsheetRMClient`.
- Type all arguments strictly. Never use bare `dict` or `Any` when a concrete schema or literal is known.
- URL path parameters **must** be safely formatted and escaped.
- Call `await self._request("METHOD", path, params=..., json=...)`.

### 2. Tool Handler (`tools/time.py`, `tools/projects.py`, `tools/admin.py`)
- Register the tool with `@server.tool(name=..., annotations=...)` in the appropriate domain sub-server module and wrap with `@rm_tool`.
- Provide an explicit, agent-friendly docstring describing capabilities, parameters, and return shape.
- Destructive operations (`POST`, `PUT`, `PATCH`, `DELETE` mutating state) **must** accept `confirm: bool = False`.

### 3. Tool Annotations & Composition Mounting
- Declare native MCP `ToolAnnotations` directly at tool registration in each domain sub-server:
  - `readOnlyHint`: `True` for inspection/GET; `False` for mutations.
  - `destructiveHint`: `True` for delete/archive/deactivate actions; `False` otherwise.
  - `idempotentHint`: `True` for GET, PUT, idempotent operations; `False` for creations.
  - `openWorldHint`: `True` when interacting with external networks/APIs.
- FastMCP 4 Server Composition:
  - Root gateway in `server.py` selectively mounts domain sub-servers with native domain namespaces (`namespace="time"`, `namespace="projects"`, `namespace="admin"`).
  - Profile filtering (`SMARTSHEET_RM_PROFILE`: `time`, `projects`, `admin`, `full`, `readonly`) is achieved via selective mounting at composition time.
  - Read-only gating (`SMARTSHEET_RM_READONLY=1` or `--readonly`) enforces fail-closed write protection via `ReadOnlyGateMiddleware` and selective tool registration.
  - Bulk protection (`SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1`) controls inclusion of bulk deletion tools (`time_bulk_delete_time_entries`, `projects_bulk_delete_assignments`).

### 4. Pure Offline Testing & Contract Sync (`tests/`)
- Add unit tests in `tests/` mocking responses via `respx` or `httpx.MockTransport`.
- **Zero live network calls during tests.** Tests must run 100% offline in CI.
- Update expected tool count in `scripts/check_tool_contract.py` and `README.md`.
- Ensure test statement and branch coverage remains at **100.0%**.

---

## 🛡️ Non-Negotiable Safety & Security Rules

1. **Destructive Confirmation Gate**:
   - Every destructive tool (e.g. deletion, bulk removal, or permanent deallocation) must accept `confirm: bool = False` and invoke `_destructive_gate`. If `False`, return a structured error document requiring explicit confirmation before executing side effects. Non-destructive mutations (creates, updates, workflows) are protected by `ReadOnlyGateMiddleware` without requiring interactive confirmation.
2. **Secret Redaction**:
   - Error messages, logs, and tracebacks must pass through regex redaction (`_redact_secrets`) stripping Bearer tokens, passwords, and API keys.
3. **Multi-Stage Non-Root Containers**:
   - The `Dockerfile` must use a multi-stage build creating and executing under non-root `USER mcp` with virtual environment `/opt/venv`.
4. **Registry Metadata Constraint**:
   - In `server.json`, the root `description` must be **strictly $\le$ 100 characters** to pass MCP Registry schema validation (longer strings trigger HTTP 422).
   - Default package transport is `stdio`, and packages must specify `"runtimeHint": "uvx"`.
5. **Git Safety**:
   - Never commit API tokens or secrets.
   - All changes proceed via feature branches and PRs.

---

## 🛠️ Development & Verification Commands

```bash
# Install editable with dev dependencies
uv sync --extra dev   # or uv pip install -e ".[dev]"

# Lint and formatting
uv run ruff check . && uv run ruff format --check .

# Strict type checking
uv run mypy --strict src/

# Test suite with 100% coverage requirement
uv run pytest --cov=src/smartsheet_rm_mcp --cov-fail-under=100 -v

# Tool contract verification
uv run python scripts/check_tool_contract.py

# Upstream OpenAPI / route drift check
uv run python scripts/check_openapi_drift.py

# Protocol integration tests (stdio handshake & stateless streamable HTTP)
uv run pytest tests/test_protocol.py

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent --uncommitted
```

---

## 🔄 CI/CD Matrix & Operational Release SOP

The GitHub Actions CI matrix enforces:
- Ruff lint & format checks.
- Mypy `--strict` type checks.
- Python 3.10, 3.11, 3.12, 3.13 test matrix with 100% coverage.
- Tool contract & OpenAPI drift validation.
- Multi-stage Docker image build.
- CodeQL security scan.

For release automation and packaging, push matching `v*` tags aligned with `pyproject.toml`'s `project.version` to trigger `.github/workflows/release.yml`.
