"""Disposable-repository acceptance tests for persistent state and deletion gates."""
from __future__ import annotations
import copy
import json
import os
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock
from test_engineering import RepositoryFixture, ROOT
from task_store import TaskStore, resource_lease
from task_archive import archive_task, ownership_errors, verify_archive
from task_lifecycle import Lifecycle, GitHub, ci_green
import task
import skill_eval


def green(sha, event="push"):
    return {"workflow_run":{"id":42,"run_attempt":1,"head_sha":sha,"event":event,
                             "status":"completed","conclusion":"success"},
            "jobs":[{"name":name,"run_id":42,"run_attempt":1,"status":"completed","conclusion":"success"}
                    for name in ("format","check","sim","tools-windows","ci-required")]}


class FakeGitHub:
    name = "fixture/repo"
    def __init__(self, pull, head):
        self.value, self.remote = pull,head
        self.snapshot = green(head)
        self.deleted = 0
        self.pushes = 0
    def refresh(self):
        pass
    def pull(self, number):
        return copy.deepcopy(self.value)
    def find_pull(self, branch):
        return self.pull(7)
    def latest_run(self,sha,event,number=None):
        return green(sha,"pull_request") if event=="pull_request" else copy.deepcopy(self.snapshot)
    def remote_head(self, branch):
        return self.remote
    def delete_remote(self, branch, head):
        if self.remote!=head:
            raise ValueError("lease changed")
        self.remote = None
        self.deleted += 1
    def push(self,*_):
        self.pushes += 1
    def diagnostics(self,*_):
        pass


class SharedStateTests(RepositoryFixture):
    def test_read_only_status_does_not_require_shared_store_write_locks(self):
        task.start("reader","fixture",["*"],["make verify"],[],self.root)
        with mock.patch("task_store.FileLease.__enter__",side_effect=AssertionError("read attempted a write lock")):
            self.assertEqual(TaskStore(self.root).load("reader")["task_id"],"reader")

    def test_other_task_cannot_claim_the_same_worktree(self):
        task.start("first","fixture",["*"],["make verify"],[],self.root)
        with self.assertRaises(ValueError):
            task.start("second","fixture",["*"],["make verify"],[],self.root)

    def test_concurrent_updates_and_journal_recovery_do_not_lose_events(self):
        value = task.start("concurrent","fixture",["*"],["make verify"],[],self.root)
        store = TaskStore(self.root)
        def update(index):
            def append(row):
                row.setdefault("numbers",[]).append(index)
                return row
            store.mutate("concurrent",append,"append")
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(update,range(24)))
        self.assertEqual(sorted(store.load("concurrent")["numbers"]),list(range(24)))
        store.path("concurrent").unlink()
        self.assertEqual(sorted(store.load("concurrent")["numbers"]),list(range(24)))
        self.assertEqual(value["schema_version"],2)

    def test_foreign_host_attempts_are_immutable_until_native_transaction_drains_them(self):
        task.start("inbox","fixture",["*"],["make verify"],[],self.root)
        store = TaskStore(self.root)
        store.enqueue_attempt("inbox",{"run_id":"external-1","status":"PASS"})
        self.assertEqual(store.load("inbox")["verification_attempts"][0]["run_id"],"external-1")
        self.assertEqual(len(store.inbox("inbox")),1)
        store.mutate("inbox",lambda value:{**value,"marker":True},"native-resume")
        self.assertEqual(len(store.inbox("inbox")),0)
        self.assertEqual(len(store.load("inbox")["verification_attempts"]),1)

    def test_incomplete_journal_tail_is_recovered_without_corrupting_new_events(self):
        task.start("journal","fixture",["*"],["make verify"],[],self.root)
        store = TaskStore(self.root)
        with store.path("journal").with_suffix(".jsonl").open("ab") as output:
            output.write(b'{"interrupted":')
        store.mutate("journal",lambda value:{**value,"marker":True},"resumed")
        self.assertTrue(store.load("journal")["marker"])
        [json.loads(line) for line in store.path("journal").with_suffix(".jsonl").read_text().splitlines()]

    def test_v1_migration_preserves_unknown_values_and_counts_delivery(self):
        folder = self.root/"build/tasks"
        folder.mkdir(parents=True)
        old = {"schema_version":1,"task_id":"legacy","branch":"dev-fixture-test","worktree":str(self.root),
               "status":"INTEGRATED","started_at":"2026-11-16T01:00:00+00:00","completed_at":"2026-11-16T02:00:00+00:00",
               "human_interventions":None,"rework_count":None,"unknown_field":{"keep":True},
               "delivery":{"merged":True,"merged_at":"2026-11-16T02:00:00Z"}}
        (folder/"legacy.json").write_text(json.dumps(old),encoding="utf-8")
        store = TaskStore(self.root)
        store.migrate(self.root)
        migrated = store.load("legacy")
        self.assertEqual(migrated["legacy"],old)
        self.assertIsNone(migrated["human_interventions"])
        report = task.metrics(folder)
        self.assertEqual(report["delivered_tasks"],1)
        self.assertEqual(report["ready_fraction"],1)
        self.assertEqual(report["cleaned_tasks"],0)
        self.assertEqual(len(store.migrate(self.root)),1)

    def test_resource_lease_blocks_cleanup_and_survives_other_worktree_context(self):
        task.start("lease","fixture",["*"],["make verify"],[],self.root)
        with resource_lease(self.root,"evaluation"):
            self.assertTrue(any("Active resource" in item for item in task.cleanup_check(self.root)))
        self.assertFalse(list((TaskStore(self.root).root/"leases").glob("*.json")))


