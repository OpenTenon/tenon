"""Read-only health and real, process-isolated Git recovery; no account/model calls."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from test_engineering import RepositoryFixture
from task_store import TaskStore
from engineering_common import write_json
import engineering_health as health
import engineering_drills as drills


def files(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


class HealthTests(RepositoryFixture):
    def setUp(self):
        super().setUp()
        self.root = self.root.resolve()

    def ready(self):
        launcher = "#!/usr/bin/env python3\n# immutable fixture launcher\n"
        (self.root / "scripts").mkdir()
        (self.root / "scripts/tenon.py").write_bytes(("def install():\n    launcher = " + repr(launcher) + "\n").encode())
        (self.root / "scripts/task_lifecycle.py").write_bytes(b"# owned source fixture\n")
        self.run_git("add", "scripts")
        self.run_git("commit", "--quiet", "-m", "health source fixture")
        self.commit = self.run_git("rev-parse", "HEAD").decode().strip()
        self.store = TaskStore(self.root)
        base = self.store.root
        directory = base / "controllers" / self.commit
        entries = []
        for name in ("scripts/tenon.py", "scripts/task_lifecycle.py"):
            payload = self.run_git("show", self.commit + ":" + name)
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            entries.append({"path": name, "sha256": hashlib.sha256(payload).hexdigest()})
        write_json(directory / "controller.json", {"schema_version": 1, "commit": self.commit, "files": entries})
        (base / "bin").mkdir()
        (base / "bin/tenon.py").write_bytes(launcher.encode())
        write_json(base / "bin/installation.json", {"schema_version": 1, "repo_id": self.store.repo_id,
            "repo": str(self.root), "repository": "fixture/tenon", "commit": self.commit, "controller": str(directory)})
        helper = self.root / "build/verifier.py"
        helper.parent.mkdir(exist_ok=True)
        helper.write_bytes(b"# existing frozen verifier, never executed by health\n")
        self.helper = helper
        write_json(base / "host.json", {"schema_version": 1, "repo_id": self.store.repo_id, "host_platform": os.name,
            "verify_command": [sys.executable, str(helper)], "inputs": [{"path": str(helper), "sha256": hashlib.sha256(helper.read_bytes()).hexdigest()}]})
        write_json(base / "worker.json", {"schema_version": 1, "repo_id": self.store.repo_id, "enabled": True})
        self.run_git("remote", "add", "origin", "https://github.com/fixture/tenon.git")
        program = self.root / "build/pythonw.exe"
        program.write_bytes(b"fixture schedule program; not executed")
        interpreter = program.with_name("python.exe")
        interpreter.write_bytes(b"fixture registered interpreter; not executed")
        metadata = json.loads((base / "bin/installation.json").read_bytes())
        metadata["python_executable"] = str(interpreter)
        write_json(base / "bin/installation.json", metadata)
        self.schedule = {"exists": True, "enabled": True, "description": "Tenon owned queue " + self.store.repo_id,
            "actions": [{"WorkingDirectory": str(self.root), "Arguments": subprocess.list2cmdline([
                str(base / "bin/tenon.py"), "worker", "run", "--max-seconds", "900"]), "Execute": str(program)}],
            "current_sid": "S-1-5-fixture", "current_name": "FIXTURE\\owner", "user_sid": "S-1-5-fixture", "logon_type": "InteractiveToken",
            "run_level": "LeastPrivilege", "interval": "PT5M", "instances": "IgnoreNew", "last_result": 0,
            "timer_enabled": True, "logon_enabled": True, "logon_user": "S-1-5-fixture",
            "hidden": True, "start_when_available": True}
        return directory

    def inspect(self, repo=None, **kwargs):
        native = shutil.which
        def which(name):
            return "fixture-gh" if name == "gh" else native(name)
        with mock.patch.object(shutil, "which", side_effect=which):
            return health.full_doctor(repo or self.root, schedule_probe=lambda _: copy.deepcopy(self.schedule), **kwargs)

    def test_uninitialized_health_never_constructs_runtime_state(self):
        before = files(self.root)
        with mock.patch.object(TaskStore, "__init__", side_effect=AssertionError("Health cannot initialize a store")):
            value = health.full_doctor(self.root)
        self.assertEqual(value["status"], "BLOCKED")
        self.assertFalse((self.root / ".git/tenon").exists())
        self.assertEqual(files(self.root), before)
        self.assertTrue(value["checks"][-1]["next_commands"])

    def test_full_health_is_read_only_and_does_not_contact_origin(self):
        self.ready()
        before = files(self.root)
        native = health._git
        observed = []
        def git(repo, *args):
            observed.append(args)
            self.assertFalse(any(name in args for name in ("fetch", "push", "ls-remote", "config-set")))
            return native(repo, *args)
        with mock.patch.object(health, "_git", side_effect=git), mock.patch.object(TaskStore, "__init__", side_effect=AssertionError("No runtime construction")):
            value = self.inspect()
        self.assertEqual(value["status"], "READY", value)
        self.assertFalse(value["network_accessed"])
        self.assertEqual(next(item for item in value["checks"] if item["id"] == "remote-availability")["status"], "UNKNOWN")
        self.assertEqual(files(self.root), before)

    def test_tooling_directory_is_never_reported_as_primary(self):
        directory = self.ready()
        value = self.inspect(directory)
        self.assertEqual(value["status"], "READY", value)
        self.assertEqual(value["primary"], str(self.root))
        self.assertNotEqual(value["primary"], str(directory))

    def test_changed_launcher_or_fixed_primary_blocks_without_repair(self):
        directory = self.ready()
        launcher = self.store.root / "bin/tenon.py"
        launcher.write_bytes(b"# changed entry\n")
        before = files(self.root)
        self.assertEqual(self.inspect()["status"], "BLOCKED")
        self.assertEqual(files(self.root), before)
        metadata = self.store.root / "bin/installation.json"
        value = json.loads(metadata.read_text())
        value["repo"] = str(directory)
        write_json(metadata, value)
        self.assertEqual(self.inspect()["status"], "BLOCKED")

    def test_missing_binding_is_unknown_not_a_format_only_identity_pass(self):
        self.ready()
        path = self.store.root / "bin/installation.json"
        value = json.loads(path.read_text()); del value["repository"]; write_json(path, value)
        result = self.inspect()
        identity = next(row for row in result["checks"] if row["id"] == "origin-identity")
        self.assertEqual(identity["status"], "UNKNOWN")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertNotIn("repository", json.loads(path.read_text()))

    def test_conflicting_recorded_origins_block_and_raw_credentials_are_not_reported(self):
        self.ready()
        self.store.create({"task_id": "foreign", "repository": "other/tenon", "worktree": str(self.root / "build/gone"),
            "branch": "dev-foreign", "status": "CLEANED", "host_platform": os.name})
        result = self.inspect()
        self.assertEqual(result["status"], "BLOCKED")
        self.run_git("remote", "set-url", "origin", "https://user:private-token@github.com/fixture/tenon.git")
        result = self.inspect()
        self.assertNotIn("private-token", json.dumps(result))

    def test_changed_frozen_input_and_schedule_dispatch_block_without_execution(self):
        self.ready()
        self.helper.write_bytes(b"# changed frozen helper\n")
        result = self.inspect()
        self.assertEqual(next(row for row in result["checks"] if row["id"] == "host-verifier")["status"], "BLOCKED")
        self.schedule["actions"][0]["Arguments"] = "unexpected command"
        result = self.inspect()
        self.assertEqual(next(row for row in result["checks"] if row["id"] == "scheduler")["status"], "BLOCKED")

    def test_schedule_requires_exact_registered_interpreter_and_labels_legacy_fallback(self):
        self.ready()
        original = self.schedule["actions"][0]["Execute"]
        foreign = self.root / "foreign-interpreter/pythonw.exe"
        foreign.parent.mkdir()
        foreign.write_bytes(b"raise if ever executed")
        self.schedule["actions"][0]["Execute"] = str(foreign)
        native_run = subprocess.run
        def inspect_git_only(argv, *args, **kwargs):
            self.assertEqual(argv[0], "git", "Health must not execute either interpreter")
            return native_run(argv, *args, **kwargs)
        with mock.patch.object(health.subprocess, "run", side_effect=inspect_git_only):
            check = next(row for row in self.inspect()["checks"] if row["id"] == "scheduler")
        self.assertEqual(check["status"], "BLOCKED")
        self.assertIn("registered windowless action executable", check["reason"])
        self.schedule["actions"][0]["Execute"] = original
        metadata = self.store.root / "bin/installation.json"
        value = json.loads(metadata.read_bytes())
        fallback = value.pop("python_executable")
        write_json(metadata, value)
        with mock.patch.object(health.sys, "executable", fallback):
            check = next(row for row in self.inspect()["checks"] if row["id"] == "scheduler")
        self.assertEqual(check["status"], "READY")
        self.assertEqual(check["interpreter_binding"]["source"], "current-native-interpreter-fallback")
        self.assertNotIn("python_executable", json.loads(metadata.read_bytes()))

    def test_explicit_install_records_canonical_repository_and_native_interpreter(self):
        self.ready()
        from tenon import install
        from test_entrypoint import Backend
        self.run_git("update-ref", "refs/remotes/origin/dev", self.commit)
        install(self.root, Backend())
        value = json.loads((self.store.root / "bin/installation.json").read_bytes())
        self.assertEqual(value["repository"], Backend.name)
        self.assertEqual(value["python_executable"], str(Path(sys.executable).resolve()))

    def test_wsl_verifier_is_distinct_from_missing_native_make(self):
        self.ready()
        path = self.store.root / "host.json"
        value = json.loads(path.read_text()); value["verify_command"] = ["wsl", "-d", "fixture-distro", "--", "python3", "frozen.py"]; write_json(path, value)
        native = shutil.which
        with mock.patch.object(shutil, "which", side_effect=lambda name: str(self.root / "build/wsl.exe") if name == "wsl" else "fixture-gh" if name == "gh" else None if name == "make" else native(name)):
            result = health.full_doctor(self.root, schedule_probe=lambda _: self.schedule, distribution_probe=lambda _: ["fixture-distro"])
        self.assertEqual(result["status"], "READY", result)
        self.assertIsNone(result["native_tools"]["make"])
        self.assertEqual(next(row for row in result["checks"] if row["id"] == "host-verifier")["mode"], "wsl")

    def test_cleanup_backlog_is_reported_instead_of_applied(self):
        self.ready()
        self.store.create({"task_id": "pending", "repository": "fixture/tenon", "worktree": str(self.root / "build/gone"),
            "branch": "dev-pending", "status": "WAITING_DEV_CI", "host_platform": os.name, "delivery": {"merged": True}})
        before = files(self.root)
        result = self.inspect()
        check = next(row for row in result["checks"] if row["id"] == "cleanup-backlog")
        self.assertEqual(check["status"], "READY", result)
        self.assertEqual(result["status"], "READY")
        self.assertEqual(check["pending_count"], 1)
        self.assertEqual(check["pending"][0]["status"], "WAITING_DEV_CI")
        self.assertEqual(check["pending"][0]["reason"], "WAITING_DEV_CI")
        self.assertTrue(check["next_commands"])
        self.assertEqual(files(self.root), before)

    def test_durable_queue_blocker_and_cleaned_resource_contradiction_block(self):
        self.ready()
        self.store.create({"task_id": "pending", "repository": "fixture/tenon", "worktree": str(self.root / "build/gone"),
            "branch": "dev-pending", "status": "WAITING_DEV_CI", "host_platform": os.name, "delivery": {"merged": True}, "owner_id": "owned-fixture"})
        path = self.store.root / "queue/pending.json"
        write_json(path, {"schema_version": 1, "repo_id": self.store.repo_id, "task_id": "pending", "state": "BLOCKED",
            "stage": "CLEANUP", "reason": "archive checksum changed", "owner_id": "owned-fixture"})
        before = files(self.root)
        check = next(row for row in self.inspect()["checks"] if row["id"] == "cleanup-backlog")
        self.assertEqual(check["status"], "BLOCKED")
        self.assertEqual(check["pending"][0]["reason"], "archive checksum changed")
        self.assertEqual(check["blockers"][0]["task_id"], "pending")
        self.assertEqual(files(self.root), before)
        queued = json.loads(path.read_text()); queued["state"] = "WAITING"; queued["owner_id"] = "different-owner"; write_json(path, queued)
        check = next(row for row in self.inspect()["checks"] if row["id"] == "cleanup-backlog")
        self.assertEqual(check["status"], "BLOCKED")
        self.assertIn("owner differs", check["reason"])
        path.unlink()
        self.store.mutate("pending", lambda task: {**task, "status": "CLEANED"}, "fixture-cleaned")
        (self.root / "build/gone").mkdir()
        check = next(row for row in self.inspect()["checks"] if row["id"] == "cleanup-backlog")
        self.assertEqual(check["status"], "BLOCKED")
        self.assertEqual(check["contradictions"], ["pending"])

    def test_foreign_or_orphan_queue_is_not_ignored(self):
        self.ready()
        write_json(self.store.root / "queue/orphan.json", {"schema_version": 1, "repo_id": self.store.repo_id,
            "task_id": "orphan", "state": "BLOCKED", "reason": "orphan evidence"})
        before = files(self.root)
        check = next(row for row in self.inspect()["checks"] if row["id"] == "cleanup-backlog")
        self.assertEqual(check["status"], "BLOCKED")
        self.assertIn("ownership", check["reason"])
        self.assertEqual(files(self.root), before)

    def test_exported_schedule_defaults_and_disabled_triggers_are_distinguished(self):
        self.ready()
        from task_worker import schedule_xml
        xml = schedule_xml(self.root, self.store.repo_id, Path(self.schedule["actions"][0]["Execute"]),
                           self.store.root / "bin/tenon.py", self.schedule["current_sid"])
        xml = xml.replace("<RunLevel>LeastPrivilege</RunLevel>", "")
        details = health._schedule_details(xml)
        self.assertEqual(details["run_level"], "LeastPrivilege")
        self.assertTrue(details["timer_enabled"] and details["logon_enabled"])
        self.assertTrue(details["hidden"] and details["start_when_available"])
        self.schedule.update(details, last_result=2, last_run_utc="2026-11-17T00:00:00Z")
        result = self.inspect()
        self.assertEqual(result["status"], "READY", result)
        check = next(row for row in result["checks"] if row["id"] == "scheduler")
        self.assertEqual(check["historical_execution"]["last_result"], 2)
        self.assertIn("Historical", check["execution_qualification"])
        for tag, field in (("Hidden", "hidden"), ("StartWhenAvailable", "start_when_available")):
            for changed in (xml.replace('<' + tag + '>true</' + tag + '>', '<' + tag + '>false</' + tag + '>'),
                            xml.replace('<' + tag + '>true</' + tag + '>', '')):
                with self.subTest(setting=field):
                    self.schedule.update(health._schedule_details(changed))
                    check = next(row for row in self.inspect()["checks"] if row["id"] == "scheduler")
                    self.assertEqual(check["status"], "BLOCKED")
                    self.assertIn(field, check["reason"])
            self.schedule.update(details)
        self.schedule["logon_user"] = "fixture\\OWNER"
        self.assertEqual(self.inspect()["status"], "READY")
        self.schedule["logon_user"] = "FIXTURE\\someone-else"
        self.assertEqual(self.inspect()["status"], "BLOCKED")
        self.schedule.update(health._schedule_details(xml.replace("<TimeTrigger>", "<TimeTrigger><Enabled>false</Enabled>")))
        check = next(row for row in self.inspect()["checks"] if row["id"] == "scheduler")
        self.assertEqual(check["status"], "BLOCKED")
        self.assertIn("timer_enabled", check["reason"])
        self.schedule["timer_enabled"] = True
        self.schedule["run_level"] = "HighestAvailable"
        self.assertEqual(self.inspect()["status"], "BLOCKED")

    def test_corrupt_metadata_is_blocked_without_becoming_initialized(self):
        self.ready()
        path = self.store.root / "bin/installation.json"
        value = json.loads(path.read_text()); value["repo"] = 4; write_json(path, value)
        before = files(self.root)
        self.assertEqual(self.inspect()["status"], "BLOCKED")
        self.assertEqual(files(self.root), before)


class DrillTests(RepositoryFixture):
    def production_state(self):
        common = self.root / ".git"
        return {"refs": self.run_git("for-each-ref"), "config": (common / "config").read_bytes(),
                "head": (common / "HEAD").read_bytes(), "tasks": files(common / "tenon/tasks"),
                "queue": files(common / "tenon/queue"),
                "identity": (common / "tenon/repository.json").read_bytes() if (common / "tenon/repository.json").exists() else None}

    def test_missing_owned_git_and_foreign_linked_pointer_never_discover_production(self):
        common = self.root / ".git"
        write_json(common / "tenon/tasks/keep.json", {"unrelated": "task original"})
        write_json(common / "tenon/queue/keep.json", {"unrelated": "queue original"})
        before = self.production_state()
        root = drills._new_fixture(common)
        manifest = drills._seed(root, "duplicate", common)
        primary, target = root / "primary", root / "task"
        try:
            self.assertEqual(drills._control(manifest)["root"], str(root))
            metadata, retained = primary / ".git", root / "retained-primary-metadata"
            with drills._fixture_context(root):
                metadata.rename(retained)
                try:
                    with self.assertRaises(ValueError):
                        drills._git(primary, "config", "tenon.must-not-write", "bad")
                    with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                        TaskStore(primary)
                    with self.assertRaises(ValueError):
                        drills._control(manifest)
                    direct = subprocess.run(["git", "-C", str(primary), "config", "tenon.must-not-write", "bad"],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
                    self.assertNotEqual(direct.returncode, 0)
                finally:
                    retained.rename(metadata)
            pointer = target / ".git"
            original = pointer.read_bytes()
            original_mode = pointer.stat().st_mode
            saved = root / "retained-task-pointer"
            pointer.rename(saved)
            try:
                with self.assertRaises(ValueError):
                    drills._git(target, "update-ref", "refs/heads/dev-escaped", "HEAD")
            finally:
                saved.rename(pointer)
            def replace_pointer(payload):
                # Windows Git marks this existing pointer hidden; avoid CREATE_ALWAYS.
                with pointer.open("r+b") as stream:
                    stream.write(payload)
                    stream.truncate()
            pointer.chmod(original_mode | 0o200)
            replace_pointer(("gitdir: " + str(common) + "\n").encode("utf-8"))
            try:
                with self.assertRaises(ValueError):
                    drills._git(target, "config", "tenon.must-not-write", "bad")
                with self.assertRaises(ValueError):
                    drills._control(manifest)
            finally:
                replace_pointer(original)
                pointer.chmod(original_mode)
            self.assertEqual(self.production_state(), before)
        finally:
            drills._run_child(manifest, tick=1)
            drills._cleanup(manifest)
        self.assertEqual(self.production_state(), before)

    def test_failed_child_retains_persistent_fixture_for_new_process_probe_and_recovery(self):
        before = self.production_state()
        header = "import json, os, sys\nfrom pathlib import Path\nsys.path.insert(0, sys.argv[1])\nimport engineering_drills as d\n"
        def child(code, *arguments):
            return subprocess.run([sys.executable, "-c", header + code, str(Path(drills.__file__).parent), *map(str, arguments)],
                cwd=self.root, env=drills._environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        failed = child("""from unittest import mock
