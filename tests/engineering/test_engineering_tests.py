"""Observable full-inventory unittest reports; disposable repositories, no model/network calls."""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import engineering
import engineering_common
import engineering_tests as runner


class RunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tenon-unittest-runner-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "tests/engineering"
        self.directory.mkdir(parents=True)
        (self.root / ".gitignore").write_text("build/\n__pycache__/\n", encoding="utf-8")
        (self.root / "input.txt").write_text("original\n", encoding="utf-8")
        for args in [("init", "--quiet"), ("config", "user.name", "Tenon fixture"),
                     ("config", "user.email", "fixture@example.invalid"),
                     ("config", "core.autocrlf", "false"), ("add", "."),
                     ("commit", "--quiet", "-m", "fixture")]:
            subprocess.run(["git", *args], cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    def module(self, body):
        name = "test_fixture_" + uuid.uuid4().hex
        (self.directory / (name + ".py")).write_text("import unittest\n" + body, encoding="utf-8")
        return name

    def run_fixture(self, **kwargs):
        return runner.run_tests(self.root, stream=io.StringIO(), **kwargs)

    def test_complete_discovery_timings_skip_reason_and_module_sum(self):
        name = self.module("""import time
class First(unittest.TestCase):
    def test_pass(self): time.sleep(0.02)
    @unittest.skip("deliberate fixture prerequisite")
    def test_skip(self): raise AssertionError("must not execute")
class Second(unittest.TestCase):
    def test_other(self): self.assertEqual(2 + 2, 4)
""")
        path, report = self.run_fixture()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["discovery"]["case_ids"], [name + ".First.test_pass", name + ".First.test_skip", name + ".Second.test_other"])
        self.assertTrue(report["all_cases_accounted"])
        cases = {row["id"].rsplit(".", 1)[1]: row for row in report["cases"]}
        self.assertGreaterEqual(cases["test_pass"]["duration_seconds"], 0.015)
        self.assertEqual(cases["test_skip"]["status"], "SKIP")
        self.assertEqual(cases["test_skip"]["reason"], "deliberate fixture prerequisite")
        self.assertEqual(report["modules"][0]["module"], name)
        self.assertEqual(report["modules"][0]["duration_seconds"], sum(row["duration_seconds"] for row in report["cases"]))
        self.assertEqual(report["modules"][0]["timing_samples"], 3)
        self.assertEqual(report["source"], engineering_common.source_state(self.root))
        self.assertTrue(report["python"]["version"])
        self.assertTrue(report["platform"]["description"])
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), report)
        self.assertTrue(path.is_relative_to(self.root.resolve() / "build/verification"))

    def test_focused_cli_runs_complete_selected_modules_and_rejects_full_evidence(self):
        first = self.module("class First(unittest.TestCase):\n"
                            "    def test_a(self): pass\n    def test_b(self): pass\n")
        second = self.module("class Second(unittest.TestCase):\n    def test_c(self): pass\n")
        omitted = self.module("class Omitted(unittest.TestCase):\n"
                              "    def test_fails_if_executed(self): self.fail('unselected')\n")
        report_path = self.root / "build/verification/focused.json"
        proc = subprocess.run([sys.executable, str(ROOT / "scripts/engineering_tests.py"),
                               "--root", str(self.root), "--report", str(report_path),
                               "--module", second, "--module", first], capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode(errors="replace"))
        report = json.loads(report_path.read_bytes())
        self.assertEqual(report["kind"], "engineering-unittest-focus")
        self.assertIs(report["partial"], True)
        self.assertEqual(report["counts"]["discovered"], 4)
        self.assertEqual(report["counts"]["selected"], 3)
        self.assertEqual({row["module"] for row in report["cases"]}, {first, second})
        self.assertTrue(any(case.startswith(omitted + ".") for case in report["discovery"]["case_ids"]))
        self.assertTrue(all(row["status"] == "PASS" for row in report["cases"]))
        with self.assertRaisesRegex(ValueError, "Partial"):
            engineering_common.validate_evidence(report_path, self.root)

    def test_focused_report_cannot_qualify_as_attached_full_evidence(self):
        name = self.module("class Focused(unittest.TestCase):\n    def test_a(self): pass\n")
        partial_path, partial = self.run_fixture(modules=[name])
        self.assertEqual(partial["status"], "PASS")
        result = engineering_common.new_summary(self.root, "verification")
        check = engineering_common.execute([sys.executable, "-c", "print('fixture')"],
                                           self.root, partial_path.parent / "tools.log", check_id="tools-test")
        check["test_report"] = {"path": partial_path.name, "sha256": engineering_common.digest(partial_path)}
        result["checks"].append(check)
        full_path = partial_path.parent / "verification.json"
        engineering_common.finish_summary(result, self.root, full_path)
        with self.assertRaisesRegex(ValueError, "Partial or invalid test report"):
            engineering_common.validate_evidence(full_path, self.root, {"tools-test"})

        # A report reference still needs validation if its check has no command.
        reference = check.pop("test_report")
        check["id"] = "other-executed-check"
        other = engineering_common.new_summary(self.root, "verification")
        other["checks"] = [check, {"id": "tools-test", "status": "PASS", "required": True, "test_report": reference}]
        engineering_common.finish_summary(other, self.root, full_path)
        with self.assertRaisesRegex(ValueError, "Partial or invalid test report"):
            engineering_common.validate_evidence(full_path, self.root, {"tools-test"})

    def test_focused_selection_fails_closed_on_unknown_duplicate_or_sharded_options(self):
        name = self.module("class Known(unittest.TestCase):\n"
                           "    def test_fails_if_executed(self): self.fail('invalid selection')\n")
        _, report = self.run_fixture(modules=[name, "test_missing"])
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["tests_run"], 0)
        self.assertEqual(report["counts"]["selected"], 0)
        with self.assertRaisesRegex(ValueError, "unique modules"):
            self.run_fixture(modules=[name, name])
        with self.assertRaisesRegex(ValueError, "CI shard"):
            self.run_fixture(modules=[name], shard_index=0, shard_count=2)

    def test_fail_error_expected_failure_unexpected_success_and_subtests(self):
        self.module("""class Cases(unittest.TestCase):
    def test_fail(self): self.fail("fixture failure")
    def test_error(self): raise RuntimeError("fixture error")
    @unittest.expectedFailure
    def test_expected(self): self.fail("expected fixture")
    @unittest.expectedFailure
    def test_unexpected(self): pass
    def test_subfail(self):
        with self.subTest(value=1): self.assertEqual(1, 2)
        with self.subTest(value=2): self.assertEqual(2, 2)
    def test_suberror(self):
        with self.subTest(value=3): raise ValueError("subcase error")
""")
        _, report = self.run_fixture()
        cases = {row["id"].rsplit(".", 1)[1]: row for row in report["cases"]}
        self.assertEqual(report["status"], "FAIL")
        for name, status in [("test_fail", "FAIL"), ("test_error", "ERROR"), ("test_expected", "EXPECTED_FAILURE"),
                             ("test_unexpected", "UNEXPECTED_SUCCESS"), ("test_subfail", "FAIL"), ("test_suberror", "ERROR")]:
            self.assertEqual(cases[name]["status"], status)
        self.assertEqual([item["status"] for item in cases["test_subfail"]["subtests"]], ["FAIL", "PASS"])
        self.assertEqual(cases["test_suberror"]["subtests"][0]["status"], "ERROR")
        self.assertTrue(all(row["duration_seconds"] is not None for row in cases.values()))

    def test_expected_failure_alone_passes_but_unexpected_success_cli_is_nonzero(self):
        p = self.module("""class Expected(unittest.TestCase):
    @unittest.expectedFailure
    def test_expected(self): self.fail("expected")
""")
        self.assertEqual(self.run_fixture()[1]["status"], "PASS")
        (self.directory / (p + ".py")).unlink()
        self.module("""class Unexpected(unittest.TestCase):
    @unittest.expectedFailure
    def test_unexpected(self): pass
""")
        report_path = self.root / "build/verification/cli.json"
        process = subprocess.run([sys.executable, str(ROOT / "scripts/engineering_tests.py"),
                                  "--root", str(self.root), "--report", str(report_path)], capture_output=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(json.loads(report_path.read_text(encoding="utf-8"))["cases"][0]["status"], "UNEXPECTED_SUCCESS")

    def test_class_setup_skip_and_error_never_fabricate_case_execution(self):
        self.module("""class Skipped(unittest.TestCase):
    @classmethod
    def setUpClass(cls): raise unittest.SkipTest("class prerequisite")
    def test_skip(self): raise AssertionError("must not run")
class Broken(unittest.TestCase):
    @classmethod
    def setUpClass(cls): raise RuntimeError("class setup error")
    def test_blocked(self): raise AssertionError("must not run")
""")
        _, report = self.run_fixture()
        cases = {row["id"].rsplit(".", 1)[1]: row for row in report["cases"]}
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(cases["test_skip"]["status"], "SKIP")
        self.assertEqual(cases["test_skip"]["reason"], "class prerequisite")
        self.assertEqual(cases["test_blocked"]["status"], "NOT_RUN")
        self.assertIsNone(cases["test_skip"]["duration_seconds"])
        self.assertIsNone(cases["test_blocked"]["duration_seconds"])
        self.assertEqual({event["status"] for event in report["fixture_events"]}, {"ERROR", "SKIP"})

    def test_shared_teardown_error_is_separate_from_successful_case(self):
        self.module("""class CleanBody(unittest.TestCase):
    @classmethod
    def tearDownClass(cls): raise RuntimeError("shared teardown")
    def test_body(self): pass
""")
        _, report = self.run_fixture()
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["cases"][0]["status"], "PASS")
        self.assertEqual(report["fixture_events"][0]["status"], "ERROR")

    def test_import_error_keeps_healthy_module_and_readable_failure_report(self):
        self.module("this is not valid Python !!!\n")
        self.module("class Healthy(unittest.TestCase):\n    def test_ok(self): pass\n")
        path, report = self.run_fixture()
        self.assertEqual(report["status"], "FAIL")
        self.assertFalse(report["discovery"]["complete"])
        self.assertTrue(report["discovery"]["errors"])
        self.assertEqual({row["status"] for row in report["cases"]}, {"ERROR", "PASS"})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["status"], "FAIL")

    def test_empty_and_duplicate_inventories_fail_without_running_duplicates(self):
        self.assertEqual(self.run_fixture()[1]["status"], "FAIL")
        self.module("""class Duplicate(unittest.TestCase):
    def test_case(self): raise RuntimeError("must not run duplicate")
def load_tests(loader, tests, pattern):
    return unittest.TestSuite([Duplicate("test_case"), Duplicate("test_case")])
""")
        _, report = self.run_fixture()
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(len(report["discovery"]["duplicate_ids"]), 1)
        self.assertEqual(len(report["discovery"]["case_ids"]), 2)
        self.assertEqual(report["tests_run"], 0)

    def test_discovery_exception_and_interrupt_checkpoint_are_readable(self):
        loader = mock.Mock(errors=[], discover=mock.Mock(side_effect=RuntimeError("loader stopped")))
        path, report = self.run_fixture(loader=loader)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("loader stopped", report["errors"][0])
        self.assertTrue(json.loads(path.read_text(encoding="utf-8"))["completed_at"])
        self.module("class Interrupted(unittest.TestCase):\n    def test_interrupt(self): raise KeyboardInterrupt('fixture interrupt')\n")
        path, report = self.run_fixture()
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["cases"][0]["status"], "ERROR")
        self.assertIsNotNone(report["cases"][0]["duration_seconds"])
        self.assertTrue(json.loads(path.read_text(encoding="utf-8"))["completed_at"])

    def test_source_fingerprint_runs_only_at_boundaries_and_mutation_fails(self):
        self.module("""from pathlib import Path
class Mutation(unittest.TestCase):
    def test_change(self):
        (Path(__file__).resolve().parents[2] / "input.txt").write_text("changed")
""")
        with mock.patch.object(runner, "source_state", wraps=engineering_common.source_state) as fingerprint:
            _, report = self.run_fixture()
        self.assertEqual(fingerprint.call_count, 2)
        self.assertEqual(report["cases"][0]["status"], "PASS")
        self.assertFalse(report["source_unchanged"])
        self.assertEqual(report["status"], "FAIL")

    def test_case_timer_excludes_checkpoint_serialization_and_writes(self):
        case = unittest.FunctionTestCase(lambda: None)
        report = {"cases": [runner.case_record(case)], "errors": [], "fixture_events": []}
        clock = [0.0]
        def slow_checkpoint(): clock[0] += 100.0
        stream = unittest.TextTestRunner(stream=io.StringIO()).stream
        result = runner.TimingResult(stream, True, 0, report=report, checkpoint=slow_checkpoint, clock=lambda: clock[0])
        result.startTest(case)
        clock[0] += 2.5
        result.addSuccess(case)
        result.stopTest(case)
        self.assertEqual(report["cases"][0]["duration_seconds"], 2.5)
        self.assertEqual(clock[0], 202.5)

    def test_report_source_and_parent_escape_cli_preserve_original_bytes(self):
        source = self.root / "input.txt"
        before = source.read_bytes()
        for target in [source, self.root / "build/verification/../../input.txt"]:
            with self.subTest(target=target):
                process = subprocess.run([sys.executable, str(ROOT / "scripts/engineering_tests.py"),
                    "--root", str(self.root), "--report", str(target)], capture_output=True)
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(source.read_bytes(), before)
        self.assertFalse((self.root / "build").exists())

    def test_report_cannot_overwrite_force_tracked_verification_file(self):
        report = self.root / "build/verification/owned.json"
        report.parent.mkdir(parents=True)
        report.write_bytes(b"tracked report bytes\n")
        subprocess.run(["git", "add", "--force", "build/verification/owned.json"], cwd=self.root, check=True)
        before = report.read_bytes()
        with self.assertRaisesRegex(ValueError, "Git-tracked"):
            self.run_fixture(report_path=report)
        self.assertEqual(report.read_bytes(), before)

    def test_report_directory_link_to_external_storage_is_rejected(self):
        external = tempfile.TemporaryDirectory(prefix="tenon-external-report-")
        self.addCleanup(external.cleanup)
        target = Path(external.name)
        build = self.root / "build"
        build.mkdir()
        try:
            (build / "verification").symlink_to(target, target_is_directory=True)
        except OSError as error:
            self.skipTest("Directory link capability unavailable: " + str(error))
        before = sorted(target.iterdir())
        with self.assertRaisesRegex(ValueError, "build/verification"):
            self.run_fixture(report_path=build / "verification/escaped.json")
        self.assertEqual(sorted(target.iterdir()), before)

    def test_native_verification_uses_runner_and_binds_case_report(self):
        (self.root / "scripts").mkdir()
        # The fixture runs the real runner, against only its own test inventory.
        scripts = self.root / "scripts"
        for name in ["engineering_tests.py", "engineering_common.py"]:
            shutil.copy2(ROOT / "scripts" / name, scripts / name)
        (scripts / "repo_policy.py").write_text("print('fixture skill gate')\n", encoding="utf-8")
        self.module("class Native(unittest.TestCase):\n    def test_native(self): pass\n")
        with mock.patch.object(engineering, "doctor", return_value={"tools": {"git": {"status": "PASS"}}}):
            path, report = engineering.verify(group="tools", root=self.root)
        self.assertEqual(report["status"], "PASS")
        check = next(item for item in report["checks"] if item["id"] == "tools-test")
        self.assertEqual(check["status"], "PASS")
        self.assertEqual(check["command"][1], "scripts/engineering_tests.py")
        cases_path = path.parent / check["test_report"]["path"]
        self.assertEqual(check["test_report"]["sha256"], engineering_common.digest(cases_path))
        self.assertEqual(json.loads(cases_path.read_text(encoding="utf-8"))["cases"][0]["status"], "PASS")

    @unittest.skipUnless(shutil.which("make"), "GNU Make is required for Make dispatch")
    def test_make_dispatches_the_same_full_runner(self):
        process = subprocess.run(["make", "--no-print-directory", "-n", "tools-test", "PYTHON=" + sys.executable],
                                 cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertEqual(process.stdout.strip().split(), [sys.executable, "scripts/engineering_tests.py"])


if __name__ == "__main__":
    unittest.main()
