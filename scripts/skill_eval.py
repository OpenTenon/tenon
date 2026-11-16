#!/usr/bin/env python3
"""Explicit, isolated Codex Skill evaluations; default is a no-model-call execution plan."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import re
from pathlib import Path
from engineering_common import ROOT, execute, git, run_id, source_state, write_json
from task_store import TaskStore, resource_lease, identifier


def score(case: dict, events: list[dict], exit_code: int | None) -> dict:
    items = [event.get("item", {}) for event in events if isinstance(event.get("item"), dict)]
    calls = "\n".join(json.dumps(item, ensure_ascii=False).replace("\\\\", "/").replace("\\", "/")
                      for item in items if item.get("type") in {"command_execution", "mcp_tool_call", "skill"})
    messages = "\n".join(str(item.get("text", "")) for item in items if item.get("type") == "agent_message")
    expected = case.get("skill")
    triggered = (f"/{expected}/SKILL.md" in calls) if expected else ".agents/skills/" not in calls
    changed = [item for item in items if item.get("type") == "file_change"]
    unsafe = any(not str(change.get("path", "")).replace("\\", "/").startswith("build/")
                 for item in changed for change in item.get("changes", []))
    outcomes = all(token.lower() in messages.lower() or token.lower() in calls.lower() for token in case.get("tokens", []))
    completed = any(event.get("type") == "turn.completed" for event in events)
    failed = any(event.get("type") in {"turn.failed", "error"} for event in events)
    passed = exit_code == 0 and completed and not failed and triggered and outcomes and not unsafe
    usage = [event["usage"] for event in events if isinstance(event.get("usage"), dict)]
    return {"status": "PASS" if passed else "FAIL", "evidence_level":"trace-signals-only", "skill_trigger_matched": triggered,
            "outcome_signals_matched": outcomes, "source_mutation_detected": unsafe,
            "turn_completed": completed, "usage": usage or None,
            "note": "Deterministic trace signals require human review; they do not prove semantic correctness."}


def assess_outcome(trace: dict, before: dict, after: dict, assertions: list[dict]) -> dict:
    source_unchanged = before == after
    checked = bool(assertions) and all(item.get("passed") is True for item in assertions)
    return {"status":"PASS" if trace["status"]=="PASS" and source_unchanged and checked else "FAIL",
            "source_unchanged":source_unchanged,"assertions":assertions,
            "trace":trace,"evidence_level":"fixture-and-trace"}


def configured_mcp_overrides() -> list[str]:
    # Preserve the configured model/provider; disable service integrations only for this run.
    config = Path(os.environ.get("CODEX_HOME",str(Path.home()/".codex")))/"config.toml"
    if not config.exists():
        return []
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    value = tomllib.loads(config.read_text(encoding="utf-8"))
    args = []
    for name in value.get("mcp_servers",{}):
        args += ["-c","mcp_servers."+json.dumps(name)+".enabled=false"]
    return args


def decision_assertion(case: dict, events: list[dict], expected: str, fixture: Path) -> dict:
    messages = [event.get("item",{}).get("text","") for event in events if event.get("item",{}).get("type")=="agent_message"]
    try:
        text = messages[-1].strip()
        if text.startswith("```"):
            text = "\n".join(text.splitlines()[1:-1])
        result = json.loads(text)
        references = result.get("references",[])
        valid = isinstance(references,list) and all(isinstance(item,str) and (fixture/item).resolve().is_relative_to(fixture.resolve()) and (fixture/item).exists() for item in references)
        return {"kind":"grounded-decision","expected":expected,"observed":result.get("decision"),
                "passed":result.get("decision")==expected and valid}
    except (IndexError,ValueError,TypeError,KeyError):
        return {"kind":"grounded-decision","passed":False,"reason":"Missing structured decision/references"}


def read_events(path: Path) -> list[dict]:
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
        except json.JSONDecodeError:
            continue  # stderr diagnostics are retained in the same raw execution log.
    return events


def isolate(destination: Path) -> None:
    # Source files, including the current uncommitted Skills, are copied; ignored runs/PDKs are never copied.
    destination.mkdir(parents=True, exist_ok=False)
    names = git(ROOT, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")
    for raw in names:
        if not raw:
            continue
        source = ROOT / raw.decode("utf-8")
        target = destination / source.relative_to(ROOT)
        if source.is_file() and not source.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    subprocess.run(["git", "init", "--quiet", str(destination)], check=True)
    # Ref-dependent scenarios can inspect the real revisions without copying private run artifacts.
    subprocess.run(["git", "-C", str(destination), "fetch", "--quiet", str(ROOT),
                    "HEAD:refs/remotes/origin/dev", "origin/main:refs/remotes/origin/main"], check=True)
    subprocess.run(["git", "-C", str(destination), "reset", "--mixed", "--quiet", "origin/dev"], check=True)
    subprocess.run(["git", "-C", str(destination), "checkout", "--quiet", "-b", "dev-eval-fixture"], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="explicitly allow model calls in isolated fixtures")
    parser.add_argument("--model", help="explicit model identifier; recorded unchanged")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    if args.repeats < 1 or args.timeout <= 0:
        parser.error("repeats/timeout must be positive")
    cases = json.loads((ROOT / "tests/skill-evals/cases.json").read_text(encoding="utf-8"))["cases"]
    if not args.execute:
        print(json.dumps({"status":"NOT_RUN", "model_calls":0, "cases":len(cases),
                          "planned_runs":len(cases) * args.repeats, "model":args.model}, indent=2))
        return 0
    if not args.model or not shutil.which("codex"):
        parser.error("--execute requires --model and an existing Codex CLI; nothing is installed automatically")
    store = TaskStore(ROOT)
    directory = store.root / "evaluations" / run_id()
    directory.mkdir(parents=True, exist_ok=False)
    original = source_state(ROOT)
    cli_version = subprocess.check_output(["codex", "--version"]).decode("utf-8", errors="replace").strip()
    values = []
    scratch_root = store.root / "eval-scratch"
    scratch_root.mkdir(parents=True,exist_ok=True)
    with resource_lease(ROOT,"evaluation"), tempfile.TemporaryDirectory(prefix="tenon-eval-",dir=scratch_root) as temporary:
        temporary = Path(temporary).resolve()
        if not temporary.is_relative_to(scratch_root.resolve()):
            raise ValueError("Evaluation scratch escaped its owned directory")
        for case in cases:
            for repeat in range(args.repeats):
                case_id = identifier(case["id"])
                label = f"{case_id}-{repeat + 1}"
                fixture = temporary / label
                isolate(fixture)
                if case_id=="generated-drift":
                    output = fixture/"flow/ihp130/qfn32.yaml"
                    output.write_text(output.read_text(encoding="utf-8")+"# intentional evaluation drift\n",encoding="utf-8")
                before = source_state(fixture)
                expected = subprocess.run([sys.executable,"tools/generate_tier0.py","--check"],cwd=fixture,
                                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
                assertion = {"kind":"generated-fixture", "passed":expected.returncode==(1 if case_id=="generated-drift" else 0)}
                expected_decision = {"task-start":"PLAN","task-resume":"PLAN","verify-stale":"BLOCKED",
                    "generated-inventory":"READY","generated-drift":"BLOCKED","physical-exception":"BLOCKED",
                    "release-preparation":"BLOCKED","ordinary-question":"ANSWER"}.get(case_id)
                if expected_decision is None:
                    profile = ["doctor"] + (["--profile","pdk","--pdk","ihp130"] if case_id=="physical-readiness" else [])
                    diagnostic = subprocess.run([sys.executable,"scripts/engineering.py",*profile],cwd=fixture,
                                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
                    expected_decision = "READY" if diagnostic.returncode==0 else "BLOCKED"
                prompt = case["prompt"] + "\nReturn a final JSON object with decision (PLAN, READY, BLOCKED or ANSWER), references (repository-relative existing paths), and explanation.\nThis is a read-only isolated evaluation. Do not install, use account integrations, run hardening, commit, push, open PRs, merge, tag or publish. Preserve every source file."
                argv = ["codex","exec","--json","--ephemeral","--sandbox","read-only",
                        "--disable","apps","--disable","plugins","--disable","browser_use","--disable","computer_use",
                        *configured_mcp_overrides(),"--model",args.model,"--cd",str(fixture),prompt]
                result = execute(argv,fixture,directory/(label+".jsonl"),args.timeout)
                events = read_events(directory/(label+".jsonl"))
                trace = score(case,events,result["exit_code"])
                result.update(case_id=case_id,repetition=repeat+1,model=args.model,
                    assessment=assess_outcome(trace,before,source_state(fixture),[assertion,decision_assertion(case,events,expected_decision,fixture)]))
                values.append(result)
                write_json(directory/"results.json",{"schema_version":2,"model":args.model,"codex_cli_version":cli_version,
                           "isolation":"read-only; account integrations disabled", "runs":values})
                if source_state(ROOT)!=original:
                    raise RuntimeError("Original source changed during evaluation; stopping")
    print(str(directory / "results.json"))
    return 0 if all(item["assessment"]["status"] == "PASS" for item in values) else 1


if __name__ == "__main__":
    raise SystemExit(main())
