# 📜 Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Breaking Changes
- **Read-only fails closed on `readOnlyHint` alone**: `ReadOnlyGateMiddleware` no longer matches tool-name prefixes. Under `--profile readonly` or `SMARTSHEET_RM_READONLY=1` it refuses any real tool not annotated `readOnlyHint=True` (a missing annotation counts as a write), including writes the read-only filter hid. Refusals are `SafetyViolationError`, a FastMCP `ToolError`, so clients get a tool result with `isError: true` instead of a `PermissionError`. A gate with no serving server context refuses. Names that are not tools on the server still get FastMCP's `Unknown tool`, directly or through `call_tool`.
- **Bulk tools listed and gated at call time**: `time_bulk_delete_time_entries` and `projects_bulk_delete_assignments` are now listed in `full`, `time` and `projects` (`full` goes from 98 to 100 tools, `time` 14 → 15, `projects` 24 → 25). The time/projects domain guards refuse them with `isError: true` unless `SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1`; `confirm=True` is still required.
- **Discovery is `full`-only**: Tool Search no longer attaches on `time`, `projects`, `admin`, `readonly` or the job profiles; requesting it there logs a warning and keeps the flat list. Tool Search and Code Mode together raise `ValueError`.
- **`create_server` signature**: now `create_server(profile, enable_tool_search, enable_code_mode, tool_search_backend)`. The unused `readonly` and `allow_bulk_destructive` keyword arguments are removed; use `--profile readonly` / `SMARTSHEET_RM_READONLY=1` and `SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1`. An unknown profile now raises `ValueError("Unknown profile ...")`.

### Added
- **Job profiles** (`profiles.py`): `timesheets` (22 tools, 12 read-only), `staffing` (25, 18), `org_setup` (35, 13) and `portfolio` (33, 18). They mount every domain and expose an explicit tool-name allowlist; prompts and resources stay available. Every profile has a one-line `job`. Unknown profile or allowlisted names raise `ValueError` at build time. The existing domain-mount profiles `full`, `time`, `projects`, `admin` and `readonly` are kept.
- **`FULL_ONLY_TOOLS`**: the 7 tools in no job profile (the two bulk deletes, `admin_delete_client`, `admin_delete_client_contact`, `admin_create_expense_category`, `admin_delete_expense_category`, `admin_delete_tag`). Tests require every tool to be in a job profile or this set.
- **BM25 Tool Search and Code Mode**: `SMARTSHEET_RM_TOOL_SEARCH_BACKEND` / `--tool-search-backend` (`regex` or `bm25`) and `SMARTSHEET_RM_ENABLE_CODE_MODE` / `--enable-code-mode` (experimental, `full` only). `search_tools`, `search` and `get_schema` are annotated `readOnlyHint=True`; Code Mode `execute` is refused under read-only.
- **Tests**: `tests/test_profiles.py` covers per-profile counts, read-only composition, prompts and resources on job profiles, unknown names, the `call_tool` unwrap (with a spy and an unwrap-removed regression test), `Unknown tool` paths, hidden writes, `isError` on refusals, `FULL_ONLY_TOOLS`, explicit `readOnlyHint` on every tool, and the bulk gate.

### Changed
- **Public FastMCP API only**: profile and read-only filtering use `root.disable`/`root.enable` visibility and a `ReadOnlyToolFilter` transform instead of the private `_local_provider` / `_tool_manager` compatibility shim (`_ToolManagerCompat` removed). `scripts/check_openapi_drift.py` counts tools with the public `list_tools()`.
- **Stale `rm_*` tool names removed**: destructive confirmation messages, the server module docstring, middleware prefixes, tests and `SECURITY.md` now use the wire names (`time_*`, `projects_*`, `admin_*`). Python function names are unchanged.
- **Contract script**: `scripts/check_tool_contract.py` asserts every profile's total and read-only counts, the README profile table, `FULL_ONLY_TOOLS`, explicit `readOnlyHint` on every tool, and that read-only composes with every profile.
- **Docs**: README, `AGENTS.md`, `SECURITY.md`, `TESTING.md` and the skill describe the profiles, read-only behavior, the call-time bulk gate and `full`-only discovery.
- **Docs**: README `uvx` install examples are intentionally unpinned (`uvx --from mcp-server-smartsheet-rm smartsheet-rm-mcp`). SemVer stays in package manifests, the release tag, and this changelog. Pin a freeze from GitHub Releases or this changelog when a host needs one.

## [1.2.3] - 2026-10-05

