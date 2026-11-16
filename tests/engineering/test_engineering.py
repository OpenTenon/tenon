"""Regression checks for observable engineering contracts; no production PDK required."""
from __future__ import annotations
import contextlib
import copy
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "smoke"))
import adapter_compile
import ci_gate
import engineering
import engineering_common as common
import physical
import task
import design_manifest


def load_generator():
    spec = importlib.util.spec_from_file_location("tenon_generator_under_test", ROOT / "tools/generate_tier0.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RepositoryFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="tenon-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run_git("init", "--quiet")
        self.run_git("config", "user.name", "Tenon test fixture")
        self.run_git("config", "user.email", "fixture@example.invalid")
        self.run_git("config", "core.autocrlf", "false")
        (self.root / ".gitignore").write_text("build/\n", encoding="utf-8")
        (self.root / "input.txt").write_text("original\n", encoding="utf-8")
        self.run_git("add", ".")
        self.run_git("commit", "--quiet", "-m", "disposable test fixture")
        self.run_git("update-ref", "refs/remotes/origin/dev", "HEAD")
        self.run_git("checkout", "--quiet", "-b", "dev-fixture-test")

    def run_git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.PIPE)

    def evidence(self):
        value = common.new_summary(self.root, "verification")
        directory = self.root / "build" / value["run_id"]
        directory.mkdir(parents=True)
        value["checks"].append(common.execute([sys.executable, "-c", "print('verified fixture')"],
            self.root, directory / "check.log", check_id="fixture"))
        path = directory / "summary.json"
        common.finish_summary(value, self.root, path)
        return path, value


