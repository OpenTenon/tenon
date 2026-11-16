#!/usr/bin/env python3
"""Fail closed on missing CI jobs and enforce PR integration directions."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
from pathlib import Path

from engineering_common import ROOT, git

REQUIRED_JOBS = {"format", "check", "sim", "tools-windows"}


def check_needs(needs: dict) -> list[str]:
    return [name for name in sorted(REQUIRED_JOBS)
            if not isinstance(needs.get(name), dict) or needs[name].get("result") != "success"]


def check_event(event: dict, repository: str) -> list[str]:
    pull = event.get("pull_request")
    if not pull:
        return []
    base, head = pull["base"]["ref"], pull["head"]["ref"]
    if base == "dev" and not head.startswith("dev-"):
        return ["Task branches targeting dev must use dev-<task-id>-<topic>"]
    if base == "main":
        same_repo = pull["head"].get("repo", {}).get("full_name") == repository
        if head != "dev" or not same_repo:
            return ["main accepts only a dev PR from this repository; only a human performs the merge"]
    elif base != "dev":
        return ["Unsupported integration target"]
    return []


def check_dev_delivery(pull: dict, workflow: dict, jobs: list[dict], head: str,
                       base: str, repository: str, contains_dev: bool) -> list[str]:
    """Assess a fresh REST snapshot; this function never performs a merge."""
    errors = []
    target, source = pull.get("base", {}), pull.get("head", {})
    if target.get("ref") != "dev" or target.get("repo", {}).get("full_name") != repository:
        errors.append("Automatic delivery targets this repository's dev branch only")
    if not source.get("ref", "").startswith("dev-") or source.get("repo", {}).get("full_name") != repository:
        errors.append("The PR must use this repository's short task branch")
    if source.get("sha") != head:
        errors.append("PR head differs from the local committed candidate")
    if target.get("sha") != base or not contains_dev:
        errors.append("Refresh current dev, integrate it normally and rerun CI")
    if pull.get("state") != "open" or pull.get("draft") or pull.get("mergeable") is not True or pull.get("mergeable_state") != "clean":
        errors.append("PR is not open, ready and cleanly mergeable")
    if not pull.get("number") or not any(item.get("number") == pull["number"] for item in workflow.get("pull_requests", [])):
        errors.append("CI run is not associated with this PR")
    checked_shas = {head, pull.get("merge_commit_sha")} - {None, ""}
    if workflow.get("head_sha") not in checked_shas or workflow.get("event") != "pull_request":
        errors.append("CI does not belong to this PR candidate")
    if workflow.get("path", "").split("@")[0] != ".github/workflows/ci.yml":
        errors.append("The repository CI workflow is missing")
    if workflow.get("status") != "completed" or workflow.get("conclusion") != "success":
        errors.append("The latest CI run is not fully successful")
    names = [job.get("name") for job in jobs]
    required = REQUIRED_JOBS | {"ci-required"}
    if not required.issubset(set(names)) or len(names) != len(set(names)):
        errors.append("CI job inventory is missing or ambiguous")
    for job in jobs:
        if job.get("run_id") != workflow.get("id") or job.get("run_attempt") != workflow.get("run_attempt"):
            errors.append("Job belongs to another CI run or attempt")
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            errors.append("Unsuccessful or unfinished CI job: " + str(job.get("name")))
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--event-only", action="store_true")
    mode.add_argument("--delivery-snapshot", type=Path)
    parser.add_argument("--repository", default="OpenTenon/tenon")
    args = parser.parse_args()
    if args.delivery_snapshot:
        snapshot = json.loads(args.delivery_snapshot.read_text(encoding="utf-8"))
        head = git(ROOT, "rev-parse", "HEAD").decode().strip()
        base = git(ROOT, "rev-parse", "origin/dev").decode().strip()
        contains_dev = subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", base, head],
                                      check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode == 0
        errors = check_dev_delivery(snapshot["pull_request"], snapshot["workflow_run"], snapshot["jobs"],
                                    head, base, args.repository, contains_dev)
        print(json.dumps({"ready": not errors, "head_sha": head, "dev_sha": base, "blockers": errors}, indent=2))
        return bool(errors)
    errors = []
    event_file = os.environ.get("GITHUB_EVENT_PATH")
    if event_file:
        errors.extend(check_event(json.loads(Path(event_file).read_text(encoding="utf-8")),
                                  os.environ.get("GITHUB_REPOSITORY", "")))
    if not args.event_only:
        errors.extend("Unsuccessful/missing job: " + name for name in check_needs(json.loads(os.environ.get("CI_NEEDS", "{}"))))
    for error in errors:
        print(error)
    print("FAIL: CI gate" if errors else "PASS: CI gate")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
