"""Exercise archive recovery and reclaim only verifiable, unreferenced controller copies."""
from __future__ import annotations
import hashlib
import json
import os
import time
import unittest
from pathlib import Path
from unittest import mock
from test_engineering import RepositoryFixture
from test_lifecycle_v2 import OwnedWorktreeFixture
from task_archive import archive_task, deploy_controller, safe_relative, verify_archive
from task_store import TaskStore
import maintenance
from engineering_common import digest, write_json


class ArchiveMaintenanceTests(OwnedWorktreeFixture):
    def test_verified_restore_exercises_payloads_and_removes_only_its_scratch(self):
        data = b'archive payload\x00' * 65536
        payload = self.owned / 'build/verification/run/large.bin'
        payload.write_bytes(data)
        info = archive_task(self.store, self.value)
        self.assertEqual(digest(payload), hashlib.sha256(data).hexdigest())
        result = maintenance.restore_check(self.root, 'owned')
        self.assertEqual(result['status'], 'PASS')
        self.assertGreater(result['restored_files'], 2)
        self.assertFalse(any((self.store.root / 'restore-scratch').iterdir()))
        self.assertTrue(payload.exists())
        self.assertTrue(Path(info['path']).exists())

    def test_corrupt_archive_records_failure_and_preserves_originals(self):
        info = archive_task(self.store, self.value)
        Path(info['path']).write_bytes(b'corrupted private fixture')
        result = maintenance.audit(self.root, live=False, include_source=False)
        self.assertEqual(result['status'], 'FAIL')
        self.assertTrue(self.owned.exists())
        self.assertTrue(json.loads(Path(result['work_items']).read_text()))
        with self.assertRaises(ValueError):
            maintenance.restore_check(self.root, 'owned')

    def test_portable_archive_paths_reject_escape_and_windows_spellings_on_every_host(self):
        for name in ('../outside', '/absolute', 'C:/outside', 'C:\\outside', '//server/share', 'folder\\file',
                     '.', 'worktree/a//b', 'worktree/a/./b', 'worktree/result.log:alternate', 'worktree/NUL', 'worktree/trailing. '):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_relative(name)
        self.assertEqual(safe_relative('worktree/build/check.log'), Path('worktree/build/check.log'))

    def test_monthly_state_and_restore_results_survive_and_are_not_repeated_early(self):
        archive_task(self.store, self.value)
        directory = self.store.root / 'maintenance/fixture'
        directory.mkdir(parents=True)
        report = {'status':'PASS', 'checks':[], 'report':str(directory/'summary.json'), 'work_items':str(directory/'work-items.json')}
        with mock.patch.object(maintenance, 'audit', return_value=report) as audit:
            result = maintenance.run_due(self.root)
            self.assertEqual(result['restore_exercise']['status'], 'PASS')
            self.assertEqual(json.loads(Path(result['report']).read_text())['restore_exercise']['status'], 'PASS')
            self.assertEqual(maintenance.run_due(self.root)['status'], 'NOT_DUE')
            self.assertEqual(audit.call_count, 1)

    def test_restore_failure_is_checkpointed_instead_of_losing_the_maintenance_result(self):
        archive_task(self.store, self.value)
        directory = self.store.root / 'maintenance/fixture'
        directory.mkdir(parents=True)
        report = {'status':'PASS', 'checks':[], 'report':str(directory/'summary.json'), 'work_items':str(directory/'work-items.json')}
        with mock.patch.object(maintenance, 'audit', return_value=report), mock.patch.object(maintenance, 'restore_check', side_effect=OSError('capacity blocked')):
            self.assertEqual(maintenance.run_due(self.root)['status'], 'FAIL')
        state = json.loads((self.store.root/'maintenance-state.json').read_text())
        self.assertEqual(state['last_status'], 'FAIL')
        self.assertTrue(json.loads(Path(report['work_items']).read_text()))