class MergedPRQueryTests(unittest.TestCase):
    def backend(self):
        backend = object.__new__(GitHub)
        backend.name = "fixture/repo"
        self.run = {**green("candidate","pull_request")["workflow_run"],
                    "path":".github/workflows/ci.yml","head_branch":"dev-owned",
                    "head_repository":{"full_name":"fixture/repo"},"pull_requests":[]}
        self.pull = {"number":7,"merged":True,"base":{"ref":"dev"},
                     "head":{"sha":"candidate","ref":"dev-owned","repo":{"full_name":"fixture/repo"}}}
        backend.pull = mock.Mock(side_effect=lambda number:copy.deepcopy(self.pull))
        backend.api = mock.Mock(side_effect=lambda endpoint: {"workflow_runs":[copy.deepcopy(self.run)]}
                                if endpoint.startswith("actions/runs?") else {"jobs":green("candidate","pull_request")["jobs"]})
        return backend

    def test_closed_pr_with_missing_association_keeps_exact_ci_proof(self):
        backend = self.backend()
        self.assertTrue(ci_green(backend.latest_run("candidate","pull_request",7),"candidate","pull_request"))
        backend.pull.assert_called_once_with(7)

    def test_detached_run_rejects_changed_or_unrelated_ownership(self):
        changes = [lambda:self.pull.update(merged=False),
                   lambda:self.pull["base"].update(ref="main"),
                   lambda:self.pull["head"].update(sha="changed"),
                   lambda:self.pull["head"]["repo"].update(full_name="foreign/repo"),
                   lambda:self.pull["head"].update(repo=None),
                   lambda:self.run.update(head_sha="changed"),
                   lambda:self.run.update(head_branch="dev-other"),
                   lambda:self.run["head_repository"].update(full_name="foreign/repo"),
                   lambda:self.run.update(head_repository=None),
                   lambda:self.run.update(pull_requests=[{"number":8}])]
        for index,change in enumerate(changes):
            with self.subTest(index=index):
                backend = self.backend()
                change()
                self.assertIsNone(backend.latest_run("candidate","pull_request",7))

    def test_explicit_association_remains_supported(self):
        backend = self.backend()
        self.run["pull_requests"] = [{"number":7}]
        self.assertTrue(ci_green(backend.latest_run("candidate","pull_request",7),"candidate","pull_request"))
        backend.pull.assert_not_called()


class ConditionalRemoteDeletionTests(RepositoryFixture):
    def test_real_git_lease_refuses_a_changed_remote_and_deletes_exact_tip(self):
        remote = self.root/"build/remote.git"
        remote.parent.mkdir(exist_ok=True)
        self.run_git("init","--bare","--quiet",str(remote))
        branch = "dev-lease-fixture"
        old = self.run_git("rev-parse","HEAD").decode().strip()
        self.run_git("push",str(remote),"HEAD:refs/heads/"+branch)
        self.run_git("commit","--quiet","--allow-empty","-m","new remote work")
        new = self.run_git("rev-parse","HEAD").decode().strip()
        self.run_git("push",str(remote),"HEAD:refs/heads/"+branch)
        backend = object.__new__(GitHub)
        backend.repo,backend.remote_url = self.root,str(remote)
        backend.assert_origin = lambda:None
        with self.assertRaises(subprocess.CalledProcessError):
            backend.delete_remote(branch,old)
        self.assertTrue(self.run_git("ls-remote","--heads",str(remote),branch).decode().startswith(new))
        backend.delete_remote(branch,new)
        self.assertFalse(self.run_git("ls-remote","--heads",str(remote),branch).strip())


