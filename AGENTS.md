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

This is `mcp-server-smartsheet-rm` — an enterprise Python Model Context Protocol (MCP) server covering the entire REST API surface for **Resource Management by Smartsheet** (formerly 10,000ft API). The default `full` profile lists all 100 tools; the two bulk-destructive tools are listed but refused at call time unless enabled. Expected per-profile counts live in `scripts/check_tool_contract.py`.

**Primary Purpose**:
Expose deep resource planning, allocation, time-tracking, project management, and budget telemetry to AI agents with strict enterprise safety gates, offline testing, and multi-tenant token isolation.

---

## 🏗️ Key Paths

- `src/smartsheet_rm_mcp/server.py` — FastMCP 4 root gateway (`create_server`): domain mounts, job allowlists, read-only filter, Tool Search / Code Mode (full only).
- `src/smartsheet_rm_mcp/profiles.py` — `PROFILES` (each with a one-line `job`), `FULL_ONLY_TOOLS`, `ReadOnlyToolFilter`, `is_read_only_tool`.
- `src/smartsheet_rm_mcp/tools/{time,projects,admin}.py` — domain sub-servers; re-exported from `tools/__init__.py`.
- `src/smartsheet_rm_mcp/common.py` — client resolution, `@rm_tool` decorator, `_destructive_gate`, secret redaction, structured logging. `middleware.py` — gateway audit middleware, the annotation-driven `ReadOnlyGateMiddleware`, and domain guardrails (including the bulk gate).
- `src/smartsheet_rm_mcp/client.py` — async HTTP client (`SmartsheetRMClient`). `errors.py` — structured exceptions and redaction. `config.py` — `SMARTSHEET_RM_*` settings.
- `scripts/check_tool_contract.py` — source of truth for expected tool counts and annotations. Do not hard-code tool counts elsewhere.
- `scripts/check_openapi_drift.py`, `scripts/check_conformance.sh` + `conformance-baseline.yml`, `scripts/determine_bump.py`.
- `tests/` — unit tests are offline; live network tests live in `tests/test_e2e_live.py` (run via `-m e2e`). Default pytest `addopts` deselect that marker with `-m 'not e2e'`; `test_all_discovered_tools_live` skips unless `SMARTSHEET_RM_API_TOKEN` is set. Offline modules: `tests/test_client.py`, `tests/test_errors.py`, `tests/test_server.py`, `tests/test_layered.py`, `tests/test_profiles.py`, `tests/test_protocol.py`, `tests/test_scripts.py`, `tests/test_determine_bump.py`. Unmarked `test_dispatch_tool_call_offline` in `tests/test_e2e_live.py` stays in the default run.
- `.github/workflows/` — `ci.yml`, `release.yml`, `rm-drift-monitor.yml`, `dependabot-automerge.yml`.
- `server.json` (MCP Registry metadata), `Dockerfile`, `fastmcp.json`, `pyproject.toml`.

---

## ⚡ The Canonical Workflow: Building Tools from API / llm.txt

When translating an API documentation page or endpoint into an MCP tool, follow these 4 steps in exact order:

### 1. Client Method (`client.py`)
- Implement a dedicated `async def` method on `SmartsheetRMClient`.
- Type all arguments strictly. Never use bare `dict` or `Any` when a concrete schema or literal is known.
- URL path parameters **must** be safely formatted and escaped.
- Call `await self._request("METHOD", path, params=..., json_data=...)`.

### 2. Tool Handler (`tools/time.py`, `tools/projects.py`, `tools/admin.py`)
- Register the tool with `@server.tool(name=..., annotations=...)` in the appropriate domain sub-server module and wrap with `@rm_tool`.
- Provide an explicit, agent-friendly docstring describing capabilities, parameters, and return shape.
- Destructive delete and bulk-delete tools **must** accept `confirm: bool = False` and call `_destructive_gate`. Creates, updates, and other non-destructive mutations do not take `confirm`.

### 3. Tool Annotations & Composition Mounting
- Declare native MCP `ToolAnnotations` directly at tool registration in each domain sub-server:
  - `readOnlyHint`: `True` for inspection/GET; `False` for mutations.
  - `destructiveHint`: `True` for delete and bulk-delete tools; `False` otherwise (archive-field updates stay non-destructive).
  - `idempotentHint`: `True` for GET/list and for explicitly idempotent writes; `False` for creations, other updates, and deletes.
  - `openWorldHint`: `True` when interacting with external networks/APIs.
- FastMCP 4 Server Composition:
  - Root gateway in `server.py` selectively mounts domain sub-servers with native domain namespaces (`namespace="time"`, `namespace="projects"`, `namespace="admin"`).
  - Profiles (`--profile` / `SMARTSHEET_RM_PROFILE`, defined in `profiles.py`): domain-mount profiles `full`, `time`, `projects`, `admin`, `readonly` select mounts; job profiles `timesheets`, `staffing`, `org_setup`, `portfolio` mount every domain and expose an explicit tool-name allowlist via the public `root.disable(components={"tool"})` then `root.enable(names=..., components={"tool"})`. Never `enable(only=True)` (it hides prompts and resources) and never private FastMCP attributes. Unknown profile or allowlist names raise `ValueError` at build time.
  - A new tool must go in at least one job profile or in `FULL_ONLY_TOOLS`; `tests/test_profiles.py` fails otherwise. Update the counts in `scripts/check_tool_contract.py` and the README profile table.
  - Read-only: `readOnlyHint=True` is the only signal. `--profile readonly` or `SMARTSHEET_RM_READONLY=1` adds `ReadOnlyToolFilter`; `ReadOnlyGateMiddleware` refuses any real non-read-only tool (`SafetyViolationError`, a FastMCP `ToolError`, so `isError: true`). Unknown names pass through to FastMCP's `Unknown tool`. `call_tool` is unwrapped only when it is a real tool (Tool Search on); otherwise it is `Unknown tool: 'call_tool'`. No server context means refuse.
  - Bulk protection: `time_bulk_delete_time_entries` and `projects_bulk_delete_assignments` are listed in `full`; the time/projects domain guards refuse them at call time unless `SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1`.
  - Tool Search (`regex` or `bm25`) and experimental Code Mode attach only on `full`, never both; on other profiles they log a warning and keep the flat list. `search_tools`, `search` and `get_schema` are annotated `readOnlyHint=True`; Code Mode `execute` is refused under read-only.

### 4. Pure Offline Testing & Contract Sync (`tests/`)
- Add unit tests in `tests/` mocking responses via `respx` or `httpx.MockTransport`.
- **Zero live network calls in the default suite.** Tests must run 100% offline in CI. The opt-in live module is `tests/test_e2e_live.py` (`-m e2e`, skips unless `SMARTSHEET_RM_API_TOKEN` is set).
- Update expected tool and profile counts in `scripts/check_tool_contract.py` and the `README.md` profile table, and place the tool in a job profile or `FULL_ONLY_TOOLS`.
- Ensure test statement coverage remains at **100.0%** (`--cov-fail-under=100`). Branch coverage is not enabled.

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
