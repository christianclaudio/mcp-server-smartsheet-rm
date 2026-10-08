#!/usr/bin/env python3
"""Assert the Smartsheet RM tool surface matches published contract and safety standards."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent

# Expected (total tools, tools annotated readOnlyHint=True) per profile. ``full`` is exhaustive:
# the two bulk tools are listed and refused at call time unless SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1.
EXPECTED_PROFILE_COUNTS: dict[str, tuple[int, int]] = {
    # Domain-mount profiles
    "full": (100, 39),
    "time": (15, 4),
    "projects": (25, 10),
    "admin": (60, 25),
    "readonly": (39, 39),
    # Job (allowlist) profiles
    "timesheets": (22, 12),
    "staffing": (25, 18),
    "org_setup": (35, 13),
    "portfolio": (33, 18),
}

# Expected annotation split on ``full``.
EXPECTED_DEFAULT = EXPECTED_PROFILE_COUNTS["full"][0]
EXPECTED_READONLY = EXPECTED_PROFILE_COUNTS["readonly"][0]
EXPECTED_READ_ONLY = 39
EXPECTED_DESTRUCTIVE = 21
EXPECTED_IDEMPOTENT = 43

BULK_TOOLS = ("time_bulk_delete_time_entries", "projects_bulk_delete_assignments")
EXPECTED_FULL_ONLY = {
    "time_bulk_delete_time_entries",
    "projects_bulk_delete_assignments",
    "admin_delete_client",
    "admin_delete_client_contact",
    "admin_create_expense_category",
    "admin_delete_expense_category",
    "admin_delete_tag",
}

PROBE = """
import asyncio, json, sys
sys.path.insert(0, "src")
from smartsheet_rm_mcp.profiles import FULL_ONLY_TOOLS, PROFILES
from smartsheet_rm_mcp.server import create_server, mcp

def describe(tools):
    return {
        "total": len(tools),
        "read_only": sum(1 for t in tools if t.annotations and t.annotations.read_only_hint is True),
        "destructive": sum(1 for t in tools if t.annotations and t.annotations.destructive_hint is True),
        "idempotent": sum(1 for t in tools if t.annotations and t.annotations.idempotent_hint is True),
        "unannotated": sum(1 for t in tools if t.annotations is None),
        "missing_read_only_hint": sorted(
            t.name for t in tools if t.annotations is None or t.annotations.read_only_hint is None
        ),
        "all_read_only": all(t.annotations and t.annotations.read_only_hint is True for t in tools),
        "names": sorted(t.name for t in tools),
        "read_only_names": sorted(
            t.name for t in tools if t.annotations and t.annotations.read_only_hint is True
        ),
        "destructive_has_confirm": all(
            isinstance(t.parameters, dict) and "confirm" in t.parameters.get("properties", {})
            for t in tools
            if t.annotations and t.annotations.destructive_hint
        ),
    }

async def main():
    report = {"default": describe(await mcp.list_tools()), "profiles": {}}
    for name in PROFILES:
        report["profiles"][name] = describe(await create_server(profile=name).list_tools())
    report["full_only"] = sorted(FULL_ONLY_TOOLS)
    print(json.dumps(report))

asyncio.run(main())
"""


def probe(**env_overrides: str) -> dict[str, Any]:
    """Import the server under given env vars and report its tool surface per profile."""
    env = dict(os.environ)
    for key in (
        "SMARTSHEET_RM_PROFILE",
        "SMARTSHEET_RM_READONLY",
        "SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE",
        "SMARTSHEET_RM_ENABLE_TOOL_SEARCH",
        "SMARTSHEET_RM_ENABLE_CODE_MODE",
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
    res: dict[str, Any] = json.loads(out.stdout.strip().splitlines()[-1])
    return res


def parse_readme_counts() -> dict[str, Any]:
    """Extract tool counts and the profile table from README.md."""
    readme = (REPO / "README.md").read_text()
    patterns = {
        "default_total": r"Default Registration\*\*:\s*(\d+)\s+tools",
        "readonly": r"Read-Only Mode\*\*:\s*(\d+)\s+tools",
    }
    results: dict[str, Any] = {}
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
    rows = re.findall(r"^\| `([a-z_]+)` \| [^|]+ \| \*\*(\d+)\*\* \| (\d+) \|$", readme, flags=re.M)
    results["profiles"] = {name: (int(total), int(ro)) for name, total, ro in rows}
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
    check("README profile table", readme_counts["profiles"], EXPECTED_PROFILE_COUNTS)

    print("\nDefault registration (profile=full):")
    base = probe()
    default = base["default"]
    check("total tools", default["total"], EXPECTED_DEFAULT)
    check("read-only annotations", default["read_only"], EXPECTED_READ_ONLY)
    check("destructive annotations", default["destructive"], EXPECTED_DESTRUCTIVE)
    check("idempotent annotations", default["idempotent"], EXPECTED_IDEMPOTENT)
    check("unannotated tools", default["unannotated"], 0)
    check("tools without explicit readOnlyHint", default["missing_read_only_hint"], [])
    for name in BULK_TOOLS:
        check(f"{name} listed in full (gated at call time)", name in default["names"], True)
    check("all destructive tools define confirm parameter", default["destructive_has_confirm"], True)
    check("FULL_ONLY_TOOLS", set(base["full_only"]), EXPECTED_FULL_ONLY)

    print("\nProfiles (total tools):")
    for name, (total, _) in EXPECTED_PROFILE_COUNTS.items():
        check(f"{name} total", base["profiles"].get(name, {}).get("total"), total)
    check("profile names", set(base["profiles"]), set(EXPECTED_PROFILE_COUNTS))
    jobs = {"timesheets", "staffing", "org_setup", "portfolio"}
    in_jobs = set().union(*(set(base["profiles"][j]["names"]) for j in jobs))
    check(
        "every full tool is in a job profile or FULL_ONLY_TOOLS",
        in_jobs | set(base["full_only"]),
        set(default["names"]),
    )
    check("job profiles and FULL_ONLY_TOOLS are disjoint", in_jobs & set(base["full_only"]), set())

    print("\nSMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1 (listing unchanged):")
    bulk = probe(SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE="1")
    check("total tools", bulk["default"]["total"], EXPECTED_DEFAULT)

    print("\nSMARTSHEET_RM_READONLY=1 composes with every profile (read-only tools only):")
    ro = probe(SMARTSHEET_RM_READONLY="1")
    for name, (_, read_only) in EXPECTED_PROFILE_COUNTS.items():
        report = ro["profiles"].get(name, {})
        check(f"{name}+readonly total", report.get("total"), read_only)
        check(f"{name}+readonly all read-only", report.get("all_read_only"), True)
        check(
            f"{name}+readonly names == {name} read-only names",
            report.get("names"),
            base["profiles"][name]["read_only_names"],
        )
    for name in BULK_TOOLS:
        check(f"{name} listed under readonly", name in ro["default"]["names"], False)

    if failures:
        print(f"\nFAILED — {len(failures)} contract violation(s):", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    print("\nAll tool-contract assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