class OwnedWorktreeFixture(RepositoryFixture):
    def setUp(self):
        super().setUp()
        self.root = self.root.resolve()
        self.owned = self.root/"build/owned-worktree"
        self.owned.parent.mkdir(parents=True)
        self.run_git("worktree","add","--quiet","-b","dev-owned-v2",str(self.owned),"HEAD")
        (self.owned/"input.txt").write_text("task result\n",encoding="utf-8")
        subprocess.run(["git","-C",str(self.owned),"add","input.txt"],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        subprocess.run(["git","-C",str(self.owned),"commit","--quiet","-m","owned fixture"],check=True)
        self.head = subprocess.check_output(["git","-C",str(self.owned),"rev-parse","HEAD"]).decode().strip()
        self.run_git("update-ref","refs/remotes/origin/dev",self.head)
        (self.owned/"build/verification/run").mkdir(parents=True)
        (self.owned/"build/verification/run/result.log").write_text("retained evidence",encoding="utf-8")
        self.store = TaskStore(self.root)
        self.value = self.store.create({"task_id":"owned","branch":"dev-owned-v2","worktree":str(self.owned),
            "host_platform":os.name,"owner_id":"fixture-owner","managed_identity":None,"status":"DEV_GREEN",
            "started_at":"2026-11-16T01:00:00+00:00","base_sha":self.head,"head_sha":self.head,
            "green_dev_sha":self.head,"allowed_paths":["*"],"goal":"fixture", "dev_ci":{"conclusion":"success"},
            "delivery":{"number":7,"merged":True,"html_url":"https://example.invalid/pr/7","head_sha":self.head,
                        "merge_commit_sha":self.head,"merged_at":"2026-11-16T02:00:00+00:00"}})
        pull = {"number":7,"merged":True,"state":"closed","head":{"sha":self.head},"base":{"ref":"dev"},
                "html_url":"https://example.invalid/pr/7","merge_commit_sha":self.head,"merged_at":"2026-11-16T02:00:00+00:00"}
        self.backend = FakeGitHub(pull,self.head)
        self.controller = Lifecycle(self.root,"owned",self.backend)
        self.deploy = mock.patch("task_lifecycle.deploy_controller",return_value=self.store.root/"controller.py")
        self.deploy.start(); self.addCleanup(self.deploy.stop)


class ArchiveAndCleanupTests(OwnedWorktreeFixture):
    def test_normal_closure_removes_only_owned_resources_and_retains_archive(self):
        result = self.controller.cleanup()
        self.assertEqual(result["status"],"CLEANED")
        self.assertFalse(self.owned.exists())
        self.assertTrue(self.root.exists())
        self.assertEqual(self.backend.deleted,1)
        self.assertEqual(self.controller.cleanup()["status"],"CLEANED")
        self.assertEqual(self.backend.deleted,1)
        manifest = verify_archive(Path(result["archive"]["path"]),result["archive"]["sha256"])
        self.assertTrue(any(item["path"].endswith("result.log") for item in manifest["files"]))
        self.assertEqual(subprocess.run(["git","-C",str(self.root),"show-ref","--verify","--quiet","refs/heads/dev-owned-v2"]).returncode,1)

    def test_managed_worktree_requires_app_archive_then_resumes_finalization(self):
        self.store.mutate("owned",lambda value:{**value,"managed_identity":str(self.owned)})
        result = self.controller.cleanup()
        self.assertEqual(result["status"],"NEEDS_APP_ARCHIVE")
        self.assertTrue(self.owned.exists())
        self.assertIsNone(self.backend.remote)
        with self.assertRaises(ValueError):
            self.controller.finalize()
        self.run_git("worktree","remove",str(self.owned))
        self.assertEqual(self.controller.finalize()["status"],"CLEANED")

    def native_receipt(self):
        self.store.mutate("owned",lambda value:{**value,"managed_identity":str(self.owned)})
        value = {"attachmentType":"archived_worktree","identityKey":str(self.owned),
                 "payload":{"worktree":{"root":str(self.owned),"hostId":"local","sourceGitRoot":str(self.root)}}}
        path = self.root/"build/native-receipt.json"
        path.write_text(json.dumps(value),encoding="utf-8")
        return path,value

    def test_confirmed_interrupted_native_archive_finishes_without_force(self):
        receipt,_ = self.native_receipt()
        self.assertEqual(self.controller.cleanup()["status"],"NEEDS_APP_ARCHIVE")
        from task_lifecycle import command
        with mock.patch("task_lifecycle.command",wraps=command) as calls:
            result = self.controller.complete_app_cleanup(receipt)
        self.assertEqual(result["status"],"CLEANED")
        removals = [call.args for call in calls.call_args_list if call.args[1:3]==("worktree","remove")]
        self.assertEqual(removals,[(self.root,"worktree","remove",str(self.owned))])
        manifest = verify_archive(Path(result["archive"]["path"]))
        self.assertIn("operations/app-archive-receipt.json",[item["path"] for item in manifest["files"]])
        self.assertFalse(self.owned.exists())

    def test_invalid_native_receipts_never_remove_resources(self):
        receipt,value = self.native_receipt()
        changes = [lambda r:r.update(attachmentType="worktree"),lambda r:r.update(identityKey=str(self.root)),
                   lambda r:r["payload"]["worktree"].update(root=str(self.root)),
                   lambda r:r["payload"]["worktree"].update(hostId="other"),
                   lambda r:r["payload"]["worktree"].update(sourceGitRoot=str(self.owned)),
                   lambda r:r["payload"]["worktree"].pop("sourceGitRoot"),
                   lambda r:r.update(payload=None)]
        for index,change in enumerate(changes):
            with self.subTest(index=index):
                changed = copy.deepcopy(value); change(changed)
                receipt.write_text(json.dumps(changed),encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.controller.complete_app_cleanup(receipt)
                self.assertTrue(self.owned.exists())
                self.assertEqual(self.backend.deleted,0)

        for malformed in ([],{},None):
            receipt.write_text(json.dumps(malformed),encoding="utf-8")
            with self.assertRaises(ValueError):
                self.controller.complete_app_cleanup(receipt)
            self.assertEqual(self.backend.deleted,0)

    def test_interrupted_nonforced_removal_is_safely_resumable(self):
        receipt,_ = self.native_receipt()
        from task_lifecycle import command
        def interrupted(repo,*args):
            if args[:2]==("worktree","remove"):
                raise OSError("directory busy")
            return command(repo,*args)
        with mock.patch("task_lifecycle.command",side_effect=interrupted),self.assertRaises(OSError):
            self.controller.complete_app_cleanup(receipt)
        self.assertTrue(self.owned.exists())
        self.assertEqual(self.backend.deleted,1)
        self.assertEqual(self.controller.complete_app_cleanup(receipt)["status"],"CLEANED")
        self.assertEqual(self.backend.deleted,1)

    def empty_native_remnant(self):
        receipt,_ = self.native_receipt()
        self.controller.cleanup()
        self.run_git("worktree","remove",str(self.owned))
        self.owned.mkdir()
        return receipt

    def test_empty_unregistered_native_remnant_finishes_nonrecursively(self):
        receipt = self.empty_native_remnant()
        self.assertEqual(self.controller.complete_app_cleanup(receipt)["status"],"CLEANED")
        self.assertFalse(self.owned.exists())
        self.assertEqual(self.backend.deleted,1)

    def test_busy_empty_remnant_keeps_refs_and_can_resume(self):
        receipt = self.empty_native_remnant()
        original = Path.rmdir
        def busy(path,*args,**kwargs):
            if path==self.owned:
                raise PermissionError("directory busy")
            return original(path,*args,**kwargs)
        with mock.patch.object(Path,"rmdir",busy),self.assertRaises(PermissionError):
            self.controller.complete_app_cleanup(receipt)
        self.assertTrue(self.owned.exists())
        self.assertEqual(self.run_git("rev-parse","dev-owned-v2").decode().strip(),self.head)
        self.assertEqual(self.controller.complete_app_cleanup(receipt)["status"],"CLEANED")

    def test_empty_remnant_rechecks_original_pr_ci_before_removal(self):
        receipt = self.empty_native_remnant()
        original = self.backend.latest_run
        def failed(sha,event,number=None):
            result = original(sha,event,number)
            if event=="pull_request":
                result["workflow_run"]["conclusion"]="failure"
            return result
        self.backend.latest_run = failed
        with self.assertRaises(ValueError):
            self.controller.complete_app_cleanup(receipt)
        self.assertTrue(self.owned.exists())
        self.assertEqual(self.run_git("rev-parse","dev-owned-v2").decode().strip(),self.head)

    def test_new_files_in_unregistered_remnant_are_preserved(self):
        receipt = self.empty_native_remnant()
        (self.owned/"new-user-work.txt").write_text("retain",encoding="utf-8")
        with self.assertRaises((ValueError,subprocess.CalledProcessError)):
            self.controller.complete_app_cleanup(receipt)
        self.assertEqual((self.owned/"new-user-work.txt").read_text(),"retain")
        self.assertEqual(self.run_git("rev-parse","dev-owned-v2").decode().strip(),self.head)

    def test_remote_tip_change_refuses_deletion(self):
        self.backend.remote = "e"*40
        with self.assertRaises(ValueError):
            self.controller.cleanup()
        self.assertTrue(self.owned.exists())
        self.assertEqual(self.backend.deleted,0)

    def test_foreign_origin_registration_blocks_controller(self):
        self.store.mutate("owned",lambda value:{**value,"repository":"another/repo"})
        with self.assertRaises(ValueError):
            Lifecycle(self.root,"owned",self.backend)
        self.assertEqual(self.backend.deleted,0)

    def test_changed_source_during_archive_keeps_originals(self):
        import task_archive
        original = task_archive.source_state
        calls = []
        def changing(root):
            if calls:
                (self.owned/"input.txt").write_text("new user edit",encoding="utf-8")
            calls.append(True)
            return original(root)
        with mock.patch.object(task_archive,"source_state",side_effect=changing), self.assertRaises(ValueError):
            archive_task(self.store,self.value)
        self.assertTrue(self.owned.exists())
        self.assertEqual(self.backend.deleted,0)

    def test_compare_and_delete_race_preserves_worktree(self):
        def changed(*_):
            self.backend.remote = "f"*40
            raise ValueError("lease changed")
        self.backend.delete_remote = changed
        with self.assertRaises(ValueError):
            self.controller.cleanup()
        self.assertTrue(self.owned.exists())

    def test_dirty_untracked_and_active_resources_stop_before_remote_mutation(self):
        (self.owned/"outside.txt").write_text("user work",encoding="utf-8")
        with self.assertRaises(ValueError):
            self.controller.cleanup()
        self.assertEqual(self.backend.deleted,0)
        (self.owned/"outside.txt").unlink()
        with resource_lease(self.owned,"verification"), self.assertRaises(ValueError):
            self.controller.cleanup()
        self.assertTrue(self.owned.exists())

    def test_primary_and_main_are_never_cleanup_targets(self):
        bad = copy.deepcopy(self.value)
        bad["worktree"] = str(self.root)
        self.assertTrue(any("Primary" in item for item in ownership_errors(self.store,bad)))
        bad = copy.deepcopy(self.value); bad["branch"]="main"
        self.assertTrue(ownership_errors(self.store,bad))

    def test_failed_original_pr_ci_never_authorizes_cleanup(self):
        original = self.backend.latest_run
        def failed(sha,event,number=None):
            result = original(sha,event,number)
            if event=="pull_request":
                result["workflow_run"]["conclusion"]="failure"
            return result
        self.backend.latest_run = failed
        with self.assertRaises(ValueError):
            self.controller.cleanup()
        self.assertEqual(self.backend.deleted,0)

    def test_failed_dev_ci_and_unmerged_pr_never_delete(self):
        self.backend.snapshot["workflow_run"]["conclusion"]="failure"
        with self.assertRaises(ValueError):
            self.controller.cleanup()
        self.assertEqual(self.backend.deleted,0)
        self.backend.snapshot=green(self.head)
        self.backend.value["merged"]=False
        with self.assertRaises(ValueError):
            self.controller.cleanup()
        self.assertTrue(self.owned.exists())

    def test_unknown_ignored_data_is_not_silently_deleted(self):
        (self.owned/"build/user-data.txt").write_text("valuable",encoding="utf-8")
        with self.assertRaises(ValueError):
            self.controller.cleanup()
        self.assertTrue((self.owned/"build/user-data.txt").exists())
        self.assertEqual(self.backend.deleted,0)

    def test_new_reports_after_archive_get_a_new_verified_revision(self):
        first = archive_task(self.store,self.value)
        self.store.mutate("owned",lambda value:{**value,"marker":"new-report"})
        (self.owned/"build/verification/run/another.log").write_text("new evidence",encoding="utf-8")
        second = archive_task(self.store,self.store.load("owned"))
        self.assertNotEqual(first["path"],second["path"])
        self.assertTrue(Path(first["path"]).exists())
        self.assertTrue(any(item["path"].endswith("another.log") for item in verify_archive(Path(second["path"]))["files"]))

    def test_archive_io_failure_preserves_originals(self):
        with mock.patch("task_archive.zipfile.ZipFile.write",side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.controller.cleanup()
        self.assertTrue((self.owned/"build/verification/run/result.log").exists())
        self.assertEqual(self.backend.deleted,0)
        self.assertFalse(list((self.store.root/"archives").rglob("*.zip")))

    def test_corrupt_archive_prevents_any_deletion(self):
        info = archive_task(self.store,self.value)
        Path(info["path"]).write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            self.controller.cleanup()
        self.assertTrue(self.owned.exists())
        self.assertEqual(self.backend.deleted,0)

    def test_new_local_commit_after_worktree_removal_is_preserved(self):
        self.store.mutate("owned",lambda value:{**value,"managed_identity":str(self.owned)})
        self.controller.cleanup()
        self.run_git("worktree","remove",str(self.owned))
        self.run_git("commit","--quiet","--allow-empty","-m","other work")
        new = self.run_git("rev-parse","HEAD").decode().strip()
        self.run_git("update-ref","refs/heads/dev-owned-v2",new)
        with self.assertRaises(ValueError):
            self.controller.finalize()
        self.assertEqual(self.run_git("rev-parse","dev-owned-v2").decode().strip(),new)

    def test_merge_crash_recovery_does_not_recommit_or_push(self):
        self.store.mutate("owned",lambda value:{**value,"delivery":{},"status":"MERGING"})
        with mock.patch.object(self.controller,"after_merge",return_value={"status":"WAITING_DEV_CI"}):
            self.assertEqual(self.controller.deliver()["status"],"WAITING_DEV_CI")
        self.assertEqual(self.backend.pushes,0)
        self.assertTrue(self.store.load("owned")["delivery"]["merged"])


class SharedCacheTests(RepositoryFixture):
    def test_reclaim_keeps_two_versions_active_references_and_unknown_directories(self):
        from tool_cache import prune_caches
        (self.root/"tools").mkdir()
        (self.root/"tools/toolchain.json").write_text("{}",encoding="utf-8")
        store = TaskStore(self.root)
        for index in range(1,5):
            key = str(index)*64
            path = store.root/"cache"/key
            path.mkdir(parents=True)
            (path/"cache.json").write_text(json.dumps({"repo_id":store.repo_id,"version_key":key,"last_used":str(index)}),encoding="utf-8")
        unknown = store.root/"cache/unregistered"
        unknown.mkdir()
        store.create({"task_id":"active-cache","worktree":str(self.root),"status":"ACTIVE","toolchain_key":"1"*64})
        self.assertEqual(prune_caches(self.root),["2"*64])
        self.assertTrue(unknown.exists())
        self.assertTrue((store.root/"cache"/("1"*64)).exists())


class EvaluationOutcomeTests(unittest.TestCase):
    def test_trace_keywords_without_fixture_assertions_are_not_outcome_success(self):
        trace={"status":"PASS"}
        self.assertEqual(skill_eval.assess_outcome(trace,{"sha":"a"},{"sha":"a"},[])["status"],"FAIL")

    def test_grounded_decision_requires_real_fixture_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/"evidence.json").write_text("{}",encoding="utf-8")
            event = {"item":{"type":"agent_message","text":json.dumps({"decision":"BLOCKED","references":["evidence.json"]})}}
            self.assertTrue(skill_eval.decision_assertion({},[event],"BLOCKED",root)["passed"])
            event["item"]["text"] = json.dumps({"decision":"READY","references":["missing.json"]})
            self.assertFalse(skill_eval.decision_assertion({},[event],"BLOCKED",root)["passed"])

    def test_shell_edits_without_file_change_events_are_detected(self):
        result=skill_eval.assess_outcome({"status":"PASS"},{"hash":"before"},{"hash":"after"},[{"passed":True}])
        self.assertEqual(result["status"],"FAIL")

    def test_success_requires_both_real_assertions_and_unchanged_sources(self):
        result=skill_eval.assess_outcome({"status":"PASS"},{"hash":"same"},{"hash":"same"},[{"passed":True}])
        self.assertEqual(result["status"],"PASS")


if __name__=="__main__":
    unittest.main()
