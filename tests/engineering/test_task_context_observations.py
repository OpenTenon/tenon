"""Attributed task observations, read-only context and recoverable queue outbox contracts."""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
import sys
import unittest
from datetime import datetime,timedelta,timezone
from unittest import mock
import test_engineering as fixtures
import test_lifecycle_v2 as lifecycle
import task
from task_store import TaskStore
import task_context as ctx
import task_observations as obs
import task_queue as queue
import engineering_metrics as metrics
from engineering_common import write_json


def files(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


class ObservationTests(fixtures.RepositoryFixture):
    def start(self):
        task.start("fixture","Observation fixture",["input.txt"],["make verify"],[],self.root)
        return TaskStore(self.root)

    def test_context_never_initializes_repairs_locks_or_contacts_origin(self):
        base=self.root/".git/tenon"
        with mock.patch.object(TaskStore,"__init__",side_effect=AssertionError("No construction")):
            with self.assertRaises(ValueError):
                ctx.context(self.root,"missing")
        self.assertFalse(base.exists())
        store=self.start()
        before=files(store.root)
        with mock.patch.object(TaskStore,"__init__",side_effect=AssertionError("No construction")):
            result=ctx.context(self.root,"fixture")
        self.assertEqual(result["status"],"OBSERVED")
        self.assertEqual(result["source"]["mode"],"live")
        self.assertIsNone(result["manual_observations"]["totals"]["human_interventions"])
        self.assertEqual(files(store.root),before)
        self.assertIsInstance(result["next_argv"],list)

    def test_note_same_payload_is_strict_noop_and_conflict_is_atomic(self):
        store=self.start()
        obs.note(self.root,"fixture","human-1","intervention","Observed manual unblock")
        before=files(store.root/"tasks")
        second=obs.note(self.root,"fixture","human-1","intervention","Observed manual unblock")
        self.assertEqual(second["status"],"UNCHANGED")
        self.assertEqual(files(store.root/"tasks"),before)
        with self.assertRaises(ValueError):
            obs.note(self.root,"fixture","human-1","rework","Different payload")
        self.assertEqual(files(store.root/"tasks"),before)
        self.assertEqual(len(store.load("fixture")["observations_v1"]["manual"]["events"]),1)

    def test_noop_preserves_pending_inbox_and_real_change_folds_conflicting_history(self):
        store=self.start()
        obs.note(self.root,"fixture","r1","rework","First observed correction")
        first={"run_id":"same","group":"all","status":"PASS","started_at":"2026-11-17T10:00:00+00:00"}
        store.mutate("fixture",lambda row:{**row,"verification_attempts":[first]},"history")
        store.enqueue_attempt("fixture",{**first,"status":"FAIL"})
        inbox=store.inbox("fixture")
        before=files(store.root/"tasks")
        obs.note(self.root,"fixture","r1","rework","First observed correction")
        self.assertEqual(files(store.root/"tasks"),before)
        self.assertTrue(all(p.exists() for p in inbox))
        self.assertEqual(len(store.load("fixture")["verification_attempts"]),2)
        obs.note(self.root,"fixture","r2","rework","Second observed correction")
        self.assertFalse(any(p.exists() for p in inbox))
        row=store.load("fixture")
        self.assertEqual(len(row["verification_attempts"]),2)
        self.assertIsNone(metrics._full_attempts(row)["first"])

    def test_default_mutate_retains_revision_semantics_but_optin_unchanged_does_not(self):
        store=self.start()
        before=store.load("fixture")["revision"]
        row=store.mutate("fixture",lambda value:value)
        self.assertEqual(row["revision"],before+1)
        row=store.mutate("fixture",lambda value:value,skip_unchanged=True)
        self.assertEqual(row["revision"],before+1)

    def test_explicit_attestation_knows_zero_and_new_note_invalidates_it(self):
        store=self.start()
        initial=ctx.context(self.root,"fixture")
        self.assertIsNone(obs.qualified_counter(store.load("fixture"),"human_interventions"))
        result=obs.attest(self.root,"fixture","complete-1",0,0,initial["attestation_evidence_digest"],"Explicit exhaustive observation","task-start")
        self.assertTrue(result["observations"]["complete"])
        self.assertEqual(obs.qualified_counter(store.load("fixture"),"human_interventions"),0)
        before=files(store.root/"tasks")
        obs.attest(self.root,"fixture","complete-1",0,0,initial["attestation_evidence_digest"],"Explicit exhaustive observation","task-start")
        self.assertEqual(files(store.root/"tasks"),before)
        obs.note(self.root,"fixture","fix-1","rework","Observed correction, not a poll")
        self.assertIsNone(obs.qualified_counter(store.load("fixture"),"human_interventions"))
        current=ctx.context(self.root,"fixture")
        with self.assertRaises(ValueError):
            obs.attest(self.root,"fixture","bad-total",0,0,current["attestation_evidence_digest"],"Contradiction","task-start")
        result=obs.attest(self.root,"fixture","complete-2",0,1,current["attestation_evidence_digest"],"Explicit totals after correction","task-start")
        self.assertEqual(result["observations"]["totals"],{"human_interventions":0,"rework_count":1})

    def test_attestation_rejects_unknown_or_stale_evidence_and_noninteger_totals(self):
        self.start()
        current=ctx.context(self.root,"fixture")
        for values in [(None,0),(True,0),(-1,0),(0,1.0)]:
            with self.subTest(values=values),self.assertRaises(ValueError):
                obs.attest(self.root,"fixture","bad",*values,current["attestation_evidence_digest"],"Explicit reason","task-start")
        with self.assertRaises(ValueError):
            obs.attest(self.root,"fixture","bad",0,0,"0"*64,"Stale proof","task-start")

    def test_legacy_unknown_cohort_is_not_migrated_or_filled_with_zero(self):
        rows=[{"human_interventions":None,"rework_count":None} for _ in range(13)]
        before=copy.deepcopy(rows)
        self.assertEqual(metrics._counter(rows,"human_interventions"),{"samples":0,"unknown":13,"known_total":None})
        self.assertEqual(rows,before)
        rows[0]["human_interventions"]=2
        self.assertEqual(metrics._counter(rows,"human_interventions")["known_total"],2)

    def test_threads_and_separate_processes_record_one_attributed_event(self):
        store=self.start()
        barrier=threading.Barrier(6)
        def write(_):
            barrier.wait()
            return obs.note(self.root,"fixture","thread-one","rework","One observed event")["status"]
        with ThreadPoolExecutor(max_workers=6) as pool:
            values=list(pool.map(write,range(6)))
        self.assertEqual(values.count("RECORDED"),1)
        script="import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from task_observations import note;note(Path(sys.argv[2]),'fixture','process-one','intervention','One explicit human action')"
        processes=[subprocess.Popen([sys.executable,"-c",script,str(fixtures.ROOT/"scripts"),str(self.root)],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(4)]
        for process in processes:
            out,error=process.communicate(timeout=30)
            self.assertEqual(process.returncode,0,error.decode(errors="replace"))
        events=store.load("fixture")["observations_v1"]["manual"]["events"]
        self.assertEqual(set(events),{"thread-one","process-one"})

    def test_tracking_scope_never_becomes_whole_task_zero_and_legacy_totals_are_not_added(self):
        store=self.start()
        store.mutate("fixture",lambda row:{**row,"human_interventions":2,"rework_count":None})
        obs.note(self.root,"fixture","new-human","intervention","An explicit observed action")
        current=ctx.context(self.root,"fixture")
        with self.assertRaises(ValueError):
            obs.attest(self.root,"fixture","reduce",1,0,current["attestation_evidence_digest"],"Cannot lower old whole-task declaration","task-start")
        result=obs.attest(self.root,"fixture","tracking",1,0,current["attestation_evidence_digest"],"Only this tracking interval","tracking-start")
        self.assertTrue(result["observations"]["scope_complete"])
        self.assertFalse(result["observations"]["complete"])
        self.assertIsNone(obs.qualified_counter(store.load("fixture"),"human_interventions"))
        current=ctx.context(self.root,"fixture")
        result=obs.attest(self.root,"fixture","whole",2,0,current["attestation_evidence_digest"],"Explicit reconciled total, not 2 plus 1","task-start")
        self.assertEqual(result["observations"]["totals"]["human_interventions"],2)

    def test_legacy_handoff_preserves_concurrent_note_and_explicit_integer_none_semantics(self):
        store=self.start()
        from engineering import required_ids
        from engineering_common import new_summary,execute,finish_summary
        value=new_summary(self.root,"verification")
        folder=self.root/"build/verification"/value["run_id"]
        for name in required_ids("all")-{"repository-unchanged"}:
            value["checks"].append(execute([sys.executable,"-c","print('fixture')"],self.root,folder/(name+".log"),check_id=name))
        report=folder/"summary.json";finish_summary(value,self.root,report)
        validate=task.validate_evidence
        def concurrent(path,owner):
            result=validate(path,owner)
            obs.note(self.root,"fixture","late-note","rework","Concurrent explicit correction")
            return result
        with mock.patch.object(task,"validate_evidence",side_effect=concurrent):
            result=task.handoff("fixture",[report],[],2,None,self.root)
        self.assertEqual(result["status"],"READY")
        self.assertEqual(result["human_interventions"],2)
        self.assertIsNone(result["rework_count"])
        self.assertIn("late-note",result["observations_v1"]["manual"]["events"])
        self.assertEqual(obs.qualified_counter(result,"human_interventions"),2)
        self.assertIsNone(obs.qualified_counter(result,"rework_count"))

    def test_readonly_context_retains_partial_tail_and_inbox_without_repair(self):
        store=self.start()
        journal=store.path("fixture").with_suffix(".jsonl")
        with journal.open("ab") as output: output.write(b'{"partial":')
        store.enqueue_attempt("fixture",{"run_id":"pending","group":"all","status":"PASS"})
        before=files(store.root)
        result=ctx.context(self.root,"fixture")
        self.assertEqual(result["status"],"BLOCKED")
        self.assertTrue(any(row.get("issue")=="partial-journal-tail" for row in result["diagnostics"]))
        self.assertEqual(files(store.root),before)


class ClosedObservationTests(lifecycle.OwnedWorktreeFixture):
    def test_note_and_attest_after_cleanup_never_recreate_resources_or_writer(self):
        self.assertEqual(self.controller.cleanup()["status"],"CLEANED")
        row=self.store.load("owned")
        archive=row["archive"]
        lease=copy.deepcopy(row.get("execution_lease"))
        obs.note(self.root,"owned","after-clean","rework","Explicit post-cleanup review")
        current=ctx.context(self.root,"owned")
        self.assertEqual(current["source"]["mode"],"historical")
        obs.attest(self.root,"owned","complete-clean",0,1,current["attestation_evidence_digest"],"Explicit complete coverage after cleanup","task-start")
        self.assertFalse(os.path.lexists(self.owned))
        self.assertEqual(self.run_git("for-each-ref","--format=%(refname)","refs/heads/"+row["branch"]),b"")
        final=self.store.load("owned")
        self.assertEqual(final["status"],"CLEANED")
        self.assertEqual(final.get("execution_lease"),lease)
        self.assertEqual(final["archive"],archive)


class MachineObservationTests(lifecycle.OwnedWorktreeFixture):
    def enqueue(self):
        return queue.submit(self.root,"owned")

    def waiting(self):
        value=mock.Mock()
        value.deliver.return_value={"status":"WAITING_DEV_CI","next_poll_seconds":30}
        return value

    def test_ci_polls_are_waits_not_fault_retries_even_with_stale_error_category(self):
        self.enqueue()
        now=datetime.now(timezone.utc)
        factory=self.waiting()
        queue.reconcile(self.root,True,lambda *_:factory,now)
        path=queue.queue_path(self.store,"owned")
        entry=queue.read_queue(self.store,"owned")
        entry.update(error_category="TRANSIENT",reason="Old timeout")
        write_json(path,entry)
        queue.reconcile(self.root,True,lambda *_:factory,now+timedelta(seconds=31))
        summary=obs.machine_summary(self.store.load("owned"),queue.read_queue(self.store,"owned"))
        self.assertEqual(summary["observed_controller_starts"],2)
        self.assertEqual(summary["observed_controller_results"],2)
        self.assertEqual(summary["observed_ci_waits"],2)
        self.assertEqual(summary["observed_transient_retries"],0)
        self.assertEqual(summary["observed_resource_retries"],0)
        self.assertIsNone(summary["whole_task_fault_retry_total"])
        self.assertIsNone(obs.qualified_counter(self.store.load("owned"),"human_interventions"))

    def test_real_transient_failure_is_separate_from_normal_wait(self):
        self.enqueue()
        now=datetime.now(timezone.utc)
        failure=mock.Mock()
        failure.deliver.side_effect=TimeoutError("Transport timeout")
        queue.reconcile(self.root,True,lambda *_:failure,now)
        entry=queue.read_queue(self.store,"owned")
        next_time=datetime.fromisoformat(entry["next_attempt_at"])+timedelta(seconds=1)
        queue.reconcile(self.root,True,lambda *_:self.waiting(),next_time)
        summary=obs.machine_summary(self.store.load("owned"),queue.read_queue(self.store,"owned"))
        self.assertEqual(summary["observed_transient_retries"],1)
        self.assertEqual(summary["observed_ci_waits"],1)

    def test_crash_after_durable_start_reuses_attempt_without_duplicate_event(self):
        self.enqueue()
        now=datetime.now(timezone.utc)
        failure=mock.Mock()
        failure.deliver.side_effect=SystemExit("simulated process exit")
        with self.assertRaises(SystemExit):
            queue.reconcile(self.root,True,lambda *_:failure,now)
        before=queue.read_queue(self.store,"owned")
        self.assertEqual(before["attempts"],0)
        identity=before["active_attempt"]
        queue.reconcile(self.root,True,lambda *_:self.waiting(),now+timedelta(seconds=1))
        after=queue.read_queue(self.store,"owned")
        self.assertNotIn("active_attempt",after)
        summary=obs.machine_summary(self.store.load("owned"),after)
        self.assertEqual((summary["observed_controller_starts"],summary["observed_controller_results"]),(1,1))
        event=next(row["payload"] for row in obs.machine_events(self.store.load("owned"),after)[0].values() if row["payload"]["operation"]=="controller-result")
        self.assertEqual(event["ordinal"],identity["ordinal"])

    def test_crash_after_result_queue_write_mirrors_once_without_executing_again(self):
        self.enqueue()
        now=datetime.now(timezone.utc)
        original=obs.mirror_machine
        def crash(store,task_id,entry):
            if any(event["payload"]["operation"]=="controller-result" for event in entry.get("observation_outbox",[])):
                raise SystemExit("simulated result delivery crash")
            return original(store,task_id,entry)
        with mock.patch.object(obs,"mirror_machine",side_effect=crash):
            with self.assertRaises(SystemExit):
                queue.reconcile(self.root,True,lambda *_:self.waiting(),now)
        entry=queue.read_queue(self.store,"owned")
        self.assertEqual(entry["attempts"],1)
        self.assertTrue(entry["observation_outbox"])
        body=mock.Mock(side_effect=AssertionError("Closed attempt cannot execute before next due"))
        queue.reconcile(self.root,True,body,now+timedelta(seconds=1))
        body.assert_not_called()
        entry=queue.read_queue(self.store,"owned")
        self.assertFalse(entry["observation_outbox"])
        summary=obs.machine_summary(self.store.load("owned"),entry)
        self.assertEqual((summary["observed_controller_starts"],summary["observed_controller_results"]),(1,1))

    def test_readonly_does_not_drain_pending_machine_outbox(self):
        self.enqueue()
        entry=queue.read_queue(self.store,"owned")
        task_record=self.store.load("owned")
        active={"owner_id":entry["owner_id"],"generation":entry["generation"],"input_candidate":entry["candidate_sha"],"ordinal":1,"started_at":"2026-11-17T12:00:00+00:00"}
        entry["observation_outbox"]=[queue._event(active,"controller-start",entry)]
        write_json(queue.queue_path(self.store,"owned"),entry)
        before=files(self.store.root)
        merged=ctx.context(self.root,"owned")
        self.assertEqual(merged["machine_observations"]["observed_controller_starts"],1)
        queue.reconcile(self.root,False,lambda *_:self.waiting())
        self.assertEqual(files(self.store.root),before)

    def test_crash_before_start_mirror_keeps_outbox_and_reuses_identity(self):
        self.enqueue()
        now=datetime.now(timezone.utc)
        with mock.patch.object(obs,"mirror_machine",side_effect=SystemExit("crash before mirror")):
            with self.assertRaises(SystemExit):
                queue.reconcile(self.root,True,lambda *_:self.waiting(),now)
        entry=queue.read_queue(self.store,"owned")
        self.assertTrue(entry["observation_outbox"])
        identity=entry["active_attempt"]
        before=files(self.store.root)
        self.assertEqual(ctx.context(self.root,"owned")["machine_observations"]["observed_controller_starts"],1)
        self.assertEqual(files(self.store.root),before)
        queue.reconcile(self.root,True,lambda *_:self.waiting(),now+timedelta(seconds=1))
        summary=obs.machine_summary(self.store.load("owned"),queue.read_queue(self.store,"owned"))
        self.assertEqual((summary["observed_controller_starts"],summary["observed_controller_results"]),(1,1))
        self.assertEqual(identity["ordinal"],1)

    def test_resource_retry_is_typed_and_an_open_attempt_has_unknown_retry_total(self):
        self.enqueue()
        failure=mock.Mock()
        error=OSError("sharing violation");error.winerror=32
        failure.deliver.side_effect=error
        now=datetime.now(timezone.utc)
        queue.reconcile(self.root,True,lambda *_:failure,now)
        row=self.store.load("owned")
        summary=obs.machine_summary(row,queue.read_queue(self.store,"owned"))
        self.assertEqual(summary["observed_resource_retries"],1)
        self.assertEqual(summary["observed_transient_retries"],0)
        # A new durable start without its result is an unfinished observation.
        entry=queue.read_queue(self.store,"owned")
        active={"owner_id":entry["owner_id"],"generation":entry["generation"],"input_candidate":entry["candidate_sha"],"ordinal":2,"started_at":"2026-11-17T12:00:00+00:00"}
        event=queue._event(active,"controller-start",entry)
        obs.mirror_machine(self.store,"owned",{**entry,"observation_outbox":[event]})
        summary=obs.machine_summary(self.store.load("owned"),entry)
        self.assertEqual(summary["unfinished_observed_attempts"],1)
        self.assertIsNone(summary["covered_scope_fault_retries"])

    def test_interrupted_attempt_accepts_only_real_durable_base_merge_derivation(self):
        self.enqueue()
        now=datetime.now(timezone.utc)
        stop=mock.Mock();stop.deliver.side_effect=SystemExit("interrupted")
        with self.assertRaises(SystemExit):
            queue.reconcile(self.root,True,lambda *_:stop,now)
        entry=queue.read_queue(self.store,"owned")
        old=entry["candidate_sha"]
        (self.root/"advance.txt").write_text("new dev input",encoding="utf-8")
        self.run_git("add","advance.txt");self.run_git("commit","--quiet","-m","advance dev")
        dev=self.run_git("rev-parse","HEAD").decode().strip()
        proof={"from_head":old,"dev_sha":dev,"token":"observation-base-merge"}
        subprocess.run(["git","-C",str(self.owned),"merge","--no-ff","-m","Merge current dev\n\nTenon-Base-Merge: "+proof["token"],dev],check=True,capture_output=True)
        new=subprocess.check_output(["git","-C",str(self.owned),"rev-parse","HEAD"]).decode().strip()
        current={**self.store.load("owned"),"base_merge_intent":proof}
        entry["candidate_sha"]=new
        recovered=queue._begin_attempt(self.store,current,queue.queue_path(self.store,"owned"),entry)
        self.assertEqual(recovered["input_candidate"],old)
        self.assertEqual(recovered["ordinal"],1)
        self.assertNotEqual(old,new)

    def test_transient_first_start_mirror_failure_does_not_consume_unexecuted_ordinal(self):
        self.enqueue()
        now=datetime.now(timezone.utc)
        original=obs.mirror_machine
        calls=[0]
        def first_timeout(store,task_id,entry):
            calls[0]+=1
            if calls[0]==1:
                raise TimeoutError("First observation mirror is busy")
            return original(store,task_id,entry)
        factory=self.waiting()
        with mock.patch.object(obs,"mirror_machine",side_effect=first_timeout):
            first=queue.reconcile(self.root,True,lambda *_:factory,now)
        self.assertEqual(first["tasks"][0]["state"],"WAITING")
        factory.deliver.assert_not_called()
        pending=queue.read_queue(self.store,"owned")
        self.assertEqual(pending["attempts"],0)
        self.assertEqual(pending["active_attempt"]["ordinal"],1)
        retry=datetime.fromisoformat(pending["next_attempt_at"])+timedelta(seconds=1)
        second=queue.reconcile(self.root,True,lambda *_:factory,retry)
        self.assertEqual(second["tasks"][0]["state"],"WAITING")
        factory.deliver.assert_called_once()
        summary=obs.machine_summary(self.store.load("owned"),queue.read_queue(self.store,"owned"),repo=self.root)
        self.assertEqual((summary["observed_controller_starts"],summary["observed_controller_results"]),(1,1))
        self.assertEqual(summary["observed_ci_waits"],1)

    def test_readonly_outbox_uses_same_event_generation_candidate_qualification(self):
        self.enqueue()
        now=datetime.now(timezone.utc)
        queue.reconcile(self.root,True,lambda *_:self.waiting(),now)
        path=queue.queue_path(self.store,"owned")
        valid=queue.read_queue(self.store,"owned")
        for mode in ("candidate","generation","event-id"):
            entry=copy.deepcopy(valid)
            active={"owner_id":entry["owner_id"],"generation":entry["generation"],
                "input_candidate":entry["candidate_sha"],"ordinal":1,"started_at":"2026-11-17T12:00:00+00:00"}
            if mode=="candidate": active["input_candidate"]="f"*40
            if mode=="generation": active["generation"]+=1
            events=[queue._event(active,"controller-start",entry),
                    queue._event(active,"controller-result",entry,status="WAITING",category="TRANSIENT")]
            if mode=="event-id": events[0]["id"]="q-"+"0"*64;events[1]["id"]="q-"+"1"*64
            entry["observation_outbox"]=events
            write_json(path,entry)
            before=files(self.store.root)
            with mock.patch.object(TaskStore,"__init__",side_effect=AssertionError("Readonly must not construct Store")):
                result=ctx.context(self.root,"owned")
            with self.subTest(mode=mode):
                self.assertEqual(result["status"],"BLOCKED")
                summary=result["machine_observations"]
                self.assertEqual(summary["observed_transient_retries"],0)
                self.assertIsNone(summary["covered_scope_fault_retries"])
                self.assertTrue(summary["diagnostics"])
                self.assertEqual(files(self.store.root),before)
                for event in events:
                    with self.assertRaises((ValueError,subprocess.SubprocessError)):
                        queue._valid_observation(self.store,self.store.load("owned"),entry,event)
        write_json(path,valid)

    def test_two_real_base_merges_survive_interruption_and_readonly_validates_entire_chain(self):
        from engineering import required_ids
        from engineering_common import new_summary,execute,finish_summary
        def verified(candidate):
            report=new_summary(self.owned,"verification")
            folder=self.owned/"build/verification"/report["run_id"]
            for name in required_ids("all")-{"repository-unchanged"}:
                report["checks"].append(execute([sys.executable,"-c","print('fixture')"],self.owned,folder/(name+".log"),check_id=name))
            path=folder/"summary.json";finish_summary(report,self.owned,path)
            self.store.mutate("owned",lambda row:{**row,"delivery":{},"status":"VERIFIED",
                "head_sha":candidate,"verified_head":candidate,"verification_report":str(path)})
        verified(self.head)
        self.enqueue()
        start=self.head
        hops=[]
        def advance(number):
            old=subprocess.check_output(["git","-C",str(self.owned),"rev-parse","HEAD"]).decode().strip()
            name="advance-chain-"+str(number)+".txt"
            (self.root/name).write_text("source "+str(number),encoding="utf-8")
            self.run_git("add",name);self.run_git("commit","--quiet","-m","advance dev "+str(number))
            dev=self.run_git("rev-parse","HEAD").decode().strip()
            self.run_git("update-ref","refs/remotes/origin/dev",dev)
            proof={"from_head":old,"dev_sha":dev,"token":"chain-hop-"+str(number)}
            self.store.mutate("owned",lambda row:{**row,"base_merge_intent":proof})
            subprocess.run(["git","-C",str(self.owned),"merge","--no-ff","-m","Merge current dev\n\nTenon-Base-Merge: "+proof["token"],dev],capture_output=True,check=True)
            new=subprocess.check_output(["git","-C",str(self.owned),"rev-parse","HEAD"]).decode().strip()
            self.store.mutate("owned",lambda row:{**row,"head_sha":new,"base_sha":dev})
            hops.append((old,new))
            return new
        first=mock.Mock()
        def interrupt(**kwargs):
            self.assertEqual(kwargs["expected_candidate"],start)
            advance(1)
            raise SystemExit("crash after first legitimate merge")
        first.deliver.side_effect=interrupt
        now=datetime.now(timezone.utc)
        with self.assertRaises(SystemExit):
            queue.reconcile(self.root,True,lambda *_:first,now)
        middle=hops[0][1]
        second=mock.Mock()
        def finish(**kwargs):
            self.assertEqual(kwargs["expected_candidate"],middle)
            final=advance(2)
            verified(final)
            return {"status":"WAITING_CI","next_poll_seconds":30}
        second.deliver.side_effect=finish
        result=queue.reconcile(self.root,True,lambda *_:second,now+timedelta(seconds=1))
        self.assertEqual(result["tasks"][0]["state"],"WAITING")
        entry=queue.read_queue(self.store,"owned")
        task_record=self.store.load("owned")
        events=obs.machine_events(task_record,entry,repo=self.root)[0]
        event_id,event=next((key,value) for key,value in events.items() if value["payload"]["operation"]=="controller-result")
        chain=event["provenance"]["base_derivations"]
        self.assertEqual([(row["from_head"],row["result_head"]) for row in chain],hops)
        self.assertEqual(len(chain),2)
        self.assertEqual(event["payload"]["input_candidate"],start)
        self.assertEqual(entry["candidate_sha"],hops[-1][1])
        pending=copy.deepcopy(entry)
        pending["observation_outbox"]=[{"id":event_id,**event}]
        write_json(queue.queue_path(self.store,"owned"),pending)
        before=files(self.store.root)
        valid=ctx.context(self.root,"owned")
        self.assertEqual(valid["status"],"OBSERVED")
        self.assertEqual(valid["machine_observations"]["observed_controller_results"],1)
        self.assertEqual(files(self.store.root),before)
        # A forged intermediate hop is rejected in both mirror and readonly paths.
        pending["observation_outbox"][0]["provenance"]["base_derivations"][0]["result_head"]="f"*40
        write_json(queue.queue_path(self.store,"owned"),pending)
        before=files(self.store.root)
        invalid=ctx.context(self.root,"owned")
        self.assertEqual(invalid["status"],"BLOCKED")
        self.assertIsNone(invalid["machine_observations"]["covered_scope_fault_retries"])
        self.assertEqual(files(self.store.root),before)

    def test_invalid_external_marker_or_nonpositive_ordinal_never_executes_controller(self):
        original=self.enqueue()
        path=queue.queue_path(self.store,"owned")
        for ordinal,external,attempts in ((0,True,0),(1,"False",1),(-1,True,0),(1.0,False,0)):
            entry=copy.deepcopy(original)
            entry["attempts"]=attempts
            entry["active_attempt"]={"owner_id":entry["owner_id"],"generation":entry["generation"],
                "input_candidate":entry["candidate_sha"],"ordinal":ordinal,"external":external,
                "started_at":"2026-11-17T12:00:00+00:00","derivations":[]}
            write_json(path,entry)
            controller=self.waiting()
            factory=mock.Mock(return_value=controller)
            result=queue.reconcile(self.root,True,factory)
            with self.subTest(ordinal=ordinal,external=external,attempts=attempts):
                factory.assert_not_called()
                controller.deliver.assert_not_called()
                self.assertEqual(result["tasks"][0]["state"],"BLOCKED")
                self.assertTrue(self.owned.exists())
                self.assertEqual(self.backend.deleted,0)

    def test_repeated_external_handoff_crashes_keep_one_logical_attempt_and_result(self):
        import task_lifecycle as implementation
        self.enqueue()
        now=datetime.now(timezone.utc)
        controller=mock.Mock()
        def fresh_handoff(**kwargs):
            self.assertFalse(kwargs["allow_commit"])
            self.assertEqual(kwargs["expected_candidate"],self.head)
            # Real cleanup gates/archive/controller deployment; only its execution
            # location is made internal so it returns the normal external handoff.
            with mock.patch.object(implementation,"__file__",str(self.owned/"inside-controller.py")):
                result=self.controller.cleanup()
            self.assertEqual(result["status"],"READY_EXTERNAL_CLEANUP")
            return result
        controller.deliver.side_effect=fresh_handoff
        normal=subprocess.check_output
        invocations=[]
        def external(argv,*args,**kwargs):
            path=self.store.load("owned").get("controller")
            if path and path in [str(item) for item in argv]:
                invocations.append(list(argv))
                if len(invocations)<=2:
                    raise SystemExit("controlled exit at external invocation")
                # Run the ordinary external cleanup against the local bare remote,
                # retaining every original ownership/CI/archive/deletion gate.
                return json.dumps(self.controller.cleanup()).encode()
            return normal(argv,*args,**kwargs)
        identities=[]
        for iteration in range(2):
            with mock.patch.object(queue.subprocess,"check_output",side_effect=external):
                with self.assertRaises(SystemExit):
                    queue.reconcile(self.root,True,lambda *_:controller,now+timedelta(seconds=31*iteration))
            entry=queue.read_queue(self.store,"owned")
            identities.append(copy.deepcopy(entry["active_attempt"]))
            self.assertEqual((entry["attempts"],entry["active_attempt"]["ordinal"]),(0,1))
            self.assertIs(entry["active_attempt"]["external"],True)
            self.assertTrue(self.owned.exists())
            self.assertEqual(self.backend.deleted,0)
        self.assertEqual(identities[0],identities[1])
        with mock.patch.object(queue.subprocess,"check_output",side_effect=external):
            result=queue.reconcile(self.root,True,lambda *_:controller,now+timedelta(seconds=62))
        self.assertEqual(result["tasks"][0]["state"],"DONE")
        self.assertFalse(self.owned.exists())
        self.assertEqual(self.backend.deleted,1)
        entry=queue.read_queue(self.store,"owned")
        self.assertEqual(entry["attempts"],1)
        self.assertNotIn("active_attempt",entry)
        self.assertEqual(entry["observation_outbox"],[])
        summary=obs.machine_summary(self.store.load("owned"),entry,repo=self.root)
        self.assertEqual((summary["observed_controller_starts"],summary["observed_controller_results"]),(1,1))
        never=mock.Mock(side_effect=AssertionError("Closed external attempt cannot execute again"))
        queue.reconcile(self.root,True,never,now+timedelta(seconds=93))
        never.assert_not_called()
        again=obs.machine_summary(self.store.load("owned"),queue.read_queue(self.store,"owned"),repo=self.root)
        self.assertEqual(summary,again)

    def _assert_external_durable_result_publisher_failure(self, error_type):
        import task_lifecycle as implementation
        self.enqueue()
        controller=mock.Mock()
        def handoff(**kwargs):
            with mock.patch.object(implementation,"__file__",str(self.owned/"inside-controller.py")):
                return self.controller.cleanup()
        controller.deliver.side_effect=handoff
        normal=subprocess.check_output
        def external(argv,*args,**kwargs):
            path=self.store.load("owned").get("controller")
            if path and path in [str(item) for item in argv]:
                return json.dumps(self.controller.cleanup()).encode()
            return normal(argv,*args,**kwargs)
        original=obs.mirror_machine
        failed=[]
        def publisher(store,task_id,entry):
            if not failed and any(event["payload"]["operation"]=="controller-result"
                                  for event in entry.get("observation_outbox",[])):
                failed.append(True)
                raise error_type("Controlled external result publisher interruption")
            return original(store,task_id,entry)
        now=datetime.now(timezone.utc)
        with mock.patch.object(queue.subprocess,"check_output",side_effect=external),mock.patch.object(obs,"mirror_machine",side_effect=publisher):
            with self.assertRaises(error_type):
                queue.reconcile(self.root,True,lambda *_:controller,now)
        entry=queue.read_queue(self.store,"owned")
        self.assertEqual(entry["state"],"DONE")
        self.assertEqual(entry["attempts"],1)
        self.assertNotIn("active_attempt",entry)
        self.assertEqual(len(entry["observation_outbox"]),1)
        self.assertEqual(entry["last_result"]["status"],"CLEANED")
        self.assertEqual(self.store.load("owned")["status"],"CLEANED")
        self.assertFalse(self.owned.exists())
        self.assertEqual(self.backend.deleted,1)
        result_id=entry["observation_outbox"][0]["id"]
        readonly=obs.machine_summary(self.store.load("owned"),entry,repo=self.root)
        self.assertEqual((readonly["observed_controller_starts"],readonly["observed_controller_results"]),(1,1))
        factory=mock.Mock(side_effect=AssertionError("Closed result may only publish, never construct a controller"))
        queue.reconcile(self.root,True,factory,now+timedelta(seconds=31))
        factory.assert_not_called()
        final=queue.read_queue(self.store,"owned")
        self.assertEqual((final["state"],final["attempts"],final["observation_outbox"]),("DONE",1,[]))
        self.assertIn(result_id,self.store.load("owned")["observations_v1"]["machine"]["events"])
        summary=obs.machine_summary(self.store.load("owned"),final,repo=self.root)
        self.assertEqual((summary["observed_controller_starts"],summary["observed_controller_results"]),(1,1))
        before=files(self.store.root/"tasks")
        queue.reconcile(self.root,True,factory,now+timedelta(seconds=62))
        factory.assert_not_called()
        self.assertEqual(files(self.store.root/"tasks"),before)
        self.assertEqual(self.backend.deleted,1)

    def test_external_durable_result_timeout_only_retries_publication(self):
        self._assert_external_durable_result_publisher_failure(TimeoutError)

    def test_external_durable_result_systemexit_only_retries_publication(self):
        self._assert_external_durable_result_publisher_failure(SystemExit)

    def test_interrupted_generation_bool_or_float_never_constructs_or_executes_controller(self):
        initial=self.enqueue()
        self.assertEqual(initial["generation"],1)
        path=queue.queue_path(self.store,"owned")
        for invalid in (True,1.0):
            entry=copy.deepcopy(initial)
            entry["active_attempt"]={"owner_id":entry["owner_id"],"generation":invalid,
                "input_candidate":entry["candidate_sha"],"ordinal":1,"started_at":"2026-11-17T12:00:00+00:00"}
            write_json(path,entry)
            controller=self.waiting()
            factory=mock.Mock(return_value=controller)
            result=queue.reconcile(self.root,True,factory)
            with self.subTest(generation=invalid,type=type(invalid).__name__):
                factory.assert_not_called()
                controller.deliver.assert_not_called()
                self.assertEqual(result["tasks"][0]["state"],"BLOCKED")
                self.assertTrue(self.owned.exists())

    def test_interrupted_attempt_owner_generation_or_unknown_candidate_cannot_execute(self):
        for field,value in (("owner_id","foreign"),("generation",999),("input_candidate","f"*40)):
            self.enqueue()
            path=queue.queue_path(self.store,"owned")
            entry=queue.read_queue(self.store,"owned")
            entry["active_attempt"]={"owner_id":entry["owner_id"],"generation":entry["generation"],
                "input_candidate":entry["candidate_sha"],"ordinal":1,"started_at":"2026-11-17T12:00:00+00:00",field:value}
            write_json(path,entry)
            factory=mock.Mock(side_effect=AssertionError("Invalid attempt cannot execute"))
            result=queue.reconcile(self.root,True,factory)
            factory.assert_not_called()
            self.assertEqual(result["tasks"][0]["state"],"BLOCKED")
            self.assertTrue(self.owned.exists())


class StableObservationTests(fixtures.RepositoryFixture):
    def test_old_main_stable_context_note_and_handoff_use_the_registered_worktree(self):
        import tenon
        import test_entrypoint
        from engineering import required_ids
        from engineering_common import new_summary,execute,finish_summary
        shutil.copytree(fixtures.ROOT/"scripts",self.root/"scripts",ignore=shutil.ignore_patterns("__pycache__"))
        self.run_git("add","scripts");self.run_git("commit","--quiet","-m","fixture runtime")
        self.run_git("update-ref","refs/remotes/origin/dev","HEAD")
        self.run_git("remote","add","origin","https://github.com/fixture/tenon.git")
        row=tenon.start_workspace(self.root,"stable","observations","Stable source fixture",["scripts/a.py"],["make verify"],
            workspace_root=self.root/"build/workspaces",backend=test_entrypoint.Backend())
        owner=Path(row["worktree"])
        self.run_git("checkout","--quiet","-b","main")
        installed=tenon.install(self.root,test_entrypoint.Backend())
        def command(*args):
            result=subprocess.run([sys.executable,installed["entry"],"task",*args],cwd=self.root,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors="replace")+result.stdout.decode(errors="replace"))
            return json.loads(result.stdout)
        current=command("context","--id","stable")
        self.assertEqual(current["source"]["fingerprint"]["branch"],row["branch"])
        command("note","--id","stable","--event-id","explicit-one","--kind","rework","--reason","Explicit observed correction")
        value=new_summary(owner,"verification");folder=owner/"build/verification"/value["run_id"]
        for name in required_ids("all")-{"repository-unchanged"}:
            value["checks"].append(execute([sys.executable,"-c","print('fixture')"],owner,folder/(name+".log"),check_id=name))
        report=folder/"summary.json";finish_summary(value,owner,report)
        result=command("handoff","--id","stable","--evidence",str(report),"--human-interventions","0","--rework","1")
        self.assertEqual(result["status"],"READY")
        self.assertEqual(result["source"]["branch"],row["branch"])
        self.assertEqual(result["output_contract"]["qualification"],"candidate-verification")
        self.assertEqual(self.run_git("branch","--show-current").decode().strip(),"main")
        self.assertEqual(self.run_git("status","--porcelain"),b"")


if __name__=="__main__":
    unittest.main()
