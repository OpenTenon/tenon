#!/usr/bin/env python3
"""Host Git/GitHub and managed-workspace adapters; lifecycle policy stays in the controller."""
from __future__ import annotations
import json
import os
import re
import subprocess
from pathlib import Path
from engineering_common import git, write_json


def repository_name(repo: Path) -> str:
    url = git(repo,"remote","get-url","origin").decode().strip()
    match = re.fullmatch(r"(?:git@github\.com:|https://github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?",url)
    if not match:
        raise ValueError("Delivery requires an explicitly configured GitHub origin")
    return match[1]


def command(repo: Path, *args: str) -> bytes:
    environment = os.environ.copy()
    environment.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    return subprocess.check_output(["git", "-c", "credential.interactive=false", "-c", "core.askPass=",
        "-c", "core.sshCommand=ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=2",
        "-C", str(repo), *args], stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, env=environment, timeout=120)


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
        run = self._selected_run(sha,event,number)
        if run is None:
            return None
        run_id,attempt = run.get("id"),run.get("run_attempt")
        if type(run_id) is not int or run_id <= 0 or type(attempt) is not int or attempt <= 0:
            raise ValueError("CI run/attempt identity is missing or invalid")
        jobs, page = [], 1
        while True:
            response = self.api(f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100&page={page}")
            jobs += response["jobs"]
            if len(response["jobs"])<100:
                break
            page += 1
        if run.get("status")=="completed" and run.get("conclusion")=="success":
            # An exact a1 endpoint must not authorize a1 after a2 started while
            # the jobs were loading. Check the current attempt and the same
            # latest-run/PR association policy again, only for possible success.
            current = self.api(f"actions/runs/{run_id}")
            if (current.get("id"),current.get("run_attempt")) != (run_id,attempt):
                return None
            latest = self._selected_run(sha,event,number)
            if latest is None or (latest.get("id"),latest.get("run_attempt")) != (run_id,attempt):
                return None
            keys = ("head_sha","event","path","status","conclusion")
            if any(current.get(key)!=latest.get(key) for key in keys):
                return None
            run = latest
        return {"workflow_run":run,"jobs":jobs}

    def _selected_run(self, sha: str, event: str, number: int | None):
        """The existing event, workflow and live same-repository PR selection."""
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
        return max(items,key=lambda item:(item["id"],item["run_attempt"]))

    def merge(self, number: int, sha: str):
        return self.api(f"pulls/{number}/merge","PUT",{"sha":sha,"merge_method":"merge"})

    def push(self, branch: str, head: str):
        self.assert_origin()
        command(self.repo,"push",self.remote_url,head+":refs/heads/"+branch)

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


def directory_delete_ready(root: Path) -> bool:
    """Probe Windows sharing permissions without changing or deleting the directory."""
    if os.name!="nt" or not root.is_dir():
        return True
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,
                                   wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # DELETE access, full sharing, OPEN_EXISTING and directory semantics only.
    # No DELETE_ON_CLOSE, disposition change or privilege adjustment is used.
    handle = kernel.CreateFileW(str(root),0x10000,7,None,3,0x02000000,None)
    if handle==ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        if error in {2,3}:
            return True
        if error==32:
            return False
        raise ctypes.WinError(error)
    kernel.CloseHandle(handle)
    return True


class WorkspaceBackend:
    """Git owns autonomous workspaces; the application owns managed archive records."""

    @staticmethod
    def remove(primary: Path, task: dict, controller: Path, native_confirmed: bool = False, runner=command):
        if task.get("managed_identity") and not native_confirmed:
            return {"status": "NEEDS_APP_ARCHIVE", "root": task["managed_identity"],
                    "controller": str(controller), "repo": str(primary), "task_id": task["task_id"]}
        root = Path(task["worktree"])
        def busy():
            error = OSError("Workspace directory has an active sharing lease")
            error.winerror = 32
            return error
        if not directory_delete_ready(root):
            raise busy()
        try:
            runner(primary, "worktree", "remove", task["worktree"])
        except subprocess.CalledProcessError:
            if not directory_delete_ready(root):
                raise busy()
            raise
        return None

    @staticmethod
    def native_receipt(task: dict, receipt: Path, primary: Path) -> dict:
        identity = task.get("managed_identity")
        record = json.loads(receipt.read_text(encoding="utf-8"))
        payload = record.get("payload") if isinstance(record, dict) else None
        workspace = payload.get("worktree") if isinstance(payload, dict) else None
        if (not identity or not isinstance(workspace, dict) or record.get("attachmentType") != "archived_worktree"
                or record.get("identityKey") != identity or workspace.get("root") != identity
                or workspace.get("hostId") != task.get("managed_host_id", "local")
                or not isinstance(workspace.get("sourceGitRoot"), str) or not workspace["sourceGitRoot"]
                or Path(workspace["sourceGitRoot"]).resolve() != primary):
            raise ValueError("Native archive receipt does not match this task/repository/host")
        return record