def fail(manifest, scenario):
    raise RuntimeError('injected scenario failure in an independent child')
with mock.patch.object(d, '_scenario', side_effect=fail):
    report = d.run_drills(Path(sys.argv[2]), cases=['duplicate'])
print(json.dumps({'pid': os.getpid(), 'report': report}), flush=True)
raise SystemExit(7 if report['status'] == 'FAIL' else 9)
""", self.root)
        self.assertEqual(failed.returncode, 7, failed.stderr.decode(errors="replace"))
        first = json.loads(failed.stdout)
        row = first["report"]["scenarios"][0]
        manifest = Path(row["recovery_manifest"])
        self.assertTrue(row["fixture_preserved"])
        self.assertEqual(manifest.parent.parent, (self.root / ".git/tenon/drill-scratch").resolve())
        original = files(manifest.parent)
        observed = child("""manifest = Path(sys.argv[2])
value = d._control(manifest)
primary = Path(value['root']) / 'primary'
print(json.dumps({'pid': os.getpid(), 'value': value,
    'common': d._git(primary, 'rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip(),
    'refs': d._git(primary, 'for-each-ref').decode(),
    'remote': d._git(primary, 'ls-remote', '--heads', 'origin').decode()}), flush=True)
""", manifest)
        self.assertEqual(observed.returncode, 0, observed.stderr.decode(errors="replace"))
        probe = json.loads(observed.stdout)
        self.assertEqual(probe["value"]["root"], str(manifest.parent))
        self.assertEqual(Path(probe["common"]), manifest.parent / "primary/.git")
        self.assertIn(probe["value"]["branch"], probe["refs"])
        self.assertIn(probe["value"]["head_sha"], probe["remote"])
        self.assertEqual(files(manifest.parent), original)
        audit = Path(first["report"]["report"]).parent
        recovered = child("""import shutil
from maintenance import restore_check
from task_store import TaskStore
from task_archive import verify_archive
from engineering_common import write_json
manifest, audit = Path(sys.argv[2]), Path(sys.argv[3])
root = manifest.parent
d._run_child(manifest, tick=1)
resources = d._snapshot(manifest)
with d._fixture_context(root):
    restored = restore_check(root / 'primary', 'fixture')
    archive = TaskStore(root / 'primary').load('fixture')['archive']
copy = audit / 'recovered-fixture.zip'
shutil.copyfile(archive['path'], copy)
verify_archive(copy, archive['sha256'])
d._cleanup(manifest)
verify_archive(copy, archive['sha256'])
result = {'pid': os.getpid(), 'status': 'PASS', 'resources': resources, 'restore_check': restored,
    'fixture_removed': not root.exists(), 'archive_copy': str(copy), 'sha256': archive['sha256'],
    'qualification': d.CI_QUALIFICATION}
write_json(audit / 'recovery.json', result)
print(json.dumps(result), flush=True)
""", manifest, audit)
        self.assertEqual(recovered.returncode, 0, recovered.stderr.decode(errors="replace"))
        result = json.loads(recovered.stdout)
        self.assertEqual(len({os.getpid(), first["pid"], probe["pid"], result["pid"]}), 4)
        self.assertTrue(result["fixture_removed"])
        self.assertEqual(result["resources"]["task_status"], "CLEANED")
        self.assertEqual(result["restore_check"]["status"], "PASS")
        self.assertTrue(Path(result["archive_copy"]).is_file())
        self.assertFalse(manifest.parent.exists())
        self.assertEqual(self.production_state(), before)

    def test_recursive_probe_ignores_only_disappeared_entries(self):
        root = drills._new_fixture((self.root / ".git").resolve())
        manifest = drills._seed(root, "duplicate", self.root / ".git")
        listed = root / "processes/finished-write.tmp"
        listed.parent.mkdir()
        listed.write_bytes(b"atomic writer's completed temporary entry")
        native_reparse = drills._reparse
        seen = []
        def disappear_before_lstat(path):
            if path == listed:
                listed.unlink()
                seen.append(path)
            return native_reparse(path)
        try:
            with mock.patch.object(drills, "_reparse", side_effect=disappear_before_lstat):
                self.assertEqual(drills._control(manifest)["phase"], "READY")
            self.assertEqual(seen, [listed])
            self.assertFalse(listed.exists())
            listed.write_bytes(b"permission errors remain qualification failures")
            def deny_lstat(path):
                if path == listed:
                    raise PermissionError("controlled inaccessible listed entry")
                return native_reparse(path)
            with mock.patch.object(drills, "_reparse", side_effect=deny_lstat), self.assertRaises(PermissionError):
                drills._control(manifest)
            self.assertTrue(listed.exists())
            listed.unlink()
            token, retained = root / "owner-token", root / "retained-owner-token"
            token.rename(retained)
            try:
                with self.assertRaises(FileNotFoundError):
                    drills._control(manifest)
            finally:
                retained.rename(token)
            def missing_root(path):
                if path == root:
                    raise FileNotFoundError("controlled missing fixture resource root")
                return native_reparse(path)
            with mock.patch.object(drills, "_reparse", side_effect=missing_root), self.assertRaises(FileNotFoundError):
                drills._control(manifest)
        finally:
            drills._run_child(manifest, tick=1)
            drills._cleanup(manifest)

    def test_concurrent_controllers_keep_a_recoverable_archive_after_fixture_removal(self):
        value = drills.run_drills(self.root, cases=["controllers"])
        self.assertEqual(value["status"], "PASS", value)
        row = value["scenarios"][0]
        self.assertTrue(row["fixture_removed"])
        self.assertFalse(Path(row["fixture"]).exists())
        self.assertEqual(row["observations"][-1]["task_status"], "CLEANED")
        self.assertEqual(row["restore_check"]["status"], "PASS")
        self.assertTrue(row["archive_copy"]["verified_after_fixture_removal"])

    def assert_private_rejection_preserves_resources(self, manifest, backend):
        data = json.loads(manifest.read_bytes())
        primary = manifest.parent / "primary"
        task = primary / ".git/tenon/tasks/fixture.json"
        queued = primary / ".git/tenon/queue/fixture.json"
        before = (task.read_bytes(), queued.read_bytes(),
                  drills._git(primary, "ls-remote", "--heads", "origin", "refs/heads/" + data["branch"]))
        native_popen, native_git = subprocess.Popen, drills._git
        def guarded_spawn(argv, *args, **kwargs):
            if argv[0] == sys.executable:
                raise AssertionError("An unqualified private worker must never start")
            return native_popen(argv, *args, **kwargs)
        def guarded_git(repo, *args):
            if args and args[0] == "push":
                raise AssertionError("An unqualified fixture must never reach remote deletion")
            return native_git(repo, *args)
        with mock.patch.object(subprocess, "Popen", side_effect=guarded_spawn), mock.patch.object(drills, "_git", side_effect=guarded_git):
            with self.assertRaises(ValueError):
                drills._spawn(manifest, "resume")
            # Also exercise an already-created adapter after recovery data drifts.
            with self.assertRaises(ValueError):
                backend.delete_remote(data["branch"], data["head_sha"])
        self.assertEqual((task.read_bytes(), queued.read_bytes(),
            drills._git(primary, "ls-remote", "--heads", "origin", "refs/heads/" + data["branch"])), before)

    def test_private_worker_rejects_task_and_queue_drift_before_start_or_delete(self):
        root = drills._new_fixture((self.root / ".git").resolve())
        manifest = drills._seed(root, "duplicate", self.root / ".git")
        backend = drills._FixtureBackend(manifest)
        outside = self.root / "outside-fixture"
        outside.mkdir()
        (outside / "keep.txt").write_bytes(b"unrelated original must survive\n")
        outside_before = files(outside)
        task = root / "primary/.git/tenon/tasks/fixture.json"
        queued = root / "primary/.git/tenon/queue/fixture.json"
        try:
            for record, field, changed in ((task, "worktree", str(outside)), (task, "owner_id", "foreign-owner"),
                    (task, "head_sha", "f" * 40), (task, "branch", "dev-foreign"),
                    (queued, "owner_id", "foreign-owner"), (queued, "candidate_sha", "f" * 40),
                    (manifest, "phase", "CREATING")):
                with self.subTest(field=field, file=record.name):
                    original = record.read_bytes()
                    value = json.loads(original)
                    value[field] = changed
                    write_json(record, value)
                    try:
                        self.assert_private_rejection_preserves_resources(manifest, backend)
                        self.assertEqual(files(outside), outside_before)
                    finally:
                        record.write_bytes(original)
            drills._git(root / "primary", "config", "remote.origin.pushurl", str(outside))
            try:
                self.assert_private_rejection_preserves_resources(manifest, backend)
                self.assertEqual(files(outside), outside_before)
            finally:
                drills._git(root / "primary", "config", "--unset-all", "remote.origin.pushurl")
        finally:
            drills._run_child(manifest, tick=1)
            drills._cleanup(manifest)

    def test_private_worker_rejects_extra_worktree_and_broken_resource_link(self):
        root = drills._new_fixture((self.root / ".git").resolve())
        manifest = drills._seed(root, "duplicate", self.root / ".git")
        backend = drills._FixtureBackend(manifest)
        primary, extra, target = root / "primary", root / "extra", root / "task"
        try:
            drills._git(primary, "worktree", "add", "--quiet", "--detach", str(extra), "HEAD")
            try:
                content = (extra / "input.txt").read_bytes()
                self.assert_private_rejection_preserves_resources(manifest, backend)
                self.assertEqual((extra / "input.txt").read_bytes(), content)
            finally:
                drills._git(primary, "worktree", "remove", str(extra))
            retained, missing = root / "retained-task", root / "missing-link-target"
            target.rename(retained)
            try:
                if os.name == "nt":
                    self.assertEqual(Path(drills._boundary(manifest)["root"]), root)
                    self.assertEqual(target.parent, root)
                    self.assertEqual(missing.parent, root)
                    self.assertFalse(os.path.lexists(target))
                    if any(character in str(path) for path in (target, missing) for character in '&|<>^%!"\r\n'):
                        raise ValueError("Fixture junction paths contain unsupported cmd metacharacters")
                    missing.mkdir()
                    environment = drills._environment()
                    environment.update(TENON_TEST_LINK=str(target), TENON_TEST_TARGET=str(missing))
                    subprocess.run('cmd.exe /d /v:off /s /c mklink /J "%TENON_TEST_LINK%" "%TENON_TEST_TARGET%"',
                        executable=str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe"),
                        env=environment, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
                    missing.rmdir()
                else:
                    target.symlink_to(missing, target_is_directory=True)
                self.assertTrue(os.path.lexists(target))
                self.assertFalse(target.exists())
                original = (retained / "input.txt").read_bytes()
                self.assert_private_rejection_preserves_resources(manifest, backend)
                self.assertTrue(os.path.lexists(target))
                self.assertEqual((retained / "input.txt").read_bytes(), original)
            finally:
                if os.path.lexists(target):
                    target.rmdir() if os.name == "nt" else target.unlink()
                retained.rename(target)
        finally:
            drills._run_child(manifest, tick=1)
            drills._cleanup(manifest)

    def test_real_process_scenarios_close_git_resources_and_remove_fixtures(self):
        original = files(self.root)
        production = self.production_state()
        value = drills.run_drills(self.root)
        self.assertEqual(value["status"], "PASS" if os.name == "nt" else "NOT_RUN", value)
        rows = {row["scenario"]: row for row in value["scenarios"]}
        for name in drills.CASES:
            row = rows[name]
            if name == "windows-sharing" and os.name != "nt":
                self.assertEqual(row["status"], "NOT_RUN")
                self.assertFalse(row["fixture_created"])
                continue
            self.assertEqual(row["status"], "PASS", row)
            self.assertTrue(row["fixture_removed"])
            self.assertFalse(Path(row["fixture"]).exists())
            last = row["observations"][-1]
            self.assertEqual(last["task_status"], "CLEANED")
            self.assertFalse(last["directory"] or last["registration"] or last["remote_task_ref"] or last["task_refs"])
            self.assertEqual(row["restore_check"]["status"], "PASS")
            self.assertGreater(row["restore_check"]["restored_files"], 0)
            retained = row["archive_copy"]
            self.assertTrue(retained["verified_before_fixture_removal"] and retained["verified_after_fixture_removal"])
            self.assertTrue(Path(retained["path"]).is_file())
            self.assertFalse(Path(retained["path"]).is_relative_to(Path(row["fixture"])))
            from task_archive import verify_archive
            self.assertEqual(len(verify_archive(Path(retained["path"]), retained["sha256"])["files"]),
                             row["restore_check"]["restored_files"])
        audit = Path(value["report"]).parent
        processes = [json.loads(path.read_text()) for path in (audit / "controllers/processes").glob("*.json")]
        self.assertEqual(len({row["pid"] for row in processes}), 2)
        self.assertTrue(all(row["pid"] != os.getpid() for row in processes))
        outcomes = [json.loads(path.read_text()) for path in (audit / "exit-resume/process-output").glob("*.json")]
        self.assertEqual(sorted(row["exit_code"] for row in outcomes), [0, 37])
        self.assertTrue(all(row["exit_code"] == row["expected_exit"] for row in outcomes))
        for path in (audit / "controllers/process-output").glob("*.json"):
            result = json.loads(path.read_text())
            self.assertEqual(result["exit_code"], 0)
            self.assertIn('"status": "OBSERVED"', path.with_suffix(".stdout.log").read_text())
        self.assertFalse((self.root / ".git/tenon/repository.json").exists())
        self.assertFalse((self.root / ".git/tenon/queue").exists())
        self.assertTrue(all(files(self.root).get(name) == content for name, content in original.items()))
        self.assertEqual(self.production_state(), production)

    def test_failure_retains_only_its_owned_recoverable_fixture(self):
        seen = []
        def fail(manifest, _):
            seen.append(manifest)
            raise RuntimeError("fixture-only injected failure")
        with mock.patch.object(drills, "_scenario", side_effect=fail):
            value = drills.run_drills(self.root, cases=["duplicate"])
        self.assertEqual(value["status"], "FAIL")
        manifest = seen[0]
        self.assertTrue(manifest.exists())
        self.assertTrue(value["scenarios"][0]["fixture_preserved"])
        self.assertEqual((manifest.parent / "task/input.txt").read_bytes(), b"isolated delivered result\n")
        # Recover using the same guarded local controller; then remove this known fixture.
        drills._run_child(manifest, tick=1)
        drills._cleanup(manifest)
        self.assertFalse(manifest.parent.exists())

    def test_private_worker_rejects_production_state_before_constructing_store(self):
        with mock.patch.object(TaskStore, "__init__", side_effect=AssertionError("Production store must not be constructed")):
            with self.assertRaises((OSError, ValueError)):
                drills.fixture_worker(self.root / ".git/tenon/repository.json", "resume")

    def test_public_drill_has_no_simulated_backend_switch(self):
        import maintenance
        with mock.patch.object(sys, "argv", ["maintenance", "--repo", str(self.root), "drill", "--backend", "fake"]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                maintenance.main()
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
