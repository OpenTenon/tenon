#!/usr/bin/env python3
"""Run the complete engineering unittest inventory with source-bound case evidence."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
import unittest

from engineering_common import ROOT, git, run_id, source_state, utc_now, write_json


OUTCOMES = ("PASS", "FAIL", "ERROR", "SKIP", "EXPECTED_FAILURE", "UNEXPECTED_SUCCESS", "NOT_RUN", "RUNNING")


def flatten(suite):
    """Preserve loader order; never consume the executable suite during reporting."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        elif item is None or not callable(getattr(item, "id", None)):
            raise ValueError("Discovery returned an object without a test identity")
        else:
            yield item


def case_record(test) -> dict:
    identity = test.id()
    if not isinstance(identity, str) or not identity:
        raise ValueError("Test identity must be a nonempty string")
    cls = type(test)
    return {"id": identity, "module": cls.__module__, "class": cls.__module__ + "." + cls.__qualname__,
            "status": "NOT_RUN", "started_at": None, "completed_at": None,
            "duration_seconds": None, "reason": None, "subtests": []}


class TimingResult(unittest.TextTestResult):
    """Measure setup/body/teardown/cleanup; checkpoint IO runs outside case timers."""
    def __init__(self, stream, descriptions, verbosity, *, report, checkpoint, clock=time.perf_counter):
        super().__init__(stream, descriptions, verbosity)
        self.report, self.checkpoint, self.clock = report, checkpoint, clock
        self.rows = {row["id"]: row for row in report["cases"]}
        self.starts = {}

    def startTest(self, test):
        super().startTest(test)
        row = self.rows.get(test.id())
        if row is None:
            self.report["errors"].append("Execution introduced a case outside its selected inventory: " + test.id())
            row = case_record(test)
            row["discovered"] = test.id() in self.report.get("discovery", {}).get("case_ids", [])
            row["selected"] = False
            self.report["cases"].append(row)
            self.rows[test.id()] = row
        row.update(status="RUNNING", started_at=utc_now())
        self.checkpoint()
        self.starts[test.id()] = self.clock()

    def stopTest(self, test):
        # Read the clock before super/report serialization or filesystem writes.
        ended = self.clock()
        row = self.rows[test.id()]
        started = self.starts.pop(test.id(), None)
        row.update(duration_seconds=max(0.0, ended - started) if started is not None else None,
                   completed_at=utc_now())
        if row["status"] == "RUNNING":
            if row["subtests"] and all(item["status"] in {"PASS", "SKIP"} for item in row["subtests"]):
                row["status"] = "PASS"
            else:
                row.update(status="ERROR", reason="Test stopped without a reported outcome")
                self.report["errors"].append(row["reason"] + ": " + row["id"])
        super().stopTest(test)
        self.checkpoint()

    def outcome(self, test, status, reason=None, details=None):
        row = self.rows.get(test.id())
        if row is not None:
            # Cleanup/subtest errors must never be overwritten by a later success.
            rank = {"ERROR": 5, "FAIL": 4, "UNEXPECTED_SUCCESS": 3, "EXPECTED_FAILURE": 2, "SKIP": 1, "PASS": 0}
            if rank.get(row["status"], -1) <= rank[status]:
                row.update(status=status, reason=reason)
            if details:
                row.setdefault("details", []).append(details)
            return
        parent = getattr(test, "test_case", None)
        if parent is not None and parent.id() in self.rows:
            self.rows[parent.id()]["subtests"].append({"id": test.id(), "status": status, "reason": reason})
            return
        event = {"id": test.id(), "status": status, "reason": reason, "details": details,
                 "duration_seconds": None}
        self.report["fixture_events"].append(event)
        # unittest reports class/module setup skips as _ErrorHolder events, without
        # startTest. Attribute them only to exact declared module/class identities.
        match = re.fullmatch(r"(setUpClass|setUpModule|tearDownClass|tearDownModule) \((.+)\)", test.id())
        if match and match[1].startswith("setUp"):
            key = "class" if match[1] == "setUpClass" else "module"
            for candidate in self.report["cases"]:
                if candidate[key] == match[2] and candidate["status"] == "NOT_RUN":
                    candidate["reason"] = reason if status == "SKIP" else "Blocked by " + test.id()
                    candidate["fixture_event"] = test.id()
                    if status == "SKIP":
                        candidate["status"] = "SKIP"
        self.checkpoint()

    def addSuccess(self, test):
        super().addSuccess(test)
        self.outcome(test, "PASS")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.outcome(test, "FAIL", details=self.failures[-1][1])

    def addError(self, test, err):
        super().addError(test, err)
        self.outcome(test, "ERROR", details=self.errors[-1][1])

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.outcome(test, "SKIP", reason=reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.outcome(test, "EXPECTED_FAILURE", details=self.expectedFailures[-1][1])

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.outcome(test, "UNEXPECTED_SUCCESS", reason="An expectedFailure test unexpectedly passed")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        state = "PASS" if err is None else "FAIL" if issubclass(err[0], test.failureException) else "ERROR"
        entry = {"id": subtest.id(), "status": state, "reason": None}
        if err is not None:
            entry["details"] = (self.failures if state == "FAIL" else self.errors)[-1][1]
            self.outcome(test, state)
        self.rows[test.id()]["subtests"].append(entry)

    def interrupted(self, reason):
        for identity in list(self.starts):
            row = self.rows[identity]
            row.update(status="ERROR", reason=reason, completed_at=utc_now(),
                       duration_seconds=max(0.0, self.clock() - self.starts.pop(identity)))


def shard_options(index, count) -> bool:
    if index is None and count is None:
        return False
    if type(index) is not int or type(count) is not int or count != 2 or index not in range(count):
        raise ValueError("Both --shard-index 0|1 and --shard-count 2 are required")
    return True


def discover_inventory(root: Path = ROOT, *, loader=None) -> dict:
    """Independently discover this checkout; never run its suite or test bodies."""
    root = root.resolve()
    before = source_state(root)
    try:
        loader = loader or unittest.TestLoader()
        suite = loader.discover(str(root / "tests/engineering"), pattern="test*.py")
        if loader.errors:
            raise ValueError("Import errors: " + "\n".join(loader.errors))
        records = [case_record(test) for test in flatten(suite)]
        identities = [row["id"] for row in records]
        if not identities or len(identities) != len(set(identities)):
            raise ValueError("Independent discovery is empty or has duplicate case identities")
        inventory = [{key: row[key] for key in ("id", "module", "class")} for row in records]
    except (Exception, KeyboardInterrupt, SystemExit) as error:
        raise ValueError("Independent unittest discovery failed: " + type(error).__name__ + ": " + str(error)) from error
    finally:
        after = source_state(root)
        if before != after:
            raise ValueError("Source changed during independent unittest discovery")
    return {"source": before, "source_after": after, "source_unchanged": True, "cases": inventory}


def load_allocation(root: Path) -> tuple[dict, str]:
    content = (root / "tests/engineering/windows-shards.json").read_bytes()
    value = json.loads(content)
    if (type(value.get("schema_version")) is not int or value.get("schema_version") != 1 or value.get("kind") != "windows-unittest-shards"
            or type(value.get("shard_count")) is not int or value["shard_count"] != 2
            or value.get("unknown_module_assignment") != "sha256-utf8-big-endian-modulo"):
        raise ValueError("Unsupported Windows module allocation")
    modules = value.get("module_assignments")
    if not isinstance(modules, dict) or not modules:
        raise ValueError("The measured module allocation is empty")
    if any(not isinstance(name, str) or not name or type(index) is not int or index not in (0, 1)
           for name, index in modules.items()):
        raise ValueError("Invalid module allocation entry")
    return value, hashlib.sha256(content).hexdigest()


def module_owner(module: str, allocation: dict) -> tuple[int, str]:
    if module in allocation["module_assignments"]:
        return allocation["module_assignments"][module], "manifest"
    return int.from_bytes(hashlib.sha256(module.encode("utf-8")).digest(), "big") % 2, "stable-hash"


def module_assignments(records: list[dict], allocation: dict) -> list[dict]:
    return [{"module": name, "index": module_owner(name, allocation)[0],
             "source": module_owner(name, allocation)[1]} for name in sorted({row["module"] for row in records})]


def select_suite(suite, identities: set[str]):
    # Flattening custom suite wrappers could discard their execution semantics.
    # Full runs retain them; sharding refuses unsupported wrappers explicitly.
    if type(suite) is not unittest.TestSuite:
        raise ValueError("Module sharding supports standard unittest.TestSuite only")
    children = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            children.append(select_suite(item, identities))
        elif item.id() in identities:
            children.append(item)
    return unittest.TestSuite(children)


def github_context(root: Path) -> dict | None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    workspace = os.environ.get("GITHUB_WORKSPACE")
    if not workspace:
        raise ValueError("GitHub workspace identity is missing")
    if Path(workspace).resolve() != root:
        # Nested disposable repositories are test fixtures, not the CI checkout.
        return None
    values = {name: os.environ.get(name, "") for name in
              ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_REPOSITORY", "GITHUB_JOB")}
    if (not re.fullmatch(r"[0-9a-f]{40}", values["GITHUB_SHA"])
            or any(not re.fullmatch(r"[1-9][0-9]*", values[name]) for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"))
            or not values["GITHUB_REPOSITORY"] or not values["GITHUB_JOB"]):
        raise ValueError("GitHub run/attempt/checkout identity is incomplete")
    return {"checkout_sha": values["GITHUB_SHA"], "run_id": int(values["GITHUB_RUN_ID"]),
            "run_attempt": int(values["GITHUB_RUN_ATTEMPT"]), "repository": values["GITHUB_REPOSITORY"],
            "job": values["GITHUB_JOB"]}


def report_destination(root: Path, path: Path) -> Path:
    """Validate before any mkdir/write, including resolved parent directory links."""
    path = path if path.is_absolute() else root / path
    expected_directory = root / "build/verification"
    directory = expected_directory.resolve()
    destination = path.resolve()
    if not directory.is_relative_to(expected_directory) or not destination.is_relative_to(directory) or destination == directory:
        raise ValueError("Unittest reports must remain inside this repository's build/verification")
    relative = destination.relative_to(root).as_posix()
    if git(root, "ls-files", "-z", "--cached", "--", ":(literal)" + relative):
        raise ValueError("Refusing to overwrite a Git-tracked unittest report")
    return destination


def run_tests(root: Path = ROOT, *, report_path: Path | None = None, loader=None, stream=None,
              shard_index: int | None = None, shard_count: int | None = None,
              modules: list[str] | None = None) -> tuple[Path, dict]:
    """Default full execution; explicit partial runs own complete modules only."""
    sharded = shard_options(shard_index, shard_count)
    focused = modules is not None
    if focused and (sharded or not isinstance(modules, list) or not modules
                    or any(not isinstance(name, str) or not name for name in modules)
                    or len(modules) != len(set(modules))):
        raise ValueError("Select unique modules explicitly, without CI shard options")
    partial = sharded or focused
    kind = "engineering-unittest-focus" if focused else "engineering-unittest-shard" if sharded else "engineering-unittest"
    root = root.resolve()
    identifier = run_id()
    report_path = report_destination(root, report_path or root / "build/verification" / ("unittest-" + identifier) / "summary.json")
    report = {"schema_version": 1, "kind": kind,
              "partial": partial, "ci": None, "run_id": identifier,
              "started_at": utc_now(), "completed_at": None, "duration_seconds": None,
              "source": None, "source_after": None, "source_unchanged": None,
              "python": {"version": sys.version, "implementation": platform.python_implementation(), "executable": sys.executable},
              "platform": {"os": os.name, "description": platform.platform()},
              "discovery": {"start_directory": "tests/engineering", "pattern": "test*.py", "case_ids": [],
                            "complete": False, "errors": [], "duplicate_ids": []},
              "cases": [], "fixture_events": [], "errors": [], "status": "RUNNING",
              "qualification": "Complete local unittest observation; skips and expected failures remain explicit; no physical signoff"}
    def checkpoint():
        counts = Counter(row["status"] for row in report["cases"])
        selection = report.get("shard", report.get("focus", {}))
        report["counts"] = {"discovered": len(report["discovery"]["case_ids"]), "selected": len(selection.get("selected_ids", report["discovery"]["case_ids"])),
                            "started": sum(row["started_at"] is not None for row in report["cases"]),
                            "outcomes": {state: counts[state] for state in OUTCOMES}}
        write_json(report_path, report)
    began = time.perf_counter()
    result = None
    try:
        report["source"] = source_state(root)
        report["ci"] = github_context(root)
        if report["ci"] and report["ci"]["checkout_sha"] != report["source"]["sha"]:
            raise ValueError("The checkout differs from GITHUB_SHA")
        checkpoint()
        loader = loader or unittest.TestLoader()
        suite = loader.discover(str(root / "tests/engineering"), pattern="test*.py")
        report["cases"] = [case_record(test) for test in flatten(suite)]
        identities = [row["id"] for row in report["cases"]]
        report["discovery"].update(case_ids=identities, errors=list(loader.errors), complete=not loader.errors,
            duplicate_ids=sorted(identity for identity, count in Counter(identities).items() if count > 1))
        report["discovery"]["cases"] = [{key: row[key] for key in ("id", "module", "class")} for row in report["cases"]]
        if sharded:
            allocation, allocation_sha = load_allocation(root)
            assignments = module_assignments(report["cases"], allocation)
            selected = [row for row in report["cases"] if module_owner(row["module"], allocation)[0] == shard_index]
            report["shard"] = {"index": shard_index, "count": shard_count, "manifest_sha256": allocation_sha,
                               "assignments": assignments, "selected_ids": [row["id"] for row in selected]}
            report["cases"] = selected
            suite = select_suite(suite, set(report["shard"]["selected_ids"]))
            if not selected:
                report["errors"].append("The requested shard selected zero cases")
        if focused:
            report["qualification"] = "Partial local module observation; not full or CI verification evidence"
            available = {row["module"] for row in report["cases"]}
            missing = set(modules) - available
            selected = [row for row in report["cases"] if row["module"] in modules] if not missing else []
            report["focus"] = {"modules": sorted(modules), "selected_ids": [row["id"] for row in selected]}
            report["cases"] = selected
            if missing:
                report["errors"].append("Unknown test modules: " + ", ".join(sorted(missing)))
            elif selected:
                suite = select_suite(suite, set(report["focus"]["selected_ids"]))
        if not identities:
            report["errors"].append("Discovery found zero test cases")
        if report["discovery"]["duplicate_ids"]:
            report["errors"].append("Discovery returned duplicate test identities")
        checkpoint()
        if identities and report["cases"] and not report["discovery"]["duplicate_ids"]:
            def result_factory(output, descriptions, verbosity):
                nonlocal result
                result = TimingResult(output, descriptions, verbosity, report=report, checkpoint=checkpoint)
                return result
            unittest.TextTestRunner(stream=stream or sys.stderr, verbosity=2, resultclass=result_factory).run(suite)
    except (Exception, KeyboardInterrupt, SystemExit) as error:
        report["errors"].append(type(error).__name__ + ": " + str(error))
        report["exception"] = traceback.format_exc()
        if result is not None:
            result.interrupted(report["errors"][-1])
    finally:
        try:
            report["source_after"] = source_state(root)
            report["source_unchanged"] = report["source"] is not None and report["source"] == report["source_after"]
            if not report["source_unchanged"]:
                report["errors"].append("Repository source changed during discovery or test execution")
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            report["errors"].append("Source fingerprint unavailable: " + str(error))
        complete = all(row["status"] not in {"NOT_RUN", "RUNNING"} for row in report["cases"])
        report["status"] = "PASS" if result is not None and result.wasSuccessful() and complete and report["discovery"]["complete"] and not report["errors"] else "FAIL"
        report.update(completed_at=utc_now(), duration_seconds=max(0.0, time.perf_counter() - began),
                      tests_run=result.testsRun if result is not None else 0, all_cases_accounted=complete)
        modules = []
        for name in sorted({row["module"] for row in report["cases"]}):
            rows = [row for row in report["cases"] if row["module"] == name]
            durations = [row["duration_seconds"] for row in rows if row["duration_seconds"] is not None]
            modules.append({"module": name, "cases": len(rows), "timing_samples": len(durations),
                "unknown_durations": len(rows) - len(durations),
                "duration_seconds": sum(durations) if durations else None,
                "outcomes": dict(Counter(row["status"] for row in rows))})
        report["modules"] = modules
        report["module_timing_qualification"] = "Sum of measured cases; excludes shared module/class fixtures, discovery and reporting IO"
        checkpoint()
    return report_path, report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--report", type=Path, help="Path inside root/build/verification; tracked files and escaping links are refused")
    parser.add_argument("--shard-index", type=int, choices=(0, 1))
    parser.add_argument("--shard-count", type=int, choices=(2,))
    parser.add_argument("--module", action="append", help="Run a complete local test module; may be repeated")
    args = parser.parse_args(argv)
    try:
        path, report = run_tests(args.root, report_path=args.report,
                                shard_index=args.shard_index, shard_count=args.shard_count, modules=args.module)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("FAIL: " + str(error), file=sys.stderr)
        return 1
    print(report["status"] + ": " + str(path))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
