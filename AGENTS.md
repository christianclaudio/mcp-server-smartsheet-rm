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

This is `mcp-server-smartsheet-rm` — an enterprise Python Model Context Protocol (MCP) server for **Resource Management by Smartsheet** (formerly 10,000ft API), built on its public REST API. What is covered is defined by the tool catalog and checked by `scripts/check_openapi_drift.py` (the client against the endpoint list in `ENDPOINT_TO_METHOD`, and against an OpenAPI spec when given `--spec-file` or `--spec-url`); do not claim complete API coverage. The default `full` profile lists every tool; the two bulk-destructive tools are listed but refused at call time unless enabled. Expected per-profile counts live in `scripts/check_tool_contract.py`.

**Primary Purpose**:
Expose deep resource planning, allocation, time-tracking, project management, and budget telemetry to AI agents with strict enterprise safety gates, offline testing, and multi-tenant token isolation.

---

## 🏗️ Key Paths

- `src/smartsheet_rm_mcp/server.py` — FastMCP 4 root gateway (`create_server`): domain mounts, job allowlists, read-only filter, Tool Search / Code Mode (full only).
- `src/smartsheet_rm_mcp/profiles.py` — `PROFILES` (each with a one-line `job`), `FULL_ONLY_TOOLS`, `ReadOnlyToolFilter`, `is_read_only_tool`.
- `src/smartsheet_rm_mcp/tools/{time,projects,admin}.py` — domain sub-servers; re-exported from `tools/__init__.py`.
- `src/smartsheet_rm_mcp/common.py` — client resolution, `@rm_tool` decorator (a failed call raises FastMCP `ToolError` with the redacted `{"error": ...}` JSON, so `isError: true`), `_destructive_gate`, secret redaction, structured logging. `middleware.py` — gateway audit middleware, the annotation-driven `ReadOnlyGateMiddleware`, and domain guardrails (including the bulk gate).
- `src/smartsheet_rm_mcp/client.py` — async HTTP client (`SmartsheetRMClient`). `auth.py` — `SMARTSHEET_RM_MCP_AUTH_TOKEN` bearer verifier and the tokenless non-localhost HTTP bind refusal. `errors.py` — structured exceptions and redaction. `config.py` — `SMARTSHEET_RM_*` settings.
- `scripts/check_tool_contract.py` — source of truth for expected tool counts and annotations. Do not hard-code tool counts elsewhere.
- `scripts/check_openapi_drift.py`, `scripts/check_conformance.sh` + `conformance-baseline.yml`.
- `scripts/release_notes.py` — release body from squash commits since the previous `v*` tag. `scripts/check_version.py` — runs after the build and reads the version from the single wheel in `dist/` (the file that ships, as release.yml's tag check does); fails on `0.0.0` (no git metadata) or `0.0.1.devN` (no reachable tag, a shallow checkout).
- `tests/` — unit tests are offline; live network tests live in `tests/test_e2e_live.py` (run via `-m e2e`). Default pytest `addopts` deselect that marker with `-m 'not e2e'`; `test_all_discovered_tools_live` skips unless `SMARTSHEET_RM_API_TOKEN` is set. Offline modules: `tests/test_client.py`, `tests/test_errors.py`, `tests/test_server.py`, `tests/test_layered.py`, `tests/test_profiles.py`, `tests/test_protocol.py`, `tests/test_scripts.py`, `tests/test_version.py`, `tests/test_release_notes.py`, `tests/test_check_version.py`, `tests/test_tool_failures.py`, `tests/test_tool_error_hygiene.py`, `tests/test_validation_errors.py`, `tests/test_http_auth.py`. Unmarked `test_dispatch_tool_call_offline` in `tests/test_e2e_live.py` stays in the default run.
- `.github/workflows/` — `ci.yml`, `release.yml` (on a `v*` tag: build with full history, check the wheel version matches the tag, build the release notes, publish to PyPI, create the GitHub Release from `scripts/release_notes.py`, stamp the tag version into `server.json` and publish to the MCP Registry, then push the Docker image only after that job succeeds), `rm-drift-monitor.yml`, `dependabot-automerge.yml` (squash auto-merge only for Dependabot PRs whose highest update is minor or patch; major updates wait for a human review).
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
  - Tool Search (`regex` or `bm25`) and experimental Code Mode attach only on `full`, never both; on other profiles they log a warning and keep the flat list. Code Mode needs `pydantic-monty` (the `fastmcp[code-mode]` extra); when it is missing, Code Mode is skipped with a warning and the flat catalog is served. `search_tools`, `search` and `get_schema` are annotated `readOnlyHint=True`; Code Mode `execute` is refused under read-only.

### 4. Pure Offline Testing & Contract Sync (`tests/`)
- Add unit tests in `tests/` mocking responses via `respx` or `httpx.MockTransport`.
- **Zero live network calls in the default suite.** Tests must run 100% offline in CI. The opt-in live module is `tests/test_e2e_live.py` (`-m e2e`, skips unless `SMARTSHEET_RM_API_TOKEN` is set).
- Update expected tool and profile counts in `scripts/check_tool_contract.py` and the `README.md` profile table, and place the tool in a job profile or `FULL_ONLY_TOOLS`.
- Ensure test statement coverage remains at **100.0%** (`--cov-fail-under=100`). Branch coverage is not enabled.

---

## 🛡️ Non-Negotiable Safety & Security Rules

1. **Destructive Confirmation Gate**:
   - Every destructive tool (e.g. deletion, bulk removal, or permanent deallocation) must accept `confirm: bool = False` and invoke `_destructive_gate`. If `False`, `_destructive_gate` returns the confirm prompt as a normal result (`isError: false`, `"status": "confirmation_required"`, re-call with `confirm=true`) and the tool executes no side effects. Input validation failures call `_invalid_request(...)`, which raises `ToolError` with the redacted payload (`isError: true`); do not use it for the confirm prompt. Other failures the tool detects itself, such as a batch where every item failed, call `_tool_failure(type, message, **details)`, which raises the same way; never return an error dict or a `"status": "failed"` result as a normal result. Batch per-item `error` objects always include a `message` key. All four batch tools catch `Exception` (not `BaseException`) so a non-API failure on one item is recorded and the batch continues, logging only the redacted message; `asyncio.CancelledError` propagates. Each batch result uses `status`, a success count, `failed_count`, `results` and `errors`, with per-item `{key, status, result|error}`, and keeps any field names it returned before alongside them (prefer additive changes over renames). Non-destructive mutations (creates, updates, workflows) are protected by `ReadOnlyGateMiddleware` without requiring interactive confirmation.
2. **Secret Redaction**:
   - Error messages, logs, and tracebacks must pass through regex redaction (`_redact_secrets`) stripping Bearer tokens, passwords, and API keys.
3. **Multi-Stage Non-Root Containers**:
   - The `Dockerfile` must use a multi-stage build creating and executing under non-root `USER mcp` with virtual environment `/opt/venv`.
4. **Registry Metadata Constraint**:
   - In `server.json`, the root `description` must be **strictly $\le$ 100 characters** to pass MCP Registry schema validation (longer strings trigger HTTP 422).
   - Default package transport is `stdio`, and packages must specify `"runtimeHint": "uvx"`.
5. **Git Safety & Releases**:
   - Never commit API tokens or secrets.
   - All changes proceed via feature branches and PRs.
   - **The git tag is the version.** `uv-dynamic-versioning` reads the `vX.Y.Z` tag at build time; `pyproject.toml` declares `dynamic = ["version"]`, `__version__` comes from `importlib.metadata`, and `server.json` commits `0.0.0` (the release workflow stamps the tag version into it). PRs never edit a version: no bump in `pyproject.toml`, `src/smartsheet_rm_mcp/__init__.py`, `server.json`, `uv.lock`, or `CHANGELOG.md`. Untagged builds report `X.Y.(Z+1).devN+<sha>`; a build with no git metadata reports the fallback `0.0.0`, which `scripts/check_version.py` rejects when run on the built wheel after the build.
   - **Breaking changes:** every `feat!` / `fix!` PR (any `type!:` title) carries a `BREAKING CHANGE:` footer as the final paragraph of the PR body, and the footer text must include the migration steps. `BREAKING CHANGE:` (or its synonym `BREAKING-CHANGE:`) is the only footer token; do not add a separate migration token. `scripts/release_notes.py` stops at the CodeRabbit marker line (outside a code fence) `<!-- This is an auto-generated comment: release notes by coderabbit.ai -->` and ignores everything after it, so the footer goes before CodeRabbit's generated summary, never inside it.
   - **Squash merges use the PR body as the commit message** (repo settings: PR title as squash title, PR body as squash message). Keep the PR body accurate up to the merge, because `scripts/release_notes.py` reads it from the squash commit.
   - **`CHANGELOG.md` is frozen** as of 1.2.2. GitHub Releases are the changelog: `scripts/release_notes.py` builds each release body from the squash commits since the previous tag (every `BREAKING CHANGE:` footer verbatim, then the commit subjects). Do not add CHANGELOG entries.
   - `skills/smartsheet-rm/SKILL.md` carries no version: the [Agent Skills specification](https://agentskills.io/specification) has no top-level `version` field. Do not add one. Update the skill only when its operator guidance changes.
   - **README is outside the release version ceremony.** Do not add or chase `README.md` `==X.Y.Z` install pins. Update `README.md` only when project behavior, install method, config, or commands actually change. Prefer unpinned install examples (`uvx --from mcp-server-smartsheet-rm smartsheet-rm-mcp`) or point readers to GitHub Releases.

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

# Build version guard (reads the single wheel in dist/; rejects 0.0.0 and the untagged 0.0.1.devN)
rm -rf dist && uv build && uv run python scripts/check_version.py

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent --uncommitted
```

---

## 🔄 CI & Releases

CI is defined in `.github/workflows/ci.yml` (jobs: lint and types, tests on Python 3.10–3.13 at 100% coverage, tool contract, OpenAPI drift, build + `scripts/check_version.py` + `twine check`, protocol and conformance, CodeQL). Jobs that install or build the package check out with `fetch-depth: 0`, because a shallow checkout has no reachable tag and reports `0.0.1.devN`. The Docker image is built only in `release.yml`, with `UV_DYNAMIC_VERSIONING_BYPASS` set to the tag version (the build context has no `.git`). Run the commands above before opening a PR. Scheduled upstream drift runs in `rm-drift-monitor.yml`.

Do not create tags or releases unless the maintainer asks. There is no release PR: merged commits accumulate on `main`, and releases go out on any weekday on the maintainer's go; no fixed release day. Before the tag:

- The release owner previews the release body on an up-to-date `main` with full history and tags (`git fetch --tags && python3 scripts/release_notes.py`) and posts it with the release Ask.
- The reviewer checks the proposed version against the commit types since the last tag (`!` / `BREAKING CHANGE:` → major, `feat` → minor, otherwise patch), that every breaking commit carries its footer with migration steps, and that the version is unused in all three places it could already exist:
  ```bash
  git ls-remote --tags origin vX.Y.Z                                                    # prints nothing
  curl -s -o /dev/null -w '%{http_code}\n' https://pypi.org/pypi/mcp-server-smartsheet-rm/X.Y.Z/json   # prints 404
  curl -s -o /dev/null -w '%{http_code}\n' https://registry.modelcontextprotocol.io/v0.1/servers/io.github.christianclaudio%2Fsmartsheet-rm/versions/X.Y.Z   # prints 404
  ```
  In a throwaway clone, the reviewer tags the release commit locally, runs `rm -rf dist && uv build`, and confirms the wheel is `mcp_server_smartsheet_rm-X.Y.Z-py3-none-any.whl` and `scripts/check_version.py` passes; then discards the clone without pushing.
- Only the maintainer's go creates the tag. PyPI never accepts the same version twice: if a release fails after the PyPI upload, do not re-run it; merge a fix and tag the next patch.
