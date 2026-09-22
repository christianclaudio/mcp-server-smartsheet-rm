# 📜 Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

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
