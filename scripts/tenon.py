#!/usr/bin/env python3
"""Source-pinned engineering entry; task workspaces are disposable, this entry is not."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from engineering_common import ROOT, git, source_state, utc_now, write_json
from task_archive import deploy_controller, worktrees
from task_lifecycle import GitHub, ci_green
from task_store import FileLease, TaskStore, identifier


def capabilities() -> dict:
    return {"python": sys.version.split()[0], "platform": os.name,
            "tools": {name: shutil.which(name) for name in ("git", "make", "wsl", "gh")}}


def start_workspace(repo: Path, task_id: str, topic: str, goal: str, allowed: list[str],
                    checks: list[str], dependencies: list[str] | None = None,
                    verify_command: list[str] | None = None, local_only: bool = False,
                    workspace_root: Path | None = None, backend=None) -> dict:
    from task import start

    task_id = identifier(task_id)
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", topic) or len(topic) > 48:
        raise ValueError("Topic must be a short lowercase hyphenated name")
    if not goal.strip() or not allowed or not checks:
        raise ValueError("A goal, bounded file scope and acceptance commands are required")
    if any(Path(item).is_absolute() or ".." in item.replace("\\", "/").split("/") for item in allowed):
        raise ValueError("File scope must stay inside the repository")
    if verify_command is not None and (not isinstance(verify_command, list) or not verify_command
            or any(not isinstance(item, str) or not item for item in verify_command)):
        raise ValueError("Verification command must be a nonempty argv array")
    store = TaskStore(repo)
    primary = worktrees(repo)[0]["path"]
    base_root = (workspace_root or primary.parent / ".tenon-worktrees" / store.repo_id).resolve()
    target = (base_root / task_id).resolve()
    if (target.parent != base_root or target == primary or primary.is_relative_to(target)
            or target.is_relative_to(store.common)):
        raise ValueError("Workspace path overlaps protected repository storage")
    branch = "dev-" + task_id + "-" + topic
    backend = backend or GitHub(repo)
    contract = {"task_id": task_id, "topic": topic, "goal": goal, "allowed_paths": allowed,
                "checks": checks, "dependencies": dependencies or [], "verify_command": verify_command,
                "delivery_mode": "local-only" if local_only else "auto"}
    with FileLease(store.root / "locks/workspace-start.lock"):
        if store.path(task_id).exists():
            raise FileExistsError("Task exists; inspect or resume it")
        backend.refresh()
        base = git(repo, "rev-parse", "origin/dev").decode().strip()
        intent = store.root / "operations/workspace-setup" / (task_id + ".json")
        prior = json.loads(intent.read_text(encoding="utf-8")) if intent.exists() else None
        existing = subprocess.run(["git", "-C", str(repo), "show-ref", "--verify", "--quiet", "refs/heads/" + branch],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if prior:
            if (prior.get("state") != "CREATING" or prior.get("contract") != contract
                    or prior.get("repository") != backend.name or prior.get("worktree") != str(target)
                    or prior.get("branch") != branch or prior.get("base_sha") != base):
                raise ValueError("Interrupted setup differs from the requested contract or current dev; inspect its intent")
            if existing or target.exists():
                matches = [item for item in worktrees(repo) if item["path"] == target]
                if (not existing or not target.is_dir() or len(matches) != 1 or "locked" in matches[0]
                        or matches[0].get("branch") != "refs/heads/" + branch or matches[0].get("HEAD") != base
                        or source_state(target)["dirty"]):
                    raise ValueError("Interrupted workspace has changed; preserve it for inspection")
        elif existing or target.exists():
            raise FileExistsError("Workspace/branch exists without this setup intent")
        if not prior:
            write_json(intent, {"schema_version": 1, "task_id": task_id, "repository": backend.name,
                               "base_sha": base, "branch": branch, "worktree": str(target),
                               "contract": contract, "state": "CREATING", "created_at": utc_now()})
        if not existing:
            base_root.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", branch, str(target), base],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        value = start(task_id, goal, allowed, checks, dependencies or [], target,
                      verify_command=verify_command, delivery_mode="local-only" if local_only else "auto")
        value = store.mutate(task_id, lambda item: {**item, "workspace_backend": "git",
            "workspace_root": str(base_root), "environment": capabilities()}, "workspace-registered")
        write_json(intent, {"schema_version": 1, "task_id": task_id, "state": "REGISTERED",
                           "branch": branch, "worktree": str(target), "base_sha": base, "contract": contract})
        return value


def verification_argv(value: dict, root: Path) -> list[str]:
    argv = value.get("verify_command") or [sys.executable, "scripts/engineering.py", "verify"]
    posix = str(root).replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", posix):
        posix = "/mnt/" + posix[0].lower() + posix[2:]
    return [part.replace("{worktree}", str(root)).replace("{worktree_posix}", posix) for part in argv]


def install(repo: Path, backend=None) -> dict:
    """Deploy only the current, fully green dev tree, independent of the checkout branch."""
    store = TaskStore(repo)
    backend = backend or GitHub(repo)
    backend.refresh()
    commit = git(repo, "rev-parse", "origin/dev").decode().strip()
    if not ci_green(backend.latest_run(commit, "push"), commit, "push"):
        raise ValueError("Install requires the current dev CI to be fully successful")
    if "scripts/tenon.py" not in git(repo, "ls-tree", "-r", "--name-only", commit, "scripts/tenon.py").decode().splitlines():
        raise ValueError("Current green dev has no stable entry yet")
    with FileLease(store.root / "locks/entry-install.lock"):
        controller = deploy_controller(store, commit)
        directory = controller.parent.parent
        entry = directory / "scripts/tenon.py"
        primary = worktrees(repo)[0]["path"]
        binary = store.root / "bin"
        binary.mkdir(parents=True, exist_ok=True)
        previous = json.loads((binary / "installation.json").read_text(encoding="utf-8")) if (binary / "installation.json").exists() else None
        metadata = {"schema_version": 1, "repo_id": store.repo_id, "repo": str(primary),
                    "commit": commit, "controller": str(directory), "installed_at": utc_now(),
                    "previous_commit": previous.get("commit") if previous and previous.get("commit") != commit
                    else (previous or {}).get("previous_commit")}
        # The small launcher validates every deployed script before executing anything.
        launcher = '''#!/usr/bin/env python3
import hashlib, json, os, subprocess, sys
from pathlib import Path
binary = Path(__file__).resolve().parent
try:
    config = json.loads((binary / "installation.json").read_text(encoding="utf-8"))
    root = Path(config["controller"])
    manifest = json.loads((root / "controller.json").read_text(encoding="utf-8"))
    if manifest["commit"] != config["commit"]:
        raise ValueError("Controller version differs")
    for item in manifest["files"]:
        path = (root / item["path"]).resolve()
        if not path.is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Controller integrity check failed")
    if "--repo" in sys.argv[1:] or any(item.startswith("--repo=") for item in sys.argv[1:]):
        raise ValueError("This installed entry is bound to its registered repository")
    raise SystemExit(subprocess.run([sys.executable, str(root / "scripts/tenon.py"), "--repo", config["repo"], *sys.argv[1:]]).returncode)
except (OSError, ValueError, KeyError) as error:
    print(json.dumps({"status": "BLOCKED", "reason": str(error)}))
    raise SystemExit(2)
'''
        temporary = binary / "tenon.py.tmp"
        temporary.write_text(launcher, encoding="utf-8")
        temporary.replace(binary / "tenon.py")
        if os.name == "nt":
            (binary / "tenon.cmd").write_text('@echo off\n"' + sys.executable + '" "%~dp0tenon.py" %*\n', encoding="utf-8")
        write_json(binary / "installation.json", metadata)
        return {"status": "INSTALLED", "entry": str(binary / "tenon.py"), "commit": commit,
                "repo": str(primary), "capabilities": capabilities()}


def task_status(repo: Path, task_id: str) -> dict:
    store = TaskStore(repo)
    value = store.load(task_id)
    root = Path(value["worktree"])
    if root.exists():
        from task import scope_errors
        value["scope_errors"] = scope_errors(value, root)
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repo", type=Path, default=ROOT)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("install")
    commands.add_parser("doctor")
    tasks = commands.add_parser("task").add_subparsers(dest="task_command", required=True)
    start = tasks.add_parser("start")
    start.add_argument("--id", required=True)
    start.add_argument("--topic", required=True)
    start.add_argument("--goal", required=True)
    start.add_argument("--allow", action="append", required=True)
    start.add_argument("--check", action="append", required=True)
    start.add_argument("--dependency", action="append", default=[])
    start.add_argument("--verify-command-json")
    start.add_argument("--local-only", action="store_true")
    start.add_argument("--workspace-root", type=Path, help="Explicit workspace root, primarily for controlled environments")
    for name in ("status", "resume", "verify", "submit", "deliver"):
        command = tasks.add_parser(name)
        command.add_argument("--id", required=True)
    reconciliation = commands.add_parser("reconcile")
    reconciliation.add_argument("--all", action="store_true", required=True)
    reconciliation.add_argument("--once", action="store_true", required=True)
    reconciliation.add_argument("--apply", action="store_true")
    reconciliation.add_argument("--json", action="store_true")
    worker = commands.add_parser("worker").add_subparsers(dest="worker_command", required=True)
    for name in ("install", "remove", "status", "disable"):
        worker.add_parser(name)
    enable = worker.add_parser("enable")
    enable.add_argument("--acceptance-task", required=True)
    run = worker.add_parser("run")
    run.add_argument("--max-seconds", type=float, default=900)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    repo = args.repo.resolve()
    try:
        if args.command == "install":
            value = install(repo)
        elif args.command == "doctor":
            value = {"status": "READY" if shutil.which("git") else "BLOCKED", "repo": str(repo),
                     "capabilities": capabilities(), "dev_sha": git(repo, "rev-parse", "origin/dev").decode().strip()}
        elif args.command == "reconcile":
            from task_queue import reconcile
            value = reconcile(repo, apply=args.apply)
        elif args.command == "worker":
            from task_worker import configuration, run, scheduler, set_enabled
            if args.worker_command in {"enable", "disable"}:
                value = set_enabled(repo, args.worker_command == "enable", getattr(args, "acceptance_task", None))
            elif args.worker_command == "run":
                value = run(repo, args.max_seconds)
            elif args.worker_command == "status":
                value = {"configuration": configuration(TaskStore(repo)), "scheduler": scheduler(repo, "status") if os.name == "nt" else None}
            else:
                value = scheduler(repo, args.worker_command)
        elif args.task_command == "start":
            value = start_workspace(repo, args.id, args.topic, args.goal, args.allow, args.check,
                args.dependency, json.loads(args.verify_command_json) if args.verify_command_json else None,
                args.local_only, args.workspace_root)
        elif args.task_command == "status":
            value = task_status(repo, args.id)
        elif args.task_command == "verify":
            value = TaskStore(repo).load(args.id)
            root = Path(value["worktree"])
            return subprocess.run(verification_argv(value, root), cwd=root).returncode
        elif args.task_command in {"submit", "deliver"}:
            from task_lifecycle import Lifecycle
            from task_queue import submit
            from task_worker import launch
            if args.task_command == "deliver":
                store = TaskStore(repo)
                with FileLease(store.root / "locks/integration.lock"), FileLease(store.root / "locks" / (args.id + "-controller.lock")):
                    result = Lifecycle(repo, args.id).deliver()
                if result.get("status") in {"NEEDS_ATTENTION", "DEV_CI_FAILED", "CLEANED"}:
                    print(json.dumps(result, indent=2))
                    return 0 if result["status"] == "CLEANED" else 2
            value = {"queue": submit(repo, args.id), "worker": launch(repo)}
        else:
            return subprocess.run([sys.executable, str(Path(__file__).with_name("task_lifecycle.py")),
                "resume", "--repo", str(repo), "--id", args.id], cwd=repo).returncode
        print(json.dumps(value, indent=2, ensure_ascii=False))
        return 2 if value.get("status") == "BLOCKED" else 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
