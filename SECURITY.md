# 🛡️ Security Policy & Best Practices

> **Disclaimer:** `mcp-server-smartsheet-rm` is an independent open-source community project and is **not** affiliated with, endorsed by, or supported by Smartsheet, Inc. *"Smartsheet"* and *"Resource Management by Smartsheet"* are registered trademarks of Smartsheet, Inc.

---

## 🔒 Supported Versions

| Version | Supported |
|---------|-----------|
| `1.0.x` | ✅ Yes    |
| `< 1.0` | ❌ No     |

---

## 🚨 Reporting a Vulnerability

**Please do NOT open public issues for security vulnerabilities.**

Report security vulnerabilities privately via [GitHub Security Advisories](https://github.com/christianclaudio/mcp-server-smartsheet-rm/security/advisories/new).

You will receive an acknowledgement within 5 business days and a status update within 15 business days. If a patch is warranted, we will publish a fix and credit you in the release notes!

---

## 🔐 Operator Security Guidelines

This MCP server holds credentials scoped to your **Smartsheet Resource Management (10,000ft)** organization. Please review the following recommendations before deployment:

### 1. Dedicated API Service Account
`SMARTSHEET_RM_API_TOKEN` operates with organizational permissions. We recommend generating a **dedicated service token** for this server rather than reusing personal user credentials.

### 2. Secret Redaction
- Never commit secrets to git.
- Keep secrets in environment variables or your MCP client's secure configuration.
- Tokens, bearer headers, and sensitive keys are automatically scrubbed from error messages and logs by `_sanitize()`.

### 3. Read-Only Mode for Agent Deployments
When connecting this server to autonomous agents or public assistant interfaces, run with `SMARTSHEET_RM_READONLY=1`:
```bash
SMARTSHEET_RM_READONLY=1 mcp-server-smartsheet-rm
```
This restricts registration exclusively to **38 read-only tools**, completely removing all mutation and deletion endpoints from the model's tool context.

### 4. Safety Gates for Destructive Operations
- **Single Deletion Tools:** Require explicit `confirm=True` on all atomic deletion endpoints (`rm_delete_project`, `rm_delete_time_entry`, etc.). Calls without `confirm=True` are automatically rejected.
- **Bulk Destructive Operations:** `rm_bulk_delete_time_entries` and `rm_bulk_delete_assignments` are excluded from registration by default and require `SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1` plus `confirm=True`.

---

## 🛡️ Summary of Deployment Postures

| Use Case | Recommended Configuration |
|----------|---------------------------|
| **Autonomous AI Assistants & Chatbots** | `SMARTSHEET_RM_READONLY=1` |
| **Interactive Developer Workstation** | Default (97 tools, single-delete confirmation gates) |
| **Enterprise Administrative Scripts** | `SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1` (with `confirm=True`) |
| **Focused Context (Timesheets only)** | `SMARTSHEET_RM_PROFILE=time` |
