"""Cross-check recovery, cache integrity and optional model-evaluation signals."""
from __future__ import annotations
import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from test_engineering import ROOT, RepositoryFixture, ci_gate
import test_engineering as fixtures
import ci_tools
import engineering
import engineering_common as common
import physical
import repo_policy
import skill_eval
import task
import check_format
import time


class AdditionalEvidenceTests(RepositoryFixture):
    def test_no_executed_checks_cannot_become_pass_from_source_invariant(self):
        value = common.new_summary(self.root, "verification")
        path = self.root / "build/no-checks.json"
        common.finish_summary(value, self.root, path)
        self.assertEqual(value["status"], "NOT_RUN")
        with self.assertRaises(ValueError):
            common.validate_evidence(path, self.root)

    def test_ci_stub_cannot_be_reported_as_a_real_pdk_model(self):
        pdk_root = self.root / "build/external-pdk"
        (pdk_root / "ihp-sg13g2").mkdir(parents=True)
        with mock.patch.dict(os.environ, {"PDK_ROOT":str(pdk_root),
            "IHP_IO_MODEL":str(ROOT / "tb/ci_sg13g2_io_stub.sv")}):
            value = engineering.pdk_info("ihp130", ROOT)
        self.assertEqual(value["status"], "BLOCKED")
        self.assertTrue(value["ci_model_rejected"])

    def test_empty_make_default_does_not_consume_the_following_line(self):
        (self.root / "Makefile").write_text("TENON_EMPTY_FIXTURE ?=\nTENON_NEXT_FIXTURE := literal\n", encoding="utf-8")
        values = engineering.make_values(self.root)
        self.assertEqual(values["TENON_EMPTY_FIXTURE"], "")
        self.assertEqual(values["TENON_NEXT_FIXTURE"], "literal")

    def test_required_check_cannot_be_made_optional(self):
        path, value = self.evidence()
        value["checks"][0].update(required=False, status="NOT_RUN")
        common.write_json(path, value)
        with self.assertRaises(ValueError):
            common.validate_evidence(path, self.root, {"fixture"})

    def test_verification_history_preserves_failed_attempts_and_unknown_counts(self):
        task.start("history", "test", ["*"], ["make verify"], [], self.root)
        path, value = self.evidence()
        value["group"] = "all"
        value["status"] = "FAIL"
        common.write_json(path, value)
        common.record_attempt(self.root, path, value)
        common.record_attempt(self.root, path, value)
        stored = json.loads(task.task_path("history", self.root).read_text(encoding="utf-8"))
        self.assertEqual(len(stored["verification_attempts"]), 1)
        report = task.metrics(self.root / "build/tasks")
        self.assertEqual(report["first_executed_verification_pass_fraction"], 0)
        self.assertIsNone(report["human_interventions"])

    def test_release_preparation_rejects_dirty_or_unverified_candidate(self):
        self.run_git("update-ref", "refs/remotes/origin/main", "HEAD")
        (self.root / "input.txt").write_text("dirty", encoding="utf-8")
        path, summary = engineering.release_check(root=self.root)
        self.assertEqual(summary["status"], "BLOCKED")
        self.assertTrue(path.exists())
        self.assertEqual(summary["release"]["merge_operator"], "human")


    def test_real_library_evidence_tracks_external_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            view = Path(directory) / "io-model.v"
            view.write_text("// external fixture v1", encoding="utf-8")
            path, value = self.evidence()
            value.update(kind="pdk-verification", pdk={"id":"ihp130", "required_files":[
                {"path":str(view),"exists":True,"sha256":common.digest(view)}]})
            common.write_json(path,value)
            self.assertEqual(common.validate_evidence(path,self.root)["status"],"PASS")
            view.write_text("// external fixture v2",encoding="utf-8")
            with self.assertRaises(ValueError):
                common.validate_evidence(path,self.root)

    def test_timeout_terminates_descendants_before_they_write(self):
        child = "import time; from pathlib import Path; time.sleep(0.8); Path('build/escaped-child').touch()"
        parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); time.sleep(20)"
        result = common.execute([sys.executable, "-c", parent], self.root, self.root / "build/tree.log", timeout=0.1)
        time.sleep(1)
        self.assertEqual(result["exit_code"], 124)
        self.assertFalse((self.root / "build/escaped-child").exists())

    def test_release_requires_real_pdk_evidence_for_changed_adapter(self):
        self.run_git("update-ref", "refs/remotes/origin/main", "HEAD")
        path = self.root / "rtl/tenon_tier0_padframe.sv"
        path.parent.mkdir()
        path.write_text("// disposable release-diff fixture\n", encoding="utf-8")
        self.run_git("add", ".")
        self.run_git("commit", "--quiet", "-m", "fixture adapter change")
        self.run_git("update-ref", "refs/remotes/origin/dev", "HEAD")
        fast = {"kind":"verification", "checks":[{"id":name, "status":"PASS"} for name in engineering.required_ids("all")]}
        with mock.patch.object(engineering, "validate_evidence", return_value=fast):
            _, summary = engineering.release_check(root=self.root, evidence=[self.root / "fake-report.json"])
        check = next(item for item in summary["checks"] if item["id"] == "required-pdk-verification")
        self.assertEqual(check["status"], "BLOCKED")
        self.assertEqual(check["missing_pdks"], ["ihp130"])

    def test_evaluation_isolation_preserves_source_and_excludes_ignored_runs(self):
        self.run_git("update-ref", "refs/remotes/origin/main", "HEAD")
        (self.root / "untracked-tool.py").write_text("# fixture tool\n", encoding="utf-8")
        cache = self.root / "build/private-run"
        cache.mkdir(parents=True)
        (cache / "do-not-copy").write_text("fixture artifact", encoding="utf-8")
        before = common.source_state(self.root)
        fixture = self.root / "build/isolated-eval"
        with mock.patch.object(skill_eval, "ROOT", self.root):
            skill_eval.isolate(fixture)
        self.assertEqual(common.source_state(self.root), before)
        self.assertTrue((fixture / "untracked-tool.py").exists())
        self.assertFalse((fixture / "build/private-run").exists())


