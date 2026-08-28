# 🤝 Contributing to mcp-server-smartsheet-rm

Thank you for your interest in contributing to `mcp-server-smartsheet-rm`! We welcome bug fixes, performance improvements, documentation enhancements, and new composite workflows.

---

## 🛠️ Development Setup

1. **Clone and create virtual environment**:
   ```bash
   git clone https://github.com/christianclaudio/mcp-server-smartsheet-rm.git
   cd mcp-server-smartsheet-rm
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. **Install editable with dev dependencies**:
   ```bash
   pip install -e ".[dev]"
   ```

---

## 🧪 Quality Standards & Testing

All pull requests must satisfy our quality and coverage gates:

```bash
# 1. Formatting and linting
ruff check . && ruff format --check .

# 2. Strict type checking
mypy --strict src/

# 3. 100% statement and branch test coverage
pytest --cov=src/smartsheet_rm_mcp --cov-fail-under=100 -v

# 4. Tool Contract & safety gate assertions
python scripts/check_tool_contract.py

# 5. OpenAPI Drift monitor
python scripts/check_openapi_drift.py

# 6. JSON-RPC stdio protocol smoke test
python scripts/smoke_test.py
```

---

## 📐 Architecture & Conventions

- **Tool Annotations**: Every MCP tool must declare `read_only_hint`, `destructive_hint`, or `idempotent_hint` annotations.
- **Destructive Gating**: All delete, deactivate, or removal tools **must** declare `confirm: bool = False` and reject execution when not explicitly `True`.
- **Secret Redaction**: Credentials must be automatically scrubbed by `_sanitize()`.
- **Error Formatting**: Use structured `SmartsheetRMAPIError` responses.
