#!/usr/bin/env python3
"""Package routed LibreLane views for the next Mini hierarchy level."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def copy(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def stage_artifact(run: Path, suffix: str, filename: str) -> Path:
    candidates = list(run.glob(f"*-{suffix}/{filename}"))
    if not candidates:
        raise FileNotFoundError(f"no {suffix} artifact {filename} below {run}")
    return max(candidates, key=lambda path: int(path.parent.name.split("-", 1)[0]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("design")
    parser.add_argument("--liberty", type=Path)
    args = parser.parse_args()
    final = args.run / "final"
    copy(
        stage_artifact(args.run, "klayout-streamout", f"{args.design}.gds"),
        final / "gds" / f"{args.design}.gds",
    )
    copy(
        stage_artifact(args.run, "odb-cellfrequencytables", f"{args.design}.odb"),
        final / "odb" / f"{args.design}.odb",
    )
    copy(
        stage_artifact(args.run, "klayout-render", f"{args.design}.png"),
        final / "render" / f"{args.design}.png",
    )
    if args.liberty is not None:
        copy(args.liberty, final / "lib" / f"{args.design}.lib")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
