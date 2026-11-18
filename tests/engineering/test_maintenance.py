"""Exercise archive recovery and reclaim only verifiable, unreferenced controller copies."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace
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

    def test_windows_superscript_device_names_are_rejected_before_archive_payload_access(self):
        import zipfile
        for prefix in ('COM','LPT'):
            for suffix in ('\u00b9','\u00b2','\u00b3'):
                name='worktree/'+prefix+suffix+'.txt'
                with self.subTest(name=name), self.assertRaises(ValueError):
                    safe_relative(name)
                archive=self.root/'reserved-name.zip'
                payload=b'not a device access'
                with zipfile.ZipFile(archive,'w') as output:
                    output.writestr(name,payload)
                    output.writestr('manifest.json',json.dumps({'schema_version':1,'files':[{
                        'path':name,'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}]}))
                with self.assertRaises(ValueError):
                    verify_archive(archive)

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
        from controller_leases import EARLY_GUARD, ENTRIES
        import shutil
        from test_engineering import ROOT
        scripts = self.root/'scripts'
        scripts.mkdir(parents=True, exist_ok=True)
        for name in ENTRIES:
            (scripts/name).write_text('from __future__ import annotations\n'+EARLY_GUARD+'\n'+text,encoding='utf-8')
        shutil.copyfile(ROOT/'scripts/controller_leases.py',scripts/'controller_leases.py')
        self.run_git('add', 'scripts')
        self.run_git('commit', '--quiet', '-m', 'controller fixture ' + text)
        commit = self.run_git('rev-parse', 'HEAD').decode().strip()
        deploy_controller(self.store, commit)
        directory = self.store.root/'controllers'/commit
        os.utime(directory, (time.time()-172800, time.time()-172800))
        return commit

    def test_gc_retains_current_previous_active_and_unknown_versions(self):
        versions = [self.version('print('+str(index)+')\n') for index in range(4)]
        write_json(self.store.root/'bin/installation.json', {'schema_version':1,'repo_id':self.store.repo_id, 'commit':versions[3], 'previous_commit':versions[2]})
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
        next(item for item in manifest['files'] if item['path']=='scripts/task_lifecycle.py')['sha256'] = digest(path)
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


class MonthlyInventoryTests(RepositoryFixture):
    def setUp(self):
        super().setUp()
        self.root=self.root.resolve()
        self.store=TaskStore(self.root)

    def drill_report(self):
        scratch=self.store.root/'drill-scratch'
        root=scratch/'tenon-drill-owned'; root.mkdir(parents=True)
        (root/'owner-token').write_text('owned-token',encoding='utf-8')
        manifest=root/'fixture.json'
        write_json(manifest,{'schema_version':1,'scenario':'exit-resume','phase':'CREATING','root':str(root),
                             'production_common':str(self.store.common),'owner_token':'owned-token'})
        output=self.store.root/'operations/drills/retained'
        write_json(output/'summary.json',{'schema_version':1,'kind':'engineering-recovery-drill',
            'production_common':str(self.store.common),'scenarios':[{'scenario':'exit-resume','status':'FAIL',
            'fixture_removed':False,'fixture_preserved':True,'recovery_manifest':str(manifest)}]})
        return manifest

    def test_partial_owned_drill_inventory_never_replays_repairs_or_deletes(self):
        manifest=self.drill_report()
        before={path: path.read_bytes() for path in self.store.root.rglob('*') if path.is_file()}
        with mock.patch('engineering_drills._control',side_effect=AssertionError('no worker qualification')):
            result=maintenance.retained_drill_reports(self.store.common)
        self.assertEqual(result['rows'][0]['phase'],'CREATING')
        self.assertEqual(result['rows'][0]['ownership'],'BOUNDARY_OWNER_MATCH')
        original=Path.lstat
        def denied(path,*args,**kwargs):
            if path==manifest:raise PermissionError('fixture metadata access denied')
            return original(path,*args,**kwargs)
        with mock.patch.object(Path,'lstat',denied):
            self.assertEqual(maintenance.retained_drill_reports(self.store.common)['rows'][0]['inspection'],'UNKNOWN_PERMISSION')
        self.assertEqual({path:path.read_bytes() for path in before},before)
        manifest.unlink()
        self.assertEqual(maintenance.retained_drill_reports(self.store.common)['rows'][0]['inspection'],'MISSING')

    def test_bad_or_foreign_drill_report_is_unknown_without_following_its_target(self):
        manifest=self.drill_report()
        report=self.store.root/'operations/drills/retained/summary.json'
        before=manifest.read_bytes()
        report.write_text('{broken',encoding='utf-8')
        self.assertEqual(maintenance.retained_drill_reports(self.store.common)['rows'][0]['inspection'],'UNKNOWN')
        value={'schema_version':1,'kind':'engineering-recovery-drill','production_common':str(self.store.common),
               'scenarios':[{'scenario':'exit-resume','status':'FAIL','recovery_manifest':str(self.root/'foreign/fixture.json')}]}
        write_json(report,value)
        self.assertEqual(maintenance.retained_drill_reports(self.store.common)['rows'][0]['inspection'],'UNKNOWN')
        self.assertEqual(manifest.read_bytes(),before)

    def test_retained_drill_inventory_does_not_follow_redirected_operations_ancestor(self):
        manifest = self.drill_report()
        parent = self.store.root / "operations"
        before = manifest.read_bytes()
        original = Path.lstat

        def redirected(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if path == parent:
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
            return info

        with mock.patch.object(Path, "lstat", redirected):
            view = maintenance.retained_drill_reports(self.store.common)
        self.assertEqual(view["namespace"], "UNKNOWN")
        self.assertEqual(manifest.read_bytes(), before)

    def test_audit_backlog_includes_older_merged_tasks_but_never_claims_cleanup(self):
        self.store.create({'task_id':'old-pending','status':'WAITING_DEV_CI',
            'worktree':str(self.root/'build/older-pending-worktree'),
            'delivery':{'merged':True,'merged_at':'2025-01-01T00:00:00Z'}})
        self.store.create({'task_id':'already-cleaned','status':'CLEANED',
            'worktree':str(self.root/'build/old-clean-worktree'),'delivery':{'merged':True}})
        with mock.patch.object(maintenance,'controller_inventory',return_value={'versions':[]}), \
                mock.patch.object(maintenance,'retained_drill_reports',return_value={'rows':[]}), \
                mock.patch.object(maintenance,'archive_capacity',return_value={'total_bytes':None}), \
                mock.patch.object(maintenance,'prune_controllers',side_effect=AssertionError('audit cannot reclaim')):
            result=maintenance.audit(self.root,live=False,include_source=False)
        self.assertEqual([row['task_id'] for row in result['merged_cleanup_backlog']],['old-pending'])
        self.assertEqual(self.store.load('old-pending')['status'],'WAITING_DEV_CI')

    def test_archive_capacity_deduplicates_known_identity_but_never_same_sha_or_unknown_bytes(self):
        root=self.store.root/'archives/owned'; root.mkdir(parents=True)
        supplementary=self.store.root/'operations/owned'
        supplementary.mkdir(parents=True)
        first,second,alias=root/'a.zip',supplementary/'acceptance-test.zip',root/'alias.zip'
        first.write_bytes(b'same'); second.write_bytes(b'same')
        try:os.link(first,alias)
        except OSError:self.skipTest('Fixture filesystem does not support a native hardlink')
        tasks=[{'task_id':'owned','archive':{'path':str(first),'sha256':'a'*64},
                'archive_history':[{'path':str(item),'sha256':'a'*64} for item in (first,second,alias)]}]
        value=maintenance.archive_capacity(self.store,tasks)
        self.assertEqual(value['duplicate_references'],1)
        self.assertEqual(value['observed_zip_paths'],3)
        self.assertEqual(value['confirmed_unique_files'],2)
        self.assertEqual(value['total_bytes'],8)
        original=Path.lstat
        def unknown(path,*args,**kwargs):
            info=original(path,*args,**kwargs)
            if path.suffix=='.zip':
                return SimpleNamespace(st_mode=info.st_mode,st_file_attributes=0,st_dev=0,st_ino=0,st_size=info.st_size)
            return info
        with mock.patch.object(Path,'lstat',unknown):
            unknown_value=maintenance.archive_capacity(self.store,tasks)
        self.assertIsNone(unknown_value['total_bytes'])
        self.assertGreater(unknown_value['known_path_bytes'],0)
        self.assertEqual(unknown_value['unknown_identity_paths'],3)

    def test_retention_reasons_observe_the_existing_current_previous_state(self):
        versions=['a'*40,'b'*40]
        for version in versions:
            directory=self.store.root/'controllers'/version
            directory.mkdir(parents=True)
            (directory/'payload').write_bytes(b'owned')
            os.utime(directory,(time.time()-172800,time.time()-172800))
        write_json(self.store.root/'bin/installation.json',{'schema_version':1,'repo_id':self.store.repo_id,
            'commit':versions[0],'previous_commit':versions[1],'host_platform':os.name})
        with mock.patch.object(maintenance,'verify_controller',return_value={'files':[]}), \
                mock.patch.object(maintenance,'unclassified_payloads',return_value=[]), \
                mock.patch('controller_leases.version_observation',return_value={'state':'IDLE','held':False}), \
                mock.patch('controller_leases.registry_issues',return_value=[]), \
                mock.patch.object(maintenance.shutil,'rmtree',side_effect=AssertionError('inventory cannot delete')):
            value=maintenance.controller_inventory(self.store)
        rows={row['commit']:row for row in value['versions']}
        self.assertEqual({row['state'] for row in rows.values()},{'RETAINED'})
        self.assertIn('current-installed',rows[versions[0]]['retention_reasons'])
        self.assertIn('previous-installed',rows[versions[1]]['retention_reasons'])

    def test_rotation_cursor_advances_only_after_the_existing_successful_restore(self):
        folder=self.store.root/'maintenance/fixture'; folder.mkdir(parents=True)
        result={'status':'PASS','checks':[],'report':str(folder/'summary.json'),'work_items':str(folder/'work-items.json')}
        with mock.patch.object(self.store.__class__,'all',return_value=[{'task_id':'sample','archive':{'path':'unused'}}]), \
                mock.patch.object(maintenance,'audit',return_value=copy.deepcopy(result)), \
                mock.patch('engineering_metrics.collect',return_value={'diagnostics':[]}), \
                mock.patch('engineering_metrics.improvement_items',return_value={'items':[]}), \
                mock.patch.object(maintenance,'restore_check',side_effect=ValueError('retained restore failure')), \
                mock.patch.object(maintenance,'prune_controllers') as reclaim:
            observed=maintenance.run_due(self.root,force=True)
        state=json.loads((self.store.root/'maintenance-state.json').read_bytes())
        self.assertEqual(observed['status'],'FAIL'); self.assertEqual(state['restore_cursor'],0)
        reclaim.assert_not_called()


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
