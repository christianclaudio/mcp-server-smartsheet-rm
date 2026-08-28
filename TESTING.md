# 🧪 Testing Guide for smartsheet-rm-mcp

This guide explains how to run local automated test suites, verify tool contracts, and run live integration tests against Resource Management by Smartsheet (10,000ft API).

---

## 🏃 Running Local Automated Tests

```bash
# Run complete test suite with coverage
pytest --cov=src/smartsheet_rm_mcp --cov-report=term-missing -q

# Run specific test modules
pytest tests/test_tools.py
pytest tests/test_tools_mocked.py
```

---

## 🛡️ Tool Contract Verification

`scripts/check_tool_contract.py` guarantees all registered tools match API schemas and parameter signatures.

```bash
python scripts/check_tool_contract.py
```

---

## 🔑 Live Testing with Smartsheet RM

To test against live Smartsheet RM:
```bash
# Using CLI with environment variables
export SMARTSHEET_RM_BEARER_TOKEN="your-token"
smartsheet-rm-mcp

# Safe read-only mode
SMARTSHEET_RM_MCP_READONLY=1 smartsheet-rm-mcp
```
