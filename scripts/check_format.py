#!/usr/bin/env python3
"""Format or verify tracked Tenon Makefile and SystemVerilog sources."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path


RTL_SUFFIXES = {".sv", ".svh", ".v", ".vh"}
RTL_ROOTS = {"rtl", "smoke", "tb"}


def tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    return [Path(path.decode("utf-8")) for path in result.stdout.split(b"\0") if path]


def select_files(paths: Iterable[Path], kind: str) -> list[Path]:
    if kind == "make":
        return sorted(path for path in paths if path.name == "Makefile" or path.suffix == ".mk")
    if kind == "rtl":
        return sorted(
            path
            for path in paths
            if path.suffix in RTL_SUFFIXES and path.parts and path.parts[0] in RTL_ROOTS
        )
    raise ValueError(f"unknown format kind: {kind}")


def chunks(paths: list[Path], size: int = 64) -> Iterator[list[Path]]:
    for start in range(0, len(paths), size):
        yield paths[start : start + size]


def require_tool(tool: str) -> str:
    executable = shutil.which(tool)
    if executable is None:
        raise RuntimeError(f"formatter executable not found: {tool}")
    return executable


def run(command: list[str], root: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=root, check=True, env=env)


def format_makefiles(tool: str, config: Path, paths: list[Path], root: Path) -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONNOUSERSITE", None)
    for batch in chunks(paths):
        run(
            [tool, "format", "--config", str(config), *(str(path) for path in batch)],
            root,
            environment,
        )


def check_makefiles(tool: str, config: Path, paths: list[Path], root: Path) -> list[Path]:
    with tempfile.TemporaryDirectory(prefix="tenon-format-") as temporary:
        temporary_root = Path(temporary)
        copies: list[Path] = []
        for source in paths:
            destination = temporary_root / source
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / source, destination)
            copies.append(destination)
        format_makefiles(tool, config, copies, root)
        return [
            source
            for source, copy in zip(paths, copies, strict=True)
            if (root / source).read_bytes() != copy.read_bytes()
        ]


def format_rtl(tool: str, config: Path, paths: list[Path], root: Path, verify: bool) -> None:
    batches: Iterable[list[Path]] = ([path] for path in paths) if verify else chunks(paths)
    for batch in batches:
        action = "--verify" if verify else "--inplace"
        run(
            [
                tool,
                f"--flagfile={config}",
                "--failsafe_success=false",
                action,
                *(str(path) for path in batch),
            ],
            root,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--kind", choices=("make", "rtl"), required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--mbake", default="mbake")
    parser.add_argument("--verible-verilog-format", default="verible-verilog-format")
    args = parser.parse_args()

    root = args.root.resolve()
    files = select_files(tracked_files(root), args.kind)
    if not files:
        print(f"no tracked {args.kind} files selected")
        return 0

    if args.kind == "make":
        tool = require_tool(args.mbake)
        config = root / ".bake.toml"
        if args.apply:
            format_makefiles(tool, config, files, root)
            return 0
        changed = check_makefiles(tool, config, files, root)
        if changed:
            print("Makefile formatting required:", file=sys.stderr)
            print("\n".join(str(path) for path in changed), file=sys.stderr)
            return 1
        return 0

    tool = require_tool(args.verible_verilog_format)
    format_rtl(tool, root / ".verible-format", files, root, verify=not args.apply)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"format check failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
