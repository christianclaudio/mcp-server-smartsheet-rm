# AGENTS.md

Instructions for AI coding agents working on `mcp-server-smartsheet-rm` or integrating Smartsheet Resource Management capabilities.

---

## 📚 Canonical Documentation & Live Doc MCPs

Always consult the official machine-readable indexes and live documentation MCP servers before designing or changing tools:

| Resource | Index / URL | Focus |
| :--- | :--- | :--- |
| **FastMCP 4 Framework** | [`https://gofastmcp.com/llms.txt`](https://gofastmcp.com/llms.txt) | `mount`, hierarchical middleware, transforms, lifespans, in-memory testing |
| **Model Context Protocol** | [`https://modelcontextprotocol.io/llms.txt`](https://modelcontextprotocol.io/llms.txt) | Wire spec, Streamable HTTP, annotations, elicitation |
| **Smartsheet Developer Portal** | [`https://developers.smartsheet.com/llms.txt`](https://developers.smartsheet.com/llms.txt) | RM API ([`/api/resource_management/openapi`](https://developers.smartsheet.com/api/resource_management/openapi), OpenAPI JSON [`/_spec/api/resource_management/openapi.json`](https://developers.smartsheet.com/_spec/api/resource_management/openapi.json)), auth, base URLs |

Live doc MCP endpoints: `https://gofastmcp.com/mcp` (`search_fast_mcp`, `query_docs_filesystem_fast_mcp`) and `https://modelcontextprotocol.io/mcp` (`search_model_context_protocol`, `query_docs_filesystem_model_context_protocol`).

---

## 🎯 Project Overview

`mcp-server-smartsheet-rm` is an enterprise Python MCP server for **Resource Management by Smartsheet** (formerly 10,000ft API) with strict safety gates, offline testing, and multi-tenant token isolation.

- **Undocumented routes policy:** 46 method+path pairs are absent from both the current reference and archived 10,000ft docs. Never drop them; re-point a tool onto a documented route only when that route is a drop-in with the same inputs and outputs, otherwise it stays on the undocumented route. Each carries `"Undocumented route"` in client/tool docstrings, a `README.md` row, and `# drift-ignore: <reason>`. `scripts/check_openapi_drift.py` requires every method in `ENDPOINT_TO_METHOD` to exist and every `self._request(...)` call to match a published OpenAPI route (`--spec-url` or `--spec-file`, required) unless it carries that marker. Any new undocumented route needs both the marker and a README row. Never claim complete API coverage.
- **Smartsheet Gov (`GOV_BASE_URL` / `GOV_HOST` in `client.py`):** opt-in only — never make it default or add to `DEFAULT_ALLOWED_HOSTS`. Gov Bearer auth applies only when host equals `GOV_HOST`, and a per-request RM token is never paired with the environment Gov token.
- Expected tool/profile counts live in `scripts/check_tool_contract.py` and `README.md`.

---

## 🏗️ Key Paths

- `src/smartsheet_rm_mcp/server.py` — FastMCP 4 root gateway (`create_server`): domain mounts, job allowlists, read-only filter, Tool Search / Code Mode (`full` only).
- `src/smartsheet_rm_mcp/profiles.py` — `PROFILES` (each with a one-line `job`), `FULL_ONLY_TOOLS`, `ReadOnlyToolFilter`, `is_read_only_tool`.
- `src/smartsheet_rm_mcp/tools/{time,projects,admin}.py` — domain sub-servers; re-exported from `tools/__init__.py`.
- `src/smartsheet_rm_mcp/common.py` — client resolution, `@rm_tool` decorator (failed calls raise redacted FastMCP `ToolError` JSON so `isError: true`), `_destructive_gate`, redaction, structured logging.
- `src/smartsheet_rm_mcp/middleware.py` — gateway audit middleware, annotation-driven `ReadOnlyGateMiddleware`, domain guardrails (including bulk gate).
- `src/smartsheet_rm_mcp/client.py`, `auth.py`, `errors.py`, `config.py` — `SmartsheetRMClient`, `SMARTSHEET_RM_MCP_AUTH_TOKEN` bearer verifier + tokenless non-localhost HTTP bind refusal, exceptions/redaction, `SMARTSHEET_RM_*` settings.
- `scripts/check_tool_contract.py` — source of truth for expected tool/profile counts and annotations (do not duplicate counts outside it and `README.md`).
- `scripts/check_openapi_drift.py`, `scripts/check_conformance.sh` + `conformance-baseline.yml`, `scripts/release_notes.py`, `scripts/check_version.py`.
- `scripts/setup_repo_governance.sh` — required status-check contexts (`Lint and types`, `Tests`, `Tool contract assertions`, `Protocol conformance`, `Build and package check`, `Container image build`). Update when check names change; **never run from CI or agents** (mutates live branch protection).
- `tests/` — 100% offline suite (`addopts` uses `-m 'not live'`); live module `tests/test_live.py` (`-m live`, skips unless `SMARTSHEET_RM_API_TOKEN` is set; unmarked `test_dispatch_tool_call_offline` stays in default run).
- `.github/workflows/` — `ci.yml`, `release.yml`, `dependabot-automerge.yml`.
- `server.json`, `Dockerfile`, `fastmcp.json`, `pyproject.toml`.

---

## ⚡ Canonical Workflow: Building Tools from API / `llms.txt`

Follow in exact order:
1. **Client method (`client.py`):** typed `async def` method on `SmartsheetRMClient` (no bare `dict`/`Any` when a schema is known), escaped URL path params, calling `await self._request("METHOD", path, params=..., json_data=...)`.
2. **Tool handler (`tools/{time,projects,admin}.py`):** register with `@server.tool(name=..., annotations=...)`, wrap with `@rm_tool`, write explicit docstrings (capabilities, params, return shape). Only delete and bulk-delete tools accept `confirm: bool = False` and call `_destructive_gate` (non-destructive creates/updates do not take `confirm`).
3. **Annotations & composition:**
   - Set `readOnlyHint` (`True` GET / `False` mutations), `destructiveHint` (`True` delete/bulk-delete / `False` others including archive-field updates), `idempotentHint` (`True` GET/list & idempotent writes / `False` creates/other updates/deletes), `openWorldHint` (`True` external APIs).
   - Gateway mounts `namespace="time"`, `"projects"`, `"admin"`.
   - **Profiles (`--profile` / `SMARTSHEET_RM_PROFILE`):** domain-mount (`full`, `time`, `projects`, `admin`, `readonly`) and job allowlists (`timesheets`, `staffing`, `org_setup`, `portfolio` via `root.disable(components={"tool"})` then `root.enable(names=..., components={"tool"})` — never `enable(only=True)` or private FastMCP attrs). Unknown profiles/names raise `ValueError` at build. New tools must be mapped in a job profile or `FULL_ONLY_TOOLS` (`tests/test_profiles.py` enforces).
   - **Read-only:** `readOnlyHint=True` is the only signal (`--profile readonly` / `SMARTSHEET_RM_READONLY=1` → `ReadOnlyToolFilter` + `ReadOnlyGateMiddleware` raising `SafetyViolationError`; unknown names stay `Unknown tool`; `call_tool` unwrapped only when Tool Search is on; missing context refuses).
   - **Bulk protection:** `time_bulk_delete_time_entries` and `projects_bulk_delete_assignments` list in `full` and refuse at call time unless `SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1`.
   - **Tool Search / Code Mode (`full` only, mutually exclusive):** `regex`/`bm25` or Code Mode (`mcp-server-smartsheet-rm[code-mode]`, `pydantic_monty`). Non-`full` profiles log a warning and serve flat list; missing `pydantic_monty` skips Code Mode (`tests/conftest.py` aborts CI if missing; tests use `pytest.importorskip`). `search_tools`, `search`, `get_schema` are `readOnlyHint=True`; Code Mode `execute` is refused under read-only.
4. **Offline tests & contract sync (`tests/`):** mock HTTP with `respx` or `httpx.MockTransport` (**zero live calls in default suite**), update `scripts/check_tool_contract.py` + `README.md` profile table + `profiles.py` / `FULL_ONLY_TOOLS`, and keep **100.0%** statement coverage (`--cov-fail-under=100`).

---

## 🛡️ Non-Negotiable Safety & Security Rules

1. **Destructive Confirmation Gate & Errors:** destructive tools default `confirm: bool = False` and return `_destructive_gate` (`isError: false`, `"status": "confirmation_required"`) without side effects. Validation failures use `_invalid_request(...)` and detected tool/batch-total failures use `_tool_failure(type, message, **details)` (`ToolError`, `isError: true`); never return error dicts/`"status": "failed"` as normal results. Batch tools catch `Exception` (not `BaseException`, so `CancelledError` propagates), include `message` on item errors, and preserve additive field names (`status`, success count, `failed_count`, `results`, `errors`). Non-destructive mutations rely on `ReadOnlyGateMiddleware`.
2. **Secret Redaction:** `_redact_secrets` / `redact_message` in `errors.py` mask Bearer tokens, passwords, API keys, and PEM blocks; raise redacted `ToolError` `from None` **after** `except` blocks (see `errors.py` and redaction tests for known multiline/bracket limits).
3. **Multi-Stage Non-Root Containers (`Dockerfile`):**
   - Multi-stage `python:3.12-slim-trixie AS builder` → runtime pinned by digest (both stages share the same pin; glibc is keyed by the Debian release in the tag, not the digest), non-root `USER mcp`, venv `/opt/venv`.
   - Trust-store floor (FastMCP Oct 2026 / `DSA-6572-1`): both stages upgrade OS `ca-certificates` and enforce `ARG CA_CERTIFICATES_FLOOR` (before pip runs in builder), builder bootstraps pip from `docker/pip-bootstrap.txt` (`--require-hashes`; Dependabot bumps it), `release.yml` & CI `drift-check` install with `pip>=26.2.2`, and `certifi>=2026.10.23` is a direct dependency. Never lower `CA_CERTIFICATES_FLOOR` via `--build-arg` (the build-arg exists only so CI can raise it for the negative floor check; CI and the release workflow never pass it otherwise).
   - Reproducible lock install: builder installs uv (`docker/uv-bootstrap.txt`, `--require-hashes`, outside venv; Dependabot bumps it), exports `uv export --locked --no-dev --no-emit-project --format requirements.txt` and installs that file with pip in hash-checking mode, then installs `docker/build-backend.txt` (`--require-hashes --only-binary=:all:` outside venv), and installs via `PYTHONPATH=/tmp/build-backend pip install --no-deps --no-build-isolation .` (never `pip install .`). Regenerate `docker/build-backend.txt` after `uv lock` via `uv run python scripts/gen_build_backend.py` (never hand-edit; Dependabot excludes it). Enforced by `tests/test_dockerfile.py` & `tests/test_build_backend_lock.py`.
4. **Registry Metadata (`server.json`):** root `description` **≤ 100 chars** (HTTP 422 otherwise); default transport `stdio`, `"runtimeHint": "uvx"`.
5. **Outbound Requests (`SSRFSafeAsyncTransport`):** HTTPS only, blocks loopback/private/internal IPs, resolves DNS via `asyncio.to_thread`, then connects only to a checked address, trying the next while the request's connect timeout lasts (one deadline shared by all addresses). Keep the hostname for the Host header, TLS SNI, certificate verification, and per-hostname pooling; never rewrite a request URL to an IP or set `sni_hostname`. httpx ignores env proxies when a transport is passed (`allow_env_proxies = trust_env and transport is None`), so the client and `fetch_pinned_https` always connect directly through the pin (no environment proxy; `trust_env=False` on the drift fetch is belt-and-braces).
6. **Git Safety & Releases:**
   - Never commit secrets; all work via feature branches and PRs.
   - **Tag is version (`uv-dynamic-versioning`):** `pyproject.toml` declares `dynamic = ["version"]`, `__version__` uses `importlib.metadata`, `server.json` commits `0.0.0`. Never bump versions in PRs (`pyproject.toml`, `src/smartsheet_rm_mcp/__init__.py`, `server.json`, `uv.lock`, `CHANGELOG.md`). Untagged builds report `X.Y.(Z+1).devN+<sha>`; `scripts/check_version.py` rejects `0.0.0` / `0.0.1.devN`.
   - **Breaking changes (`feat!` / `fix!`):** carry `BREAKING CHANGE:` as the final PR body paragraph (before `<!-- This is an auto-generated comment: release notes by coderabbit.ai -->` if present) and **must include migration steps** (do not add a separate migration token; `scripts/release_notes.py` stops at the CodeRabbit marker). Keep PR body accurate before squash merge.
   - **`CHANGELOG.md` frozen at 1.2.2:** GitHub Releases are the changelog. Keep `README.md` unpinned (`uvx --from mcp-server-smartsheet-rm smartsheet-rm-mcp`).
   - **Version level:** before tagging, check the proposed version against the commit types since the last tag (`!` / `BREAKING CHANGE:` → major, `feat` → minor, otherwise patch), and that every breaking commit carries its footer with migration steps.
   - **Tagging (maintainer go only, no release PR):** preview `git fetch --tags && python3 scripts/release_notes.py` on `main` and post it with the release Ask; verify version is unused on git tags, PyPI (`https://pypi.org/pypi/mcp-server-smartsheet-rm/X.Y.Z/json`), and MCP Registry (`https://registry.modelcontextprotocol.io/v0.1/servers/io.github.christianclaudio%2Fsmartsheet-rm/versions/X.Y.Z`). In a throwaway clone, tag the release commit locally, run `rm -rf dist && uv build`, and confirm the wheel is `mcp_server_smartsheet_rm-X.Y.Z-py3-none-any.whl` and `scripts/check_version.py` passes; discard the clone without pushing. PyPI rejects re-uploads: on post-PyPI failure, merge a fix and tag next patch.

---

## 🛠️ Development & Verification Commands

Requires `uv >= 0.13.4` (`[tool.uv] required-version` in `pyproject.toml`).

```bash
# Install editable with dev dependencies
uv sync --locked --extra dev --extra code-mode   # or uv pip install -e ".[dev,code-mode]"

# Lint and formatting
uv run ruff check . && uv run ruff format --check .

# Strict type checking
uv run mypy --strict src/

# Test suite with 100% coverage requirement
uv run pytest --cov=src/smartsheet_rm_mcp --cov-fail-under=100 -v

# Tool contract verification
uv run python scripts/check_tool_contract.py

# Upstream OpenAPI / route drift check (a spec is required; exit 1 = wrong-path drift, or with
# --fail-on-new-uncovered a spec operation that is not called and not in the baseline; exit 2 =
# any other failure, including a spec that cannot be fetched or read, which never reaches the gate)
uv run python scripts/check_openapi_drift.py --spec-url https://developers.smartsheet.com/_spec/api/resource_management/openapi.json
# Offline, against the paths-only fixture the tests use
uv run python scripts/check_openapi_drift.py --spec-file tests/fixtures/rm-openapi-paths.json
# Scheduled monitor: also fail when a new spec operation is missing from scripts/drift_uncovered_baseline.txt
uv run python scripts/check_openapi_drift.py --spec-file tests/fixtures/rm-openapi-paths.json --fail-on-new-uncovered

# Protocol integration tests (stdio handshake & stateless streamable HTTP)
uv run pytest tests/test_protocol.py

# Build version guard (reads the single wheel in dist/; rejects 0.0.0 and the untagged 0.0.1.devN)
rm -rf dist && uv build && uv run python scripts/check_version.py

# Local pre-commit CodeRabbit CLI review
coderabbit review --agent --uncommitted
```

---

## 🔄 CI & Releases

- **CI (`.github/workflows/ci.yml`):** runs on `push`/`pull_request` to `main`, weekly schedule, and `workflow_dispatch` (with `fetch-depth: 0` on package jobs). Jobs: `lint-and-types`, `test` (Python 3.11–3.14, 100% cov) + `tests`, `tool-contract`, OpenAPI drift validation, `build-check` (`check_version.py` + `twine check`), `protocol`, `conformance`, `docker-build` (no push; verifies stripped build deps, stdio `initialize`/`tools/list`, negative `CA_CERTIFICATES_FLOOR`), and `drift-check` (schedule/manual only; passes `--fail-on-new-uncovered` against `scripts/drift_uncovered_baseline.txt` and opens one issue on main for wrong-path drift, new uncovered operations, both, or a monitor failure such as a spec fetch or parse error; the per-push drift job does not pass the flag). CodeQL runs via GitHub default setup.
- **Dependabot (`dependabot-automerge.yml`):** squash-merges minor/patch PRs after CI passes on that head commit, only when every commit is Dependabot's or a clean GitHub update-branch merge; major and `docker` updates always wait for human review.
- **Releases (`.github/workflows/release.yml`):** triggered on `v*` tags (`fetch-depth: 0`); verifies wheel matches tag, publishes to PyPI, creates GitHub Release from `scripts/release_notes.py`, stamps tag version into `server.json` & publishes to MCP Registry, then pushes Docker image (`UV_DYNAMIC_VERSIONING_BYPASS` set to tag version because the Docker build context has no `.git`).

---

## VCS

```yaml
vcs:
  host: github
  remote: origin
  repository: christianclaudio/mcp-server-smartsheet-rm
  default_branch: main
  protected_branches: [main]
  branch_policy: pr_only
  merge_strategy: squash
  delete_branch_on_merge: true
  require_coderabbit: true
  branch_naming: type/scope-desc
```