### Security
- **FastMCP floor**: Raise the `fastmcp` dependency floor from `>=4.0.10` to `>=4.0.11` in `pyproject.toml` and `fastmcp.json`, and refresh `uv.lock` so the locked resolution is the 4.0.11 security release. `mcp>=2.2.0` is unchanged. No server code changes.

## [1.2.2] - 2026-10-02

### Security
- **Default API host allowlist**: `SMARTSHEET_RM_BASE_URL` and the `x-smartsheet-rm-base-url` header must name a host in `SMARTSHEET_RM_ALLOWED_HOSTS`. When that variable is unset or blank, the allowlist is `api.rm.smartsheet.com` and the client path prefix stays `https://api.rm.smartsheet.com/api/v1`. An explicit allowlist replaces the default. Loopback, link-local, private, and other non-global destinations stay blocked even if they are listed. Credentialed requests do not follow redirects.
- **OpenAPI drift fetch**: `scripts/check_openapi_drift.py --spec-url` validates the URL (HTTPS, no loopback/private/metadata targets) before GET and does not follow redirects.
- **Connect-time DNS pinning**: The API client and `--spec-url` open TCP to the public IP that passed validation (one lookup). `Host`, SNI, and certificate verification stay on the original hostname. A redirect `Location` is re-validated and is not requested.

## [1.2.1] - 2026-09-27

