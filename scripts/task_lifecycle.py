#!/usr/bin/env python3
"""Resumable dev delivery and archive-first, expected-head resource cleanup."""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from ci_gate import REQUIRED_JOBS, check_dev_delivery
from engineering import required_ids
from engineering_common import ROOT, execute, git, utc_now, validate_evidence, write_json
from task_archive import archive_task, deploy_controller, expected_head, ownership_errors, verify_archive, verify_task_archive, worktrees
from task_store import FileLease, TaskStore


def repository_name(repo: Path) -> str:
    url = git(repo,"remote","get-url","origin").decode().strip()
    match = re.fullmatch(r"(?:git@github\.com:|https://github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?",url)
    if not match:
        raise ValueError("Delivery requires an explicitly configured GitHub origin")
    return match[1]


def command(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git","-C",str(repo),*args],stderr=subprocess.PIPE)


class GitHub:
    def __init__(self, repo: Path):
        self.repo = repo.resolve()
        self.name = repository_name(repo)
        self.remote_url = git(repo,"remote","get-url","origin").decode().strip()

    def api(self, endpoint: str, method: str = "GET", body: dict | None = None):
        argv = ["gh","api",f"repos/{self.name}/{endpoint}","--method",method]
        payload = None
        if body is not None:
            argv += ["--input","-"]
            payload = json.dumps(body).encode()
        result = subprocess.run(argv,input=payload,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60,check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.decode("utf-8",errors="replace")[-3000:])
        return json.loads(result.stdout) if result.stdout.strip() else None

    def assert_origin(self):
        if repository_name(self.repo).lower()!=self.name.lower():
            raise ValueError("Origin changed during delivery")

    def refresh(self):
        self.assert_origin()
        command(self.repo,"-c","core.sshCommand=ssh -o BatchMode=yes -o ConnectTimeout=15","fetch",self.remote_url,
                "refs/heads/dev:refs/remotes/origin/dev","refs/heads/main:refs/remotes/origin/main")

    def pull(self, number: int):
        return self.api(f"pulls/{number}")

    def find_pull(self, branch: str):
        owner = self.name.split("/")[0]
        items = self.api(f"pulls?state=all&head={owner}:{branch}&base=dev&per_page=100")
        if len(items)>1:
            open_items = [item for item in items if item["state"]=="open"]
            if len(open_items)!=1:
                raise ValueError("Ambiguous task PR history")
            return open_items[0]
        return items[0] if items else None

    def create_pull(self, task: dict):
        # On an ambiguous transport failure, resume searches this branch before retrying.
        return self.api("pulls","POST",{"base":"dev","head":task["branch"],
            "title":task.get("commit_title","build: "+task["task_id"]),
            "body":task.get("pr_body",task["goal"]+"\n\nValidation: committed-candidate make verify. Main merging remains human."),"draft":False})

    def latest_run(self, sha: str, event: str, number: int | None = None):
        items = self.api(f"actions/runs?head_sha={sha}&event={event}&per_page=100")["workflow_runs"]
        items = [item for item in items if item["path"].split("@")[0]==".github/workflows/ci.yml"]
        if number is not None:
            associated = [item for item in items if any(p["number"]==number for p in item.get("pull_requests",[]))]
            detached = [item for item in items if not item.get("pull_requests")]
            # GitHub can drop pull_requests from a run after its PR is merged.
            # Recover the association only from a live, exact, same-repository PR.
            if detached:
                pull = self.pull(number)
                head = pull.get("head",{})
                if (pull.get("merged") and pull.get("base",{}).get("ref")=="dev"
                        and head.get("sha")==sha
                        and (head.get("repo") or {}).get("full_name","").lower()==self.name.lower()):
                    associated += [item for item in detached if item.get("head_sha")==sha
                        and item.get("head_branch")==head.get("ref")
                        and (item.get("head_repository") or {}).get("full_name","").lower()==self.name.lower()]
            items = associated
        if not items:
            return None
        run = max(items,key=lambda item:(item["id"],item["run_attempt"]))
        jobs, page = [], 1
        while True:
            response = self.api(f"actions/runs/{run['id']}/jobs?filter=latest&per_page=100&page={page}")
            jobs += response["jobs"]
            if len(response["jobs"])<100:
                break
            page += 1
        return {"workflow_run":run,"jobs":jobs}

    def merge(self, number: int, sha: str):
        return self.api(f"pulls/{number}/merge","PUT",{"sha":sha,"merge_method":"merge"})

    def push(self, branch: str):
        self.assert_origin()
        command(self.repo,"-c","core.sshCommand=ssh -o BatchMode=yes -o ConnectTimeout=15","push",self.remote_url,"refs/heads/"+branch+":refs/heads/"+branch)

    def remote_head(self, branch: str):
        self.assert_origin()
        output = command(self.repo,"ls-remote","--heads",self.remote_url,"refs/heads/"+branch).decode().strip()
        return output.split()[0] if output else None

    def delete_remote(self, branch: str, head: str):
        self.assert_origin()
        command(self.repo,"push","--force-with-lease=refs/heads/"+branch+":"+head,
                self.remote_url,":refs/heads/"+branch)

    def diagnostics(self, snapshot: dict, directory: Path):
        directory.mkdir(parents=True,exist_ok=True)
        write_json(directory/"ci.json",snapshot)
        failed = [job for job in snapshot["jobs"] if job.get("conclusion")=="failure"]
        for job in failed:
            result = subprocess.run(["gh","run","view",str(snapshot["workflow_run"]["id"]),"--repo",self.name,
                                     "--job",str(job["id"]),"--log-failed"],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=False,timeout=60)
            (directory/(job["name"]+".log")).write_bytes(result.stdout)
        # Per-command test failures are deliberately kept as artifacts by the verifier.
        subprocess.run(["gh","run","download",str(snapshot["workflow_run"]["id"]),"--repo",self.name,
                        "--dir",str(directory/"artifacts")],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False,timeout=60)


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
        return self.store.mutate(self.task_id,lambda value:{**value,**fields},action)

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
        if task.get("verified_head")==head:
            try:
                validate_evidence(Path(task["verification_report"]),root,required_ids("all"))
                return
            except (ValueError,OSError,KeyError):
                pass
        self.update("verification-intent",status="VERIFYING",head_sha=head)
        argv = task.get("verify_command") or [sys.executable,"scripts/engineering.py","verify"]
        posix = str(root).replace("\\","/")
        if re.match(r"^[A-Za-z]:/",posix):
            posix = "/mnt/"+posix[0].lower()+posix[2:]
        argv = [arg.replace("{worktree}",str(root)).replace("{worktree_posix}",posix) for arg in argv]
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

    def deliver(self) -> dict:
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
            self.update("merge-recovered",status="MERGED",delivered_at=existing["merged_at"],delivery={
                "number":existing["number"],"html_url":existing["html_url"],"merged":True,
                "head_sha":existing["head"]["sha"],"merge_commit_sha":existing["merge_commit_sha"],"merged_at":existing["merged_at"]})
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
        if names:
            staged = git(root,"diff","--cached","--name-only").decode().splitlines()
            if set(staged)-set(names):
                raise ValueError("Unrelated staged changes remain")
            self.update("commit-intent",status="COMMITTING",previous_head=git(root,"rev-parse","HEAD").decode().strip(),staged_paths=names)
            command(root,"-c","core.safecrlf=false","add","--",*names)
            command(root,"-c","core.safecrlf=false","diff","--cached","--check")
            command(root,"commit","-m",task.get("commit_title","build: "+task["task_id"]),"-m","Tenon-Task: "+task["task_id"])
        head = git(root,"rev-parse","HEAD").decode().strip()
        if head==task["base_sha"]:
            raise ValueError("No task change to deliver")
        self.update("candidate-observed",head_sha=head)
        self.backend.refresh()
        base = git(root,"rev-parse","origin/dev").decode().strip()
        if not self._ancestor(base,head):
            command(root,"merge","--no-edit","origin/dev")
            head = git(root,"rev-parse","HEAD").decode().strip()
            self.update("dev-integrated",base_sha=base,head_sha=head)
        self.verification(self.task(),root,head)
        remote = self.backend.remote_head(task["branch"])
        if remote!=head:
            if remote and not self._ancestor(remote,head):
                raise ValueError("Remote task branch contains other work")
            self.backend.push(task["branch"])
        self.update("push-observed",pushed_head=head)
        pull = self.backend.find_pull(task["branch"])
        if not pull:
            pull = self.backend.create_pull(self.task())
        if pull.get("merged"):
            self.update("existing-merge-observed",delivery={"number":pull["number"],"html_url":pull["html_url"],"merged":True,
                "head_sha":pull["head"]["sha"],"merge_commit_sha":pull["merge_commit_sha"],"merged_at":pull["merged_at"]},status="MERGED")
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
        self.update("merge-intent",status="MERGING",pr_ci={"run_id":snapshot["workflow_run"]["id"],"head_sha":head,"conclusion":"success"})
        self.backend.merge(pull["number"],head)
        merged = self.backend.pull(pull["number"])
        if not merged.get("merged"):
            raise ValueError("Merge is not confirmed")
        self.update("merge-confirmed",status="MERGED",delivered_at=merged["merged_at"],delivery={
            "number":merged["number"],"html_url":merged["html_url"],"merged":True,"head_sha":head,
            "merge_commit_sha":merged["merge_commit_sha"],"merged_at":merged["merged_at"],
            "pr_ci":{"run_id":snapshot["workflow_run"]["id"],"conclusion":"success"}})
        return self.after_merge()

    def after_merge(self) -> dict:
        task = self.task()
        self.backend.refresh()
        tip = git(self.store.repo,"rev-parse","origin/dev").decode().strip()
        pull = self.backend.pull(task["delivery"]["number"])
        head = expected_head(task)
        if not pull.get("merged") or pull["base"]["ref"]!="dev" or pull["head"]["sha"]!=head or not self._ancestor(head,tip):
            raise ValueError("Task merge/ancestry no longer matches")
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
        self.update("dev-ci-green",status="DEV_GREEN",green_dev_sha=tip,dev_ci={"run_id":snapshot["workflow_run"]["id"],"head_sha":tip,"conclusion":"success"})
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
            if task.get("managed_identity"):
                return {"status":"NEEDS_APP_ARCHIVE","root":task["managed_identity"],"controller":str(controller),"repo":str(primary),"task_id":self.task_id}
            command(primary,"worktree","remove",str(root))
        return self.finalize()

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
        if root.exists() or any(item["path"]==root for item in worktrees(self.store.repo)):
            raise ValueError("Worktree removal is not confirmed")
        verify_task_archive(self.store,task)
        self.backend.refresh()
        tip = git(self.store.repo,"rev-parse","origin/dev").decode().strip()
        if not self._ancestor(expected_head(task),tip) or not ci_green(self.backend.latest_run(tip,"push"),tip,"push"):
            raise ValueError("Green dev ancestry changed before finalization")
        if self.backend.remote_head(task["branch"]):
            raise ValueError("Remote branch still exists")
        if any(item.get("branch")=="refs/heads/"+task["branch"] for item in worktrees(self.store.repo)):
            raise ValueError("Task branch is checked out elsewhere")
        ref = "refs/heads/"+task["branch"]
        result = subprocess.run(["git","-C",str(self.store.repo),"rev-parse","--verify",ref],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
        if result.returncode==0:
            if result.stdout.decode().strip()!=expected_head(task):
                raise ValueError("Local branch advanced; conditional deletion refused")
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("deliver","resume","cleanup","finalize-cleanup","status"))
    parser.add_argument("--repo",type=Path,default=ROOT)
    parser.add_argument("--id",required=True)
    args = parser.parse_args()
    try:
        lifecycle = Lifecycle(args.repo,args.id)
        with FileLease(lifecycle.store.root/"locks"/(args.id+"-controller.lock")):
            if args.action=="status":
                result = lifecycle.task()
            elif args.action in {"deliver","resume"}:
                result = lifecycle.deliver()
            elif args.action=="finalize-cleanup":
                result = lifecycle.finalize()
            else:
                result = lifecycle.cleanup()
        print(json.dumps(result,indent=2,ensure_ascii=False))
        return 2 if result.get("status") in {"NEEDS_ATTENTION","DEV_CI_FAILED"} else 0
    except (ValueError,OSError,RuntimeError,subprocess.SubprocessError) as error:
        if "lifecycle" in locals():
            try:
                lifecycle.update("operation-blocked",last_error=str(error),blocked_at=utc_now())
            except (OSError,ValueError):
                pass
        print(json.dumps({"status":"BLOCKED","reason":str(error)}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