class ControllerMaintenanceTests(RepositoryFixture):
    def setUp(self):
        super().setUp()
        self.store = TaskStore(self.root)

    def version(self, text):
        path = self.root/'scripts/task_lifecycle.py'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        self.run_git('add', 'scripts')
        self.run_git('commit', '--quiet', '-m', 'controller fixture ' + text)
        commit = self.run_git('rev-parse', 'HEAD').decode().strip()
        deploy_controller(self.store, commit)
        directory = self.store.root/'controllers'/commit
        os.utime(directory, (time.time()-172800, time.time()-172800))
        return commit

    def test_gc_retains_current_previous_active_and_unknown_versions(self):
        versions = [self.version('print('+str(index)+')\n') for index in range(4)]
        write_json(self.store.root/'bin/installation.json', {'repo_id':self.store.repo_id, 'commit':versions[3], 'previous_commit':versions[2]})
        self.store.create({'task_id':'active', 'branch':'dev-active', 'worktree':str(self.root/'build/active'),
            'status':'ACTIVE', 'host_platform':os.name, 'controller':str(self.store.root/'controllers'/versions[1]/'scripts/task_lifecycle.py')})
        unknown = self.store.root/'controllers/manual'
        unknown.mkdir()
        (unknown/'keep.txt').write_text('preserve unknown data')
        before = maintenance.prune_controllers(self.root)
        self.assertEqual(before['removed'], [])
        self.assertTrue((self.store.root/'controllers'/versions[0]).exists())
        result = maintenance.prune_controllers(self.root, True)
        self.assertEqual(result['removed'], [versions[0]])
        self.assertTrue(all((self.store.root/'controllers'/value).exists() for value in versions[1:]))
        self.assertTrue((unknown/'keep.txt').exists())

    def test_manifest_and_payload_tampering_cannot_override_the_git_source(self):
        commit = self.version("print('original')\n")
        directory = self.store.root/'controllers'/commit
        path = directory/'scripts/task_lifecycle.py'
        path.write_text("print('tampered')\n")
        manifest_path = directory/'controller.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['files'][0]['sha256'] = digest(path)
        write_json(manifest_path, manifest)
        with self.assertRaises(ValueError):
            maintenance.verify_controller(directory, self.root)
        result = maintenance.prune_controllers(self.root, True)
        self.assertEqual(result['removed'], [])
        self.assertTrue(directory.exists())
        with self.assertRaises(ValueError):
            deploy_controller(self.store, commit)

    def test_unclassified_files_inside_a_real_old_controller_are_preserved(self):
        commit = self.version("print('owned')\n")
        directory = self.store.root/'controllers'/commit
        (directory/'important-notes.txt').write_text('unowned data to preserve')
        os.utime(directory, (time.time()-172800, time.time()-172800))
        result = maintenance.prune_controllers(self.root, True)
        self.assertEqual(result['removed'], [])
        self.assertTrue((directory/'important-notes.txt').exists())

    def test_live_worker_without_heartbeat_protects_old_controller_versions(self):
        from task_store import FileLease
        commit = self.version("print('live old worker')\n")
        with FileLease(self.store.root/'locks/worker.lock'):
            result = maintenance.prune_controllers(self.root, True)
            self.assertEqual(result['removed'], [])
            self.assertTrue(result['active_worker_identity_unknown'])
        self.assertTrue((self.store.root/'controllers'/commit).exists())
        self.assertEqual(maintenance.prune_controllers(self.root, True)['removed'], [commit])

    def test_foreign_host_reclamation_is_rejected_before_native_locks(self):
        commit = self.version("print('native ownership')\n")
        write_json(self.store.root/'host.json', {'schema_version':1, 'repo_id':self.store.repo_id,
                   'host_platform':'posix' if os.name == 'nt' else 'nt'})
        with mock.patch.object(maintenance.FileLease, '__enter__', side_effect=AssertionError('foreign write lock')):
            with self.assertRaises(ValueError):
                maintenance.prune_controllers(self.root, True)
            self.assertEqual(maintenance.prune_controllers(self.root)['removed'], [])
        self.assertTrue((self.store.root/'controllers'/commit).exists())


class WorkerMaintenanceTests(RepositoryFixture):
    def test_worker_publishes_lease_identity_before_its_first_queue_operation(self):
        import task_worker
        store = TaskStore(self.root)
        def observe(repo, apply=False):
            heartbeat = json.loads((store.root/'operations/worker-latest.json').read_text())
            with (store.root/'locks/worker.lock').open('rb') as stream:
                stream.seek(1)  # byte zero is the native Windows lock range
                lease = json.loads(stream.read())
            self.assertEqual(heartbeat['state'], 'STARTING')
            self.assertEqual(heartbeat['lease_token'], lease['token'])
            self.assertEqual(heartbeat['pid'], lease['pid'])
            return {'tasks':[], 'mode':'read-only'}
        with mock.patch.object(task_worker, 'reconcile', side_effect=observe):
            self.assertFalse(task_worker.run(self.root, max_seconds=0.01)['enabled'])

    def test_monthly_upkeep_waits_for_an_idle_queue(self):
        import task_worker
        store = TaskStore(self.root)
        write_json(store.root/'worker.json', {'schema_version':1,'repo_id':store.repo_id,'enabled':True})
        with mock.patch.object(task_worker, 'reconcile', return_value={'tasks':[{'state':'WAITING'}]}), \
                mock.patch.object(maintenance, 'run_due') as upkeep:
            task_worker.run(self.root, max_seconds=0.001, interval=0.001)
            upkeep.assert_not_called()
        with mock.patch.object(task_worker, 'reconcile', return_value={'tasks':[]}), \
                mock.patch.object(maintenance, 'run_due', return_value={'status':'NOT_DUE'}) as upkeep:
            value = task_worker.run(self.root, max_seconds=0.001)
            self.assertEqual(value['maintenance']['status'], 'NOT_DUE')
            upkeep.assert_called_once()
