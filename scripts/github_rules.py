#!/usr/bin/env python3
"""Review or audit branch protections; writes require an explicit separate --apply action."""
from __future__ import annotations
import argparse
import json
import re
import subprocess
from pathlib import Path
from engineering_common import ROOT


def api(repository: str, endpoint: str, body: Path | None = None):
    command = ["gh", "api", f"repos/{repository}/{endpoint}", "-H", "Accept: application/vnd.github+json"]
    if body:
        command.extend(["--method", "PUT", "--input", str(body)])
    return json.loads(subprocess.check_output(command))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="OpenTenon/tenon")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--audit", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-repository")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
        parser.error("Invalid repository identifier")
    templates = {branch: ROOT / ".github/branch-protection" / (branch + ".json") for branch in ("dev", "main")}
    if not args.audit and not args.apply:
        print(json.dumps({branch:json.loads(path.read_text(encoding="utf-8")) for branch,path in templates.items()}, indent=2))
        return 0
    if args.apply:
        if args.confirm_repository != args.repository:
            parser.error("--apply requires an explicit matching --confirm-repository")
        # Applying before ci-required exists would block merges. Check the published dev workflow first.
        branch = api(args.repository, "branches/dev")
        runs = api(args.repository, f"commits/{branch['commit']['sha']}/check-runs")
        if not any(run["name"] == "ci-required" and run.get("conclusion") == "success" for run in runs.get("check_runs", [])):
            raise SystemExit("BLOCKED: publish and pass ci-required on dev before applying branch protections")
    results = {}
    for branch,path in templates.items():
        results[branch] = api(args.repository, f"branches/{branch}/protection", path if args.apply else None)
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
