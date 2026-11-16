#!/usr/bin/env python3
"""Verified, local-only task archives and ownership checks before resource removal."""
from __future__ import annotations
import hashlib
import json
import os
import zipfile
from pathlib import Path
from engineering_common import digest, git, source_state, utc_now, write_json
from task_store import TaskStore, activity_blockers


def worktrees(repo: Path) -> list[dict]:
    records = []
    for block in git(repo,"worktree","list","--porcelain").decode().strip().split("\n\n"):
        record = {}
        for line in block.splitlines():
            key,_,value = line.partition(" ")
            record[key] = value
        if "worktree" in record:
            record["path"] = Path(record["worktree"]).resolve()
            records.append(record)
    return records


def expected_head(task: dict) -> str:
    return task.get("delivery",{}).get("head_sha") or task.get("head_sha") or task.get("source",{}).get("sha","")


def ownership_errors(store: TaskStore, task: dict, absent_ok: bool = False) -> list[str]:
    errors = []
    root = Path(task["worktree"]).resolve()
    branch = task.get("branch","")
    records = worktrees(store.repo)
    if task.get("repo_id") != store.repo_id or task.get("host_platform") != os.name:
        errors.append("Repository identity or lifecycle host differs")
    if not branch.startswith("dev-") or branch in {"dev","main","master"}:
        errors.append("Only registered short task branches can be removed")
    try:
        default = git(store.repo,"symbolic-ref","refs/remotes/origin/HEAD").decode().strip().removeprefix("refs/remotes/origin/")
        if branch == default:
            errors.append("Default branch is protected")
    except Exception:
        pass
    if not records or root == records[0]["path"]:
        errors.append("Primary worktree is protected")
    matches = [item for item in records if item["path"]==root]
    empty_unregistered = absent_ok and root.is_dir() and not matches and not any(root.iterdir())
    if root.exists() and not empty_unregistered:
        if len(matches)!=1 or matches[0].get("branch")!="refs/heads/"+branch or "locked" in matches[0]:
            errors.append("Worktree registration/branch/lock differs")
        elif matches[0].get("HEAD") != expected_head(task):
            errors.append("Worktree has a different candidate commit")
        if git(root,"diff","--name-only","HEAD","--").strip() or git(root,"ls-files","--others","--exclude-standard").strip():
            errors.append("Uncommitted or untracked source changes remain")
        if (root/"build/locks/physical.lock").exists():
            errors.append("Physical execution lease remains")
        if any(path != root/".git" for path in root.rglob(".git")):
            errors.append("Embedded Git repositories require separate owned-fixture cleanup")
        if (root/".gitmodules").exists():
            for line in git(root,"ls-files","--stage").decode().splitlines():
                if line.startswith("160000 ") and (root/line.split("\t",1)[1]/".git").exists():
                    errors.append("Initialized submodule is protected")
    elif not absent_ok:
        errors.append("Worktree is missing")
    elif matches:
        errors.append("Missing worktree still has a Git registration")
    for value in store.all():
        if value["task_id"]!=task["task_id"] and value.get("status")!="CLEANED" and Path(value["worktree"]).resolve()==root:
            errors.append("Another task owns the worktree")
    if root.exists() and not empty_unregistered:
        archived = archive_roots(root)
        disposables = [(root/name).resolve() for name in task.get("disposable_roots",[])]
        for item in disposables:
            if not item.is_relative_to(root) or item==root:
                errors.append("Disposable cache path escaped the worktree")
        for raw in git(root,"ls-files","--others","--ignored","--exclude-standard","-z").split(b"\0"):
            if not raw:
                continue
            path = root/raw.decode("utf-8")
            if "__pycache__" in path.parts and path.suffix in {".pyc",".pyo"}:
                continue
            if any(path==folder or path.is_relative_to(folder) for folder in archived):
                continue
            if any(path.resolve().is_relative_to(folder) for folder in disposables):
                continue
            errors.append("Unclassified ignored file must be preserved: "+str(path.relative_to(root)))
    errors.extend(activity_blockers(store,task["task_id"]))
    return errors


def archive_roots(root: Path) -> list[Path]:
    roots = [root/"build"/name for name in ("tasks","verification","delivery","sim","smoke")]
    roots += [root/"build/run_validation.py"]
    roots += list((root/"flow").glob("*/runs"))+list((root/"smoke").glob("*/runs"))
    evaluations = root/"build/skill-evals"
    if evaluations.exists():
        roots += [path for path in evaluations.glob("*/*") if path.is_file()]
    return [path for path in roots if path.exists()]


