"""Exercise real Git workspaces and a tamper-checked entry outside disposable checkouts."""
from __future__ import annotations
import json
import contextlib
import io
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

from test_engineering import RepositoryFixture, ROOT
import ci_gate
import tenon
from task_store import TaskStore


class Backend:
    name = "fixture/tenon"

    def refresh(self):
        pass

    def latest_run(self, sha, event):
        run = {"id": 42, "run_attempt": 1, "head_sha": sha, "event": event,
               "status": "completed", "conclusion": "success"}
        return {"workflow_run": run, "jobs": [{"name": name, "run_id": 42, "run_attempt": 1,
                "status": "completed", "conclusion": "success"}
                for name in ci_gate.REQUIRED_JOBS | {"ci-required"}]}


class EntryTests(RepositoryFixture):
    def setUp(self):
        super().setUp()
        self.root = self.root.resolve()
        self.addCleanup(self.remove_workspaces)

    def remove_workspaces(self):
        for record in reversed(tenon.worktrees(self.root)[1:]):
            subprocess.run(["git", "-C", str(self.root), "worktree", "remove", str(record["path"])],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def start(self, name="entry", **options):
        return tenon.start_workspace(self.root, name, "tooling", "fixture engineering change",
            ["scripts/*"], ["make verify"], backend=Backend(), **options)

    def test_default_workspace_is_owned_sibling_and_primary_edits_are_preserved(self):
        (self.root / "input.txt").write_text("user edits\n", encoding="utf-8")
        value = self.start()
        root = Path(value["worktree"])
        self.assertEqual(root.parent, self.root.parent / ".tenon-worktrees" / value["repo_id"])
        self.assertEqual(value["workspace_backend"], "git")
        self.assertEqual(self.run_git("branch", "--show-current").decode().strip(), "dev-fixture-test")
        self.assertEqual((self.root / "input.txt").read_text(), "user edits\n")
        self.assertEqual((root / "input.txt").read_text(), "original\n")
        self.assertEqual(TaskStore(self.root).load("entry")["branch"], value["branch"])
        with self.assertRaises(FileExistsError):
            self.start()

    def test_invalid_contract_is_rejected_before_creating_a_branch(self):
        before = self.run_git("for-each-ref")
        for topic, scope in (("../escape", ["scripts/*"]), ("tooling", ["../outside"])):
            with self.assertRaises(ValueError):
                tenon.start_workspace(self.root, "invalid", topic, "goal", scope,
                                      ["make verify"], backend=Backend())
        self.assertEqual(before, self.run_git("for-each-ref"))

    def test_explicit_controlled_root_and_verification_argv_are_recorded(self):
        value = self.start(workspace_root=self.root / "build/workspaces",
                           verify_command=["existing-validator", "{worktree}"], local_only=True)
        self.assertEqual(value["delivery_mode"], "local-only")
        self.assertEqual(value["verify_command"], ["existing-validator", "{worktree}"])
        self.assertEqual(Path(value["worktree"]).parent, self.root / "build/workspaces")

    def test_interrupted_registration_resumes_only_the_exact_clean_setup(self):
        with mock.patch.object(TaskStore, "create", side_effect=OSError("interrupted registration")):
            with self.assertRaises(OSError):
                self.start()
        intent = TaskStore(self.root).root / "operations/workspace-setup/entry.json"
        self.assertEqual(json.loads(intent.read_text())["contract"]["allowed_paths"], ["scripts/*"])
        with self.assertRaises(ValueError):
            tenon.start_workspace(self.root, "entry", "tooling", "different goal", ["scripts/*"],
                                  ["make verify"], backend=Backend())
        value = self.start()
        self.assertEqual(value["task_id"], "entry")
        self.assertEqual(len(tenon.worktrees(self.root)), 2)

    def test_registered_external_verifier_is_actually_executed(self):
        command = [sys.executable, "-c", "from pathlib import Path; Path('build').mkdir(exist_ok=True); Path('build/selected-verifier').write_text('ran')"]
        value = self.start(verify_command=command)
        output = io.StringIO()
        with mock.patch.object(tenon, "GitHub", side_effect=AssertionError("Local verification must not construct GitHub")), contextlib.redirect_stdout(output):
            self.assertEqual(tenon.main(["--repo", str(self.root), "task", "verify", "--id", "entry"]), 2)
        self.assertEqual((Path(value["worktree"]) / "build/selected-verifier").read_text(), "ran")
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("no source-matched full verification report", result["reason"])
        self.assertNotEqual(TaskStore(self.root).load("entry").get("verified_head"),
                            self.run_git("rev-parse", "HEAD").decode().strip())

    def test_transport_failure_returns_structured_blocker(self):
        output = io.StringIO()
        with mock.patch.object(tenon, "install", side_effect=RuntimeError("transport interrupted")), contextlib.redirect_stdout(output):
            self.assertEqual(tenon.main(["--repo", str(self.root), "install"]), 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "BLOCKED")

    def deploy_source(self):
        shutil.copytree(ROOT / "scripts", self.root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        self.run_git("add", "scripts")
        self.run_git("commit", "--quiet", "-m", "entry fixture")
        self.run_git("update-ref", "refs/remotes/origin/dev", "HEAD")

    def test_install_from_green_dev_runs_after_source_checkout_is_unavailable(self):
        self.deploy_source()
        value = tenon.install(self.root, Backend())
        entry = Path(value["entry"])
        shutil.move(str(self.root / "scripts"), str(self.root / "build/old-scripts"))
        result = subprocess.run([sys.executable, str(entry), "doctor"], cwd=self.root,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["dev_sha"], value["commit"])
        config = json.loads((entry.parent / "installation.json").read_text())
        deployed = Path(config["controller"]) / "scripts/task_lifecycle.py"
        deployed.write_text("raise SystemExit('tampered')\n", encoding="utf-8")
        rejected = subprocess.run([sys.executable, str(entry), "doctor"], cwd=self.root,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("integrity", rejected.stdout)

    def test_install_refuses_missing_or_failed_current_dev_ci(self):
        self.deploy_source()
        backend = Backend()
        with mock.patch.object(backend, "latest_run", return_value=None), self.assertRaises(ValueError):
            tenon.install(self.root, backend)
        self.assertFalse((TaskStore(self.root).root / "bin/installation.json").exists())