### Changed
- **Docs (#18)**: README pin and layered tool docs landed on main (docs-only). Host `uvx` examples stay on `mcp-server-smartsheet-rm==1.2.0` (`smartsheet-rm-mcp`) and document namespaced tools (`time_*`, `projects_*`, `admin_*`). This patch bump is so hosts can pin `==1.2.1`.

## [1.2.0] - 2026-09-21

### Breaking Changes
- **Domain Tool Namespacing**: Tools are now registered via domain sub-servers mounted with native prefixes (`time_*`, `projects_*`, `admin_*`). Clients invoking legacy `rm_*` tool names (e.g. `rm_list_time_entries`, `rm_get_project`, `rm_list_users`) must update tool calls to their respective namespaced counterparts:
  - `time_*`: Time entries, user suggestions, approvals, timesheet locking, weekly fills (`time_list_time_entries`, `time_get_time_entry`, etc.).
  - `projects_*`: Projects, phases, milestones, assignments, placeholders, clone schedules (`projects_list_projects`, `projects_get_project`, etc.).
  - `admin_*`: Users, roles, clients, bill rates, custom fields, disciplines, tags, expenses, reports (`admin_list_users`, `admin_get_user`, etc.).

### Added
- **FastMCP 4 Server Composition**: Modularized root server into dedicated domain sub-servers (`time`, `projects`, `admin`) with root gateway composition via `mount()`.
- **Hierarchical Middleware Pipeline**: Added `ParentAuditMiddleware` for request timing, structured lifecycle logging, and secret redaction, and `ReadOnlyGateMiddleware` for global mutation gating.
- **Domain Guardrails**: Added domain middleware (`TimeDomainGuardMiddleware`, `ProjectsDomainGuardMiddleware`, `AdminDomainGuardMiddleware`) validating realistic parameter bounds, non-empty project names, and batch limits.
- **Dynamic Tool Search Transform**: Added opt-in dynamic regex search transform (`--enable-tool-search` / `SMARTSHEET_RM_ENABLE_TOOL_SEARCH=1`) for efficient tool discovery.
- **Pydantic Settings Management**: Introduced `config.py` with `Settings` model binding `SMARTSHEET_RM_*` environment variables.
- **SSRF and DNS-Rebinding Mitigations**: Added hostname resolution validation and non-global IP rejection in `common.py`, with optional explicit allowlist via `SMARTSHEET_RM_ALLOWED_HOSTS`.
- **Layered Composition Tests**: Added comprehensive test suite `tests/test_layered.py` reaching 100.0% statement coverage.

### Changed
- **FastMCP Floor**: Mode A raises the `fastmcp` dependency floor from `>=4.0.5` to `>=4.0.10` in `pyproject.toml` and `fastmcp.json`, and refreshes `uv.lock` so the locked resolution is 4.0.10.
- **Locked CI Installs**: Replaced unbound `pip install -e ".[dev]"` steps in CI with `uv sync --locked --extra dev` (commands via `uv run`) so a green run cannot float past the lockfile. SemVer stays **1.2.0** (first ship of this untagged mainline).

## [1.1.5] - 2026-09-13

### Changed
- **Modernized Testing Pyramid**: Integrated end-to-end live testing module `tests/test_e2e_live.py` with `@pytest.mark.e2e` marker and offline mock dispatch validation.
- **Retired Legacy Smoke Script**: Retired out-of-band `scripts/smoke_test.py` in favor of standardized pytest test suites and CI protocol verification.
- **Workflow Protocol Verification**: Updated CI workflow to run pytest protocol checks with `--no-cov` in the isolated protocol validation job.
- **Server Metadata Alignment**: Synchronized `server.json` versioning to 1.1.5.

## [1.1.4] - 2026-09-03

### Changed
- **Synchronized Sunday Maintenance Schedule**: Standardized upstream API drift monitoring to Sunday 12:00 AM EDT / 04:00 UTC (`cron: '0 4 * * 0'`) and Dependabot dependency reconciliation to Sunday 12:30 AM EDT / 04:30 UTC (`time: "04:30"`).
- **Enhanced Parameter & Schema Drift Engine**: Upgraded `scripts/check_openapi_drift.py` with AST client parameter parsing, parameter deprecation detection (`deprecated: true` / `[Deprecated]`), and missing required parameter audits.

## [1.1.3] - 2026-08-30

### Fixed
- **Graceful Shutdown Interceptor**: Registered custom `SIGTERM` and `SIGINT` signal handlers in `server.py` to exit with status code `0`, preventing supervisor `exit status 143` errors on client restarts.

## [1.1.0] - 2026-08-27

### Changed
- **License Standardization**: Upgraded repository license to Apache 2.0 for enterprise patent indemnity and legal uniformity.
- **Suite Baseline**: Synchronized versioning across the enterprise MCP server suite.

## [1.0.2] - 2026-08-14

### Fixed
- **MCP Registry Ownership Verification**: Added `<!-- mcp-name: io.github.christianclaudio/smartsheet-rm -->` identifier to package `README.md` for official Model Context Protocol Registry publishing validation.

## [1.0.1] - 2026-08-14

### Added
- **Official MCP Registry Publishing**: Configured `mcp-publisher` with GitHub OIDC for indexing into the official Model Context Protocol Registry.
- **Docker Container Publishing**: Automated multi-tag build & push to GitHub Container Registry (`ghcr.io/christianclaudio/mcp-server-smartsheet-rm`).
- **Weekend Scheduling Support**: Added `include_weekends: bool = False` and `weekend_hours: float | None = None` to `rm_fill_weekly_timesheet`.
- **Project Users Endpoint**: Added `rm_list_project_users` covering `GET /projects/{project_id}/users` (98 default tools, 100 with bulk).

## [1.0.0] - 2026-08-14

### Added
- **Complete REST API Coverage**: 97 default tools (99 with bulk-destructive operations) across all Smartsheet Resource Management (10,000ft) endpoints.
- **Client Architecture**: Async client `SmartsheetRMClient` supporting 102 REST operations, retry backoff with jitter on HTTP 429 rate limits, and sanitized error structures.
- **Composite Recipes**:
  - `rm_fill_weekly_timesheet`: Batch log Monday–Friday hours with project auto-resolution.
  - `rm_confirm_suggested_hours`: Auto-confirm unconfirmed schedule suggestions with per-item resilience.
  - `rm_reconcile_and_submit_week`: 40-hour balance audit and optional approval submission (`auto_submit: bool = False` default).
  - `rm_clone_project_schedule`: Duplicate project phases and assignment schedules.
  - `rm_bulk_delete_time_entries` & `rm_bulk_delete_assignments`: Bulk cleanup operations with per-item error capture.
- **Safety Postures**:
  - `SMARTSHEET_RM_READONLY=1`: Startup filtering for 38 read-only tools.
  - `SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1`: Explicit startup opt-in for bulk deletions.
  - `confirm: bool = False` safety gating on all 21 destructive tools.
- **Dynamic Profile Filtering**: `SMARTSHEET_RM_PROFILE` support for `time`, `projects`, `admin`, and `full`.
- **Quality & Testing**:
  - 100% statement and branch test coverage across the entire codebase.
  - Automated tool contract validator (`scripts/check_tool_contract.py`).
  - Automated 102-endpoint OpenAPI drift check (`scripts/check_openapi_drift.py`).
  - End-to-end stdio JSON-RPC protocol smoke test (`scripts/smoke_test.py`).
- **CI/CD Pipeline**: GitHub Actions for multi-python testing (3.10–3.13), CodeQL security scanning, Dependabot auto-merge, daily OpenAPI drift monitor, and Trusted PyPI publishing.