def verify_archive(path: Path, expected_sha: str | None = None) -> dict:
    if expected_sha and digest(path)!=expected_sha:
        raise ValueError("Task archive hash does not match")
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("schema_version")!=1:
            raise ValueError("Unsupported archive manifest")
        entries = manifest.get("files",[])
        names = [item["path"] for item in entries]
        if len(names)!=len(set(names)) or set(archive.namelist())!={"manifest.json",*names}:
            raise ValueError("Archive inventory is incomplete or duplicated")
        for item in entries:
            relative = Path(item["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe archive path")
            payload = archive.read(item["path"])
            if len(payload)!=item["size"] or hashlib.sha256(payload).hexdigest()!=item["sha256"]:
                raise ValueError("Archive payload hash does not match: "+item["path"])
    return manifest


def _archive_sources(store: TaskStore, task: dict, root: Path) -> tuple[dict, list]:
    sources, external = {}, []
    for folder in archive_roots(root):
        for path in ([folder] if folder.is_file() else sorted(folder.rglob("*"))):
            if path.is_symlink() and not path.resolve().is_relative_to(root):
                external.append({"path":path.relative_to(root).as_posix(),"target":os.readlink(path),
                                 "sha256":digest(path) if path.is_file() else None})
            elif path.is_file():
                sources["worktree/"+path.relative_to(root).as_posix()] = path
    operations = store.root/"operations"/task["task_id"]
    if operations.exists():
        for path in operations.rglob("*"):
            if path.is_file() and not path.is_symlink():
                sources["operations/"+path.relative_to(operations).as_posix()] = path
    return sources,external


def verify_task_archive(store: TaskStore, task: dict) -> dict:
    info = task["archive"]
    manifest = verify_archive(Path(info["path"]),info["sha256"])
    if manifest.get("repo_id")!=store.repo_id or manifest.get("task_id")!=task["task_id"] or manifest.get("head_sha")!=expected_head(task):
        raise ValueError("Archive belongs to another repository/task/candidate")
    return manifest


def archive_task(store: TaskStore, task: dict) -> dict:
    root = Path(task["worktree"]).resolve()
    if root.exists():
        errors = ownership_errors(store,task)
        if errors:
            raise ValueError("; ".join(errors))
    if task.get("archive"):
        info = task["archive"]
        old = verify_task_archive(store,task)
        if not root.exists():
            return info
        sources,external = _archive_sources(store,task,root)
        old_files = {item["path"]:item["sha256"] for item in old["files"] if item["path"]!="task.json"}
        if old_files=={name:digest(path) for name,path in sources.items()} and external==old.get("external_references",[]):
            return info
    errors = ownership_errors(store,task)
    if errors:
        raise ValueError("; ".join(errors))
    root = Path(task["worktree"]).resolve()
    before = source_state(root)
    destination = store.root/"archives"/task["task_id"]
    destination.mkdir(parents=True,exist_ok=True)
    target = destination/("task-"+str(task["revision"])+".zip")
    temporary = target.with_suffix(".zip.partial")
    if target.exists():
        raise FileExistsError("An unregistered archive already exists; inspect before replacement")
    manifest = {"schema_version":1,"repo_id":store.repo_id,"task_id":task["task_id"],
                "head_sha":expected_head(task),"source":before,"created_at":utc_now(),
                "qualification":"historical-local-evidence; not current verification or signoff",
                "files":[],"external_references":[]}
    sources,external = _archive_sources(store,task,root)
    manifest["external_references"] = external
    try:
        with zipfile.ZipFile(temporary,"w",compression=zipfile.ZIP_DEFLATED) as archive:
            task_bytes = (json.dumps(task,indent=2,ensure_ascii=False)+"\n").encode("utf-8")
            archive.writestr("task.json",task_bytes)
            manifest["files"].append({"path":"task.json","size":len(task_bytes),"sha256":hashlib.sha256(task_bytes).hexdigest()})
            for relative,path in sorted(sources.items()):
                signature = digest(path)
                size = path.stat().st_size
                archive.write(path,relative)
                manifest["files"].append({"path":relative,"size":size,"sha256":signature})
            archive.writestr("manifest.json",json.dumps(manifest,indent=2))
        verify_archive(temporary)
        if source_state(root)!=before:
            raise ValueError("Source changed while archiving")
        for item in manifest["files"]:
            if item["path"] in sources and digest(sources[item["path"]])!=item["sha256"]:
                raise ValueError("Run evidence changed while archiving")
        temporary.replace(target)
        write_json(destination/"manifest.json",manifest)
        info = {"path":str(target),"sha256":digest(target),"manifest":str(destination/"manifest.json"),"verified_at":utc_now()}
        store.mutate(task["task_id"],lambda value:{**value,"archive":info,"archive_history":value.get("archive_history",[])+([value["archive"]] if value.get("archive") else []),"status":"ARCHIVED"},"archive-verified")
        return info
    except BaseException:
        # Never delete original source or native runs on archival failure.
        temporary.unlink(missing_ok=True)
        raise


def deploy_controller(store: TaskStore, commit: str) -> Path:
    directory = store.root/"controllers"/commit
    manifest_path = directory/"controller.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["files"]:
            if digest(directory/item["path"])!=item["sha256"]:
                raise ValueError("Deployed controller was modified")
        return directory/"scripts/task_lifecycle.py"
    files = git(store.repo,"ls-tree","-r","--name-only",commit,"scripts/").decode().splitlines()
    if "scripts/task_lifecycle.py" not in files:
        raise ValueError("Verified dev version does not contain the cleanup controller")
    manifest = {"schema_version":1,"commit":commit,"files":[]}
    for name in files:
        if not name.startswith("scripts/") or ".." in Path(name).parts:
            raise ValueError("Invalid controller source path")
        payload = git(store.repo,"show",commit+":"+name)
        path = directory/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(payload)
        manifest["files"].append({"path":name,"sha256":digest(path)})
    write_json(manifest_path,manifest)
    return directory/"scripts/task_lifecycle.py"
