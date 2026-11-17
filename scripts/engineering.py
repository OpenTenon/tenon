#!/usr/bin/env python3
"""Read-only engineering commands shared by Make, Skills and CI (Python >= 3.10)."""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from engineering_common import (ROOT, digest, execute, finish_summary, git, new_summary,
                                record_attempt, source_state, status_exit, validate_evidence, write_json)

PDKS = ("ihp130", "sky130", "gf180", "ics55")
GROUP_TARGETS = {
    "format": ["format-check"],
    "check": ["check", "tools-test", "policy-check", "workflow-check"],
    "sim": ["sim", "adapter-compile"],
    "tools": ["tools-test", "skill-check"],
}
TARGET_TOOLS = {
    "format-check": ["mbake", "verible-verilog-format"], "check": ["iverilog", "vvp"],
    "sim": ["iverilog", "vvp"], "adapter-compile": ["iverilog"],
    "workflow-check": ["yamllint", "actionlint"],
}
TOOL_ENV = {"make": "MAKE", "iverilog": "IVERILOG", "vvp": "VVP", "mbake": "MBAKE",
            "verible-verilog-format": "VERIBLE_FORMAT", "openroad": "OPENROAD", "librelane": "LIBRELANE"}


def make_values(root: Path = ROOT) -> dict[str, str]:
    """Resolve the scalar adapter defaults in Makefile without running EDA or a shell."""
    values = {"HOME": str(Path.home())}
    for name, operator, value in re.findall(r"^([A-Z][A-Z0-9_]*)[ \t]*(\?=|:=)[ \t]*(.*?)$",
                                           (root / "Makefile").read_text(encoding="utf-8"), re.M):
        values[name] = os.environ.get(name, value) if operator == "?=" else value
    for _ in range(20):
        previous = values.copy()
        values = {name: re.sub(r"\$\(([A-Z][A-Z0-9_]*)\)",
                              lambda match: previous.get(match[1], match[0]), value)
                  for name, value in values.items()}
        if values == previous:
            break
    return values


def tool_info(name: str) -> dict:
    command = os.environ.get(TOOL_ENV.get(name, ""), name)
    executable = shutil.which(command)
    result = {"command": command, "path": executable, "status": "BLOCKED", "version": None}
    if not executable:
        return result
    try:
        flag = "-V" if name in {"iverilog", "vvp"} else "--version"
        completed = subprocess.run([executable, flag], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   timeout=15, check=False)
        version = completed.stdout.decode("utf-8", errors="replace").strip()
        result.update(status="PASS" if completed.returncode == 0 and version else "BLOCKED",
                      version="\n".join(version.splitlines()[:4])[:512])
    except (OSError, subprocess.TimeoutExpired) as error:
        result["reason"] = str(error)
    return result


def pdk_info(name: str, root: Path = ROOT) -> dict:
    values = make_values(root)
    prefix = {"ihp130": "", "sky130": "SKY130_", "gf180": "GF180_", "ics55": "ICS55_"}[name]
    pdk_root = Path(values[prefix + "PDK_ROOT"]).expanduser()
    pdk_name = values[prefix + "PDK"]
    directory = pdk_root / pdk_name
    files = {"ihp130": [values["IHP_IO_MODEL"]],
             "sky130": [values["SKY130_IO_MODEL"], values["SKY130_EF_IO_MODEL"]],
             "gf180": [values["GF180_IO_MODEL"]],
             "ics55": [str(directory / path) for path in (
                 "libs.tech/librelane/config.tcl",
                 "libs.ref/ics55_LLSC_H7CR/lef/ics55_LLSC_H7CR_M2.lef",
                 "libs.ref/ics55_io_3p3/lef/SP55NLLD2P_3P3V_V0p4a_6MT_1TM.lef",
                 "libs.ref/ics55_pll/lef/PLL_TOP.lef")]}[name]
    adapter_manifest = directory / "ADAPTER_MANIFEST.json"
    if adapter_manifest.is_file():
        files.append(str(adapter_manifest))
    checks = [{"path": path, "exists": Path(path).is_file(),
               "sha256": digest(Path(path)) if Path(path).is_file() else None} for path in files]
    ci_model = any(Path(item["path"]).resolve().is_relative_to((root / "tb").resolve())
                   or re.fullmatch(r"ci_.*_stub\.sv", Path(item["path"]).name) for item in checks)
    return {"id": name, "root": str(pdk_root), "name": pdk_name,
            "ci_model_rejected": ci_model,
            "status": "PASS" if directory.is_dir() and all(item["exists"] for item in checks) and not ci_model else "BLOCKED",
            "required_files": checks}


