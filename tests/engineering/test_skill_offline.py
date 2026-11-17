"""Scripted workflow acceptance stays distinct from model metrics and host configuration."""
from __future__ import annotations
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from test_engineering import ROOT
import skill_eval
import skill_offline as offline



def _control(directory):
    control=directory/"control"
    control.mkdir()
    clean=offline.child_environment(directory/"control-environment")
    def git(*args):
        return subprocess.check_output(["git","--no-optional-locks","-C",str(control),*args],
                                       env=clean,stderr=subprocess.PIPE)
    git("init","--quiet","--initial-branch=main")
    git("config","user.name","Preserved outside control")
    git("config","user.email","fixture@example.invalid")
    (control/"sentinel.txt").write_text("outside owned worker\n",encoding="utf-8")
    git("add","."); git("commit","--quiet","-m","preserve control")
    return control,clean


def _bytes(directory):
    return {path.relative_to(directory).as_posix():path.read_bytes() for path in directory.rglob("*") if path.is_file()}


class OfflineGitBoundaryTests(unittest.TestCase):
    def invoke_both_entries(self, directory, environment):
        for kind in ("worker","direct"):
            owned=directory/kind
            owned.mkdir()
            if kind=="worker":
                argv=[sys.executable,"-B",str(ROOT/"scripts/skill_offline.py"),"--fixture-worker",str(owned),"--scenario","partial-evidence"]
            else:
                code="import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); import skill_offline; fixture=skill_offline.Fixture(Path(sys.argv[2])); fixture.verify_identity(); assert fixture.unchanged_primary()"
                argv=[sys.executable,"-B","-c",code,str(ROOT/"scripts"),str(owned)]
            result=subprocess.run(argv,cwd=owned,env=environment,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
            self.assertEqual(result.returncode,0,(kind,result.stderr.decode(errors="replace")[-2000:]))
            if kind=="worker":
                self.assertEqual(json.loads((owned/"result.json").read_text(encoding="utf-8"))["status"],"PASS")

    def test_worker_and_direct_fixture_ignore_inherited_git_redirection(self):
        with tempfile.TemporaryDirectory(prefix="tenon-skill-poison-") as temporary:
            directory=Path(temporary).resolve()
            control,clean=_control(directory)
            before=_bytes(control)
            hostile={**clean,"GIT_DIR":str(control/".git"),"GIT_COMMON_DIR":str(control/".git"),
                     "GIT_WORK_TREE":str(control),"GIT_INDEX_FILE":str(control/".git/index"),
                     "GIT_OBJECT_DIRECTORY":str(control/".git/objects"),"GIT_NAMESPACE":"unowned",
                     "GIT_EXEC_PATH":str(directory/"no-executable-directory")}
            self.invoke_both_entries(directory,hostile)
            self.assertEqual(_bytes(control),before)

    def test_worker_and_direct_fixture_do_not_execute_external_config_hooks(self):
        with tempfile.TemporaryDirectory(prefix="tenon-skill-hooks-") as temporary:
            directory=Path(temporary).resolve()
            control,clean=_control(directory)
            before=_bytes(control)
            hooks=directory/"outside-hooks"; hooks.mkdir()
            marker=directory/"outside-hook-must-not-start"
            shell=str(marker).replace("\\","/").replace("'","'\"'\"'")
            hook=hooks/"pre-commit"
            hook.write_text("#!/bin/sh\nprintf 'started' > '"+shell+"'\n",encoding="utf-8",newline="\n")
            hook.chmod(0o700)
            config=directory/"outside-global-config"
            config.write_text("[core]\n\thooksPath = "+str(hooks).replace("\\","/")+"\n",encoding="utf-8")
            hostile={**clean,"GIT_CONFIG_COUNT":"1","GIT_CONFIG_KEY_0":"core.hooksPath",
                     "GIT_CONFIG_VALUE_0":str(hooks),"GIT_CONFIG_GLOBAL":str(config),
                     "GIT_CONFIG_SYSTEM":str(config),"GIT_TEMPLATE_DIR":str(directory/"outside-template")}
            self.invoke_both_entries(directory,hostile)
            self.assertEqual(_bytes(control),before)
            self.assertFalse(marker.exists())
            self.assertTrue(hook.is_file())

    def test_public_fingerprint_uses_true_root_and_restores_caller_environment(self):
        expected=offline.source_state(ROOT)
        with tempfile.TemporaryDirectory(prefix="tenon-skill-source-") as temporary:
            directory=Path(temporary).resolve()
            control,clean=_control(directory)
            before=_bytes(control)
            hostile={"GIT_DIR":str(control/".git"),"GIT_WORK_TREE":str(control),
                     "GIT_COMMON_DIR":str(control/".git"),"GIT_INDEX_FILE":str(control/".git/index"),
                     "GIT_CONFIG_COUNT":"1","GIT_CONFIG_KEY_0":"core.hooksPath","GIT_CONFIG_VALUE_0":str(directory/"outside-hooks")}
            with mock.patch.dict(os.environ,hostile):
                caller=dict(os.environ)
                # Skip fixture executions only to isolate the two public fingerprint observations.
                with mock.patch.dict(offline.SCENARIOS,{},clear=True):
                    result=offline.run_offline(ROOT,include_config=False)
                self.assertEqual(dict(os.environ),caller)
            self.assertEqual(result["source"],expected)
            self.assertEqual(result["source_after"],expected)
            self.assertEqual(_bytes(control),before)

    def test_existing_redirected_fixture_resource_is_rejected_before_git(self):
        with tempfile.TemporaryDirectory(prefix="tenon-skill-owned-") as temporary:
            directory=Path(temporary).resolve()
            control,clean=_control(directory)
            owned=directory/"owned"; owned.mkdir(); (owned/"primary").mkdir()
            (owned/"primary/.git").write_text("gitdir: "+str(control/".git")+"\n",encoding="utf-8")
            before=_bytes(control)
            with mock.patch.object(offline,"_git",side_effect=AssertionError("No Git before ownership")) as git:
                with self.assertRaises(ValueError):
                    offline.Fixture(owned)
                git.assert_not_called()
            self.assertEqual(_bytes(control),before)


class OfflineAcceptanceTests(unittest.TestCase):
    def test_actual_local_workflows_pass_without_models_and_preserve_source(self):
        with mock.patch.object(skill_eval, "run_bounded", side_effect=AssertionError("No model calls")):
            result = offline.run_offline(ROOT, include_config=False)
        self.assertEqual(result["status"], "PASS", result["scenarios"])
        self.assertTrue(result["source_unchanged"])
        self.assertEqual({row["scenario"] for row in result["scenarios"]}, set(offline.SCENARIOS))
        self.assertTrue(all(row["assertions"] and all(row["assertions"].values()) for row in result["scenarios"]))
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["model_metrics"], {"status":"NOT_RUN","trigger_accuracy":None,"completion_rate":None,"usage":None})
        self.assertEqual(result["configuration"]["status"], "NOT_RUN")
        self.assertEqual(len(result["model_regression_corpus"]["case_ids"]), 23)
        self.assertEqual(result["model_regression_corpus"]["status"], "NOT_RUN")
        self.assertEqual(result["offline_scenario_count"], 4)

    def test_failed_worker_assertion_cannot_pass_by_printing_routing_words(self):
        with tempfile.TemporaryDirectory(prefix="tenon-offline-result-") as temporary:
            directory = Path(temporary)
            with mock.patch.object(offline, "Fixture"), mock.patch.dict(offline.SCENARIOS,
                {"partial-evidence":lambda fixture: {"actual_outcome":False, "said_PASS_and_correct_skill":True}}):
                result = offline.fixture_worker(directory, "partial-evidence")
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(json.loads((directory/"result.json").read_text())["status"], "FAIL")

    def test_fixture_observer_exception_is_a_readable_failure(self):
        with tempfile.TemporaryDirectory(prefix="tenon-offline-error-") as temporary:
            with mock.patch.object(offline, "Fixture", side_effect=ValueError("Fixture failed")):
                result = offline.fixture_worker(Path(temporary), "partial-evidence")
            self.assertEqual(result["status"], "FAIL")
            self.assertIn("ValueError", result["diagnostic"])

    def test_report_cannot_overwrite_source_or_escape_verification(self):
        original = (ROOT/"Makefile").read_bytes()
        for path in (ROOT/"Makefile", ROOT/"build/verification/../../../outside.json"):
            with self.subTest(path=path), contextlib.redirect_stderr(io.StringIO()), mock.patch.object(offline, "run_offline") as run:
                with self.assertRaises(SystemExit):
                    offline.main(["--report",str(path)])
                run.assert_not_called()
        self.assertEqual((ROOT/"Makefile").read_bytes(), original)

    def test_worker_timeout_is_reported_without_raw_output_or_model_call(self):
        error=subprocess.TimeoutExpired(["fixture-worker"],1,output=b"PRIVATE_OUTPUT",stderr=b"PRIVATE_STDERR")
        with mock.patch.object(offline.subprocess,"run",side_effect=error),mock.patch.object(offline,"source_state",return_value={"sha":"fixture"}):
            result=offline.run_offline(ROOT,include_config=False)
        self.assertEqual(result["status"],"FAIL")
        self.assertEqual(len(result["scenarios"]),len(offline.SCENARIOS))
        self.assertNotIn("PRIVATE",json.dumps(result))
        self.assertEqual(result["model_calls"],0)

    def test_config_check_uses_fresh_child_env_and_sanitizes_failure_payloads(self):
        sentinel = "PRIVATE_INVENTORY_MUST_NOT_ESCAPE"
        real_run = subprocess.run
        seen = []
        def invoke(argv, *args, **kwargs):
            seen.append((argv,kwargs))
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(argv,0,b"codex-cli 0.166.0-alpha.6",sentinel.encode())
            return real_run(argv,*args,**kwargs)
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY":sentinel,"CODEX_HOME":sentinel}), \
             mock.patch.object(offline, "codex_command",return_value=["fixture-codex"]), \
             mock.patch.object(subprocess, "run",side_effect=invoke), \
             mock.patch.object(offline, "configured_mcp_overrides",side_effect=ValueError(sentinel)) as inventory:
            result = offline.configuration_check()
        self.assertEqual(result["status"], "FAIL")
        self.assertNotIn(sentinel,json.dumps(result))
        env = inventory.call_args.kwargs["env"]
        self.assertNotIn("OPENAI_API_KEY",env)
        self.assertNotEqual(env["CODEX_HOME"],sentinel)
        self.assertTrue(all("exec" not in call[0] for call in seen))
        self.assertEqual(result["model_calls"],0)

    def test_unavailable_cli_and_unknown_version_are_not_false_pass(self):
        with mock.patch.object(offline,"codex_command",side_effect=ValueError("Absent")):
            result=offline.configuration_check()
        self.assertEqual(result["status"],"NOT_RUN")
        self.assertIsNone(result["cli_version"])
        real_run=subprocess.run
        def invoke(argv,*args,**kwargs):
            if argv[-1]=="--version":
                return subprocess.CompletedProcess(argv,127,b"",b"PRIVATE_STDERR")
            return real_run(argv,*args,**kwargs)
        with mock.patch.object(offline,"codex_command",return_value=["fixture-codex"]),mock.patch.object(subprocess,"run",side_effect=invoke):
            result=offline.configuration_check()
        self.assertEqual(result["status"],"NOT_RUN")
        self.assertNotIn("PRIVATE_STDERR",json.dumps(result))

    @unittest.skipUnless(os.name=="nt","Windows desktop native CLI fallback")
    def test_safe_desktop_native_entry_is_used_despite_batch_shim_on_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            native=root/"OpenAI/Codex/bin/version/codex.exe"
            native.parent.mkdir(parents=True); native.write_bytes(b"fixture-not-executed")
            with mock.patch.dict(os.environ,{"LOCALAPPDATA":str(root)}),mock.patch.object(skill_eval.shutil,"which",return_value=str(root/"codex.cmd")):
                self.assertEqual(skill_eval.codex_command(),[str(native)])

    def test_strict_budget_refuses_before_cli_configuration_or_model_invocation(self):
        argv=["skill_eval","--execute","--model","fixture","--max-calls","1","--token-budget","10"]
        with mock.patch.object(sys,"argv",argv),mock.patch.object(skill_eval,"codex_command") as discover, \
             mock.patch.object(skill_eval,"configured_mcp_overrides") as inventory, \
             mock.patch.object(skill_eval,"run_bounded") as invoke,contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(skill_eval.main(),2)
        result=json.loads(output.getvalue())
        self.assertEqual(result["status"],"BLOCKED")
        self.assertEqual(result["model_calls"],0)
        discover.assert_not_called(); inventory.assert_not_called(); invoke.assert_not_called()


if __name__=="__main__":
    unittest.main()
