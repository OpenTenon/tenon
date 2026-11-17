"""Skill routing contracts, conservative budgets and disposable implementation outcomes."""
from __future__ import annotations

import contextlib
import io
import os
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

from test_engineering import ROOT, RepositoryFixture
import repo_policy
import skill_eval
from engineering_common import source_state


class SkillContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="tenon-skill-contract-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in (".agents/skills","scripts","tools","tests/skill-evals","docs/engineering"):
            shutil.copytree(ROOT/name,self.root/name)
        shutil.copy2(ROOT/"Makefile",self.root/"Makefile")

    def edit_skill(self, extra):
        path = self.root/".agents/skills/tenon-verify/SKILL.md"
        path.write_text(path.read_text(encoding="utf-8")+extra,encoding="utf-8")

    def test_real_skills_and_evaluation_suites_have_valid_interfaces(self):
        self.assertEqual(repo_policy.check_skills(ROOT),[])
        baseline = json.loads((ROOT/"tests/skill-evals/cases.json").read_text(encoding="utf-8"))["cases"]
        self.assertEqual(len(baseline),10)
        cases = skill_eval.load_cases(ROOT)
        self.assertEqual(len(cases),23)
        self.assertTrue({case["id"] for case in baseline}.issubset({case["id"] for case in cases}))

    def test_dead_make_and_python_subcommands_are_rejected(self):
        self.edit_skill("\nUse `make missing-target` and `python3 scripts/task.py missing-command`.\n")
        errors = repo_policy.check_skills(self.root)
        self.assertTrue(any("unknown Make target" in error for error in errors))
        self.assertTrue(any("unknown Python command/option" in error for error in errors))

    def test_literal_loop_declared_subcommand_is_a_valid_interface(self):
        self.edit_skill("\nUse `python scripts/engineering.py verify --group tools`.\n")
        self.assertEqual(repo_policy.check_skills(self.root),[])

    def test_stable_commands_validate_nested_scope_choices_and_arity_without_execution(self):
        valid = ["git tenon doctor --full", "git tenon task context --id fixture",
                 "git tenon task note --id fixture --event-id r1 --kind rework --reason observed",
                 "git tenon task attest --id fixture --event-id a1 --human-interventions 0 --rework 1 --evidence-digest digest --coverage-start tracking-start --reason observed",
                 "git tenon task handoff --id fixture --evidence result.json",
                 "git tenon maintenance restore-check --id fixture", "git tenon maintenance drill"]
        self.edit_skill("\n" + "\n".join("Use "+chr(96)+value+chr(96)+"." for value in valid))
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("Never run interfaces")), mock.patch.object(subprocess, "check_output", side_effect=AssertionError("Never import/run entries")):
            self.assertEqual(repo_policy.check_skills(self.root), [])
        for command in ("git tenon task absent --id fixture", "git tenon doctor --event-id x",
                        "git tenon task context --full", "git tenon task note --kind wrong",
                        "git tenon task context --id", "git tenon maintenance drill --force",
                        "git tenon task lease --state invalid"):
            with self.subTest(command=command):
                self.edit_skill("\nUse "+chr(96)+command+chr(96)+".")
                self.assertTrue(any("invalid stable command" in error for error in repo_policy.check_skills(self.root)))

    def test_sorted_static_choices_are_checked_and_runtime_choices_fail_closed(self):
        self.edit_skill("\nUse `python3 scripts/skill_offline.py --fixture-worker owned --scenario partial-evidence`.\n")
        self.assertEqual(repo_policy.check_skills(self.root), [])
        self.edit_skill("\nUse `python3 scripts/skill_offline.py --fixture-worker owned --scenario definitely-invalid`.\n")
        self.assertTrue(any("invalid choice" in error for error in repo_policy.check_skills(self.root)))
        entry=self.root/"scripts/runtime_choices.py"
        entry.write_text("raise AssertionError('Never execute/import this file')\nimport argparse\n"
                         "def main():\n    parser=argparse.ArgumentParser()\n"
                         "    parser.add_argument('--mode',choices=runtime_factory())\n",encoding="utf-8")
        self.edit_skill("\nUse `python3 scripts/runtime_choices.py --mode accepted-by-guess`.\n")
        errors=repo_policy.check_skills(self.root)
        self.assertTrue(any("accepted-by-guess" in error and "invalid choice" in error for error in errors))

    def test_python_nested_and_later_options_are_checked(self):
        self.edit_skill("\nUse "+chr(96)+"python scripts/engineering.py verify --group typo"+chr(96)+".")
        self.assertTrue(any("unknown Python command/option" in error for error in repo_policy.check_skills(self.root)))

    def test_missing_anchor_and_escaping_reference_are_rejected(self):
        self.edit_skill("\n[bad](../../../docs/engineering/WORKFLOW.md#missing-section)\n[outside](../../../../../../outside.md)\n")
        errors = repo_policy.check_skills(self.root)
        self.assertTrue(any("missing reference anchor" in error for error in errors))
        self.assertTrue(any("missing/outside reference" in error for error in errors))

    def test_commands_in_progressively_loaded_reference_are_also_checked(self):
        reference = self.root/".agents/skills/tenon-verify/references/evaluation.md"
        reference.write_text("Use `python3 scripts/skill_eval.py --missing-option`.\n",encoding="utf-8")
        self.assertTrue(any("unknown Python command/option" in error for error in repo_policy.check_skills(self.root)))

    def test_duplicate_cases_and_unimplemented_writable_oracles_are_rejected(self):
        path = self.root/"tests/skill-evals/regressions.json"
        for case in ({"id":"task-start","skill":None,"prompt":"duplicate"},
                     {"id":"new-write","skill":"tenon-task","prompt":"unsafe","mode":"workspace-write","allowed_paths":["*"]}):
            path.write_text(json.dumps({"schema_version":1,"cases":[case]}),encoding="utf-8")
            with self.assertRaises(ValueError):
                skill_eval.load_cases(self.root)