class EvidenceTests(RepositoryFixture):
    def test_source_matched_command_and_log_are_accepted(self):
        path, _ = self.evidence()
        self.assertEqual(common.validate_evidence(path, self.root, {"fixture"})["status"], "PASS")

    def test_untracked_file_invalidates_earlier_evidence(self):
        path, _ = self.evidence()
        (self.root / "new-source.py").write_text("print('new')\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            common.validate_evidence(path, self.root)

    def test_modified_source_invalidates_earlier_evidence(self):
        path, _ = self.evidence()
        (self.root / "input.txt").write_text("changed\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            common.validate_evidence(path, self.root)

    def test_wrong_sha_missing_check_and_corrupt_log_are_rejected(self):
        for mode in ("sha", "inventory", "log"):
            with self.subTest(mode=mode):
                path, value = self.evidence()
                if mode == "sha":
                    value["source"]["sha"] = "0" * 40
                    common.write_json(path, value)
                elif mode == "log":
                    (path.parent / "check.log").write_text("changed", encoding="utf-8")
                with self.assertRaises(ValueError):
                    common.validate_evidence(path, self.root, {"missing"} if mode == "inventory" else {"fixture"})

    def test_dry_run_is_not_passing_evidence(self):
        value = common.new_summary(self.root, "verification")
        directory = self.root / "build/dry"
        directory.mkdir(parents=True)
        value["checks"].append(common.execute([sys.executable, "-c", "raise SystemExit(99)"],
            self.root, directory / "dry.log", dry_run=True))
        path = directory / "summary.json"
        common.finish_summary(value, self.root, path)
        self.assertEqual(value["status"], "NOT_RUN")
        self.assertFalse((directory / "dry.log").exists())
        with self.assertRaises(ValueError):
            common.validate_evidence(path, self.root)

    def test_mutating_check_fails_readonly_contract(self):
        value = common.new_summary(self.root, "verification")
        value["checks"].append({"id": "fixture", "status": "PASS"})
        (self.root / "input.txt").write_text("unexpected mutation", encoding="utf-8")
        common.finish_summary(value, self.root, self.root / "build/report.json")
        self.assertEqual(value["status"], "FAIL")

    def test_missing_executable_is_blocked(self):
        result = common.execute(["tenon-missing-tool-000"], self.root, self.root / "build/missing.log")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(result["log_sha256"])

    def test_nonzero_exit_and_timeout_propagate(self):
        failure = common.execute([sys.executable, "-c", "raise SystemExit(7)"], self.root, self.root / "build/fail.log")
        timed = common.execute([sys.executable, "-c", "import time; time.sleep(20)"], self.root,
                               self.root / "build/timeout.log", timeout=0.1)
        self.assertEqual(failure["exit_code"], 7)
        self.assertEqual(timed["exit_code"], 124)
        self.assertEqual(timed["status"], "FAIL")

    def test_log_cannot_escape_report_directory(self):
        path, value = self.evidence()
        value["checks"][0]["log"] = "../../input.txt"
        common.write_json(path, value)
        with self.assertRaises(ValueError):
            common.validate_evidence(path, self.root)

    def test_missing_tools_do_not_skip_available_checks_as_success(self):
        with mock.patch.object(engineering, "doctor", return_value={"tools": {"make": {"status": "BLOCKED"}}}):
            path, value = engineering.verify(group="format", root=self.root)
        self.assertEqual(value["status"], "BLOCKED")
        self.assertTrue(path.exists())
        self.assertFalse(any(item.get("exit_code") == 0 for item in value["checks"] if "command" in item))


class PhysicalPolicyTests(unittest.TestCase):
    def setUp(self):
        self.environment = mock.patch.dict(os.environ, {name: "0" for name in (
            "SKIP_DRC", "SKIP_MAGIC_SPICE", "SKIP_NETGEN_LVS", "UNSIGNED_IO_PG")})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_standard_command_keeps_all_checks(self):
        self.assertEqual(physical.validate_policy(["librelane", "config.yaml"], "ihp130", "tenon-qfn32"), ([], []))

    def test_unapproved_checker_skips_are_rejected(self):
        for step in ("Magic.SpiceExtraction", "Netgen.LVS", "Checker.DisconnectedPins", "OpenROAD.CheckAntennas", "Checker.PowerGridViolations"):
            with self.subTest(step=step), self.assertRaises(ValueError):
                physical.validate_policy(["librelane", "--skip", step], "ics55", "tenon-qfn32-no-pll")

    def test_drc_reference_is_required_and_scoped(self):
        with mock.patch.dict(os.environ, {"SKIP_DRC": "1", "TENON_DRC_APPROVAL": ""}):
            with self.assertRaises(ValueError):
                physical.validate_policy(["librelane", "--skip", "Magic.DRC"], "ihp130", "tenon-qfn32")
        with mock.patch.dict(os.environ, {"SKIP_DRC": "1", "TENON_DRC_APPROVAL": "fixture-authorization"}):
            exceptions, skipped = physical.validate_policy(["librelane", "--skip", "Magic.DRC"], "ihp130", "tenon-qfn32")
            self.assertEqual(skipped, ["Magic.DRC"])
            self.assertTrue(exceptions)
            with self.assertRaises(ValueError):
                physical.validate_policy(["librelane", "--skip", "Netgen.LVS"], "ihp130", "tenon-qfn32")

    def test_unsigned_requires_named_variant_and_acknowledgement(self):
        command = ["librelane", "--override-config", "ERROR_ON_PDN_VIOLATIONS=false", "--skip", "OpenROAD.IRDropReport"]
        with self.assertRaises(ValueError):
            physical.validate_policy(command, "ics55", "tenon-qfn32-no-pll-unsigned-io-pg")
        with mock.patch.dict(os.environ, {"UNSIGNED_IO_PG": "1"}):
            self.assertEqual(physical.validate_policy(command, "ics55", "tenon-qfn32-no-pll-unsigned-io-pg")[1], ["OpenROAD.IRDropReport"])
            with self.assertRaises(ValueError):
                physical.validate_policy(command, "ics55", "tenon-qfn32-pll")

    def test_disabling_checks_with_overrides_is_rejected(self):
        for assignment in ("ERROR_ON_LVS_ERROR=false", "RUN_MAGIC_DRC=false", "RUN_NETGEN_LVS=false"):
            with self.subTest(assignment=assignment), self.assertRaises(ValueError):
                physical.validate_policy(["librelane", "--override-config", assignment], "ihp130", "tenon-qfn32")

    def test_overwrite_is_always_rejected(self):
        with self.assertRaises(ValueError):
            physical.validate_policy(["librelane", "--overwrite"], "ihp130", "tenon-qfn32")


class PhysicalEvidenceTests(RepositoryFixture):
    def setUp(self):
        super().setUp()
        with (self.root / ".gitignore").open("a", encoding="utf-8") as handle:
            handle.write("flow/*/runs/\n")
        self.config = self.root / "flow/ihp130/config.yaml"
        self.config.parent.mkdir(parents=True)
        self.config.write_text("fixture", encoding="utf-8")
        self.pdk_patch = mock.patch.object(physical, "pdk_info", return_value={"status": "PASS", "required_files": []})
        self.doctor_patch = mock.patch.object(physical, "doctor", return_value={"tools": {}})
        self.pdk_patch.start()
        self.doctor_patch.start()
        self.addCleanup(self.pdk_patch.stop)
        self.addCleanup(self.doctor_patch.stop)

    def test_existing_native_run_is_not_overwritten(self):
        run = physical.PhysicalRun(self.root, "ihp130", self.config, "tenon-qfn32")
        run.native.mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            with run:
                self.fail("Existing run entered")
        self.assertFalse(run.lock.path.exists())

    def test_workspace_lease_excludes_second_run(self):
        with common.PhysicalLock(self.root, "one"):
            with self.assertRaises(FileExistsError):
                with common.PhysicalLock(self.root, "two"):
                    self.fail("Concurrent physical lease acquired")
        self.assertFalse((self.root / "build/locks/physical.lock").exists())

    def test_native_stage_hash_changes_invalidate_evidence(self):
        run = physical.PhysicalRun(self.root, "ihp130", self.config, "tenon-qfn32")
        # Native fixtures are ignored, just as real flow run directories are.
        with (self.root / ".gitignore").open("a", encoding="utf-8") as handle:
            handle.write("flow/*/runs/\n")
        run = physical.PhysicalRun(self.root, "ihp130", self.config, "tenon-qfn32")
        command = [sys.executable, "-c", "from pathlib import Path; p=Path('flow/ihp130/runs/tenon-qfn32/final'); p.mkdir(parents=True); (p/'metrics.json').write_text('{}')"]
        with run:
            self.assertTrue(run.command(command))
        self.assertEqual(physical.read_run_evidence(run.native, self.root)["status"], "PASS")
        (run.native / "final/metrics.json").write_text('{"changed": true}', encoding="utf-8")
        with self.assertRaises(ValueError):
            physical.read_run_evidence(run.native, self.root)


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.generator = load_generator()
        self.spec = json.loads((ROOT / "specs/tier0_profiles.json").read_text(encoding="utf-8"))
        self.pdks = json.loads((ROOT / "specs/tier0_pdks.json").read_text(encoding="utf-8"))

    def test_package_contract_and_inventory_include_all_generated_classes(self):
        outputs = self.generator.expected_outputs(self.spec, self.pdks)
        names = {path.relative_to(ROOT).as_posix() for path in outputs}
        for profile in self.spec["profiles"]:
            records = self.generator.build_records(self.spec, profile)
            self.assertEqual([record["pin"] for record in records], list(range(1, profile["package_leads"] + 1)))
            self.assertEqual(sum(record["function"] == "GPIO" for record in records), profile["gpio_count"])
            for pdk in ("ihp130", "sky130", "gf180"):
                self.assertIn(f"flow/{pdk}/{profile['id']}.yaml", names)
            for variant in ("no-pll", "pll"):
                self.assertIn(f"flow/ics55/generated/{profile['id']}-{variant}_placement.tcl", names)
        self.assertIn("rtl/tenon_tier0_padframe_ics55_no_pll_fillers.sv", names)
        self.assertIn("flow/sky130/generated/qfn128_pdn_bridge.tcl", names)

    def test_invalid_package_count_is_rejected(self):
        profile = copy.deepcopy(self.spec["profiles"][0])
        profile["package_leads"] = 33
        with self.assertRaises(ValueError):
            self.generator.build_records(self.spec, profile)

    def test_check_detects_missing_stale_obsolete_files_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "output.txt"
            obsolete = root / "obsolete.txt"
            obsolete.write_text("retained", encoding="utf-8")
            for existing in (None, "stale", "expected"):
                if existing is not None:
                    expected.write_text(existing, encoding="utf-8")
                before = {p.name: p.read_bytes() for p in root.iterdir()}
                with mock.patch.multiple(self.generator, ROOT=root, SPEC_PATH=ROOT / "specs/tier0_profiles.json", PDK_SPEC_PATH=ROOT / "specs/tier0_pdks.json"), mock.patch.object(self.generator, "expected_outputs", return_value={expected: "expected"}), mock.patch.object(self.generator, "obsolete_outputs", return_value=[obsolete]), mock.patch.object(sys, "argv", ["generator", "--check"]), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(self.generator.main(), 1)
                self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        shutil.copytree(ROOT / "smoke/inverter", root / "inverter")
        (root / "manifest.json").write_text(json.dumps({"schema_version": 1, "designs": ["inverter"]}), encoding="utf-8")
        self.root = root
        self.patcher = mock.patch.multiple(design_manifest, ROOT=root, CATALOG_PATH=root / "manifest.json")
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_existing_manifest_is_reused_without_new_schema(self):
        self.assertEqual(design_manifest.load_designs()[0]["name"], "inverter")

    def test_unknown_duplicate_and_missing_sources_fail(self):
        with self.assertRaises(design_manifest.ManifestError):
            design_manifest.load_designs(["unknown"])
        (self.root / "manifest.json").write_text(json.dumps({"schema_version": 1, "designs": ["inverter", "inverter"]}), encoding="utf-8")
        with self.assertRaises(design_manifest.ManifestError):
            design_manifest.load_designs()
        (self.root / "manifest.json").write_text(json.dumps({"schema_version": 1, "designs": ["inverter"]}), encoding="utf-8")
        (self.root / "inverter/inverter.sv").unlink()
        with self.assertRaises(design_manifest.ManifestError):
            design_manifest.load_designs()


class IntegrationContractTests(unittest.TestCase):
    def test_ci_gate_fails_for_every_unsuccessful_or_missing_required_job(self):
        good = {name: {"result": "success"} for name in ci_gate.REQUIRED_JOBS}
        self.assertFalse(ci_gate.check_needs(good))
        for name in good:
            for outcome in ("failure", "cancelled", "skipped", "neutral", None):
                bad = copy.deepcopy(good)
                bad[name]["result"] = outcome
                self.assertIn(name, ci_gate.check_needs(bad))
            bad = copy.deepcopy(good)
            del bad[name]
            self.assertIn(name, ci_gate.check_needs(bad))

    def test_pr_directions_and_human_main_preparation(self):
        event = {"pull_request": {"base": {"ref": "dev"}, "head": {"ref": "dev-123-test", "repo": {"full_name": "OpenTenon/tenon"}}, "user": {"type": "User"}}}
        self.assertFalse(ci_gate.check_event(event, "OpenTenon/tenon"))
        event["pull_request"]["base"]["ref"] = "main"
        self.assertTrue(ci_gate.check_event(event, "OpenTenon/tenon"))
        event["pull_request"]["head"]["ref"] = "dev"
        self.assertFalse(ci_gate.check_event(event, "OpenTenon/tenon"))
        event["pull_request"]["user"]["type"] = "Bot"
        self.assertFalse(ci_gate.check_event(event, "OpenTenon/tenon"))
        event["pull_request"]["head"]["repo"]["full_name"] = "another/repository"
        self.assertTrue(ci_gate.check_event(event, "OpenTenon/tenon"))

    def test_adapter_coverage_is_compilation_only_and_complete(self):
        configs = list(adapter_compile.configurations())
        self.assertEqual(len(configs), 20)
        self.assertEqual(len({top for top, _ in configs}), 20)
        for top, command in configs:
            self.assertIn("-tnull", command)
            self.assertTrue(any(item.startswith("tb/ci_") for item in command))
            self.assertIn(top, command)

    @unittest.skipUnless(shutil.which("make"), "GNU Make is required for command dispatch tests")
    def test_test_target_runs_real_library_compile_and_simulator(self):
        result = subprocess.run(["make", "--no-print-directory", "-n", "test", "IHP_IO_MODEL=/external/ihp-model.v"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        self.assertIn("-s tenon_tier0_tb", result.stdout)
        self.assertIn("/external/ihp-model.v", result.stdout)
        self.assertIn("vvp build/tests/tenon_tier0_tb.vvp", result.stdout)

    @unittest.skipUnless(shutil.which("make"), "GNU Make is required for command dispatch tests")
    def test_ics55_lint_does_not_dispatch_ihp_tests(self):
        result = subprocess.run(["make", "--no-print-directory", "-n", "lint-ics55-pll"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        self.assertNotIn("-s tenon_tier0_tb", result.stdout)
        self.assertNotIn("build/tests/tenon_tier0_tb.vvp", result.stdout)
        self.assertIn("tenon_tier0_ics55_qfn128_pll", result.stdout)


class TaskTests(RepositoryFixture):
    def test_resume_preserves_contract_and_detects_out_of_scope_change(self):
        value = task.start("fixture", "test tooling", ["input.txt"], ["make verify"], [], self.root)
        restored = json.loads(task.task_path("fixture", self.root).read_text(encoding="utf-8"))
        self.assertEqual(value, restored)
        with self.assertRaises(FileExistsError):
            task.start("fixture", "overwrite", ["*"], ["make verify"], [], self.root)
        (self.root / "outside.sv").write_text("fixture", encoding="utf-8")
        self.assertEqual(task.scope_errors(restored, self.root), ["outside.sv"])

    def test_cleanup_blocks_dirty_active_and_native_artifacts(self):
        task.start("fixture", "test", ["*"], ["make verify"], [], self.root)
        self.assertTrue(any("Active task" in item for item in task.cleanup_check(self.root)))
        artifacts = self.root / "flow/ihp130/runs/example"
        artifacts.mkdir(parents=True)
        (artifacts / "metrics.json").write_text("{}", encoding="utf-8")
        self.assertTrue(any("Native run" in item for item in task.cleanup_check(self.root)))

    def test_unknown_manual_counts_are_not_reported_as_zero(self):
        values = task.metrics(self.root / "build/tasks")
        self.assertIsNone(values["human_interventions"])
        self.assertFalse(values["baseline_ready"])


if __name__ == "__main__":
    unittest.main()