def doctor(profile: str, pdk: str | None = None, root: Path = ROOT) -> dict:
    names = {"git", "make"}
    if profile == "fast":
        names.update({"iverilog", "vvp", "mbake", "verible-verilog-format", "yamllint", "actionlint"})
    else:
        names.add("iverilog")
        if pdk == "ihp130":
            names.add("vvp")
        if pdk == "ics55":
            names.add("openroad")
        if profile == "physical":
            names.update({"librelane", "openroad"})
    tools = {name: tool_info(name) for name in sorted(names)}
    result = {"schema_version": 1, "kind": "doctor", "profile": profile,
              "platform": platform.platform(), "python": sys.version, "tools": tools,
              "status": "PASS" if all(item["status"] == "PASS" for item in tools.values()) else "BLOCKED"}
    if sys.version_info < (3, 10):
        result["status"] = "BLOCKED"
    if profile != "fast":
        if pdk not in PDKS:
            raise ValueError("A PDK must be selected for this profile")
        result["pdk"] = pdk_info(pdk, root)
        if result["pdk"]["status"] != "PASS":
            result["status"] = "BLOCKED"
    return result


def make_command(target: str) -> list[str]:
    return [os.environ.get("MAKE", "make"), "--no-print-directory", target,
            f"PYTHON={sys.executable}"]


def required_ids(group: str) -> set[str]:
    groups = GROUP_TARGETS if group == "all" else {group: GROUP_TARGETS[group]}
    return {target for targets in groups.values() for target in targets} | {"repository-unchanged"}


def _verify(group: str = "all", pdk: str | None = None, dry_run: bool = False,
           timeout: float = 900, root: Path = ROOT) -> tuple[Path, dict]:
    summary = new_summary(root, "pdk-verification" if pdk else "verification")
    report = root / "build" / "verification" / summary["run_id"] / "summary.json"
    report.parent.mkdir(parents=True, exist_ok=False)
    summary["group"] = "pdk" if pdk else group
    summary["dry_run"] = dry_run
    summary["tools"] = doctor("pdk" if pdk else "fast", pdk, root)["tools"]
    if pdk:
        info = pdk_info(pdk, root)
        summary["pdk"] = info
        targets = {"ihp130": ["lint", "test"], "sky130": ["lint-sky130"],
                   "gf180": ["lint-gf180"], "ics55": ["lint-ics55"]}[pdk]
        if info["status"] != "PASS":
            summary["checks"].append({"id": "pdk-environment", "status": "BLOCKED", "required": True,
                                      "reason": "Required external PDK views are missing"})
    else:
        groups = GROUP_TARGETS if group == "all" else {group: GROUP_TARGETS[group]}
        targets = list(dict.fromkeys(target for items in groups.values() for target in items))
    for index, target in enumerate(targets):
        native_commands = {
            "tools-test": [sys.executable, "scripts/engineering_tests.py", "--report", str(report.parent / "tools-test.json")],
            "skill-check": [sys.executable, "scripts/repo_policy.py", "--skills-only"],
        }
        command = native_commands.get(target, make_command(target))
        required_tools = (["git"] if target in native_commands else ["make", *TARGET_TOOLS.get(target, [])])
        if pdk:
            required_tools += ["iverilog"] + (["vvp"] if pdk == "ihp130" else [])
            required_tools += ["openroad"] if pdk == "ics55" else []
        missing = [name for name in required_tools if summary["tools"].get(name, {}).get("status") != "PASS"]
        if missing or (pdk and summary["pdk"]["status"] != "PASS"):
            summary["checks"].append({"id": target, "status": "BLOCKED", "required": True,
                                      "command": command, "reason": "Missing tools or PDK: " + ", ".join(missing)})
        else:
            summary["checks"].append(execute(command, root,
                report.parent / f"{index:02d}-{target}.log", timeout, dry_run, target,
                coverage={"kind": ("manual-interface-and-view-check" if pdk == "ics55" else "production-io-model-check") if pdk else target}))
        if target == "tools-test" and not dry_run:
            cases_report = report.parent / "tools-test.json"
            check = summary["checks"][-1]
            if cases_report.is_file():
                check["test_report"] = {"path": cases_report.name, "sha256": digest(cases_report)}
            elif check.get("status") == "PASS":
                check.update(status="FAIL", reason="The unittest process returned no case report")
        write_json(report, summary)
    if pdk:
        summary["checks"].append({"id": "pdk-inputs-unchanged", "required": True,
                                  "status": "PASS" if pdk_info(pdk, root) == summary["pdk"] else "FAIL"})
    summary["preparation"] = [json.loads(path.read_text(encoding="utf-8"))
                              for path in (root / "build/tooling").glob("*-preparation.json")]
    finish_summary(summary, root, report)
    record_attempt(root, report, summary)
    return report, summary


def verify(group: str = "all", pdk: str | None = None, dry_run: bool = False,
           timeout: float = 900, root: Path = ROOT) -> tuple[Path, dict]:
    from task_store import resource_lease
    with resource_lease(root,"verification"):
        return _verify(group,pdk,dry_run,timeout,root)


