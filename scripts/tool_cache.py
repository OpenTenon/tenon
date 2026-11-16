#!/usr/bin/env python3
"""Versioned, repository-local lightweight tool caches; no PDK installation logic."""
from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from engineering_common import digest, utc_now, write_json
from task_store import FileLease, TaskStore


def toolchain_key(repo: Path) -> str:
    return hashlib.sha256((repo/"tools/toolchain.json").read_bytes()).hexdigest()


def cache_directory(repo: Path, group: str) -> Path:
    store = TaskStore(repo)
    key = toolchain_key(repo)
    root = store.root/"cache"/key
    root.mkdir(parents=True,exist_ok=True)
    write_json(root/"cache.json",{"schema_version":1,"repo_id":store.repo_id,"version_key":key,"last_used":utc_now()})
    path = root/(sys.platform+f"-py{sys.version_info.major}{sys.version_info.minor}")/group
    path.mkdir(parents=True,exist_ok=True)
    return path


def prune_caches(repo: Path) -> list[str]:
    store = TaskStore(repo)
    parent = (store.root/"cache").resolve()
    active = {value.get("toolchain_key") for value in store.all() if value.get("status")!="CLEANED"}
    owned = []
    for path in parent.iterdir() if parent.exists() else []:
        manifest = path/"cache.json"
        if path.is_symlink() or not path.is_dir() or not re.fullmatch(r"[0-9a-f]{64}",path.name) or not manifest.is_file():
            continue
        value = json.loads(manifest.read_text(encoding="utf-8"))
        if value.get("repo_id")==store.repo_id and value.get("version_key")==path.name:
            owned.append((value["last_used"],path))
    keep = {path.name for _,path in sorted(owned,reverse=True)[:2]}|active|{toolchain_key(repo)}
    removed = []
    for _,path in owned:
        if path.name in keep:
            continue
        resolved = path.resolve()
        if resolved.parent!=parent or resolved==parent or path.is_symlink():
            raise ValueError("Cache deletion escaped its owned version directory")
        try:
            with FileLease(store.root/"locks"/(path.name+"-cache.lock"),timeout=0.01):
                # Only regenerable files in an explicitly registered cache version are removed.
                shutil.rmtree(resolved)
                removed.append(path.name)
        except TimeoutError:
            continue
    return removed
