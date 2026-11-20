#!/usr/bin/env python3
"""Expose one power-interface layer in a routed macro's abstract LEF."""

from __future__ import annotations

import argparse
from pathlib import Path


def pin_block(lines: list[str], pin: str) -> list[str]:
    start = next(index for index, line in enumerate(lines) if line.strip() == f"PIN {pin}")
    end = next(
        index for index in range(start + 1, len(lines)) if lines[index].strip() == f"END {pin}"
    )
    return lines[start : end + 1]


def expose(
    path: Path,
    layer: str,
    power_pin: str,
    ground_pin: str,
    orientation: str,
    minimum_pin_width: float,
) -> None:
    lines = path.read_text().splitlines(keepends=True)
    for pin, use in ((power_pin, "POWER"), (ground_pin, "GROUND")):
        block = pin_block(lines, pin)
        if not any(line.strip() == f"USE {use} ;" for line in block):
            raise ValueError(f"{pin} is not marked USE {use}")
        if not any(line.strip() == f"LAYER {layer} ;" for line in block):
            raise ValueError(f"{pin} has no {layer} geometry")

    # write_abstract_lef exports every internal PG shape as a macro pin. A
    # parent hierarchy must see only the intended interface layer; otherwise
    # PDNGen attempts to connect internal lower-layer mesh fragments as ports.
    filtered: list[str] = []
    target_pin: str | None = None
    dropping_pin_layer = False
    interface_layer = False
    interface_rectangles = {power_pin: 0, ground_pin: 0}
    for line in lines:
        stripped = line.strip()
        if stripped in {f"PIN {power_pin}", f"PIN {ground_pin}"}:
            target_pin = stripped.removeprefix("PIN ")
            filtered.append(line)
            continue
        if target_pin is not None and stripped == f"END {target_pin}":
            target_pin = None
            dropping_pin_layer = False
            interface_layer = False
            filtered.append(line)
            continue
        if target_pin is not None and stripped.startswith("LAYER "):
            dropping_pin_layer = stripped != f"LAYER {layer} ;"
            interface_layer = not dropping_pin_layer
            if not dropping_pin_layer:
                filtered.append(line)
            continue
        if target_pin is not None and stripped == "END":
            dropping_pin_layer = False
            interface_layer = False
            filtered.append(line)
            continue
        if target_pin is not None and interface_layer and stripped.startswith("RECT "):
            coordinates = stripped.removesuffix(";").split()[1:]
            if len(coordinates) != 4:
                raise ValueError(f"cannot parse PG rectangle: {stripped}")
            x1, y1, x2, y2 = (float(coordinate) for coordinate in coordinates)
            width = abs(x2 - x1) if orientation == "vertical" else abs(y2 - y1)
            if width + 1e-9 < minimum_pin_width:
                continue
            interface_rectangles[target_pin] += 1
        if not dropping_pin_layer:
            filtered.append(line)

    for pin, count in interface_rectangles.items():
        if count == 0:
            raise ValueError(
                f"{pin} has no {orientation} {layer} rectangle at least "
                f"{minimum_pin_width:g} um wide"
            )

    output: list[str] = []
    in_obs = False
    dropping = False
    removed = False
    for line in filtered:
        stripped = line.strip()
        if stripped == "OBS":
            in_obs = True
            output.append(line)
            continue
        if in_obs and stripped.startswith("LAYER "):
            dropping = stripped == f"LAYER {layer} ;"
            removed = removed or dropping
            if not dropping:
                output.append(line)
            continue
        if in_obs and stripped == "END":
            in_obs = False
            dropping = False
            output.append(line)
            continue
        if not dropping:
            output.append(line)
    if not removed:
        raise ValueError(f"OBS has no {layer} section")
    path.write_text("".join(output))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lef", type=Path)
    parser.add_argument("--layer", default="MET5")
    parser.add_argument("--power-pin", default="VDD")
    parser.add_argument("--ground-pin", default="VSS")
    parser.add_argument("--orientation", choices=("horizontal", "vertical"), default="horizontal")
    parser.add_argument("--minimum-pin-width", type=float, default=0.0)
    args = parser.parse_args()
    expose(
        args.lef,
        args.layer,
        args.power_pin,
        args.ground_pin,
        args.orientation,
        args.minimum_pin_width,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, StopIteration, ValueError) as error:
        raise SystemExit(f"ERROR: {error}") from error
