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
This lists only the **39 tools annotated `readOnlyHint=true`** (on any `--profile`, it keeps that profile's read-only tools). `ReadOnlyGateMiddleware` refuses any call to a tool that is not annotated read-only, including writes the filter hid, with `isError: true`. A missing annotation counts as a write.

### 4. Safety Gates for Destructive Operations
- **Single Deletion Tools:** Require explicit `confirm=True` on all atomic deletion endpoints (`projects_delete_project`, `time_delete_time_entry`, etc.). Calls without `confirm=True` are automatically rejected.
- **Bulk Destructive Operations:** `time_bulk_delete_time_entries` and `projects_bulk_delete_assignments` are listed in the `full` catalog but refused at call time unless `SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1`, and they still require `confirm=True`. They are in no job profile.

### 5. SSRF & Host Validation
When running in multi-tenant environments where callers may provide per-request `x-smartsheet-rm-base-url` headers or `SMARTSHEET_RM_BASE_URL`:
- Base URLs are strictly restricted to the HTTPS scheme and must not include userinfo.
- By default only `api.rm.smartsheet.com` is allowed (the client path prefix remains `https://api.rm.smartsheet.com/api/v1`). Set `SMARTSHEET_RM_ALLOWED_HOSTS` to a comma-separated hostname list to replace that default. Include `api.rm.smartsheet.com` in the list if the official API should remain reachable.
- Loopback addresses (`localhost`, `127.0.0.0/8`, `::1`), `.local` / `.internal` names, link-local (`169.254.0.0/16`, including the cloud metadata address), private RFC1918 subnets, and hostnames resolving to non-global IP addresses are blocked fail-closed, including when they appear in the allowlist.
- Credentialed API requests do not follow redirects.
- At connect time the client resolves the hostname once and opens TCP to an address that passed those checks. The `Host` header, TLS SNI, and certificate verification stay on the original hostname, so a later DNS answer cannot move the connection onto a private or metadata address.

---

## 🛡️ Summary of Deployment Postures

| Use Case | Recommended Configuration |
|----------|---------------------------|
| **Autonomous AI Assistants & Chatbots** | `SMARTSHEET_RM_READONLY=1` |
| **Interactive Developer Workstation** | Default `full` profile (100 tools, single-delete confirmation gates; the two bulk-destructive tools are listed but refused at call time unless `SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1`) |
| **Enterprise Administrative Scripts** | `SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1` (with `confirm=True`) |
| **Focused Context (weekly timesheets)** | `SMARTSHEET_RM_PROFILE=timesheets` (job profile) or `SMARTSHEET_RM_PROFILE=time` (time domain only) |
