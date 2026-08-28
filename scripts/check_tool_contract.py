#!/usr/bin/env python3
"""Assert the Smartsheet RM tool surface matches published contract and safety standards."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Expected registered-tool counts per configuration.
EXPECTED_DEFAULT = 98
EXPECTED_WITH_BULK = 100
EXPECTED_READONLY = 39

# Expected annotation split at default registration.
EXPECTED_READ_ONLY = 39
EXPECTED_DESTRUCTIVE = 19
EXPECTED_IDEMPOTENT = 4

PROBE = """
import asyncio, json, sys
sys.path.insert(0, "src")
from smartsheet_rm_mcp.server import mcp

async def main():
    tools = await mcp.list_tools()
    print(json.dumps({
        "total": len(tools),
        "read_only": sum(1 for t in tools if t.annotations and t.annotations.read_only_hint),
        "destructive": sum(1 for t in tools if t.annotations and t.annotations.destructive_hint),
        "idempotent": sum(1 for t in tools if t.annotations and t.annotations.idempotent_hint),
        "unannotated": sum(1 for t in tools if t.annotations is None),
        "all_read_only": all(t.annotations and t.annotations.read_only_hint for t in tools),
        "names": sorted(t.name for t in tools),
        "read_only_names": sorted(t.name for t in tools if t.annotations and t.annotations.read_only_hint),
    }))

asyncio.run(main())
"""


def probe(**env_overrides: str) -> dict:
    """Import the server under given env vars and report its tool surface."""
    import json

    env = dict(os.environ)
    for key in (
        "SMARTSHEET_RM_PROFILE",
        "SMARTSHEET_RM_READONLY",
        "SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE",
    ):
        env.pop(key, None)
    env.update(env_overrides)
    env.setdefault("SMARTSHEET_RM_API_TOKEN", "ci-placeholder-token")

    out = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def parse_readme_counts() -> dict[str, int]:
    """Extract tool/annotation counts from README.md."""
    readme = (REPO / "README.md").read_text()
    patterns = {
        "default_total": r"Default Registration\*\*:\s*(\d+)\s+tools",
        "readonly": r"Read-Only Mode\*\*:\s*(\d+)\s+tools",
        "with_bulk": r"With Bulk Operations\*\*:\s*(\d+)\s+total tools",
    }
    results: dict[str, int] = {}
    for key, pat in patterns.items():
        m = re.search(pat, readme)
        if not m:
            print(
                f"FATAL: Could not locate README pattern for '{key}': /{pat}/",
                file=sys.stderr,
            )
            print(
                "Update README.md to include the expected count pattern, or update "
                "the regex in scripts/check_tool_contract.py.",
                file=sys.stderr,
            )
            sys.exit(2)
        results[key] = int(m.group(1))
    return results


def main() -> int:
    failures: list[str] = []

    def check(label: str, actual: object, expected: object) -> None:
        if actual != expected:
            failures.append(f"{label}: expected {expected!r}, got {actual!r}")
        else:
            print(f"  ok  {label} = {actual!r}")

    print("README count validation:")
    readme_counts = parse_readme_counts()
    check("README default_total", readme_counts["default_total"], EXPECTED_DEFAULT)
    check("README readonly", readme_counts["readonly"], EXPECTED_READONLY)
    check("README with_bulk", readme_counts["with_bulk"], EXPECTED_WITH_BULK)

    print("\nDefault registration:")
    base = probe()
    check("total tools", base["total"], EXPECTED_DEFAULT)
    check("read-only annotations", base["read_only"], EXPECTED_READ_ONLY)
    check("destructive annotations", base["destructive"], EXPECTED_DESTRUCTIVE)
    check("idempotent annotations", base["idempotent"], EXPECTED_IDEMPOTENT)
    check("unannotated tools", base["unannotated"], 0)
    check(
        "bulk_delete_time absent by default",
        "rm_bulk_delete_time_entries" in base["names"],
        False,
    )
    check(
        "bulk_delete_assignments absent by default",
        "rm_bulk_delete_assignments" in base["names"],
        False,
    )

    print("\nSMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1:")
    bulk = probe(SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE="1")
    check("total tools", bulk["total"], EXPECTED_WITH_BULK)
    check(
        "bulk_delete_time present with opt-in",
        "rm_bulk_delete_time_entries" in bulk["names"],
        True,
    )
    check(
        "bulk_delete_assignments present with opt-in",
        "rm_bulk_delete_assignments" in bulk["names"],
        True,
    )

    print("\nSMARTSHEET_RM_READONLY=1:")
    ro = probe(SMARTSHEET_RM_READONLY="1")
    check("total tools", ro["total"], EXPECTED_READONLY)
    check("every tool is read-only", ro["all_read_only"], True)
    check(
        "bulk_delete_time absent from readonly",
        "rm_bulk_delete_time_entries" in ro["names"],
        False,
    )

    print("\nSMARTSHEET_RM_READONLY=1 + SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1 (combined):")
    combined = probe(SMARTSHEET_RM_READONLY="1", SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE="1")
    check("combined: every tool is read-only", combined["all_read_only"], True)
    check(
        "combined: bulk_delete_time absent",
        "rm_bulk_delete_time_entries" in combined["names"],
        False,
    )
    check(
        "combined: bulk_delete_assignments absent",
        "rm_bulk_delete_assignments" in combined["names"],
        False,
    )

    print("\nProfiles:")
    time_p = probe(SMARTSHEET_RM_PROFILE="time")
    proj_p = probe(SMARTSHEET_RM_PROFILE="projects")
    admin_p = probe(SMARTSHEET_RM_PROFILE="admin")
    check("time < default", time_p["total"] < base["total"], True)
    check("projects < default", proj_p["total"] < base["total"], True)
    check("admin < default", admin_p["total"] < base["total"], True)
    check("time != projects", time_p["total"] != proj_p["total"], True)
    print(f"  info time={time_p['total']} projects={proj_p['total']} admin={admin_p['total']}")

    print("\nProfile + readonly compose:")
    admin_ro = probe(SMARTSHEET_RM_PROFILE="admin", SMARTSHEET_RM_READONLY="1")
    check("admin+readonly all read-only", admin_ro["all_read_only"], True)
    check("admin+readonly <= admin", admin_ro["total"] <= admin_p["total"], True)

    admin_ro_name_set = set(admin_ro["names"])
    admin_read_only_name_set = set(admin_p["read_only_names"])
    check(
        "admin+readonly names == admin read-only names",
        admin_ro_name_set,
        admin_read_only_name_set,
    )

    if failures:
        print(f"\nFAILED — {len(failures)} contract violation(s):", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    print("\nAll tool-contract assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
