#!/usr/bin/env python3
"""Resumable dev delivery and archive-first, expected-head resource cleanup."""
from __future__ import annotations

# Controller execution ABI 1: keep before all local/business imports.
import os as _tenon_os
import sys as _tenon_sys
from pathlib import Path as _tenon_Path

def _tenon_cache_entry():
    entry = _tenon_Path(__file__).absolute()
    version = entry.parent.parent
    if (entry.parent.name != "scripts" or version.parent.name != "controllers"
            or version.parent.parent.name != "tenon" or len(version.name) != 40
            or any(char not in "0123456789abcdef" for char in version.name)):
        return
    module = _tenon_sys.modules.get("controller_leases")
    if module is not None and module.active_for(entry):
        return
    if __name__ != "__main__":
        raise RuntimeError("Owned controller imports require an admitted execution; use the installed entry")
    _tenon_sys.dont_write_bytecode = True
    _tenon_os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    launcher = version.parent.parent / "bin/tenon.py"
    if not launcher.is_file():
        raise RuntimeError("Controller admission is unavailable; run the qualified source installer explicitly")
    import json as _tenon_boot_json
    configuration = _tenon_boot_json.loads((launcher.parent / "installation.json").read_bytes())
    if configuration.get("controller_lease_abi") != 1:
        raise RuntimeError("Installed launcher has no execution admission ABI; run the qualified new source installer explicitly after dev is green")
    import runpy as _tenon_runpy
    _tenon_sys.argv = [str(launcher), "_controller", str(entry), *_tenon_sys.argv[1:]]
    _tenon_runpy.run_path(str(launcher), run_name="__main__")
    raise SystemExit(0)

try:
    _tenon_cache_entry()
except (OSError, ValueError, RuntimeError) as _tenon_error:
    import json as _tenon_json
    print(_tenon_json.dumps({"status": "BLOCKED", "reason": str(_tenon_error)}))
    raise SystemExit(2)

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from ci_gate import REQUIRED_JOBS, check_dev_delivery
from engineering import required_ids
from engineering_common import safe_exception, safe_error_payload
from engineering_common import ROOT, digest, execute, git, utc_now, validate_evidence, write_json
from task_archive import archive_task, deploy_controller, expected_head, ownership_errors, verify_archive, verify_task_archive, worktrees
from controller_leases import integration_scope
from task_store import FileLease, TaskStore
from task_backends import GitHub, command, repository_name, directory_delete_ready, WorkspaceBackend
from ci_observation import accepted_record, recovered_pr_record


def ci_green(snapshot: dict | None, sha: str, event: str) -> bool:
    if not snapshot:
        return False
    run,jobs = snapshot["workflow_run"],snapshot["jobs"]
    names = [item["name"] for item in jobs]
    return (run.get("head_sha")==sha and run.get("event")==event and run.get("status")=="completed"
            and run.get("conclusion")=="success" and (REQUIRED_JOBS|{"ci-required"}).issubset(set(names))
            and len(names)==len(set(names)) and all(item.get("status")=="completed" and item.get("conclusion")=="success"
                and item.get("run_id")==run["id"] and item.get("run_attempt")==run["run_attempt"] for item in jobs))


