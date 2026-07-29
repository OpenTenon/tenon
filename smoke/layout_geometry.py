"""Shared physical geometry for standalone ICS55 smoke layout units."""

from __future__ import annotations

import math

UNIT_DIE_WIDTH_UM = 25.0
UNIT_DIE_HEIGHT_UM = 40.0
UNIT_CORE_WIDTH_UM = 10.0
UNIT_CORE_HEIGHT_UM = 14.0
SITE_WIDTH_UM = 0.2
SITE_HEIGHT_UM = 1.4


def closest_grid(units: int) -> tuple[int, int]:
    """Return the exact-unit factor pair whose die is closest to square."""
    if units < 1:
        raise ValueError("units must be at least one")
    candidates = [
        (columns, units // columns)
        for columns in range(1, units + 1)
        if units % columns == 0
    ]
    return min(
        candidates,
        key=lambda grid: abs(
            math.log(
                (UNIT_DIE_WIDTH_UM * grid[0]) / (UNIT_DIE_HEIGHT_UM * grid[1])
            )
        ),
    )


def _floor_to_site(value: float, pitch: float) -> float:
    return round(math.floor((value / pitch) + 1e-9) * pitch, 1)


def geometry(columns: int, rows: int) -> tuple[list[float], list[float]]:
    """Return centered, site-aligned die and core rectangles for a unit grid."""
    if columns < 1 or rows < 1:
        raise ValueError("columns and rows must be at least one")
    die_width = UNIT_DIE_WIDTH_UM * columns
    die_height = UNIT_DIE_HEIGHT_UM * rows
    core_width = UNIT_CORE_WIDTH_UM * columns
    core_height = UNIT_CORE_HEIGHT_UM * rows
    core_x_min = _floor_to_site((die_width - core_width) / 2, SITE_WIDTH_UM)
    core_y_min = _floor_to_site((die_height - core_height) / 2, SITE_HEIGHT_UM)
    return (
        [0.0, 0.0, die_width, die_height],
        [
            core_x_min,
            core_y_min,
            round(core_x_min + core_width, 1),
            round(core_y_min + core_height, 1),
        ],
    )
