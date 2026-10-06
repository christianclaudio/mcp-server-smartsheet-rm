## Description
Briefly describe the changes introduced by this pull request.

## Domain Coverage
- [ ] Time Tracking & Approvals
- [ ] Projects & Phases
- [ ] Assignments & Scheduling
- [ ] Users, Roles & Capacity
- [ ] Clients & Contacts
- [ ] Leaves & Holidays
- [ ] Expense Tracking
- [ ] Tags & Custom Fields
- [ ] Approvals, Statuses & Placeholders
- [ ] Subtasks, Reports & Webhooks
- [ ] Composite Recipes

## Quality & Safety Checklist
- [ ] 100% statement test coverage maintained (`uv run pytest --cov=src/smartsheet_rm_mcp --cov-report=term-missing --cov-fail-under=100 -v`)
- [ ] Tool contract assertion passed (`uv run python scripts/check_tool_contract.py`)
- [ ] OpenAPI drift check passed (`uv run python scripts/check_openapi_drift.py`)
- [ ] Protocol & stdio verification passed (`uv run pytest tests/test_protocol.py --no-cov`)
- [ ] Protocol conformance suite passed (`./scripts/check_conformance.sh`)
- [ ] Strict type checking passed (`uv run mypy --strict src/`)
- [ ] Ruff lint & format checks passed (`uv run ruff check . && uv run ruff format --check .`)
- [ ] Destructive tools require `confirm=True`