class Lifecycle:
    def __init__(self, repo: Path, task_id: str, backend=None):
        self.store = TaskStore(repo)
        self.task_id = task_id
        self.backend = backend or GitHub(repo)
        registered = self.store.load(task_id).get("repository")
        if registered and registered.lower()!=self.backend.name.lower():
            raise ValueError("Registered task repository differs from the active origin")
        if not registered and isinstance(self.backend,GitHub):
            old_url = self.store.load(task_id).get("delivery",{}).get("html_url","")
            match = re.match(r"https://github\.com/([^/]+/[^/]+)/pull/",old_url)
            if match and match[1].lower()!=self.backend.name.lower():
                raise ValueError("Legacy PR belongs to another origin")
            self.update("repository-observed",repository=self.backend.name)

    def update(self, action: str, **fields):
        return self.store.mutate(self.task_id,lambda value:{**value,**safe_error_payload(fields)},action)

    def task(self):
        return self.store.load(self.task_id)

    def _ancestor(self, older: str, newer: str) -> bool:
        return subprocess.run(["git","-C",str(self.store.repo),"merge-base","--is-ancestor",older,newer],
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False).returncode==0

    def operations(self) -> Path:
        path = self.store.root/"operations"/self.task_id
        path.mkdir(parents=True,exist_ok=True)
        return path

    def verification(self, task: dict, root: Path, head: str):
        from host_profile import validate_task_inputs
        validate_task_inputs(task)
        if task.get("verified_head")==head:
            try:
                validate_evidence(Path(task["verification_report"]),root,required_ids("all"))
                return
            except (ValueError,OSError,KeyError):
                pass
        self.update("verification-intent",status="VERIFYING",head_sha=head)
        from tenon import verification_argv
        argv = verification_argv(task, root)
        result = execute(argv,root,self.operations()/"local-verify.log",timeout=1800,check_id="committed-candidate")
        if result["status"]!="PASS":
            self.update("verification-failed",status="NEEDS_ATTENTION",verification_failure=result)
            raise ValueError("Committed-candidate validation failed; see "+str(self.operations()/"local-verify.log"))
        reports = sorted((root/"build/verification").glob("*/summary.json"),key=lambda path:path.stat().st_mtime,reverse=True)
        for path in reports:
            try:
                value = validate_evidence(path,root,required_ids("all"))
                if value.get("kind")=="verification":
                    self.update("verification-passed",status="VERIFIED",verified_head=head,verification_report=str(path))
                    return
            except (ValueError,OSError,KeyError):
                continue
        raise ValueError("Validation returned no source-matched full verification report")

    def acceptance(self, task: dict, root: Path, head: str):
        """Mandatory full verification plus explicitly executed task-specific argv."""
        if task.get("kind") != "engineering":
            return
        from task_contract import (acceptance_commands, acceptance_errors, contract_digest,
                                   extra_acceptance_commands, output_observations)
        from engineering_common import new_summary, finish_summary, source_state
        from tenon import verification_argv
        if not acceptance_errors(task, root):
            return
        extras = extra_acceptance_commands(task)
        info = None
        if extras:
            summary = new_summary(root, "task-acceptance")
            summary.update(task_id=self.task_id, contract_sha256=contract_digest(task))
            path = root / "build/verification" / summary["run_id"] / "summary.json"
            for index, argv in enumerate(extras):
                summary["checks"].append(execute(verification_argv({"verify_command": argv}, root), root,
                    path.parent / ("acceptance-" + str(index) + ".log"), timeout=1800,
                    check_id="acceptance-" + str(index)))
            finish_summary(summary, root, path)
            if summary["status"] != "PASS":
                self.update("acceptance-failed", status="NEEDS_ATTENTION", acceptance_failure=str(path))
                raise ValueError("Declared acceptance command failed or changed source: " + str(path))
            info = {"path": str(path), "sha256": digest(path)}
        outputs, errors = output_observations(task, root)
        if errors:
            self.update("acceptance-output-failed", status="NEEDS_ATTENTION", blockers=errors)
            raise ValueError("; ".join(errors))
        state = source_state(root)
        if state["sha"] != head:
            raise ValueError("Acceptance candidate changed")
        record = {"schema_version": 1, "source": state, "contract_sha256": contract_digest(task),
                  "commands": acceptance_commands(task["checks"]), "outputs": outputs, "report": info,
                  "full_verification_sha256": digest(Path(task["verification_report"]))}
        current = self.update("acceptance-qualified", acceptance=record)
        errors = acceptance_errors(current, root)
        if errors:
            raise ValueError("; ".join(errors))

    def deliver(self, *, allow_commit: bool = True, expected_candidate: str | None = None) -> dict:
        task = self.task()
        if task.get("kind") != "engineering":
            return self._deliver(allow_commit=allow_commit, expected_candidate=expected_candidate)
        if task.get("host_platform") != os.name:
            raise ValueError("Use the registered lifecycle host Git implementation")
        if task.get("delivery_mode") == "local-only" or task.get("role") not in {"writer", "integrator"}:
            raise ValueError("Read-only/local-only tasks cannot enter automatic delivery")
        if task.get("status") == "CLEANED" or task.get("delivery", {}).get("merged"):
            return self._deliver(allow_commit=allow_commit, expected_candidate=expected_candidate)
        from task_contract import set_lease_state
        owner = task["owner_id"]
        set_lease_state(self.store, self.task_id, "active", owner_id=owner, role="integrator")
        try:
            return self._deliver(allow_commit=allow_commit, expected_candidate=expected_candidate)
        finally:
            current = self.task()
            lease = current.get("execution_lease", {})
            if lease.get("state") == "active" and lease.get("role") == "integrator":
                state = "completed" if current.get("delivery", {}).get("merged") else "paused"
                set_lease_state(self.store, self.task_id, state, owner_id=owner)

    def _deliver(self, *, allow_commit: bool = True, expected_candidate: str | None = None) -> dict:
        task = self.task()
        if task.get("status")=="CLEANED":
            return task
        if task.get("delivery_mode")=="local-only":
            raise ValueError("Task explicitly holds delivery")
        if task.get("delivery",{}).get("merged"):
            return self.after_merge()
        root = Path(task["worktree"]).resolve()
        if task.get("host_platform")!=os.name:
            raise ValueError("Use the registered lifecycle host Git implementation")
        if git(root,"branch","--show-current").decode().strip()!=task["branch"] or not task["branch"].startswith("dev-"):
            raise ValueError("Task branch ownership changed")
        existing = self.backend.find_pull(task["branch"])
        if existing and existing.get("merged"):
            if git(root,"diff","--name-only","HEAD","--").strip() or existing["head"]["sha"]!=expected_head(task):
                raise ValueError("Merged task has new work; create a new fix branch")
            retained = recovered_pr_record(task.get("pr_ci"),existing["head"]["sha"])
            self.update("merge-recovered",status="MERGED",delivered_at=existing["merged_at"],delivery={
                "number":existing["number"],"html_url":existing["html_url"],"merged":True,
                "head_sha":existing["head"]["sha"],"merge_commit_sha":existing["merge_commit_sha"],"merged_at":existing["merged_at"],
                **({"pr_ci":retained} if retained else {})})
            return self.after_merge()
        self.backend.refresh()
        observed_base = git(root,"rev-parse","origin/dev").decode().strip()
        if self._ancestor(observed_base,git(root,"rev-parse","HEAD").decode().strip()) and observed_base!=task["base_sha"]:
            task = self.update("base-reconciled",base_sha=observed_base)
        from task import changed_paths, scope_errors
        errors = scope_errors(task,root)
        if errors:
            raise ValueError("Out-of-scope changes: "+", ".join(errors))
        names = set(git(root,"diff","--name-only","-z","HEAD","--").split(b"\0"))
        names.update(git(root,"ls-files","--others","--exclude-standard","-z").split(b"\0"))
        names = sorted(item.decode() for item in names if item)
        if not allow_commit:
            if names:
                raise ValueError("Unsubmitted edits appeared; background delivery never stages or commits them")
            if git(root,"rev-parse","HEAD").decode().strip() != expected_candidate:
                raise ValueError("Background candidate changed before delivery")
        if names:
            staged = git(root,"diff","--cached","--name-only").decode().splitlines()
            if set(staged)-set(names):
                raise ValueError("Unrelated staged changes remain")
            self.update("commit-intent",status="COMMITTING",previous_head=git(root,"rev-parse","HEAD").decode().strip(),staged_paths=names)
            command(root,"-c","core.safecrlf=false","add","--",*names)
            command(root,"-c","core.safecrlf=false","diff","--cached","--check")
            command(root,"commit","-m",task.get("commit_title","build: "+task["task_id"]),"-m","Tenon-Task: "+task["task_id"])
        head = git(root,"rev-parse","HEAD").decode().strip()
        if not allow_commit and head != expected_candidate:
            raise ValueError("Background candidate changed before preparation")
        if head==task["base_sha"]:
            raise ValueError("No task change to deliver")
        self.update("candidate-observed",head_sha=head)
        self.backend.refresh()
        base = git(root,"rev-parse","origin/dev").decode().strip()
        if not self._ancestor(base,head):
            intent = {"from_head":head,"dev_sha":base,"token":__import__("uuid").uuid4().hex}
            self.update("base-merge-intent",base_merge_intent=intent)
            command(root,"merge","--no-edit","-m","Merge current dev for Tenon task "+self.task_id+"\n\nTenon-Base-Merge: "+intent["token"],"origin/dev")
            head = git(root,"rev-parse","HEAD").decode().strip()
            if not allow_commit:
                parents = git(root,"show","-s","--format=%P",head).decode().split()
                message = git(root,"show","-s","--format=%B",head).decode().splitlines()
                if parents != [intent["from_head"],base] or "Tenon-Base-Merge: "+intent["token"] not in message:
                    raise ValueError("Background merge differs from its recorded submitted head and dev base")
            self.update("dev-integrated",base_sha=base,head_sha=head,base_merge_intent={**intent,"result_head":head})
        if task.get("kind") == "engineering":
            from task_contract import integration_errors
            errors = integration_errors(self.store, self.task(), current_dev=base)
            if errors:
                raise ValueError("; ".join(errors))
        self.verification(self.task(),root,head)
        if task.get("kind") == "engineering":
            self.acceptance(self.task(), root, head)
            errors = integration_errors(self.store, self.task(), current_dev=base, require_evidence=True)
            if errors:
                raise ValueError("; ".join(errors))
        if not allow_commit:
            from engineering_common import source_state
            state = source_state(root)
            if state["dirty"] or state["sha"] != head:
                raise ValueError("Source changed after background verification")
        remote = self.backend.remote_head(task["branch"])
        if remote!=head:
            if remote and not self._ancestor(remote,head):
                raise ValueError("Remote task branch contains other work")
            self.backend.push(task["branch"],head)
        self.update("push-observed",pushed_head=head)
        pull = self.backend.find_pull(task["branch"])
        if not pull:
            pull = self.backend.create_pull(self.task())
        if pull.get("merged"):
            retained = recovered_pr_record(self.task().get("pr_ci"),pull["head"]["sha"])
            self.update("existing-merge-observed",delivery={"number":pull["number"],"html_url":pull["html_url"],"merged":True,
                "head_sha":pull["head"]["sha"],"merge_commit_sha":pull["merge_commit_sha"],"merged_at":pull["merged_at"],
                **({"pr_ci":retained} if retained else {})},status="MERGED")
            return self.after_merge()
        self.update("pr-observed",status="WAITING_CI",pr_number=pull["number"],pr_url=pull["html_url"],head_sha=head)
        snapshot = self.backend.latest_run(head,"pull_request",pull["number"])
        if not snapshot or snapshot["workflow_run"]["status"]!="completed":
            return {"status":"WAITING_CI","next_poll_seconds":25,"pr_url":pull["html_url"]}
        snapshot["pull_request"] = self.backend.pull(pull["number"])
        write_json(self.operations()/"pr-ci.json",snapshot)
        errors = check_dev_delivery(snapshot["pull_request"],snapshot["workflow_run"],snapshot["jobs"],head,base,self.backend.name,self._ancestor(base,head))
        if errors:
            if snapshot["workflow_run"].get("conclusion")!="success":
                self.backend.diagnostics(snapshot,self.operations()/str(snapshot["workflow_run"]["id"]))
                self.update("ci-failed",status="NEEDS_ATTENTION",blockers=errors)
                return {"status":"NEEDS_ATTENTION","blockers":errors,"diagnostics":str(self.operations())}
            return {"status":"WAITING_CI","blockers":errors,"next_poll_seconds":25}
        self.backend.refresh()
        fresh = self.backend.pull(pull["number"])
        latest_base = git(root,"rev-parse","origin/dev").decode().strip()
        if latest_base!=base or fresh["head"]["sha"]!=head or fresh["base"]["ref"]!="dev":
            return {"status":"WAITING_CI","reason":"Candidate/base changed; resume to integrate and recheck"}
        if task.get("kind") == "engineering":
            from task_contract import integration_errors
            errors = integration_errors(self.store, self.task(), current_dev=latest_base, require_evidence=True)
            if errors:
                raise ValueError("; ".join(errors))
        pr_ci = accepted_record(snapshot,task_id=self.task_id,repository=self.backend.name,candidate_head=head,
            accepted_head=head,event="pull_request",observed_at=utc_now(),previous=self.task().get("pr_ci"))
        self.update("merge-intent",status="MERGING",pr_ci=pr_ci)
        self.backend.merge(pull["number"],head)
        merged = self.backend.pull(pull["number"])
        if not merged.get("merged"):
            raise ValueError("Merge is not confirmed")
        self.update("merge-confirmed",status="MERGED",delivered_at=merged["merged_at"],delivery={
            "number":merged["number"],"html_url":merged["html_url"],"merged":True,"head_sha":head,
            "merge_commit_sha":merged["merge_commit_sha"],"merged_at":merged["merged_at"],
            "pr_ci":pr_ci})
        return self.after_merge()

    def after_merge(self) -> dict:
        task = self.task()
        if task.get("kind") == "engineering" and task.get("host_platform") != os.name:
            raise ValueError("Use the registered lifecycle host Git implementation")
        self.backend.refresh()
        tip = git(self.store.repo,"rev-parse","origin/dev").decode().strip()
        pull = self.backend.pull(task["delivery"]["number"])
        head = expected_head(task)
        if not pull.get("merged") or pull["base"]["ref"]!="dev" or pull["head"]["sha"]!=head or not self._ancestor(head,tip):
            raise ValueError("Task merge/ancestry no longer matches")
        if task.get("kind") == "engineering" and task.get("execution_lease", {}).get("state") != "completed":
            from task_contract import set_lease_state
            set_lease_state(self.store, self.task_id, "completed", owner_id=task["owner_id"])
            task = self.task()
        pr_snapshot = self.backend.latest_run(head,"pull_request",pull["number"])
        if not ci_green(pr_snapshot,head,"pull_request"):
            self.update("pr-ci-unconfirmed",status="NEEDS_ATTENTION")
            return {"status":"NEEDS_ATTENTION","reason":"Original PR CI must still be verifiably green"}
        snapshot = self.backend.latest_run(tip,"push")
        if not snapshot or snapshot["workflow_run"]["status"]!="completed":
            self.update("waiting-dev-ci",status="WAITING_DEV_CI")
            return {"status":"WAITING_DEV_CI","next_poll_seconds":25}
        write_json(self.operations()/"dev-ci.json",snapshot)
        if not ci_green(snapshot,tip,"push"):
            self.backend.diagnostics(snapshot,self.operations()/str(snapshot["workflow_run"]["id"]))
            self.update("dev-ci-failed",status="DEV_CI_FAILED",dev_ci=snapshot["workflow_run"])
            return {"status":"DEV_CI_FAILED","reason":"Keep resources; fix through a new dev task branch"}
        dev_ci = accepted_record(snapshot,task_id=self.task_id,repository=self.backend.name,candidate_head=head,
            accepted_head=tip,event="push",observed_at=utc_now(),previous=self.task().get("dev_ci"))
        self.update("dev-ci-green",status="DEV_GREEN",green_dev_sha=tip,dev_ci=dev_ci)
        return self.cleanup()

    def cleanup(self) -> dict:
        task = self.task()
        if task.get("status")=="CLEANED":
            return task
        if task.get("dev_ci",{}).get("conclusion")!="success" or not task.get("green_dev_sha"):
            return self.after_merge()
        # Re-query the live merged PR/dev run before every destructive continuation.
        self.backend.refresh()
        tip = git(self.store.repo,"rev-parse","origin/dev").decode().strip()
        snapshot = self.backend.latest_run(tip,"push")
        pull = self.backend.pull(task["delivery"]["number"])
        pr_snapshot = self.backend.latest_run(expected_head(task),"pull_request",pull["number"])
        if not ci_green(pr_snapshot,expected_head(task),"pull_request") or not ci_green(snapshot,tip,"push") or not pull.get("merged") or pull["base"]["ref"]!="dev" or pull["head"]["sha"]!=expected_head(task) or not self._ancestor(expected_head(task),tip):
            raise ValueError("Fresh merged-dev CI/ownership proof is required")
        root = Path(task["worktree"]).resolve()
        task = self.update("cleanup-ci-refreshed",green_dev_sha=tip)
        errors = ownership_errors(self.store,task,absent_ok=bool(task.get("cleanup",{}).get("remote_deleted")))
        if errors:
            raise ValueError("; ".join(errors))
        registered = any(item["path"] == root for item in worktrees(self.store.repo))
        empty_remnant = root.is_dir() and not registered and not any(root.iterdir())
        if task.get("cleanup", {}).get("remote_deleted") and task.get("archive") and (not root.exists() or empty_remnant):
            # Git can remove its registration/files before a Windows directory lease blocks rmdir.
            # Existing verified evidence is authoritative for finalization; no new archive is possible.
            return self.finalize()
        archive_task(self.store,task)
        controller = deploy_controller(self.store,tip)
        primary = worktrees(self.store.repo)[0]["path"]
        self.update("external-controller-ready",controller=str(controller),primary_repo=str(primary))
        if Path.cwd().resolve().is_relative_to(root) or Path(__file__).resolve().is_relative_to(root):
            return {"status":"READY_EXTERNAL_CLEANUP","controller":str(controller),"repo":str(primary),"task_id":self.task_id}
        task = self.task()
        if root.exists():
            archive_task(self.store,task)
        remote = self.backend.remote_head(task["branch"])
        if remote and remote!=expected_head(task):
            raise ValueError("Remote task branch advanced; conditional deletion refused")
        if remote:
            self.backend.delete_remote(task["branch"],expected_head(task))
        self.store.mutate(self.task_id,lambda value:{**value,"status":"CLEANING","cleanup":{**value.get("cleanup",{}),"remote_deleted":True}},"remote-deleted")
        if root.exists():
            remaining = ownership_errors(self.store,self.task())
            if remaining:
                raise ValueError("; ".join(remaining))
            archive_task(self.store,self.task())
            result = WorkspaceBackend.remove(primary, task, controller, runner=command)
            if result:
                return result
        return self.finalize()

    def native_archive_record(self, receipt: Path) -> dict:
        task = self.task()
        primary = worktrees(self.store.repo)[0]["path"]
        return WorkspaceBackend.native_receipt(task, receipt, primary)

    def complete_app_cleanup(self, receipt: Path) -> dict:
        """Host-adapter recovery after a confirmed native archive was interrupted."""
        task = self.task()
        root = Path(task["worktree"]).resolve()
        primary = worktrees(self.store.repo)[0]["path"]
        if Path.cwd().resolve().is_relative_to(root) or Path(__file__).resolve().is_relative_to(root):
            raise ValueError("Native recovery needs an external executor")
        record = self.native_archive_record(receipt)
        # This is an adapter receipt from list_artifacts, not permission to skip gates.
        saved = self.operations()/"app-archive-receipt.json"
        write_json(saved,record)
        self.update("native-archive-confirmed",app_archive_receipt=str(saved))
        registered = any(item["path"]==root for item in worktrees(self.store.repo))
        if not root.exists() or (not registered and root.is_dir() and not any(root.iterdir())):
            return self.finalize()
        result = self.cleanup()
        if result["status"]=="CLEANED":
            return result
        if result["status"]!="NEEDS_APP_ARCHIVE":
            raise ValueError("Native recovery has not passed all cleanup gates")
        task = self.task()
        errors = ownership_errors(self.store,task)
        if errors:
            raise ValueError("; ".join(errors))
        archive_task(self.store,task)
        # Native archiving already retained its recovery record. Never force removal.
        WorkspaceBackend.remove(primary, task, Path(task["controller"]), native_confirmed=True, runner=command)
        return self.finalize()

    def wait_app_cleanup(self, receipt: Path, wait_seconds: float, poll_seconds: float = 30) -> dict:
        """One bounded external execution, retrying only OS directory sharing leases."""
        if not 0<=wait_seconds<=86400 or not 0<poll_seconds<=300:
            raise ValueError("Use a bounded wait up to 86400 seconds and a positive poll up to 300")
        task = self.task()
        root = Path(task["worktree"]).resolve()
        if Path.cwd().resolve().is_relative_to(root) or Path(__file__).resolve().is_relative_to(root):
            raise ValueError("Resource waiting needs an external executor")
        self.native_archive_record(receipt)
        worker = {"pid":os.getpid(),"host_platform":os.name,"started_at":utc_now(),
                  "limit_seconds":wait_seconds,"state":"RUNNING"}
        self.update("cleanup-worker-started",cleanup_worker=worker)
        deadline,waiting = time.monotonic()+wait_seconds,False
        try:
            while True:
                if directory_delete_ready(root):
                    waiting = False
                    try:
                        result = self.complete_app_cleanup(receipt)
                        self.update("cleanup-worker-finished",cleanup_worker={**worker,"state":"DONE","finished_at":utc_now()})
                        return result
                    except OSError as error:
                        if getattr(error,"winerror",None)!=32:
                            raise
                if not waiting:
                    self.update("cleanup-waiting-for-resource",status="WAITING_RESOURCE",
                                cleanup_worker={**worker,"state":"WAITING_RESOURCE"})
                    waiting = True
                remaining = deadline-time.monotonic()
                if remaining<=0:
                    raise TimeoutError("Directory lease remains held; originals and refs are retained")
                time.sleep(min(poll_seconds,remaining))
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
            self.update("cleanup-worker-stopped",status="WAITING_RESOURCE" if isinstance(error,TimeoutError) else "BLOCKED",
                        cleanup_worker={**worker,"state":"STOPPED","finished_at":utc_now(),"reason":safe_exception(error)})
            raise

    def finalize(self) -> dict:
        task = self.task()
        if task.get("status")=="CLEANED":
            return task
        root = Path(task["worktree"]).resolve()
        errors = ownership_errors(self.store,task,absent_ok=True)
        if errors:
            raise ValueError("; ".join(errors))
        if not task.get("cleanup",{}).get("remote_deleted"):
            raise ValueError("Remote deletion checkpoint is missing")
        registered = any(item["path"]==root for item in worktrees(self.store.repo))
        empty_remnant = root.is_dir() and not any(root.iterdir()) and not registered
        missing_registration = registered and not root.exists()
        if (registered and not missing_registration) or (root.exists() and not empty_remnant):
            raise ValueError("Worktree removal is not confirmed")
        verify_task_archive(self.store,task)
        self.backend.refresh()
        tip = git(self.store.repo,"rev-parse","origin/dev").decode().strip()
        pull = self.backend.pull(task["delivery"]["number"])
        pr_ci = self.backend.latest_run(expected_head(task),"pull_request",pull["number"])
        if (not pull.get("merged") or pull["base"]["ref"]!="dev" or pull["head"]["sha"]!=expected_head(task)
                or not ci_green(pr_ci,expected_head(task),"pull_request")
                or not self._ancestor(expected_head(task),tip) or not ci_green(self.backend.latest_run(tip,"push"),tip,"push")):
            raise ValueError("Live merged PR/CI and green dev ancestry are required before finalization")
        if self.backend.remote_head(task["branch"]):
            raise ValueError("Remote branch still exists")
        if any(item.get("branch")=="refs/heads/"+task["branch"] and item["path"]!=root for item in worktrees(self.store.repo)):
            raise ValueError("Task branch is checked out elsewhere")
        ref = "refs/heads/"+task["branch"]
        result = subprocess.run(["git","-C",str(self.store.repo),"rev-parse","--verify",ref],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
        if result.returncode==0:
            if result.stdout.decode().strip()!=expected_head(task):
                raise ValueError("Local branch advanced; conditional deletion refused")
        if (missing_registration or empty_remnant) and task.get("managed_identity"):
            if not task.get("app_archive_receipt"):
                raise ValueError("Managed remnants require their native archive receipt")
            self.native_archive_record(Path(task["app_archive_receipt"]))
        if missing_registration:
            # Reconcile only this owned entry; never prune other worktrees or force.
            command(self.store.repo,"worktree","remove",str(root))
        if empty_remnant:
            # rmdir is non-recursive and atomically refuses new files or busy directories.
            root.rmdir()
        if root.exists() or any(item["path"]==root for item in worktrees(self.store.repo)):
            raise ValueError("Worktree directory/registration is still present")
        if result.returncode==0:
            command(self.store.repo,"update-ref","-d",ref,expected_head(task))
        tracking = "refs/remotes/origin/"+task["branch"]
        result = subprocess.run(["git","-C",str(self.store.repo),"rev-parse","--verify",tracking],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
        if result.returncode==0:
            old = result.stdout.decode().strip()
            if not self._ancestor(old,tip):
                raise ValueError("Tracking ref contains unintegrated work")
            command(self.store.repo,"update-ref","-d",tracking,old)
        for ref in (ref,tracking):
            if subprocess.run(["git","-C",str(self.store.repo),"show-ref","--verify","--quiet",ref],check=False).returncode==0:
                raise ValueError("Task ref deletion is incomplete")
        value = self.update("cleanup-confirmed",status="CLEANED",cleaned_at=utc_now(),completed_at=utc_now(),
                            cleanup={"remote_deleted":True,"worktree_removed":True,"local_deleted":True,"tracking_deleted":True})
        return {"status":"CLEANED","task_id":self.task_id,"archive":value["archive"],"pr_url":task["delivery"]["html_url"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("action",choices=("deliver","resume","cleanup","finalize-cleanup","complete-app-cleanup","wait-app-cleanup","status"))
    parser.add_argument("--repo",type=Path,default=ROOT)
    parser.add_argument("--id",required=True)
    parser.add_argument("--app-archive-receipt",type=Path)
    parser.add_argument("--wait-seconds",type=float,default=7200)
    parser.add_argument("--poll-seconds",type=float,default=30)
    args = parser.parse_args()
    try:
        if args.action=="status":
            print(json.dumps(TaskStore(args.repo).load(args.id),indent=2,ensure_ascii=False))
            return 0
        lifecycle = Lifecycle(args.repo,args.id)
        if lifecycle.task().get("host_platform") != os.name:
            raise ValueError("Use the registered lifecycle host Git implementation")
        with integration_scope(lifecycle.store), FileLease(lifecycle.store.root/"locks"/(args.id+"-controller.lock")):
            if args.action in {"deliver","resume"}:
                result = lifecycle.deliver()
            elif args.action=="wait-app-cleanup":
                if args.app_archive_receipt is None:
                    raise ValueError("A native archived attachment receipt is required")
                result = lifecycle.wait_app_cleanup(args.app_archive_receipt,args.wait_seconds,args.poll_seconds)
            elif args.action=="complete-app-cleanup":
                if args.app_archive_receipt is None:
                    raise ValueError("A native archived attachment receipt is required")
                result = lifecycle.complete_app_cleanup(args.app_archive_receipt)
            elif args.action=="finalize-cleanup":
                result = lifecycle.finalize()
            else:
                result = lifecycle.cleanup()
        print(json.dumps(result,indent=2,ensure_ascii=False))
        return 2 if result.get("status") in {"NEEDS_ATTENTION","DEV_CI_FAILED"} else 0
    except (ValueError,OSError,RuntimeError,subprocess.SubprocessError) as error:
        if "lifecycle" in locals():
            try:
                lifecycle.update("operation-blocked",last_error=safe_exception(error),blocked_at=utc_now())
            except (OSError,ValueError):
                pass
        reason = safe_exception(error)
        print(json.dumps({"status":"BLOCKED","reason":reason}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
