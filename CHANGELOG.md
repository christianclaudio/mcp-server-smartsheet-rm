# 📜 Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

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
