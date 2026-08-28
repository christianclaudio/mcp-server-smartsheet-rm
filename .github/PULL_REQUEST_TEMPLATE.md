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
- [ ] 100% statement and branch test coverage maintained (`pytest --cov --cov-fail-under=100`)
- [ ] Tool contract assertion passed (`python scripts/check_tool_contract.py`)
- [ ] OpenAPI drift check passed (`python scripts/check_openapi_drift.py`)
- [ ] Stdio smoke test passed (`python scripts/smoke_test.py`)
- [ ] Strict type checking passed (`mypy --strict src/`)
- [ ] Ruff lint & format checks passed (`ruff check . && ruff format --check .`)
- [ ] Destructive tools require `confirm=True`