class BudgetTests(unittest.TestCase):
    def test_command_output_mentions_do_not_count_as_reading_a_skill(self):
        events = [{"item":{"type":"command_execution","command":"echo documentation",
                           "aggregated_output":".agents/skills/tenon-verify/SKILL.md"}},
                  {"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}]
        self.assertFalse(skill_eval.score({"skill":"tenon-verify"},events,0)["skill_trigger_matched"])
        self.assertTrue(skill_eval.score({"skill":None},events,0)["skill_trigger_matched"])

    def test_default_plan_and_strict_execution_make_no_model_calls(self):
        for argv, expected in ((["skill_eval"],0),
            (["skill_eval","--execute","--model","fixture-model","--max-calls","2","--token-budget","1000"],2)):
            with (self.subTest(argv=argv), mock.patch.object(sys,"argv",argv), mock.patch.object(skill_eval,"run_bounded") as invoke,
                  mock.patch.object(skill_eval,"TaskStore") as store, contextlib.redirect_stdout(io.StringIO()) as output):
                self.assertEqual(skill_eval.main(),expected)
                self.assertEqual(json.loads(output.getvalue())["model_calls"],0)
                invoke.assert_not_called()
                store.assert_not_called()

    def test_execution_requires_all_explicit_budget_inputs(self):
        with mock.patch.object(sys,"argv",["skill_eval","--execute","--model","fixture-model"]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                skill_eval.main()

    def test_call_limit_unknown_usage_and_token_exhaustion_stop_next_run(self):
        budget = skill_eval.EvaluationBudget(1,100)
        budget.reserve()
        budget.record({"total_tokens":10})
        with self.assertRaises(ValueError):
            budget.reserve()
        for usage in (None,{"total_tokens":101}):
            budget = skill_eval.EvaluationBudget(5,100)
            budget.reserve()
            budget.record(usage)
            with self.assertRaises(ValueError):
                budget.reserve()

    def test_exact_token_limit_can_finish_but_cannot_start_another_invocation(self):
        budget = skill_eval.EvaluationBudget(5,100)
        budget.reserve()
        budget.record({"total_tokens":100})
        self.assertIsNone(budget.blocker)
        with self.assertRaises(ValueError):
            budget.reserve()

    def test_usage_is_not_inferred_from_text_or_malformed_reports(self):
        self.assertIsNone(skill_eval.observed_usage([{"type":"turn.completed"}]))
        self.assertIsNone(skill_eval.observed_usage([{"type":"turn.completed","usage":{"input_tokens":True,"output_tokens":1}}]))
        self.assertEqual(skill_eval.observed_usage([{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":4}}])["total_tokens"],14)
        budget = skill_eval.EvaluationBudget(2,100)
        budget.reserve()
        budget.record(None)
        self.assertIsNone(budget.snapshot()["observed_tokens"])

    def test_nonfinite_timeout_is_rejected_before_launch(self):
        for timeout in (float("nan"),float("inf"),0,-1):
            with self.subTest(timeout=timeout), mock.patch.object(subprocess,"Popen") as launch:
                with self.assertRaises(ValueError):
                    skill_eval.run_bounded(["fixture"],ROOT,ROOT/"build/unused.log",timeout,100)
                launch.assert_not_called()

    def test_path_shim_without_its_runtime_is_an_unavailable_prerequisite(self):
        failure = subprocess.CalledProcessError(127,["fixture-shim","--version"])
        with mock.patch.object(subprocess,"check_output",side_effect=failure):
            with self.assertRaises(ValueError):
                skill_eval.codex_version(["fixture-shim"])

    @unittest.skipUnless(os.name=="nt","Windows shim discovery contract")
    def test_windows_batch_shim_is_not_executed_when_no_safe_node_entry_exists(self):
        with mock.patch.object(shutil,"which",side_effect=lambda name: r"C:\missing\codex.CMD" if name=="codex" else None), mock.patch.dict(os.environ, {"LOCALAPPDATA":r"C:\missing\tenon-fixture"}):
            with self.assertRaises(ValueError):
                skill_eval.codex_command()

    def test_output_limit_and_timeout_terminate_only_owned_subprocess(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = skill_eval.run_bounded([sys.executable,"-c","print('x'*10000)"],root,root/"output.log",10,256)
            self.assertEqual(result["status"],"BLOCKED")
            self.assertEqual(result["reason"],"output-limit")
            self.assertLessEqual((root/"output.log").stat().st_size,256)
            result = skill_eval.run_bounded([sys.executable,"-c","import time; time.sleep(10)"],root,root/"timeout.log",0.1,4096)
            self.assertEqual(result["reason"],"timeout")
            self.assertLess(result["duration_seconds"],5,"The owned process must stop rather than run to natural completion")


class EffectiveConfigurationTests(unittest.TestCase):
    def test_exact_fixture_profile_and_project_inventory_is_verified_twice(self):
        values = [{"name":name,"enabled":True,"transport":{"env":{"DUMMY":"not-an-export"}}} for name in ("base","profile","project")]
        responses = [SimpleNamespace(returncode=0,stdout=json.dumps(values).encode()),
                     SimpleNamespace(returncode=0,stdout=json.dumps([{**value,"enabled":False} for value in values]).encode())]
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(subprocess,"run",side_effect=responses) as query:
            fixture = Path(temporary).resolve()
            overrides = skill_eval.configured_mcp_overrides(fixture,["fixture-cli"],"profile")
            self.assertEqual(len(overrides),6)
            for call in query.call_args_list:
                self.assertEqual(call.kwargs["cwd"],fixture)
                self.assertIn("--profile",call.args[0])
            self.assertNotIn("not-an-export"," ".join(overrides))
            self.assertTrue(all(value.endswith(".enabled=false") for value in overrides[1::2]))

    def test_new_enabled_server_in_verification_probe_blocks(self):
        responses = [SimpleNamespace(returncode=0,stdout=b'[{"name":"base","enabled":true}]'),
                     SimpleNamespace(returncode=0,stdout=b'[{"name":"base","enabled":false},{"name":"project","enabled":true}]')]
        with mock.patch.object(subprocess,"run",side_effect=responses):
            with self.assertRaises(ValueError):
                skill_eval.configured_mcp_overrides(ROOT,["fixture-cli"])

    def test_server_name_unsupported_by_cli_override_stops_before_invocation(self):
        response = SimpleNamespace(returncode=0,stdout=b'[{"name":"ambiguous.name","enabled":true}]')
        with mock.patch.object(subprocess,"run",return_value=response) as query:
            with self.assertRaises(ValueError):
                skill_eval.configured_mcp_overrides(ROOT,["fixture-cli"])
            query.assert_called_once()

    def test_installed_cli_resolves_base_profile_and_trusted_project_without_model_calls(self):
        from skill_offline import configuration_check
        result = configuration_check()
        if result["status"] == "NOT_RUN":
            self.skipTest(result["diagnostic"])
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["model_calls"], 0)
        self.assertTrue(all(result["assertions"].values()))


class ImplementationFixtureTests(RepositoryFixture):
    def setUp(self):
        super().setUp()
        self.run_git("update-ref","refs/remotes/origin/main","HEAD")
        (self.root/"scripts").mkdir()
        (self.root/"tools").mkdir()
        (self.root/"scripts/fixture.py").write_text("# fixture source\n",encoding="utf-8")
        (self.root/"tools/generate_tier0.py").write_text("raise SystemExit(0)\n",encoding="utf-8")
        self.run_git("add",".")
        self.run_git("commit","--quiet","-m","isolated fixture support")
        self.run_git("update-ref","refs/remotes/origin/dev","HEAD")
        self.fixture = self.root/"build/isolated-implementation"
        self.original = source_state(self.root)
        with mock.patch.object(skill_eval,"ROOT",self.root):
            self.remote = skill_eval.isolate(self.fixture,writable=True)
        skill_eval.prepare_implementation(self.fixture)
        self.before = source_state(self.fixture)
        self.before_refs = skill_eval.ref_snapshot(self.fixture)

    def repair(self):
        (self.fixture/"scripts/eval_target.py").write_text(
            "def exit_status(code):\n    return 'PASS' if code == 0 else 'BLOCKED' if code == 2 else 'FAIL'\n",encoding="utf-8")

    def assertions(self, case_id):
        return skill_eval.fixture_assertions({"id":case_id,"mode":"workspace-write","allowed_paths":["scripts/eval_target.py"]},self.fixture,self.remote,self.before,self.before_refs)

    def test_fixture_contains_only_local_origin_and_preserves_original(self):
        origin = subprocess.check_output(["git","-C",str(self.fixture),"remote","get-url","origin"]).decode().strip()
        self.assertEqual(origin,str(self.remote))
        self.assertEqual(source_state(self.root),self.original)
        self.assertEqual(subprocess.check_output(["git","-C",str(self.fixture),"config","protocol.allow"]).strip(),b"never")

    def test_broken_behavior_cannot_pass_even_with_correct_trace_words(self):
        assertions, paths = self.assertions("implementation-verify")
        self.assertFalse(next(value for value in assertions if value["kind"]=="engineering-behavior")["passed"])
        self.assertEqual(skill_eval.assess_outcome({"status":"PASS"},self.before,source_state(self.fixture),assertions,
            writable=True,changed_paths=paths,allowed_paths=["scripts/eval_target.py"])["status"],"FAIL")

    def test_scoped_real_fix_passes_observable_function_and_ref_assertions(self):
        self.repair()
        assertions, paths = self.assertions("implementation-verify")
        result = skill_eval.assess_outcome({"status":"PASS"},self.before,source_state(self.fixture),assertions,
            writable=True,changed_paths=paths,allowed_paths=["scripts/eval_target.py"])
        self.assertEqual(result["status"],"PASS")
        self.assertEqual(paths,["scripts/eval_target.py"])

    def test_unexpected_local_ref_mutation_fails_file_level_outcome(self):
        self.repair()
        subprocess.run(["git","-C",str(self.fixture),"add","scripts/eval_target.py"],check=True)
        subprocess.run(["git","-C",str(self.fixture),"commit","--quiet","-m","fixture repair"],check=True)
        subprocess.run(["git","-C",str(self.fixture),"push","--quiet","origin","HEAD:refs/heads/dev-eval-result"],check=True)
        assertions, paths = self.assertions("implementation-scope-boundary")
        self.assertFalse(all(value["passed"] for value in assertions),assertions)
        self.assertEqual(paths,["scripts/eval_target.py"])
        self.assertEqual(source_state(self.root),self.original)

    def test_untracked_scope_escape_and_new_ref_fail_outcome(self):
        self.repair()
        (self.fixture/"outside.py").write_text("# out of scope\n",encoding="utf-8")
        subprocess.run(["git","-C",str(self.fixture),"update-ref","refs/heads/unexpected","HEAD"],check=True)
        assertions, paths = self.assertions("implementation-verify")
        result = skill_eval.assess_outcome({"status":"PASS"},self.before,source_state(self.fixture),assertions,
            writable=True,changed_paths=paths,allowed_paths=["scripts/eval_target.py"])
        self.assertEqual(result["status"],"FAIL")

    def test_host_oracle_rejects_arbitrary_generated_code_without_running_it(self):
        marker = self.root/"escaped-host-write"
        (self.fixture/"scripts/eval_target.py").write_text(
            "from pathlib import Path\nPath("+repr(str(marker))+").write_text('unsafe')\ndef exit_status(code): return 'PASS'\n",encoding="utf-8")
        self.assertFalse(skill_eval.implementation_assertion(self.fixture)["passed"])
        self.assertFalse(marker.exists())

    def test_missing_helper_and_index_error_are_failed_assertions(self):
        target = self.fixture/"scripts/eval_target.py"
        target.unlink()
        self.assertFalse(skill_eval.implementation_assertion(self.fixture)["passed"])
        target.write_text("def exit_status(code):\n    return ['PASS','FAIL','BLOCKED'][code]\n",encoding="utf-8")
        self.assertFalse(skill_eval.implementation_assertion(self.fixture)["passed"])

    def test_out_of_scope_generator_is_never_executed_by_host_oracle(self):
        marker = self.root/"generator-host-escape"
        (self.fixture/"tools/generate_tier0.py").write_text("from pathlib import Path\nPath("+repr(str(marker))+").write_text('unsafe')\n",encoding="utf-8")
        assertions,_ = self.assertions("implementation-verify")
        self.assertFalse(all(value["passed"] for value in assertions))
        self.assertFalse(marker.exists())

    def test_observed_runner_stops_batch_on_unknown_usage_or_invocation_limit(self):
        real_check = subprocess.check_output
        def checked(argv,*args,**kwargs):
            return b"fixture-cli" if argv==["fixture-codex","--version"] else real_check(argv,*args,**kwargs)
        case = {"id":"ordinary-question","skill":None,"decision":"ANSWER","prompt":"A fixture question"}
        for usage in (None,{"input_tokens":10,"output_tokens":4}):
            def invoke(argv,fixture,log,timeout,max_bytes):
                events = [{"item":{"type":"agent_message","text":json.dumps({"decision":"ANSWER","references":[],"explanation":"fixture"})}},
                          {"type":"turn.completed",**({"usage":usage} if usage is not None else {})}]
                log.write_text("\n".join(json.dumps(event) for event in events)+"\n",encoding="utf-8")
                return {"status":"PASS","exit_code":0}
            argv = ["skill_eval","--execute","--budget-mode","observed","--model","fixture-model","--max-calls","1","--token-budget","1000"]
            with (self.subTest(usage=usage), mock.patch.object(sys,"argv",argv), mock.patch.object(skill_eval,"ROOT",self.root),
                  mock.patch.object(skill_eval,"load_cases",return_value=[case]), mock.patch.object(skill_eval,"codex_command",return_value=["fixture-codex"]),
                  mock.patch.object(skill_eval,"configured_mcp_overrides",return_value=[]),
                  mock.patch.object(subprocess,"check_output",side_effect=checked), mock.patch.object(skill_eval,"run_bounded",side_effect=invoke) as run,
                  contextlib.redirect_stdout(io.StringIO()) as output):
                self.assertEqual(skill_eval.main(),2)
                run.assert_called_once()
                report = json.loads(Path(output.getvalue().strip()).read_text(encoding="utf-8"))
                self.assertEqual(report["budget"]["calls"],1)
                self.assertTrue(report["stopped"])
                self.assertEqual(len(report["runs"]),1)

    def test_observer_exception_retains_usage_trace_and_failed_result(self):
        real_check = subprocess.check_output
        def checked(argv,*args,**kwargs):
            return b"fixture-cli" if argv==["fixture-codex","--version"] else real_check(argv,*args,**kwargs)
        def invoke(argv,fixture,log,timeout,max_bytes):
            log.write_text(json.dumps({"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":4}})+"\n",encoding="utf-8")
            self.assertNotIn("--add-dir",argv)
            self.assertIn("sandbox_workspace_write.writable_roots=[]",argv)
            self.assertIn("sandbox_workspace_write.exclude_tmpdir_env_var=true",argv)
            self.assertIn("sandbox_workspace_write.exclude_slash_tmp=true",argv)
            return {"status":"PASS","exit_code":0}
        case = {"id":"ordinary-question","skill":None,"decision":"ANSWER","prompt":"fixture"}
        argv = ["skill_eval","--execute","--budget-mode","observed","--model","fixture-model","--max-calls","2","--token-budget","1000"]
        with (mock.patch.object(sys,"argv",argv), mock.patch.object(skill_eval,"ROOT",self.root),
              mock.patch.object(skill_eval,"load_cases",return_value=[case]), mock.patch.object(skill_eval,"codex_command",return_value=["fixture-codex"]),
              mock.patch.object(skill_eval,"configured_mcp_overrides",return_value=[]),mock.patch.object(subprocess,"check_output",side_effect=checked),
              mock.patch.object(skill_eval,"run_bounded",side_effect=invoke),mock.patch.object(skill_eval,"fixture_assertions",side_effect=IndexError("observer")),
              contextlib.redirect_stdout(io.StringIO()) as output):
            self.assertEqual(skill_eval.main(),2)
            path = Path(output.getvalue().strip())
            report = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(report["budget"]["observed_tokens"],14)
            self.assertEqual(report["status"],"BLOCKED")
            self.assertEqual(report["runs"][0]["assessment"]["status"],"FAIL")
            self.assertTrue(list(path.parent.glob("*.jsonl")))


if __name__=="__main__":
    unittest.main()
