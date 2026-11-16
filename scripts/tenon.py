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
import shlex
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
                    workspace_root: Path | None = None, backend=None, *, kind: str | None = None,
                    role: str = "writer", read_scope: list[str] | None = None,
                    inputs: list[str] | None = None, outputs: list[str] | None = None) -> dict:
    from contextlib import nullcontext
    from task import start
    from task_contract import READERS, allocation_lock, preflight

    task_id = identifier(task_id)
    if role in READERS:
        raise ValueError("Reader roles do not create disposable workspaces. Register a local-only analysis with scripts/task.py start --kind engineering --role "
                         + role + " on the existing frozen source snapshot; it owns no branch/worktree")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", topic) or len(topic) > 48:
        raise ValueError("Topic must be a short lowercase hyphenated name")
    if (not isinstance(goal, str) or not goal.strip() or not isinstance(allowed, list)
            or not isinstance(checks, list) or not checks or (not allowed and role not in READERS)):
        raise ValueError("A goal, bounded file scope and acceptance commands are required")
    if any(not isinstance(item, str) or Path(item).is_absolute() or ".." in item.replace("\\", "/").split("/") for item in allowed):
        raise ValueError("File scope must stay inside the repository")
    if kind not in {None, "engineering", "general", "legacy"}:
        raise ValueError("Unknown task kind")
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
    with FileLease(store.root / "locks/workspace-start.lock"):
        if store.path(task_id).exists() or store.path(task_id).with_suffix(".jsonl").exists():
            raise FileExistsError("Task exists; inspect or resume it")
        backend.refresh()
        base = git(repo, "rev-parse", "origin/dev").decode().strip()
        intent = store.root / "operations/workspace-setup" / (task_id + ".json")
        prior = json.loads(intent.read_text(encoding="utf-8")) if intent.exists() else None
        from host_profile import bind_verifier
        verifier = bind_verifier(repo, verify_command, prior.get("contract") if prior else None)
        requested = "general" if kind == "legacy" else kind
        if prior and "kind" not in prior.get("contract", {}):
            if requested not in {None, "general"}:
                raise ValueError("Existing setup has an unclassified legacy contract; use general or a new task ID")
            effective_kind = None
        else:
            effective_kind = requested or ((prior or {}).get("contract", {}).get("kind")) or "engineering"
        if effective_kind not in {None, "engineering", "general"}:
            raise ValueError("Existing setup task kind is unsupported")
        if effective_kind != "engineering" and (role != "writer" or read_scope is not None or inputs is not None or outputs is not None):
            raise ValueError("Role/read/input/output contracts require engineering task kind")
        manager = allocation_lock(store) if effective_kind == "engineering" else nullcontext(None)
        with manager as ticket:
            prepared = preflight(store, task_id, allowed, checks, dependencies or [], role=role,
                read_scope=read_scope, inputs=inputs, outputs=outputs, base_sha=base, allocation=ticket) if effective_kind == "engineering" else None
            selected_allowed = prepared["allowed_paths"] if prepared else allowed
            selected_checks = prepared["checks"] if prepared else checks
            selected_dependencies = prepared["dependencies"] if prepared else dependencies or []
            mode = "local-only" if local_only or (prepared and role in READERS) else "auto"
            contract = {"task_id": task_id, "topic": topic, "goal": goal, "allowed_paths": selected_allowed,
                        "checks": selected_checks, "dependencies": selected_dependencies, "verify_command": verifier["verify_command"],
                        "delivery_mode": mode, **{key: value for key, value in verifier.items() if key != "verify_command"}}
            if effective_kind is not None:
                contract["kind"] = effective_kind
            if prepared:
                contract.update(role=role, read_scope=prepared["read_scope"], inputs=prepared["inputs"], outputs=prepared["outputs"])
            existing = subprocess.run(["git", "-C", str(repo), "show-ref", "--verify", "--quiet", "refs/heads/" + branch],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            if prior:
                if (prior.get("state") != "CREATING" or prior.get("contract") != contract
                        or prior.get("repository") != backend.name or prior.get("worktree") != str(target)
                        or prior.get("branch") != branch):
                    raise ValueError("Interrupted setup differs from the requested contract or current dev; inspect its intent")
                if existing or target.exists():
                    matches = [item for item in worktrees(repo) if item["path"] == target]
                    old = prior.get("base_sha")
                    transition = prior.get("base_transition", {})
                    admissible = {old}
                    if transition.get("from") == old:
                        admissible.add(transition.get("to"))
                    if (not existing or not target.is_dir() or len(matches) != 1 or "locked" in matches[0]
                            or matches[0].get("branch") != "refs/heads/" + branch or matches[0].get("HEAD") not in admissible
                            or source_state(target)["dirty"]):
                        raise ValueError("Interrupted workspace has changed; preserve it for inspection")
                    actual = matches[0]["HEAD"]
                    if actual != base:
                        if subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", actual, base],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode:
                            raise ValueError("Interrupted startup base is not an ancestor of current dev")
                        prior["base_transition"] = {"from": old, "to": base, "observed_head": actual,
                                                    "prepared_at": utc_now()}
                        write_json(intent, prior)
                        subprocess.run(["git", "-C", str(target), "merge", "--ff-only", base],
                                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        if git(target, "rev-parse", "HEAD").decode().strip() != base or source_state(target)["dirty"]:
                            raise ValueError("Startup changed while fast-forwarding; preserve it")
                    if old != base:
                        prior.update(base_sha=base, base_history=prior.get("base_history", []) + [{"from": old, "to": base}])
                        write_json(intent, prior)
                elif prior.get("base_sha") != base:
                    prior.update(base_sha=base, base_history=prior.get("base_history", []) + [{"from": prior.get("base_sha"), "to": base}])
                    write_json(intent, prior)
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
            options = {"kind": effective_kind}
            if prepared:
                options.update(role=role, read_scope=prepared["read_scope"], inputs=prepared["inputs"], outputs=prepared["outputs"], allocation=ticket)
            value = start(task_id, goal, selected_allowed, selected_checks, selected_dependencies, target,
                          verify_command=verifier["verify_command"], delivery_mode=mode, **options)
            value = store.mutate(task_id, lambda item: {**item, "workspace_backend": "git",
                "workspace_root": str(base_root), "environment": capabilities(),
                **{key: value for key, value in verifier.items() if key != "verify_command"}}, "workspace-registered")
            current_intent = json.loads(intent.read_text(encoding="utf-8"))
            write_json(intent, {**current_intent, "schema_version": 1, "task_id": task_id, "state": "REGISTERED",
                               "branch": branch, "worktree": str(target), "base_sha": base, "contract": contract})
            return value


def verification_argv(value: dict, root: Path) -> list[str]:
    from host_profile import validate_task_inputs
    validate_task_inputs(value)
    argv = value.get("verify_command") or [sys.executable, "scripts/engineering.py", "verify"]
    posix = str(root).replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", posix):
        posix = "/mnt/" + posix[0].lower() + posix[2:]
    return [part.replace("{worktree}", str(root)).replace("{worktree_posix}", posix) for part in argv]


def install_git_alias(repo: Path, launcher: Path, previous: dict | None, requested: bool) -> tuple[dict | None,str]:
    """Own only the exact local alias we installed; never shadow an unrelated alias."""
    def values(local):
        result = subprocess.run(["git","-C",str(repo),"config",*(["--local"] if local else []),
            "--null","--get-all","alias.tenon"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
        if result.returncode not in {0,1}:
            raise ValueError("Cannot inspect repository Git alias")
        return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]
    local, effective = values(True),values(False)
    old = (previous or {}).get("git_alias")
    owned = old if (isinstance(old,dict) and old.get("scope")=="local" and old.get("key")=="alias.tenon"
        and local==[old.get("value")] and effective and effective[-1]==old.get("value")) else None
    if not requested:
        return owned,"OWNED_RETAINED" if owned else "NOT_REQUESTED"
    if effective and (not owned or any(item!=owned["value"] for item in effective)):
        return owned,"PRESERVED_EXISTING"
    command = "!f() { exec "+shlex.quote(Path(sys.executable).as_posix())+" "+shlex.quote(launcher.as_posix())+' "$@"; }; f'
    if local != [command]:
        subprocess.run(["git","-C",str(repo),"config","--local","alias.tenon",command],check=True,
                       stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if values(True) != [command]:
        raise ValueError("Repository alias installation was not confirmed")
    return {"scope":"local","key":"alias.tenon","value":command},"INSTALLED"


def audit(repo: Path, ref: str, published_rules: bool = False) -> dict:
    from host_profile import python_context
    from ci_contract import audit_source
    from github_rules import audit as audit_rules
    from task_backends import repository_name
    commit = git(repo,"rev-parse","--verify","--end-of-options",ref+"^{commit}").decode().strip()
    with python_context(repo):
        source = audit_source(repo,commit)
        published = audit_rules(repository_name(repo),repo,commit) if published_rules else None
    statuses = [source["status"]]+([published["status"]] if published else [])
    return {"schema_version":1,"kind":"engineering-audit","requested_ref":ref,"commit":commit,
            "status":"PASS" if all(value=="PASS" for value in statuses) else "FAIL",
            "source_audit":source,"published_rules":published}


def install(repo: Path, backend=None, *, git_alias: bool = False) -> dict:
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
        owned_alias, alias_status = install_git_alias(primary,binary/"tenon.py",previous,git_alias)
        if owned_alias:
            metadata["git_alias"] = owned_alias
        write_json(binary / "installation.json", metadata)
        return {"status": "INSTALLED", "entry": str(binary / "tenon.py"), "commit": commit,
                "repo": str(primary), "capabilities": capabilities(),"git_alias":alias_status}


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
    install_command = commands.add_parser("install")
    install_command.add_argument("--git-alias",action="store_true",help="Install an owned per-repository git tenon shortcut if the name is free")
    commands.add_parser("doctor")
    host = commands.add_parser("host").add_subparsers(dest="host_command",required=True)
    configure = host.add_parser("configure")
    configure.add_argument("--verify-command-json",required=True)
    configure.add_argument("--input",type=Path,action="append",default=[])
    configure.add_argument("--python-path",type=Path,action="append",default=[])
    auditing = commands.add_parser("audit")
    auditing.add_argument("--ref",default="origin/dev")
    auditing.add_argument("--published-rules",action="store_true")
    tasks = commands.add_parser("task").add_subparsers(dest="task_command", required=True)
    start = tasks.add_parser("start")
    start.add_argument("--id", required=True)
    start.add_argument("--topic", required=True)
    start.add_argument("--goal", required=True)
    start.add_argument("--allow", action="append", default=[])
    start.add_argument("--check", action="append", required=True)
    start.add_argument("--dependency", action="append", default=[])
    start.add_argument("--verify-command-json")
    start.add_argument("--local-only", action="store_true")
    start.add_argument("--workspace-root", type=Path, help="Explicit workspace root, primarily for controlled environments")
    start.add_argument("--kind", choices=("engineering", "general", "legacy"), help="New tasks default to engineering; untagged interrupted setups retain their original policy")
    start.add_argument("--role", choices=("writer", "integrator", "coordinator", "reviewer"), default="writer")
    start.add_argument("--read-allow", action="append")
    start.add_argument("--input", action="append")
    start.add_argument("--output", action="append")
    lease = tasks.add_parser("lease")
    lease.add_argument("--id", required=True)
    lease.add_argument("--state", choices=("active", "paused", "completed"), required=True)
    lease.add_argument("--role", choices=("writer", "integrator", "coordinator", "reviewer"))
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
            value = install(repo,git_alias=args.git_alias)
        elif args.command == "host":
            from host_profile import configure,load
            configure(repo,json.loads(args.verify_command_json),args.input,args.python_path)
            value = {"status":"CONFIGURED",**load(repo)}
        elif args.command == "audit":
            value = audit(repo,args.ref,args.published_rules)
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
                args.local_only, args.workspace_root, kind=args.kind, role=args.role,
                read_scope=args.read_allow, inputs=args.input, outputs=args.output)
        elif args.task_command == "lease":
            from task_contract import set_lease_state
            store = TaskStore(repo)
            owner = store.load(args.id)["owner_id"]
            value = set_lease_state(store, args.id, args.state, owner_id=owner, role=args.role)
        elif args.task_command == "status":
            value = task_status(repo, args.id)
        elif args.task_command == "verify":
            value = TaskStore(repo).load(args.id)
            root = Path(value["worktree"])
            if value.get("kind") == "engineering" and value.get("role") in {"writer", "integrator"}:
                from task_lifecycle import Lifecycle
                from types import SimpleNamespace
                with FileLease(TaskStore(repo).root / "locks" / (args.id + "-controller.lock")):
                    # Local qualification needs the shared task/evidence context,
                    # never a GitHub origin, account, transport or delivery backend.
                    context = SimpleNamespace(name=value.get("repository") or "local-verification")
                    controller = Lifecycle(repo, args.id, backend=context)
                    head = git(root, "rev-parse", "HEAD").decode().strip()
                    controller.verification(value, root, head)
                    controller.acceptance(controller.task(), root, head)
                print(json.dumps({"status": "VERIFIED", "task_id": args.id, "head_sha": head}))
                return 0
            return subprocess.run(verification_argv(value, root), cwd=root).returncode
        elif args.task_command in {"submit", "deliver"}:
            from task_lifecycle import Lifecycle
            from task_queue import submit
            from task_worker import launch
            if args.task_command == "deliver":
                store = TaskStore(repo)
                if store.load(args.id).get("host_platform") != os.name:
                    raise ValueError("Use the registered lifecycle host Git implementation")
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
        return 0 if value.get("status") not in {"BLOCKED","FAIL"} else 2
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