class PhysicalFailureTests(fixtures.PhysicalEvidenceTests):
    # Reuse fixture setup, not the inherited test methods.
    test_existing_native_run_is_not_overwritten = None
    test_workspace_lease_excludes_second_run = None
    test_native_stage_hash_changes_invalidate_evidence = None

    def test_aliased_workspace_and_resolved_config_share_one_native_path(self):
        with tempfile.TemporaryDirectory() as directory:
            alias = Path(directory) / "workspace-alias"
            try:
                alias.symlink_to(self.root.resolve(), target_is_directory=True)
            except OSError:
                self.skipTest("This host does not permit directory symlinks")
            run = physical.PhysicalRun(alias, "ihp130", self.config.resolve(), "tenon-qfn32")
            self.assertEqual(run.summary["config"], "flow/ihp130/config.yaml")
            self.assertEqual(run.native.resolve(), self.config.resolve().parent / "runs/tenon-qfn32")

    def test_missing_environment_blocks_before_process_launch(self):
        run = physical.PhysicalRun(self.root, "ihp130", self.config, "tenon-qfn32")
        with mock.patch.object(physical, "doctor", return_value={"tools":{"librelane":{"status":"BLOCKED"}}}), mock.patch.object(physical, "execute") as execute:
            with run:
                self.assertFalse(run.command(["librelane", "config.yaml"]))
            execute.assert_not_called()
        self.assertEqual(run.summary["status"], "BLOCKED")

    def test_corrupt_native_metrics_retain_failure_summary(self):
        with (self.root / ".gitignore").open("a", encoding="utf-8") as output:
            output.write("flow/*/runs/\n")
        run = physical.PhysicalRun(self.root, "ihp130", self.config, "tenon-qfn32")
        command = [sys.executable,"-c","from pathlib import Path; p=Path('flow/ihp130/runs/tenon-qfn32/final'); p.mkdir(parents=True); (p/'metrics.json').write_text('broken json')"]
        with run:
            self.assertTrue(run.command(command))
        retained = json.loads(run.path.read_text(encoding="utf-8"))
        self.assertEqual(retained["status"], "FAIL")
        self.assertFalse(run.lock.path.exists())

    def test_policy_supports_joined_override_syntax_without_bypass(self):
        with mock.patch.dict(os.environ, {"SKIP_DRC":"0"}), self.assertRaises(ValueError):
            physical.validate_policy(["librelane","--override-config=RUN_NETGEN_LVS=false"],"ihp130","tenon-qfn32")


    def test_external_pdk_interface_change_invalidates_previous_run(self):
        with tempfile.TemporaryDirectory() as directory:
            view = Path(directory) / "io.lef"
            view.write_text("external interface v1", encoding="utf-8")
            info = {"status":"PASS", "required_files":[{"path":str(view),"exists":True}]}
            with mock.patch.object(physical, "pdk_info", return_value=info):
                run = physical.PhysicalRun(self.root, "ihp130", self.config, "tenon-qfn32")
                command = [sys.executable,"-c","from pathlib import Path; p=Path('flow/ihp130/runs/tenon-qfn32/final'); p.mkdir(parents=True); (p/'metrics.json').write_text('{}')"]
                with run:
                    self.assertTrue(run.command(command))
                self.assertEqual(physical.read_run_evidence(run.native, self.root)["status"], "PASS")
                view.write_text("external interface v2", encoding="utf-8")
                with self.assertRaises(ValueError):
                    physical.read_run_evidence(run.native, self.root)


