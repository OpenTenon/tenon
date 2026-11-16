#!/usr/bin/env python3
"""Prepare only the existing lightweight CI linters from audited pins; never install EDA/PDKs."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify_archive(path: Path, expected: str) -> bool:
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected


def unpack(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as handle:
        for member in handle.getmembers():
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination.resolve()) or member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise ValueError("Unsafe tool archive member: " + member.name)
        handle.extractall(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=("format", "check"), required=True)
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache/tenon-ci")
    args = parser.parse_args()
    started = time.monotonic()
    pins = json.loads((ROOT / "tools/toolchain.json").read_text(encoding="utf-8"))
    args.cache.mkdir(parents=True, exist_ok=True)
    constraints = args.cache / "python-constraints.txt"
    constraints.write_text("".join(f"{name}=={version}\n" for name, version in pins["python_packages"].items()), encoding="utf-8")
    additions = []
    if args.group == "format":
        subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--user", "--constraint", str(constraints), "mbake==" + pins["python_packages"]["mbake"]], check=True)
        entry = pins["verible"]
    else:
        subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--user", "--constraint", str(constraints), "yamllint==" + pins["python_packages"]["yamllint"]], check=True)
        entry = pins["actionlint_archive"]
    archive = args.cache / (args.group + "-" + entry["version"] + ".tar.gz")
    if not verify_archive(archive, entry["sha256"]):
        with urllib.request.urlopen(entry["url"], timeout=60) as response:
            archive.write_bytes(response.read())
    if not verify_archive(archive, entry["sha256"]):
        raise ValueError("Tool archive failed SHA256 verification")
    destination = args.cache / entry.get("extract_root", "")
    destination.mkdir(parents=True, exist_ok=True)
    # Re-extract verified input after every hit; cached executables are not trusted.
    unpack(archive, destination)
    additions.append(args.cache / entry["directory"] / entry.get("bin_subdir", "bin"))
    additions.append(Path.home() / ".local/bin")
    if os.environ.get("GITHUB_PATH"):
        with Path(os.environ["GITHUB_PATH"]).open("a", encoding="utf-8") as output:
            output.write("\n".join(str(path) for path in additions) + "\n")
    for path in additions:
        print("Tool path: " + str(path))
    preparation = ROOT / "build/tooling" / (args.group + "-preparation.json")
    preparation.parent.mkdir(parents=True, exist_ok=True)
    preparation.write_text(json.dumps({"group": args.group, "duration_seconds": round(time.monotonic() - started, 3),
                                      "scope": "pinned-linter-preparation; excludes system package installation"}) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
