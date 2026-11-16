#!/usr/bin/env python3
"""Explicit, isolated Codex Skill evaluations; default is a no-model-call execution plan."""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from engineering_common import ROOT, execute, git, run_id, source_state, write_json


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
    return {"status": "PASS" if passed else "FAIL", "skill_trigger_matched": triggered,
            "outcome_signals_matched": outcomes, "source_mutation_detected": unsafe,
            "turn_completed": completed, "usage": usage or None,
            "note": "Deterministic trace signals require human review; they do not prove semantic correctness."}


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
    directory = ROOT / "build/skill-evals" / run_id()
    directory.mkdir(parents=True, exist_ok=False)
    original = source_state(ROOT)
    cli_version = subprocess.check_output(["codex", "--version"]).decode("utf-8", errors="replace").strip()
    values = []
    for case in cases:
        for repeat in range(args.repeats):
            identifier = f"{case['id']}-{repeat + 1}"
            fixture = directory / identifier
            isolate(fixture)
            prompt = case["prompt"] + "\nThis is an isolated engineering evaluation. Preserve source files; ignored build/ reports are allowed. Do not install, access external services, run hardening, commit, push, open PRs, merge, tag, or publish."
            command = ["codex", "exec", "--json", "--ephemeral", "--sandbox", "workspace-write", "--model", args.model,
                       "--cd", str(fixture), prompt]
            result = execute(command, fixture, directory / (identifier + ".jsonl"), args.timeout)
            result.update(case_id=case["id"], repetition=repeat + 1, model=args.model,
                          assessment=score(case, read_events(directory / (identifier + ".jsonl")), result["exit_code"]))
            values.append(result)
            write_json(directory / "results.json", {"schema_version":1, "model":args.model, "codex_cli_version":cli_version, "runs":values})
            if source_state(ROOT) != original:
                raise RuntimeError("Original source changed during isolated evaluation; stopping")
    print(str(directory / "results.json"))
    return 0 if all(item["assessment"]["status"] == "PASS" for item in values) else 1


if __name__ == "__main__":
    raise SystemExit(main())
