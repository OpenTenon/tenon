#!/usr/bin/env python3
"""Protect native physical runs, enforce existing exceptions, and retain local provenance."""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from engineering import PDKS, doctor, pdk_info
from task_store import resource_lease
from engineering_common import (ROOT, PhysicalLock, digest, execute, finish_summary,
                                new_summary, status_exit, validate_evidence, write_json)

TRUTHY = {"1", "true", "yes"}


def enabled(name: str) -> bool:
    return os.environ.get(name, "0").lower() in TRUTHY


def validate_policy(command: list[str], pdk: str, tag: str) -> tuple[list[str], list[str]]:
    """Only the exceptions already present in AGENTS.md are executable."""
    if enabled("SKIP_MAGIC_SPICE") or enabled("SKIP_NETGEN_LVS"):
        raise ValueError("AGENTS.md does not permit skipping Magic extraction or Netgen LVS")
    skips, overrides = [], {}
    for index, token in enumerate(command):
        if token == "--overwrite" or token.startswith("--overwrite="):
            raise ValueError("Existing native runs must never be overwritten")
        if token == "--skip":
            if index + 1 >= len(command):
                raise ValueError("Missing --skip argument")
            skips.extend(command[index + 1].split(","))
        elif token.startswith("--skip="):
            skips.extend(token.split("=", 1)[1].split(","))
        if token == "--override-config" or token.startswith("--override-config="):
            assignment = token.split("=", 1)[1] if token.startswith("--override-config=") else (command[index + 1] if index + 1 < len(command) else "")
            if "=" not in assignment:
                raise ValueError("Malformed configuration override")
            key, value = assignment.split("=", 1)
            overrides[key] = value
    unsigned = bool(pdk == "ics55" and re.fullmatch(r"tenon-qfn(32|64|88|128)-no-pll-unsigned-io-pg", tag))
    allowed = set()
    exceptions = []
    if enabled("SKIP_DRC"):
        if not os.environ.get("TENON_DRC_APPROVAL", "").strip():
            raise ValueError("SKIP_DRC requires TENON_DRC_APPROVAL referencing explicit user authorization")
        allowed.update({"Magic.DRC", "KLayout.DRC"})
        exceptions.append("user-authorized-drc-only:" + os.environ["TENON_DRC_APPROVAL"])
    if unsigned:
        if os.environ.get("UNSIGNED_IO_PG") != "1":
            raise ValueError("Explicit ICS55 unsigned target requires UNSIGNED_IO_PG=1")
        allowed.add("OpenROAD.IRDropReport")
        exceptions.append("ics55-sp55-unsigned-io-pg")
    if set(skips) - allowed:
        raise ValueError("Disallowed skipped steps: " + ", ".join(sorted(set(skips) - allowed)))
    if "Checker.PowerGridViolations" in skips:
        raise ValueError("The power-grid checker must execute and retain its reports")
    for key, value in overrides.items():
        false = value.lower() in {"0", "false", "no"}
        if key in {"RUN_MAGIC_DRC", "RUN_KLAYOUT_DRC"} and false:
            if not enabled("SKIP_DRC"):
                raise ValueError("DRC disabling requires the explicit authorized DRC-only exception")
            skips.append("Magic.DRC" if key == "RUN_MAGIC_DRC" else "KLayout.DRC")
        elif key == "ERROR_ON_PDN_VIOLATIONS" and false and unsigned:
            continue
        elif false and (key.startswith("ERROR_ON_") or any(word in key for word in ("DRC", "LVS", "ANTENNA", "DISCONNECTED", "SPICE"))):
            raise ValueError("Disallowed checker override: " + key)
    return exceptions, sorted(set(skips))