class CacheAndSkillTests(unittest.TestCase):
    def test_download_cache_is_hash_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "tool.tar.gz"
            archive.write_bytes(b"verified")
            expected = common.digest(archive)
            self.assertTrue(ci_tools.verify_archive(archive, expected))
            archive.write_bytes(b"corrupted cached binary")
            self.assertFalse(ci_tools.verify_archive(archive, expected))

    def test_tool_archive_rejects_traversal_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for symbolic in (False, True):
                archive = directory / ("link.tar.gz" if symbolic else "traversal.tar.gz")
                with tarfile.open(archive, "w:gz") as output:
                    member = tarfile.TarInfo("link" if symbolic else "../escaped")
                    if symbolic:
                        member.type = tarfile.SYMTYPE
                        member.linkname = "../escaped"
                        output.addfile(member)
                    else:
                        member.size = 1
                        output.addfile(member, io.BytesIO(b"x"))
                with self.assertRaises(ValueError):
                    ci_tools.unpack(archive, directory / "out")
            self.assertFalse((directory / "escaped").exists())

    def test_repository_skills_have_valid_resolvable_contracts(self):
        self.assertEqual(repo_policy.check_skills(ROOT), [])
        cases = json.loads((ROOT / "tests/skill-evals/cases.json").read_text(encoding="utf-8"))["cases"]
        self.assertEqual(len(cases), 10)
        self.assertEqual(len({case["id"] for case in cases}), 10)
        self.assertEqual({case["skill"] for case in cases if case["skill"]}, repo_policy.SKILLS)

    def test_skill_trace_signals_require_completion_and_correct_trigger(self):
        case = {"skill":"tenon-verify", "tokens":["BLOCKED"]}
        events = [{"type":"item.completed","item":{"type":"command_execution","command":"cat .agents/skills/tenon-verify/SKILL.md"}},
                  {"type":"item.completed","item":{"type":"agent_message","text":"BLOCKED: missing prerequisites"}},
                  {"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":20}}]
        self.assertEqual(skill_eval.score(case, events, 0)["status"], "PASS")
        self.assertEqual(skill_eval.score(case, events[:-1], 0)["status"], "FAIL")
        self.assertEqual(skill_eval.score({"skill":None}, events, 0)["status"], "FAIL")

    def test_skill_trace_with_source_mutation_does_not_pass(self):
        events = [{"type":"item.completed", "item":{"type":"file_change","changes":[{"path":"rtl/design.sv"}]}},
                  {"type":"turn.completed"}]
        result = skill_eval.score({"skill":None}, events, 0)
        self.assertTrue(result["source_mutation_detected"])
        self.assertEqual(result["status"], "FAIL")

    def test_crlf_format_check_is_canonical_and_preserves_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "rtl").mkdir()
            source = root / "rtl/fixture.sv"
            config = root / "formatter.flags"
            source.write_bytes(b"module fixture;\r\nendmodule\r\n")
            config.write_bytes(b"--assignment_statement_alignment=align\r\n")
            before = source.read_bytes()
            def formatter(command, *_):
                flagfile = Path(next(arg.split("=",1)[1] for arg in command if arg.startswith("--flagfile=")))
                self.assertNotIn(b"\r", flagfile.read_bytes())
                self.assertNotIn(b"\r", Path(command[-1]).read_bytes())
            with mock.patch.object(check_format, "run", side_effect=formatter):
                check_format.format_rtl("fixture-formatter", config, [Path("rtl/fixture.sv")], root, True)
            self.assertEqual(source.read_bytes(), before)

    def test_ci_workflow_has_complete_always_run_aggregation(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("Workflow parser is provided by the Linux workflow-check toolchain")
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
        jobs = workflow["jobs"]
        self.assertEqual(set(jobs["required"]["needs"]), ci_gate.REQUIRED_JOBS)
        self.assertEqual(jobs["required"]["if"], "always()")
        self.assertEqual(jobs["required"]["name"], "ci-required")
        events = workflow.get("on", workflow.get(True))
        self.assertEqual(set(events["push"]["branches"]), {"dev","main"})


if __name__ == "__main__":
    unittest.main()
