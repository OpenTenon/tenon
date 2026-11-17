#!/usr/bin/env python3
"""Run the complete engineering unittest inventory with source-bound case evidence."""
from __future__ import annotations

import argparse
from collections import Counter
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
            self.report["errors"].append("Execution introduced an undiscovered case: " + test.id())
            row = case_record(test)
            row["discovered"] = False
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


def run_tests(root: Path = ROOT, *, report_path: Path | None = None, loader=None, stream=None) -> tuple[Path, dict]:
    """Default full discovery. This API deliberately provides no shard/filter option."""
    root = root.resolve()
    identifier = run_id()
    report_path = report_destination(root, report_path or root / "build/verification" / ("unittest-" + identifier) / "summary.json")
    report = {"schema_version": 1, "kind": "engineering-unittest", "run_id": identifier,
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
        report["counts"] = {"discovered": len(report["discovery"]["case_ids"]),
                            "started": sum(row["started_at"] is not None for row in report["cases"]),
                            "outcomes": {state: counts[state] for state in OUTCOMES}}
        write_json(report_path, report)
    began = time.perf_counter()
    result = None
    try:
        report["source"] = source_state(root)
        checkpoint()
        loader = loader or unittest.TestLoader()
        suite = loader.discover(str(root / "tests/engineering"), pattern="test*.py")
        report["cases"] = [case_record(test) for test in flatten(suite)]
        identities = [row["id"] for row in report["cases"]]
        report["discovery"].update(case_ids=identities, errors=list(loader.errors), complete=not loader.errors,
            duplicate_ids=sorted(identity for identity, count in Counter(identities).items() if count > 1))
        if not identities:
            report["errors"].append("Discovery found zero test cases")
        if report["discovery"]["duplicate_ids"]:
            report["errors"].append("Discovery returned duplicate test identities")
        checkpoint()
        if identities and not report["discovery"]["duplicate_ids"]:
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
    args = parser.parse_args(argv)
    try:
        path, report = run_tests(args.root, report_path=args.report)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("FAIL: " + str(error), file=sys.stderr)
        return 1
    print(report["status"] + ": " + str(path))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