class PhysicalRun:
    def __init__(self, root: Path, pdk: str, config: Path, tag: str, timeout: float = 21600):
        # Resolve both inputs together: Windows TEMP may use an 8.3 alias while
        # resolving the configuration expands it to the long directory name.
        root = root.resolve()
        self.root, self.pdk, self.config, self.tag, self.timeout = root, pdk, config.resolve(), tag, timeout
        if not self.config.is_relative_to(root.resolve()) or not self.config.is_file():
            raise ValueError("Physical configuration must be an existing repository input")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", tag) or timeout <= 0:
            raise ValueError("Invalid native run tag or timeout")
        self.native = self.config.parent / "runs" / tag
        self.summary = new_summary(root, "physical-run")
        self.directory = self.native.parent / (".tenon-" + self.summary["run_id"])
        self.path = self.directory / "summary.json"
        self.lock = PhysicalLock(root, self.summary["run_id"])
        self.summary.update(pdk_id=pdk, pdk=pdk_info(pdk, root), config=self.config.relative_to(root).as_posix(),
                            config_sha256=digest(self.config), native_run=self.native.relative_to(root).as_posix(),
                            qualification="physical-non-signoff")
        for item in self.summary["pdk"]["required_files"]:
            if item["exists"]:
                item["sha256"] = digest(Path(item["path"]))

    def __enter__(self):
        self.started = time.monotonic()
        self.lock.__enter__()
        try:
            if self.native.exists():
                raise FileExistsError("Native run already exists; use another isolated worktree: " + str(self.native))
            self.directory.mkdir(parents=True, exist_ok=False)
            self.summary["tools"] = doctor("physical", self.pdk, self.root)["tools"]
            write_json(self.path, self.summary)
            self.resource = resource_lease(self.root,"physical")
            self.resource.__enter__()
        except BaseException:
            self.lock.__exit__()
            raise
        return self

    def command(self, command: list[str], cwd: Path | None = None, check_id: str = "flow") -> bool:
        exceptions, skipped = validate_policy(command, self.pdk, self.tag)
        if self.summary["pdk"].get("status") != "PASS" or any(info.get("status") != "PASS" for info in self.summary["tools"].values()):
            self.summary["checks"].append({"id": check_id, "status": "BLOCKED", "command": command,
                                          "reason": "Required physical tools or external PDK views are missing"})
            return False
        if "--run-tag" in command and command[command.index("--run-tag") + 1] != self.tag:
            raise ValueError("Command run tag does not match the protected native run")
        self.summary["exceptions"] = sorted(set(self.summary["exceptions"] + exceptions))
        self.summary["skipped_steps"] = sorted(set(self.summary["skipped_steps"] + skipped))
        remaining = self.timeout - (time.monotonic() - self.started)
        if remaining <= 0:
            self.summary["checks"].append({"id": check_id, "status": "FAIL", "reason": "Physical run time budget exhausted"})
            return False
        item = execute(command, cwd or self.root, self.directory / (check_id + ".log"),
                       remaining, check_id=check_id,
                       coverage={"kind": "physical", "pdk": self.pdk, "run_tag": self.tag})
        self.summary["checks"].append(item)
        write_json(self.path, self.summary)
        return item["status"] == "PASS"

    def __exit__(self, kind, error, traceback):
        try:
            if error is not None:
                self.summary["checks"].append({"id": "physical-execution", "status": "BLOCKED" if isinstance(error, ValueError) else "FAIL", "reason": str(error)})
            if self.summary["checks"] and all(item["status"] == "PASS" for item in self.summary["checks"]):
                metrics = self.native / "final/metrics.json"
                if not metrics.is_file():
                    self.summary["checks"].append({"id": "native-final-metrics", "status": "FAIL", "reason": "Missing native final metrics"})
                else:
                    try:
                        parsed = json.loads(metrics.read_text(encoding="utf-8"))
                        if not isinstance(parsed, dict):
                            raise ValueError("Final metrics must be an object")
                        artifacts = [metrics, *self.native.glob("*/state_out.json")]
                        for state in artifacts[1:]:
                            json.loads(state.read_text(encoding="utf-8"))
                        self.summary["artifacts"] = [{"path": path.relative_to(self.root).as_posix(), "sha256": digest(path)}
                                                     for path in sorted(artifacts)]
                    except (ValueError, OSError) as metrics_error:
                        self.summary["checks"].append({"id": "native-final-metrics", "status": "FAIL", "reason": str(metrics_error)})
                if "ics55-sp55-unsigned-io-pg" in self.summary["exceptions"] and not list(self.native.glob("*-checker-powergridviolations/*")):
                    self.summary["checks"].append({"id": "retained-psm-checker", "status": "FAIL", "reason": "Power-grid checker evidence is missing"})
            finish_summary(self.summary, self.root, self.path)
            if self.native.is_dir():
                write_json(self.native / "tenon-run.json", {"schema_version": 1,
                    "evidence": self.path.relative_to(self.root).as_posix(), "sha256": digest(self.path)})
        finally:
            if hasattr(self,"resource"):
                self.resource.__exit__(None,None,None)
            self.lock.__exit__()


def read_run_evidence(native: Path, root: Path = ROOT) -> dict:
    root, native = root.resolve(), native.resolve()
    pointer = json.loads((native / "tenon-run.json").read_text(encoding="utf-8"))
    path = (root / pointer["evidence"]).resolve()
    if not path.is_relative_to(native.parent.resolve()) or digest(path) != pointer.get("sha256"):
        raise ValueError("Physical evidence pointer/hash does not match the native run")
    value = validate_evidence(path, root)
    if value.get("kind") != "physical-run" or (root / value.get("native_run", "")).resolve() != native.resolve():
        raise ValueError("Physical evidence belongs to another run")
    current_pdk = pdk_info(value["pdk_id"], root)
    previous_pdk = value["pdk"]
    if current_pdk.get("root") != previous_pdk.get("root") or current_pdk.get("name") != previous_pdk.get("name"):
        raise ValueError("External PDK selection changed since this run")
    if [item["path"] for item in current_pdk["required_files"]] != [item["path"] for item in previous_pdk["required_files"]]:
        raise ValueError("External PDK interface inventory changed")
    for item in previous_pdk["required_files"]:
        if not item.get("exists") or not item.get("sha256") or digest(Path(item["path"])) != item["sha256"]:
            raise ValueError("External PDK/adapter interface fingerprint changed")
    artifacts = value.get("artifacts", [])
    if not artifacts:
        raise ValueError("Physical stage evidence is missing")
    for item in artifacts:
        artifact = (root / item["path"]).resolve()
        if not artifact.is_relative_to(native.resolve()) or digest(artifact) != item["sha256"]:
            raise ValueError("Physical stage input/report hashes no longer match")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdk", choices=PDKS, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--timeout", type=float, default=21600)
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    if not command:
        parser.error("An explicit LibreLane command is required")
    run = None
    try:
        with PhysicalRun(ROOT, args.pdk, args.config, args.tag, args.timeout) as run:
            run.command(command)
        print(f"{run.summary['status']}: {run.path}")
        return status_exit(run.summary["status"])
    except (ValueError, OSError, KeyError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
