"""Metric observations preserve unknowns, provenance and real resource contradictions."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import shutil
import contextlib
import io
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from test_engineering import RepositoryFixture
from task_store import TaskStore
import engineering_metrics as metrics


NOW = "2026-11-16T12:00:00+00:00"


def task(name="fixture", merged=True, cleaned=True):
    return {"task_id":name,"status":"CLEANED" if cleaned else "ACTIVE","started_at":"2026-11-16T10:00:00Z",
            "delivered_at":"2026-11-16T11:00:00Z" if merged else None,
            "cleaned_at":"2026-11-16T11:10:00Z" if cleaned else None,
            "delivery":{"merged":merged},"verification_attempts":[],"human_interventions":None,"rework_count":None}


def attempt(identity,status,group="all",minute=0):
    return {"run_id":identity,"status":status,"group":group,
            "started_at":f"2026-11-16T10:{minute:02d}:00Z","completed_at":f"2026-11-16T10:{minute:02d}:05Z"}


def event(revision,second,status,action="observation"):
    return {"revision":revision,"at":f"2026-11-16T10:00:{second:02d}Z","action":action,"state":{"status":status}}


class ReducerTests(unittest.TestCase):
    def test_twenty_confirmed_deliveries_exclude_other_finished_records(self):
        rows=[task(str(index)) for index in range(19)]
        unfinished=task("unmerged",False,False);unfinished["completed_at"]=NOW
        value=metrics.summarize([*rows,unfinished],as_of=NOW)
        self.assertEqual(value["finished_tasks"],20)
        self.assertEqual(value["delivered_tasks"],19)
        self.assertFalse(value["baseline_ready"])
        self.assertTrue(metrics.summarize([*rows,task("twentieth")],as_of=NOW)["baseline_ready"])

    def test_full_first_pass_ignores_partial_blocked_and_dry_runs_and_orders_by_time(self):
        first=task("first");first["verification_attempts"]=[attempt("partial","FAIL","tools"),attempt("blocked","BLOCKED",minute=1),
            attempt("dry","NOT_RUN",minute=2),attempt("good","PASS",minute=3),attempt("good","PASS",minute=3)]
        second=task("second");second["verification_attempts"]=[attempt("later","PASS",minute=5),attempt("earlier","FAIL",minute=4)]
        missing=task("missing");missing["verification_attempts"]=[{"run_id":"unknown","group":"all","status":"PASS"}]
        original=copy.deepcopy([first,second,missing])
        result=metrics.summarize([first,second,missing],as_of=NOW)
        metric=result["full_verification_first_pass"]
        self.assertEqual((metric["samples"],metric["passes"],metric["failures"],metric["unknown_tasks"]),(2,1,1,1))
        self.assertEqual(metric["fraction"],0.5)
        self.assertEqual((metric["blocked_attempts"],metric["not_run_attempts"]),(1,1))
        self.assertEqual([first,second,missing],original)

    def test_conflicting_duplicate_attempt_is_unknown(self):
        row=task();row["verification_attempts"]=[attempt("same","PASS"),attempt("same","FAIL")]
        result=metrics.summarize([row],as_of=NOW)
        self.assertIsNone(result["full_verification_first_pass"]["fraction"])
        self.assertIsNone(result["per_task"][0]["stages_seconds"]["full_verification_execution"])

    def test_unknown_counters_are_not_zero_and_booleans_are_not_observations(self):
        rows=[task(str(index)) for index in range(3)]
        rows[0]["human_interventions"]=0;rows[1]["human_interventions"]=True
        result=metrics.summarize(rows,as_of=NOW)
        self.assertEqual(result["counters"]["human_interventions"],{"samples":1,"unknown":2,"known_total":0})
        self.assertIsNone(result["counters"]["rework_count"]["known_total"])

    def test_repeated_ci_observations_do_not_double_count_and_pending_intervals_are_unknown(self):
        row=task();events=[event(1,10,"WAITING_CI"),event(2,20,"WAITING_CI"),event(3,40,"MERGING")]
        events.append(copy.deepcopy(events[1]))
        result=metrics.summarize([row],{"fixture":events},as_of=NOW)
        self.assertEqual(result["per_task"][0]["stages_seconds"]["observed_pr_ci_wait"],30)
        pending=metrics.summarize([row],{"fixture":events[:2]},as_of=NOW)
        self.assertIsNone(pending["per_task"][0]["stages_seconds"]["observed_pr_ci_wait"])
        self.assertGreater(pending["per_task"][0]["open_pr_ci_wait_age_seconds"],0)

    def test_historical_failures_do_not_mark_cleaned_tasks_currently_failed(self):
        row=task();row.update(blocked_at=NOW,last_error="old failure",verification_failure={"status":"FAIL"})
        journal=[event(1,10,"NEEDS_ATTENTION","ci-failed"),event(2,20,"CLEANED","cleanup-confirmed")]
        queue={"fixture":{"state":"DONE","reason":"old network timeout"}}
        result=metrics.summarize([row],{"fixture":journal},queue,as_of=NOW)
        self.assertEqual(result["failures"]["current_tasks"],[])
        self.assertEqual(result["failures"]["historical_event_counts"],{"ci-failed":1})
        blocked=task("blocked",True,False);blocked["status"]="BLOCKED"
        value=metrics.summarize([blocked],as_of=NOW)
        self.assertEqual(value["delivered_tasks"],1)
        self.assertEqual(value["per_task"][0]["failure_stage"],"cleanup")

    def test_cleanup_checkpoints_cannot_override_observed_resources_or_remote_unknowns(self):
        row=task();row["cleanup"]={key:True for key in ("remote_deleted","worktree_removed","local_deleted","tracking_deleted")}
        observed={"fixture":{"applicable":True,"directory":True,"registration":False,"local_ref":False,"tracking_ref":False,"remote_ref":None}}
        result=metrics.summarize([row],observations=observed,as_of=NOW)
        self.assertEqual(result["resource_health"]["cleaned_but_resources_present"],["fixture"])
        self.assertEqual(result["resource_health"]["incomplete_observation_tasks"],["fixture"])
        self.assertIsNone(result["per_task"][0]["resources"]["remote_ref"])
        self.assertTrue(result["per_task"][0]["recorded_remote_deleted"])

    def test_nearest_rank_and_invalid_durations_keep_missing_samples(self):
        value=metrics.distribution([1,2,3,4,None,float("nan"),-1,True])
        self.assertEqual((value["samples"],value["missing"],value["p50_seconds"],value["p95_seconds"]),(4,4,2,4))
        self.assertIsNone(metrics.elapsed("2026-11-16T10:00:00",NOW))
        self.assertIsNone(metrics.elapsed(NOW,"2026-11-16T10:00:00Z"))
        self.assertEqual(metrics.elapsed(NOW,NOW),0)
        self.assertIsNone(metrics.distribution([])["p50_seconds"])

    def test_monthly_items_use_utc_boundaries_and_keep_current_pending_items(self):
        row=task();row["started_at"]="2026-10-31T20:00:00-08:00";row["delivered_at"]=None
        row["verification_attempts"]=[attempt("failed","FAIL")]
        result=metrics.summarize([row],as_of=NOW)
        items=metrics.improvement_items(result,"2026-11")
        self.assertIn("full-verification-failures",{item["code"] for item in items["items"]})
        self.assertIn("insufficient-delivery-baseline",{item["code"] for item in items["items"]})
        self.assertIn("explicit-counter-coverage",{item["code"] for item in items["items"]})
        with self.assertRaises(ValueError):metrics.improvement_items(result,"2026-1")

    def test_stored_worker_pid_is_not_liveness_and_due_queue_can_trigger_inspection(self):
        row=task("pending",True,False)
        queue={"pending":{"state":"WAITING","stage":"CLEANUP","queued_at":"2026-11-16T10:00:00Z","next_attempt_at":"2026-11-16T10:01:00Z","attempts":2}}
        worker={"enabled":True,"pid":1234,"completed_at":"2026-11-16T10:00:00Z","lease_held":False}
        result=metrics.summarize([row],queues=queue,worker=worker,as_of=NOW)
        self.assertNotIn("running",result["worker"])
        self.assertIn("due-queue-worker-observation",{item["code"] for item in metrics.improvement_items(result)["items"]})
        self.assertIsNone(result["per_task"][0]["stages_seconds"]["actual_ci_execution"])


class CollectorTests(RepositoryFixture):
    def files(self):
        return {path.relative_to(self.root).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.root.rglob("*") if path.is_file()}

    def seed(self,status="CLEANED"):
        store=TaskStore(self.root)
        value=store.create({"task_id":"observed","worktree":str(self.root),"branch":"dev-fixture-test","host_platform":os.name,
            "status":status,"started_at":"2026-11-16T10:00:00Z","delivery":{"merged":True,"merged_at":"2026-11-16T11:00:00Z"},
            "cleanup":{"remote_deleted":True},"verification_attempts":[]})
        return store,value

    def test_uninitialized_collection_creates_no_runtime_identity(self):
        before=self.files()
        result=metrics.collect(self.root,as_of=NOW)
        self.assertFalse(result["initialized"])
        self.assertEqual(self.files(),before)
        self.assertFalse((self.root/".git/tenon").exists())

    def test_snapshot_recovery_inbox_and_partial_tail_are_read_without_mutation(self):
        store,value=self.seed()
        newer={**value,"revision":value["revision"]+1,"status":"CLEANED"}
        journal=store.path("observed").with_suffix(".jsonl")
        with journal.open("a",encoding="utf-8") as stream:
            stream.write(json.dumps({"revision":newer["revision"],"at":NOW,"action":"cleanup-confirmed","state":newer})+"\n")
            stream.write('{"revision":999')
        store.enqueue_attempt("observed",attempt("external","PASS"))
        before=self.files()
        result=metrics.collect(self.root,as_of=NOW)
        self.assertEqual(result["full_verification_first_pass"]["samples"],1)
        self.assertIn("partial-journal-tail",{item["issue"] for item in result["diagnostics"]})
        self.assertEqual(result["resource_health"]["cleaned_but_resources_present"],["observed"])
        self.assertEqual(self.files(),before)
        self.assertTrue(list((store.root/"inbox/observed").glob("*.json")))

    def test_contradictory_same_run_inbox_is_unknown_and_preserves_all_source_bytes(self):
        store,_=self.seed()
        store.mutate("observed",lambda row:{**row,"verification_attempts":[attempt("same-run","PASS")]},"snapshot-attempt-fixture")
        store.enqueue_attempt("observed",attempt("same-run","FAIL"))
        before=self.files()
        result=metrics.collect(self.root,as_of=NOW)
        first=result["full_verification_first_pass"]
        self.assertEqual(first["samples"],0)
        self.assertEqual(first["unknown_tasks"],1)
        self.assertIsNone(first["fraction"])
        self.assertIsNone(result["per_task"][0]["first_full_verification"]["first"])
        self.assertTrue(result["per_task"][0]["first_full_verification"]["ordering_unknown"])
        self.assertEqual(self.files(),before)
        self.assertEqual(json.loads(store.path("observed").read_text())["verification_attempts"][0]["status"],"PASS")
        inbox=list((store.root/"inbox/observed").glob("*.json"))
        self.assertEqual(len(inbox),1)
        self.assertEqual(json.loads(inbox[0].read_text())["attempt"]["status"],"FAIL")

    def test_remote_observation_is_explicit_only_and_does_not_fetch(self):
        self.seed()
        native=metrics._git;calls=[]
        def git(repo,*args):
            calls.append(args)
            if "ls-remote" in args:return b"a"*40+b"\trefs/heads/dev-fixture-test\n"
            self.assertNotIn("fetch",args);self.assertNotIn("push",args)
            return native(repo,*args)
        with mock.patch.object(metrics,"_git",side_effect=git):
            without=metrics.collect(self.root,as_of=NOW)
            self.assertIsNone(without["per_task"][0]["resources"]["remote_ref"])
            self.assertFalse(any("ls-remote" in call for call in calls))
            with_remote=metrics.collect(self.root,as_of=NOW,probe_remote=True)
        self.assertTrue(with_remote["per_task"][0]["resources"]["remote_ref"])
        self.assertEqual(sum("ls-remote" in call for call in calls),1)

    def test_older_git_worktree_interface_preserves_independent_ref_observations(self):
        self.seed()
        native=metrics._git
        def git(repo,*args):
            if "-z" in args:raise subprocess.CalledProcessError(129,["git",*args])
            return native(repo,*args)
        with mock.patch.object(metrics,"_git",side_effect=git):
            result=metrics.collect(self.root,as_of=NOW)
        resources=result["per_task"][0]["resources"]
        self.assertTrue(resources["local_ref"])
        self.assertTrue(resources["registration"])

    def test_foreign_host_paths_are_unknown_not_missing(self):
        store,_=self.seed()
        store.mutate("observed",lambda row:{**row,"host_platform":"posix" if os.name=="nt" else "nt"},"foreign-host-fixture")
        result=metrics.collect(self.root,as_of=NOW)
        resources=result["per_task"][0]["resources"]
        self.assertIsNone(resources["directory"])
        self.assertIsNone(resources["registration"])
        self.assertTrue(resources["local_ref"])

    def test_bad_repository_identity_remains_unknown_without_repair(self):
        store,_=self.seed()
        path=store.root/"repository.json";path.write_text('{"schema_version":999}',encoding="utf-8")
        before=self.files()
        result=metrics.collect(self.root,as_of=NOW)
        self.assertFalse(result["initialized"])
        self.assertEqual(self.files(),before)


class WiringTests(RepositoryFixture):
    def seed(self,name,merged=True,**extra):
        store=TaskStore(self.root)
        return store.create({"task_id":name,"status":"CLEANED" if merged else "READY","branch":"dev-"+name,
            "worktree":str(self.root/"build/retired"/name),"host_platform":os.name,
            "started_at":"2026-11-16T10:00:00Z","delivered_at":"2026-11-16T11:00:00Z" if merged else None,
            "cleaned_at":"2026-11-16T11:01:00Z" if merged else None,"delivery":{"merged":merged},**extra})

    def records(self):
        base=self.root/".git/tenon"
        return {str(path.relative_to(base)):path.read_bytes() for folder in ("tasks","queue") for path in (base/folder).glob("*") if path.is_file()}

    def test_legacy_metrics_threshold_and_unknown_counter_semantics(self):
        import task as task_module
        for index in range(19):self.seed("sample-"+str(index))
        self.seed("finished-only",False,completed_at=NOW,human_interventions=True,rework_count=-1)
        value=task_module.metrics(self.root/"build/tasks")
        self.assertEqual(value["finished_tasks"],20)
        self.assertEqual(value["delivered_tasks"],19)
        self.assertFalse(value["baseline_ready"])
        self.assertIsNone(value["human_interventions"])
        self.assertIsNone(value["rework_count"])
        self.assertEqual(value["intervention_samples"],0)
        self.assertEqual(value["engineering_metrics_v1"]["schema_version"],1)
        self.seed("twentieth",human_interventions=0,rework_count=0)
        value=task_module.metrics(self.root/"build/tasks")
        self.assertTrue(value["baseline_ready"])
        self.assertEqual(value["human_interventions"],0)
        self.assertEqual(value["intervention_samples"],1)

    def test_real_stable_cli_metrics_runs_from_old_main_shared_metadata_without_repairs(self):
        import tenon
        from test_entrypoint import Backend
        from test_engineering import ROOT
        initial=self.run_git("rev-parse","HEAD").decode().strip()
        shutil.copytree(ROOT/"scripts",self.root/"scripts",ignore=shutil.ignore_patterns("__pycache__"))
        self.run_git("add","scripts");self.run_git("commit","--quiet","-m","metrics runtime fixture")
        self.run_git("update-ref","refs/remotes/origin/dev","HEAD")
        self.run_git("checkout","--quiet","-b","main",initial)
        self.assertFalse((self.root/"scripts").exists())
        installed=tenon.install(self.root,Backend())
        self.seed("visible-shared-task")
        before=self.records();refs=self.run_git("for-each-ref")
        result=subprocess.run([sys.executable,installed["entry"],"metrics","--month","2026-11"],cwd=self.root,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors="replace"))
        value=json.loads(result.stdout)
        self.assertEqual(value["status"],"OBSERVED")
        self.assertEqual(value["tasks"],1)
        self.assertEqual(value["improvement_items"]["month"],"2026-11")
        self.assertFalse(value["remote_probe_requested"])
        self.assertEqual(self.records(),before)
        self.assertEqual(self.run_git("for-each-ref"),refs)
        self.assertEqual(self.run_git("branch","--show-current").strip(),b"main")

    def test_metrics_diagnostics_return_failure_and_do_not_claim_signoff(self):
        import tenon
        report=metrics.summarize([],as_of=NOW,diagnostics=[{"issue":"incomplete-observation"}])
        with mock.patch.object(metrics,"collect",return_value=report),contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(tenon.main(["--repo",str(self.root),"metrics"]),2)
        value=json.loads(output.getvalue())
        self.assertEqual(value["status"],"BLOCKED")
        self.assertIn("no hardware",value["qualification"])

    def test_reconcile_blocked_item_has_non_success_status_and_waiting_is_pending(self):
        import tenon,task_queue
        from engineering_common import write_json
        store=TaskStore(self.root)
        path=store.root/"queue/fixture.json"
        write_json(path,{"schema_version":1,"repo_id":store.repo_id,"task_id":"fixture","state":"BLOCKED"})
        before=path.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(tenon.main(["--repo",str(self.root),"reconcile","--all","--once"]),2)
        self.assertEqual(json.loads(output.getvalue())["status"],"BLOCKED")
        self.assertEqual(path.read_bytes(),before)
        write_json(path,{"schema_version":1,"repo_id":store.repo_id,"task_id":"fixture","state":"WAITING_APP"})
        pending=task_queue.reconcile(self.root)
        self.assertEqual(pending["status"],"WAITING")
        self.assertNotEqual(pending["status"],"CLEANED")

    def test_windows_gh_timeout_and_explicit_rate_limit_are_transient_but_policy_errors_are_not(self):
        import task_queue
        message='Get "https://api.github.com/repos/OpenTenon/tenon/pulls/13": dial tcp 198.18.0.17:443: connectex: A connection attempt failed because the connected party did not properly respond after a period of time, or established connection failed because connected host has failed to respond.'
        for error in (RuntimeError(message), subprocess.CalledProcessError(1,["gh","api"],stderr=message.encode()),
                      RuntimeError("HTTP 429: Too Many Requests"),RuntimeError("API rate limit exceeded"),RuntimeError("secondary rate limit")):
            with self.subTest(error=error):self.assertEqual(task_queue.classify(error),"TRANSIENT")
        for error in (ValueError("Evidence timeout fixture changed"),ValueError("Ownership changed"),
                      RuntimeError("HTTP 401: Bad credentials"),RuntimeError("Resource not accessible by integration (HTTP 403)"),
                      subprocess.CalledProcessError(128,["git","-c","core.sshCommand=ssh -o ConnectTimeout=10","push"],stderr=b"Permission denied (publickey)")):
            with self.subTest(error=error):self.assertEqual(task_queue.classify(error),"POLICY")

    def test_github_root_and_normal_endpoints_use_canonical_argv_and_keep_diagnostics(self):
        import github_rules
        with mock.patch.object(subprocess,"check_output",return_value=b"{}") as query:
            github_rules.api("OpenTenon/tenon","")
            self.assertEqual(query.call_args.args[0][2],"repos/OpenTenon/tenon")
            self.assertNotIn("--method",query.call_args.args[0])
            github_rules.api("OpenTenon/tenon","branches/dev/protection")
            self.assertEqual(query.call_args.args[0][2],"repos/OpenTenon/tenon/branches/dev/protection")
        failure=subprocess.CalledProcessError(1,["gh","api"],stderr=b"HTTP 503: service unavailable fixture")
        with mock.patch.object(subprocess,"check_output",side_effect=failure):
            with self.assertRaisesRegex(RuntimeError,"HTTP 503"):
                github_rules.api("OpenTenon/tenon","")

    def test_monthly_metrics_suggestions_preserve_failure_status_records_and_existing_work(self):
        import maintenance
        self.seed("historical-task")
        store=TaskStore(self.root)
        directory=store.root/"maintenance/fixture"
        directory.mkdir(parents=True)
        prepared={"status":"FAIL","checks":[{"id":"existing-audit-failure","status":"FAIL","reason":"preserve me"}],
            "report":str(directory/"summary.json"),"work_items":str(directory/"work-items.json")}
        before=self.records();source=(self.root/"input.txt").read_bytes()
        native=metrics.collect
        with mock.patch.object(maintenance,"audit",return_value=prepared),mock.patch.object(metrics,"collect",wraps=native) as collect:
            result=maintenance.run_due(self.root,force=True)
        self.assertEqual(result["status"],"FAIL")
        self.assertFalse(collect.call_args.kwargs["probe_remote"])
        self.assertEqual(self.records(),before)
        self.assertEqual((self.root/"input.txt").read_bytes(),source)
        self.assertTrue(Path(result["metrics_report"]).is_file())
        self.assertTrue(Path(result["improvement_items_report"]).is_file())
        items=json.loads(Path(result["work_items"]).read_text())
        self.assertIn("existing-audit-failure",{item["check"] for item in items})
        self.assertIn("metrics:insufficient-delivery-baseline",{item["check"] for item in items})
        state=json.loads((store.root/"maintenance-state.json").read_text())
        self.assertEqual(state["last_status"],"FAIL")


if __name__=="__main__":
    unittest.main()
