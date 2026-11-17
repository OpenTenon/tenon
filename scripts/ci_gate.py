#!/usr/bin/env python3
"""Fail closed on missing CI jobs and enforce PR integration directions."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime
import math
import re
import json
import os
import subprocess
from pathlib import Path

from engineering_common import ROOT, git

REQUIRED_JOBS = {"format", "check", "sim", "tools-windows", "tools-windows-extra"}
WINDOWS_JOBS = {0: "tools-windows", 1: "tools-windows-extra"}
CRITICAL_WINDOWS_CASES = (
    "test_background.QueueTests.test_real_windows_interrupted_git_removal_resumes_after_handle_release",
    "test_lifecycle_v2.ArchiveAndCleanupTests.test_native_windows_probe_observes_a_real_sharing_lease_without_deletion",
)


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


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _timestamp(value):
    _require(isinstance(value, str) and value, "Execution timestamp is missing")
    parsed = datetime.fromisoformat(value)
    _require(parsed.tzinfo is not None, "Execution timestamp has no timezone")
    return parsed


def check_windows_reports(reports: list[dict], expected: dict, allocation: dict, allocation_sha: str,
                          trusted_inventory: list[dict]) -> list[str]:
    """Match partial observations to independent checkout discovery, never report-derived expectations."""
    from engineering_tests import OUTCOMES, module_assignments, module_owner
    errors, seen, selected_sets, baselines, all_rows = [], [], [], [], {}
    try:
        _require(len(reports) == 2, "Exactly two Windows reports are required")
        _require(re.fullmatch(r"[0-9a-f]{40}", expected["checkout_sha"]), "Invalid expected checkout SHA")
        _require(all(type(expected[key]) is int and expected[key] > 0 for key in ("run_id", "run_attempt")), "Invalid expected run/attempt")
        _require(isinstance(expected["repository"], str) and expected["repository"], "Missing expected repository")
        _require(re.fullmatch(r"[0-9a-f]{64}", allocation_sha), "Invalid allocation hash")
        _require(isinstance(trusted_inventory, list) and trusted_inventory, "Trusted checkout inventory is required")
        trusted_ids = [row["id"] for row in trusted_inventory]
        _require(all(isinstance(identity, str) and identity for identity in trusted_ids)
                 and len(trusted_ids) == len(set(trusted_ids)), "Trusted checkout inventory is empty or duplicated")
        for row in trusted_inventory:
            _require(set(row) == {"id", "module", "class"} and isinstance(row["module"], str) and row["module"]
                     and isinstance(row["class"], str) and row["class"].startswith(row["module"] + ".")
                     and row["id"].startswith(row["class"] + "."), "Trusted checkout identity is invalid")
    except (KeyError, TypeError, ValueError) as error:
        return ["Invalid Windows aggregation inputs: " + str(error)]
    for number, report in enumerate(reports):
        try:
            _require(type(report["schema_version"]) is int and report["schema_version"] == 1 and report["kind"] == "engineering-unittest-shard"
                     and report["partial"] is True, "Report is not explicit partial unittest evidence")
            _require(report["status"] == "PASS" and isinstance(report["run_id"], str) and report["run_id"], "Report is unfinished or failed")
            _require(_timestamp(report["completed_at"]) >= _timestamp(report["started_at"]), "Report timestamps are reversed")
            _require(isinstance(report["errors"], list) and not report["errors"] and not report.get("exception"), "Report has execution/discovery exceptions")
            source = report["source"]
            _require(source["sha"] == expected["checkout_sha"] and source["dirty"] is False, "Stale or dirty checkout")
            _require(all(re.fullmatch(r"[0-9a-f]{64}", source[key]) for key in ("worktree_sha256", "diff_sha256")), "Missing source fingerprints")
            _require(report["source_unchanged"] is True and source == report["source_after"], "Source changed during execution")
            shard, ci = report["shard"], report["ci"]
            index = shard["index"]
            _require(type(index) is int and index in WINDOWS_JOBS and type(shard["count"]) is int and shard["count"] == 2, "Invalid shard index/count")
            seen.append(index)
            _require(index == number, "Artifact directory and shard index differ")
            _require(all(ci[key] == value and type(ci[key]) is type(value) for key, value in expected.items()), "Run/attempt/repository/checkout context differs")
            _require(ci["job"] == WINDOWS_JOBS[index], "Report belongs to the wrong Windows job")
            _require(report["platform"]["os"] == "nt" and report["platform"]["description"].startswith("Windows-"), "Report is not native Windows")
            python = report["python"]
            _require(python["implementation"] == "CPython" and re.match(r"^3\.13\.[0-9]+(?:\s|$)", python["version"]), "Unexpected Windows Python version")
            discovery = report["discovery"]
            ids, inventory = discovery["case_ids"], discovery["cases"]
            _require(discovery["complete"] is True and isinstance(discovery["errors"], list) and not discovery["errors"]
                     and isinstance(discovery["duplicate_ids"], list) and not discovery["duplicate_ids"], "Discovery is incomplete")
            _require(isinstance(ids, list) and ids and all(isinstance(identity, str) and identity for identity in ids)
                     and len(ids) == len(set(ids)), "Discovery is empty or duplicated")
            _require([row["id"] for row in inventory] == ids, "Discovery metadata differs from its case list")
            _require(ids == trusted_ids and inventory == trusted_inventory,
                     "Windows discovery differs from independent current-checkout inventory; platform-dependent inventories are unsupported")
            for row in inventory:
                _require(isinstance(row["module"], str) and row["module"] and row["class"].startswith(row["module"] + ".")
                         and row["id"].startswith(row["class"] + "."), "Case module/class identity is inconsistent")
            assignments = module_assignments(trusted_inventory, allocation)
            _require(shard["manifest_sha256"] == allocation_sha and shard["assignments"] == assignments
                     and all(type(item["index"]) is int for item in shard["assignments"]), "Allocation differs from this source version")
            selected = [row["id"] for row in trusted_inventory if module_owner(row["module"], allocation)[0] == index]
            _require(selected and shard["selected_ids"] == selected, "Selected inventory has omissions, duplicates or wrong ownership")
            rows = report["cases"]
            _require([row["id"] for row in rows] == selected, "Executed cases differ from the selected inventory")
            metadata = {row["id"]: row for row in trusted_inventory}
            fixture_events = report["fixture_events"]
            _require(isinstance(fixture_events, list) and all(event["status"] == "SKIP" and isinstance(event["reason"], str)
                     and event["reason"] for event in fixture_events), "Shared fixture errors are not successful tests")
            for row in rows:
                _require(all(row[key] == metadata[row["id"]][key] for key in ("module", "class")), "Executed case metadata differs")
                _require(row["status"] in {"PASS", "SKIP", "EXPECTED_FAILURE"}, "Case did not successfully finish")
                if row["status"] == "SKIP":
                    _require(isinstance(row["reason"], str) and row["reason"], "Skip reason is missing")
                if row["started_at"] is None:
                    _require(row["status"] == "SKIP" and row["duration_seconds"] is None
                             and any(event["id"] == row.get("fixture_event") and event["reason"] == row["reason"]
                                     for event in fixture_events), "Unexecuted case was presented as completed")
                else:
                    duration = row["duration_seconds"]
                    _require(_timestamp(row["completed_at"]) >= _timestamp(row["started_at"])
                             and type(duration) in (int, float) and math.isfinite(duration) and duration >= 0,
                             "Executed case timing is missing/invalid")
                _require(all(part["status"] in {"PASS", "SKIP"} and (part["status"] != "SKIP" or isinstance(part.get("reason"), str) and part["reason"])
                             for part in row["subtests"]), "A failed/error subtest was hidden by its parent")
            _require(all(any(row.get("fixture_event") == event["id"] for row in rows) for event in fixture_events),
                     "Fixture events include an unselected/unknown module")
            counts = report["counts"]
            outcomes = Counter(row["status"] for row in rows)
            _require(counts["discovered"] == len(ids) and type(counts["discovered"]) is int
                     and counts["selected"] == len(selected) and type(counts["selected"]) is int
                     and counts["outcomes"] == {state: outcomes[state] for state in OUTCOMES}
                     and all(type(value) is int for value in counts["outcomes"].values()), "Case outcome counts differ")
            started = sum(row["started_at"] is not None for row in rows)
            _require(type(report["tests_run"]) is int and report["tests_run"] == started
                     and counts["started"] == started and type(counts["started"]) is int
                     and report["all_cases_accounted"] is True, "Execution is incomplete")
            baselines.append((ids, inventory, source, python["version"], python["implementation"], report["platform"]))
            selected_sets.append(set(selected))
            for row in rows:
                _require(row["id"] not in all_rows, "A case executed in more than one shard")
                all_rows[row["id"]] = row
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            errors.append("Windows report " + str(number) + ": " + str(error))
    if sorted(seen) != [0, 1]:
        errors.append("Windows shard indices are missing or duplicated")
    if len(baselines) != 2 or baselines[0] != baselines[1]:
        errors.append("Windows discovery/source/platform/Python baselines differ")
    if len(baselines) == 2:
        if selected_sets[0] & selected_sets[1] or selected_sets[0] | selected_sets[1] != set(trusted_ids):
            errors.append("Windows executed union is incomplete or duplicated")
    for identity in CRITICAL_WINDOWS_CASES:
        row = all_rows.get(identity)
        if (not row or row["status"] != "PASS" or not row["started_at"]
                or any(part["status"] != "PASS" for part in row["subtests"])):
            errors.append("Required native Windows occupancy case did not PASS: " + identity)
    return errors


def windows_report_errors(directory: Path, root: Path = ROOT) -> list[str]:
    """Discover this checkout and read exactly two artifacts; never execute test bodies."""
    from engineering_tests import discover_inventory, github_context, load_allocation
    try:
        root = root.resolve()
        expected = github_context(root)
        _require(expected is not None, "The CI checkout/run context is required")
        expected = {key: expected[key] for key in ("checkout_sha", "run_id", "run_attempt", "repository")}
        _require(git(root, "rev-parse", "HEAD").decode().strip() == expected["checkout_sha"], "Aggregator checkout differs from GITHUB_SHA")
        trusted = discover_inventory(root)
        _require(trusted["source"]["sha"] == expected["checkout_sha"] and trusted["source"]["dirty"] is False
                 and trusted["source_unchanged"] is True, "Independent discovery is not from the clean current checkout")
        allocation, allocation_sha = load_allocation(root)
        directory = directory if directory.is_absolute() else root / directory
        directory = directory.resolve()
        paths = []
        for index in (0, 1):
            found = list((directory / str(index)).rglob("tools-test.json"))
            _require(len(found) == 1, "Missing or duplicate report for Windows shard " + str(index))
            _require(found[0].resolve().is_relative_to(directory) and found[0].is_file(), "Report path escaped downloaded artifacts")
            paths.append(found[0])
        _require(set(directory.rglob("tools-test.json")) == set(paths), "Unexpected extra Windows case report")
        reports = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
        return check_windows_reports(reports, expected, allocation, allocation_sha, trusted["cases"])
    except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as error:
        return ["Windows reports unavailable/invalid: " + str(error)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--event-only", action="store_true")
    mode.add_argument("--delivery-snapshot", type=Path)
    parser.add_argument("--repository", default="OpenTenon/tenon")
    parser.add_argument("--windows-shards", type=Path)
    args = parser.parse_args()
    if args.windows_shards and (args.event_only or args.delivery_snapshot):
        parser.error("--windows-shards requires the complete CI needs gate")
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
    if args.windows_shards:
        errors.extend(windows_report_errors(args.windows_shards))
    for error in errors:
        print(error)
    print("FAIL: CI gate" if errors else "PASS: CI gate")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