def affected_pdks(paths: list[str]) -> set[str]:
    affected = set()
    for name in paths:
        if name.startswith("specs/") or name == "flow/pdn.tcl":
            affected.update(PDKS)
        for pdk in PDKS:
            if name.startswith(f"flow/{pdk}/"):
                affected.add(pdk)
        if name.startswith("smoke/") and (name.endswith((".sv", ".tcl", "/config.json", "/manifest.json")) or name == "smoke/manifest.json"):
            affected.add("ics55")
        if name.startswith("rtl/"):
            matches = {pdk for pdk in PDKS if pdk != "ihp130" and pdk in name}
            if matches:
                affected.update(matches)
            elif "pdk_" in name:
                affected.update({"sky130", "gf180", "ics55"})
            else:
                affected.add("ihp130")
    return affected


def release_check(root: Path = ROOT, evidence: list[Path] | None = None,
                  dev_ref: str = "origin/dev", main_ref: str = "origin/main") -> tuple[Path, dict]:
    summary = new_summary(root, "release-readiness")
    report = root / "build" / "verification" / summary["run_id"] / "summary.json"
    report.parent.mkdir(parents=True, exist_ok=False)
    dev = git(root, "rev-parse", dev_ref).decode().strip()
    main = git(root, "rev-parse", main_ref).decode().strip()
    summary["release"] = {"dev_ref": dev_ref, "dev_sha": dev, "main_ref": main_ref, "main_sha": main,
        "changed_files": git(root, "diff", "--name-only", main, dev).decode().splitlines(),
        "commits": git(root, "log", "--format=%h %s", f"{main}..{dev}").decode().splitlines(),
        "merge_operator": "human", "version_policy": "SemVer; maintainer selects and creates the tag"}
    affected = affected_pdks(summary["release"]["changed_files"])
    summary["release"]["affected_pdks"] = sorted(affected)
    at_dev = summary["source"]["sha"] == dev and not summary["source"]["dirty"]
    summary["checks"].append({"id": "clean-dev-candidate", "required": True,
                              "status": "PASS" if at_dev else "BLOCKED"})
    try:
        values = [validate_evidence(path, root) for path in evidence or []]
        verified = set()
        for value in values:
            if value.get("kind") == "verification":
                verified.update(check["id"] for check in value["checks"] if check.get("status") == "PASS")
        complete = required_ids("all").issubset(verified)
        real_pdks = set()
        for value in values:
            if value.get("kind") == "pdk-verification":
                info = value.get("pdk", {})
                if info.get("id") in PDKS and info == pdk_info(info["id"], root):
                    real_pdks.add(info["id"])
        summary["checks"].append({"id": "required-pdk-verification", "required": True,
                                  "status": "PASS" if affected.issubset(real_pdks) else "BLOCKED",
                                  "missing_pdks": sorted(affected - real_pdks)})
        summary["checks"].append({"id": "candidate-verification", "status": "PASS" if complete else "BLOCKED",
                                  "required": True, "evidence": [str(path) for path in evidence or []]})
    except (ValueError, OSError, TypeError) as error:
        summary["checks"].append({"id": "candidate-verification", "status": "FAIL", "reason": str(error)})
    finish_summary(summary, root, report)
    return report, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    diagnose = commands.add_parser("doctor")
    diagnose.add_argument("--profile", choices=("fast", "pdk", "physical"), default="fast")
    diagnose.add_argument("--pdk", choices=PDKS)
    for name in ("verify", "verify-pdk"):
        command = commands.add_parser(name)
        command.add_argument("--dry-run", action="store_true")
        command.add_argument("--timeout", type=float, default=900)
        if name == "verify":
            command.add_argument("--group", choices=("all", *GROUP_TARGETS), default="all")
        else:
            command.add_argument("--pdk", choices=PDKS, required=True)
    release = commands.add_parser("release-check")
    release.add_argument("--evidence", type=Path, action="append", default=[])
    release.add_argument("--dev-ref", default="origin/dev")
    release.add_argument("--main-ref", default="origin/main")
    validate = commands.add_parser("validate-evidence")
    validate.add_argument("path", type=Path)
    validate.add_argument("--group", choices=("all", *GROUP_TARGETS), default="all")
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            result = doctor(args.profile, args.pdk)
            print(json.dumps(result, indent=2))
            return status_exit(result["status"])
        if args.command == "validate-evidence":
            validate_evidence(args.path, ROOT, required_ids(args.group))
            print("PASS: evidence matches the current source and required checks")
            return 0
        if args.command == "release-check":
            path, result = release_check(evidence=args.evidence, dev_ref=args.dev_ref, main_ref=args.main_ref)
        else:
            if args.timeout <= 0:
                parser.error("--timeout must be positive")
            path, result = verify(getattr(args, "group", "all"), getattr(args, "pdk", None),
                                  args.dry_run, args.timeout)
        print(f"{result['status']}: {path}")
        for item in result["checks"]:
            print(f"  {item['status']}: {item['id']}")
            if item["status"] in {"FAIL","BLOCKED"}:
                if item.get("reason"):
                    print("    " + item["reason"])
                if item.get("log") and (path.parent/item["log"]).is_file():
                    tail = (path.parent/item["log"]).read_text(encoding="utf-8",errors="replace").splitlines()[-30:]
                    print("\n".join(tail))
        return status_exit(result["status"])
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
