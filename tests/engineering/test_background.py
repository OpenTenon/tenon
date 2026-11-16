"""Real disposable Git cleanup with durable queue restart and ownership boundaries."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import shutil
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from test_lifecycle_v2 import OwnedWorktreeFixture
from test_engineering import RepositoryFixture, ROOT
from task_store import FileLease
from engineering import required_ids
import engineering_common as common
from task_backends import GitHub, command
import task_queue
import task_worker


class QueueTests(OwnedWorktreeFixture):
    def factory(self, repo, task_id):
        self.assertEqual(repo, self.root)
        self.assertEqual(task_id, "owned")
        return self.controller

    def enqueue(self):
        return task_queue.submit(self.root, "owned")

    def verified(self, head=None):
        head = head or self.head
        report = common.new_summary(self.owned, "verification")
        directory = self.owned / "build/verification" / report["run_id"]
        for name in required_ids("all") - {"repository-unchanged"}:
            report["checks"].append(common.execute([sys.executable, "-c", "print('fixture verification')"],
                self.owned, directory / (name + ".log"), check_id=name))
        path = directory / "summary.json"
        common.finish_summary(report, self.owned, path)
        self.store.mutate("owned", lambda item: {**item, "delivery": {}, "status": "VERIFIED",
            "head_sha": head, "verified_head": head, "verification_report": str(path)})
        return path

    def test_read_only_observation_preserves_queue_and_all_resources(self):
        self.enqueue()
        path = task_queue.queue_path(self.store, "owned")
        before = path.read_bytes()
        value = task_queue.reconcile(self.root, factory=self.factory)
        self.assertEqual(value["tasks"][0]["state"], "READY")
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(self.owned.exists())
        self.assertEqual(self.backend.deleted, 0)

    def test_restart_finishes_the_real_git_cleanup_once_and_retains_archive(self):
        self.enqueue()
        result = task_queue.reconcile(self.root, apply=True, factory=self.factory)
        self.assertEqual(result["tasks"][0]["state"], "DONE")
        self.assertFalse(self.owned.exists())
        self.assertEqual(self.store.load("owned")["status"], "CLEANED")
        again = task_queue.reconcile(self.root, apply=True, factory=self.factory)
        self.assertEqual(again["tasks"][0]["state"], "DONE")
        self.assertEqual(self.backend.deleted, 1)
        task_worker.set_enabled(self.root, True, "owned")
        self.assertTrue(task_worker.configuration(self.store)["enabled"])

    def test_new_source_blocks_before_remote_or_local_deletion(self):
        self.enqueue()
        (self.owned / "new-source.txt").write_text("preserve", encoding="utf-8")
        result = task_queue.reconcile(self.root, apply=True, factory=self.factory)
        self.assertEqual(result["tasks"][0]["state"], "BLOCKED")
        self.assertEqual(self.backend.deleted, 0)
        self.assertTrue((self.owned / "new-source.txt").exists())

    def test_changed_owner_or_candidate_is_never_adopted(self):
        self.enqueue()
        self.store.mutate("owned", lambda item: {**item, "owner_id": "another-owner"})
        value = task_queue.reconcile(self.root, apply=True, factory=self.factory)
        self.assertEqual(value["tasks"][0]["state"], "BLOCKED")
        self.assertEqual(self.backend.deleted, 0)

    def test_transient_failure_is_durable_and_recovers_on_later_tick(self):
        self.enqueue()
        failed = mock.Mock()
        failed.deliver.side_effect = TimeoutError("network timeout")
        now = datetime.now(timezone.utc)
        result = task_queue.reconcile(self.root, True, lambda *_: failed, now)
        self.assertEqual(result["tasks"][0]["state"], "WAITING")
        self.assertEqual(task_queue.read_queue(self.store, "owned")["error_category"], "TRANSIENT")
        self.assertEqual(task_queue.reconcile(self.root, True, self.factory, now)["tasks"][0]["state"], "BACKOFF")
        later = task_queue.reconcile(self.root, True, self.factory, now + timedelta(minutes=6))
        self.assertEqual(later["tasks"][0]["state"], "DONE")

    def test_managed_archive_handoff_waits_without_impersonating_the_application(self):
        self.store.mutate("owned", lambda item: {**item, "managed_identity": str(self.owned)})
        self.enqueue()
        value = task_queue.reconcile(self.root, True, self.factory)
        self.assertEqual(value["tasks"][0]["state"], "WAITING_APP")
        self.assertTrue(self.owned.exists())
        calls = self.backend.deleted
        task_queue.reconcile(self.root, True, self.factory)
        self.assertEqual(self.backend.deleted, calls)

    def test_unverified_active_task_cannot_enter_background_delivery(self):
        self.store.mutate("owned", lambda item: {**item, "delivery": {}, "status": "ACTIVE"})
        with self.assertRaises(ValueError):
            self.enqueue()
        self.assertFalse(task_queue.queue_path(self.store, "owned").exists())

    def test_disabled_worker_observes_without_processing_and_singleton_is_enforced(self):
        self.enqueue()
        value = task_worker.run(self.root, max_seconds=0.1)
        self.assertFalse(value["enabled"])
        self.assertTrue(self.owned.exists())
        with FileLease(self.store.root / "locks/worker.lock"):
            with self.assertRaises(TimeoutError):
                task_worker.run(self.root, max_seconds=0.1)
        with self.assertRaises(ValueError):
            task_worker.set_enabled(self.root, True, "owned")

    def test_local_only_authorization_is_preserved(self):
        self.store.mutate("owned", lambda item: {**item, "delivery_mode": "local-only"})
        with self.assertRaises(ValueError):
            self.enqueue()

    def test_edits_arriving_after_preflight_cannot_be_committed_by_the_worker(self):
        self.verified()
        self.backend.value["merged"] = False
        self.enqueue()
        original = task_queue.preflight
        def interleaved(store, entry, apply=False):
            result = original(store, entry, apply)
            if apply:
                (self.owned / "user-edit.txt").write_text("preserve unsubmitted work", encoding="utf-8")
            return result
        with mock.patch.object(task_queue, "preflight", side_effect=interleaved):
            value = task_queue.reconcile(self.root, True, self.factory)
        self.assertEqual(value["tasks"][0]["state"], "BLOCKED")
        self.assertEqual(subprocess.check_output(["git", "-C", str(self.owned), "rev-parse", "HEAD"]).decode().strip(), self.head)
        self.assertTrue((self.owned / "user-edit.txt").exists())
        self.assertEqual(self.backend.pushes, 0)

    def advance_base(self):
        base = self.run_git("rev-parse", "HEAD").decode().strip()
        (self.root / "dev-only.txt").write_text("new dev work", encoding="utf-8")
        self.run_git("add", "dev-only.txt")
        self.run_git("commit", "--quiet", "-m", "advance dev fixture")
        tip = self.run_git("rev-parse", "HEAD").decode().strip()
        self.run_git("update-ref", "refs/remotes/origin/dev", tip)
        self.store.mutate("owned", lambda item: {**item, "base_sha": base})
        self.backend.value.update(merged=False, state="open")
        self.backend.latest_run = lambda *_: None
        return tip

    def test_base_advance_refreshes_candidate_and_evidence_for_the_next_tick(self):
        self.verified()
        self.enqueue()
        self.advance_base()
        def verifier(task, root, head):
            self.verified(head)
        with mock.patch.object(self.controller, "verification", side_effect=verifier):
            value = task_queue.reconcile(self.root, True, self.factory)
        self.assertEqual(value["tasks"][0]["state"], "WAITING")
        queued = task_queue.read_queue(self.store, "owned")
        self.assertNotEqual(queued["candidate_sha"], self.head)
        self.assertEqual(queued["verification_sha256"], common.digest(Path(self.store.load("owned")["verification_report"])))
        task_queue.preflight(self.store, queued)

    def test_crash_after_base_merge_recovers_its_recorded_derivation(self):
        self.verified()
        self.enqueue()
        tip = self.advance_base()
        intent = {"from_head": self.head, "dev_sha": tip, "token": "owned-merge-fixture"}
        self.store.mutate("owned", lambda item: {**item, "base_merge_intent": intent})
        subprocess.run(["git", "-C", str(self.owned), "merge", "--no-edit", "-m",
                        "merge fixture\n\nTenon-Base-Merge: owned-merge-fixture", "origin/dev"],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        entry = task_queue.read_queue(self.store, "owned")
        result = task_queue.preflight(self.store, entry, apply=True)
        self.assertTrue(entry["needs_verification"])
        self.assertEqual(result["head_sha"], entry["candidate_sha"])
        # A second crash can read the durable adopted head and still require verification.
        restored = task_queue.read_queue(self.store, "owned")
        self.assertEqual(task_queue.preflight(self.store, restored)["head_sha"], entry["candidate_sha"])

    def test_push_publishes_only_the_verified_sha_when_the_local_branch_advances(self):
        remote = self.root / "build/remote.git"
        self.run_git("init", "--bare", "--quiet", str(remote))
        (self.owned / "input.txt").write_text("later unsubmitted commit", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.owned), "commit", "-am", "later local work", "--quiet"], check=True)
        backend = object.__new__(GitHub)
        backend.repo, backend.remote_url = self.owned, str(remote)
        backend.assert_origin = lambda: None
        backend.push("dev-owned-v2", self.head)
        observed = subprocess.check_output(["git", "ls-remote", str(remote), "refs/heads/dev-owned-v2"]).decode().split()[0]
        self.assertEqual(observed, self.head)

    def test_git_backend_sets_a_timeout_and_noninteractive_input(self):
        with mock.patch("task_backends.subprocess.check_output", return_value=b"") as called:
            command(self.root, "ls-remote", "fixture")
        self.assertEqual(called.call_args.kwargs["timeout"], 120)
        self.assertEqual(called.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(called.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_git_auth_failure_is_policy_despite_connecttimeout_in_argv(self):
        error = subprocess.CalledProcessError(128, ['git', '-c', 'core.sshCommand=ssh -o ConnectTimeout=15'],
                                               stderr=b'Permission denied (publickey)')
        self.assertEqual(task_queue.classify(error), 'POLICY')
        self.assertEqual(task_queue.classify(subprocess.TimeoutExpired(['git'], 120)), 'TRANSIENT')

    def test_git_empty_remnant_recovers_after_interrupted_removal(self):
        self.enqueue()
        from task_backends import WorkspaceBackend
        original = WorkspaceBackend.remove
        def interrupted(primary, task, controller, native_confirmed=False, runner=command):
            original(primary, task, controller, native_confirmed, runner)
            self.owned.mkdir()
            error = OSError('directory lease retained an empty remnant')
            error.winerror = 32
            raise error
        now = datetime.now(timezone.utc)
        with mock.patch.object(WorkspaceBackend, 'remove', side_effect=interrupted):
            first = task_queue.reconcile(self.root, True, self.factory, now)
        self.assertEqual(first['tasks'][0]['state'], 'WAITING')
        self.assertTrue(self.owned.is_dir())
        after = task_queue.reconcile(self.root, True, self.factory, now + timedelta(minutes=6))
        self.assertEqual(after['tasks'][0]['state'], 'DONE')
        self.assertFalse(self.owned.exists())

    def test_commit_arriving_between_candidate_reads_is_not_adopted(self):
        self.verified()
        self.backend.value['merged'] = False
        self.enqueue()
        import task_lifecycle
        original = task_lifecycle.git
        reads = 0
        def interleaved(root, *args):
            nonlocal reads
            if args == ('rev-parse', 'HEAD'):
                reads += 1
                if reads == 3:
                    (self.owned / 'input.txt').write_text('new user commit', encoding='utf-8')
                    subprocess.run(['git', '-C', str(self.owned), 'commit', '-am', 'unsubmitted user work', '--quiet'], check=True)
            return original(root, *args)
        with mock.patch.object(task_lifecycle, 'git', side_effect=interleaved):
            value = task_queue.reconcile(self.root, True, self.factory)
        self.assertEqual(value['tasks'][0]['state'], 'BLOCKED')
        self.assertEqual(self.backend.pushes, 0)
        self.assertNotEqual(subprocess.check_output(['git', '-C', str(self.owned), 'rev-parse', 'HEAD']).decode().strip(), self.head)

    def test_explicit_resubmission_accepts_a_verified_fix_and_preserves_failed_generation(self):
        self.verified()
        self.backend.value['merged'] = False
        first = self.enqueue()
        first.update(state='BLOCKED', reason='original PR CI failed')
        common.write_json(task_queue.queue_path(self.store, 'owned'), first)
        (self.owned / 'input.txt').write_text('verified correction', encoding='utf-8')
        subprocess.run(['git', '-C', str(self.owned), 'commit', '-am', 'fix failed candidate', '--quiet'], check=True)
        head = subprocess.check_output(['git', '-C', str(self.owned), 'rev-parse', 'HEAD']).decode().strip()
        self.verified(head)
        replacement = self.enqueue()
        self.assertEqual(replacement['candidate_sha'], head)
        self.assertEqual(replacement['generation'], 2)
        self.assertEqual(replacement['history'][0]['reason'], 'original PR CI failed')
        self.backend.latest_run = lambda *_: None
        value = task_queue.reconcile(self.root, True, self.factory)
        self.assertEqual(value['tasks'][0]['state'], 'WAITING')

    @unittest.skipUnless(os.name == 'nt', 'Real Windows directory sharing semantics')
    def test_real_windows_interrupted_git_removal_resumes_after_handle_release(self):
        import ctypes
        from ctypes import wintypes
        import task_backends
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(self.owned), 0x80000000, 3, None, 3, 0x02000000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self.enqueue()
        now = datetime.now(timezone.utc)
        probe = task_backends.directory_delete_ready
        count = 0
        def race(path):
            nonlocal count
            count += 1
            return True if count == 1 else probe(path)
        try:
            self.assertFalse(probe(self.owned))
            with mock.patch.object(task_backends, 'directory_delete_ready', side_effect=race):
                first = task_queue.reconcile(self.root, True, self.factory, now)
            self.assertEqual(first['tasks'][0]['state'], 'WAITING')
            self.assertEqual(task_queue.read_queue(self.store, 'owned')['error_category'], 'RESOURCE')
            self.assertTrue(self.owned.is_dir())
        finally:
            kernel.CloseHandle(handle)
        final = task_queue.reconcile(self.root, True, self.factory, now + timedelta(minutes=6))
        self.assertEqual(final['tasks'][0]['state'], 'DONE')
        self.assertFalse(self.owned.exists())


class SchedulerContractTests(unittest.TestCase):
    def test_xml_binds_exact_repository_and_user_without_credentials_or_parallel_instances(self):
        repo = Path('C:/工程 workspace with spaces/tenon')
        xml = task_worker.schedule_xml(repo, "repo-fixture", Path('C:/Python/pythonw.exe'),
                                        repo / '.git/tenon/bin/tenon.py', 'S-1-5-21-fixture')
        root = ET.fromstring(xml)
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        find = lambda name: root.find('.//t:' + name, ns).text
        self.assertEqual(find('MultipleInstancesPolicy'), 'IgnoreNew')
        self.assertEqual(find('Interval'), 'PT5M')
        self.assertEqual(find('LogonType'), 'InteractiveToken')
        self.assertEqual(find('RunLevel'), 'LeastPrivilege')
        self.assertEqual(find('StartWhenAvailable'), 'true')
        self.assertEqual(find('WorkingDirectory'), str(repo))
        self.assertIn('worker run --max-seconds 900', find('Arguments'))
        self.assertIsNone(root.find('.//t:Password', ns))


class LauncherExitTests(RepositoryFixture):
    def test_installed_entry_waits_and_propagates_a_forwarded_failure(self):
        from test_entrypoint import Backend
        import tenon
        shutil.copytree(ROOT / 'scripts', self.root / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        self.run_git('add', 'scripts')
        self.run_git('commit', '--quiet', '-m', 'entry fixture')
        self.run_git('update-ref', 'refs/remotes/origin/dev', 'HEAD')
        value = tenon.install(self.root, Backend())
        result = subprocess.run([sys.executable, value['entry'], 'task', 'status', '--id', 'missing-task'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['status'], 'BLOCKED')
