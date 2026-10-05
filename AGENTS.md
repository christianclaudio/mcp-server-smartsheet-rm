---
vcs:
  system: github
  owner: christianclaudio
  repo: mcp-server-smartsheet-rm
  default_branch: main
  branch_policy: pr_only
---

# AGENTS.md

Instructions for AI coding agents (Antigravity, Claude Code, Copilot, Cursor, Windsurf) working on this repository or integrating Smartsheet Resource Management capabilities.

---

## 📚 Canonical Documentation & Live Doc MCPs

Before designing, implementing, or updating any MCP tool, always consult the official machine-readable documentation indexes ("the bibles") and live documentation MCP servers:

### Machine-Readable Documentation Indexes (`llms.txt`)
| Resource | URL | Focus Areas |
| :--- | :--- | :--- |
| **FastMCP 4 Framework** | [`https://gofastmcp.com/llms.txt`](https://gofastmcp.com/llms.txt) | Server composition (`mount`), hierarchical middleware, transforms, lifespans, in-memory testing |
| **Model Context Protocol (Official)** | [`https://modelcontextprotocol.io/llms.txt`](https://modelcontextprotocol.io/llms.txt) | Wire protocol spec, Streamable HTTP framing, tool annotations, elicitation |
| **Smartsheet RM Developer Portal** | [`https://developer.smartsheet.com/10000ft-api/`](https://developer.smartsheet.com/10000ft-api/) | Smartsheet RM (10,000ft) REST API reference, endpoints, schemas, authentication |

### Live Documentation MCP Servers
Both ecosystems publish live, queryable Documentation MCP servers exposing full search and doc navigation tools:

1. **FastMCP Documentation Server**:
   - **Endpoint**: `https://gofastmcp.com/mcp` (SSE / Streamable HTTP)
   - **Tools**: `search_fast_mcp(query)`, `query_docs_filesystem_fast_mcp(command)`, `submit_feedback(...)`
2. **Anthropic Model Context Protocol Server**:
   - **Endpoint**: `https://modelcontextprotocol.io/mcp` (SSE / Streamable HTTP)
   - **Tools**: `search_model_context_protocol(query)`, `query_docs_filesystem_model_context_protocol(command)`, `submit_feedback(...)`

---

## 🎯 Project Overview

This is `mcp-server-smartsheet-rm` — an enterprise Python Model Context Protocol (MCP) server covering the entire REST API surface for **Resource Management by Smartsheet** (formerly 10,000ft API). Bulk-destructive tools register only when enabled; the expected default and bulk tool counts live in `scripts/check_tool_contract.py`.

**Primary Purpose**:
Expose deep resource planning, allocation, time-tracking, project management, and budget telemetry to AI agents with strict enterprise safety gates, offline testing, and multi-tenant token isolation.

---

## 🏗️ Key Paths

- `src/smartsheet_rm_mcp/server.py` — FastMCP 4 root gateway: composition mounting, profiles, tool search.
- `src/smartsheet_rm_mcp/tools/{time,projects,admin}.py` — domain sub-servers; re-exported from `tools/__init__.py`.
- `src/smartsheet_rm_mcp/common.py` — client resolution, `@rm_tool` decorator, `_destructive_gate`, secret redaction, structured logging. `middleware.py` — gateway audit/readonly middleware and domain guardrails.
- `src/smartsheet_rm_mcp/client.py` — async HTTP client (`SmartsheetRMClient`). `errors.py` — structured exceptions and redaction. `config.py` — `SMARTSHEET_RM_*` settings.
- `scripts/check_tool_contract.py` — source of truth for expected tool counts and annotations. Do not hard-code tool counts elsewhere.
- `scripts/check_openapi_drift.py`, `scripts/check_conformance.sh` + `conformance-baseline.yml`, `scripts/determine_bump.py`.
- `tests/` — offline unit, layered-composition, and protocol tests.
- `.github/workflows/` — `ci.yml`, `release.yml`, `rm-drift-monitor.yml`, `dependabot-automerge.yml`.
- `server.json` (MCP Registry metadata), `Dockerfile`, `fastmcp.json`, `pyproject.toml`.

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
  - Read-only gating (`SMARTSHEET_RM_READONLY=1` or `--profile readonly`) enforces fail-closed write protection via `ReadOnlyGateMiddleware` and selective tool registration.
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
uv sync --locked --extra dev   # or uv pip install -e ".[dev]"

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

## 🔄 CI & Releases

CI is defined in `.github/workflows/ci.yml` (jobs: lint and types, tests on Python 3.10–3.13 at 100% coverage, tool contract, OpenAPI drift, build + `twine check`, protocol and conformance, CodeQL). The Docker image is built only in `release.yml`. Run the commands above before opening a PR. Scheduled upstream drift runs in `rm-drift-monitor.yml`.

Do not create tags or releases unless the maintainer asks.
