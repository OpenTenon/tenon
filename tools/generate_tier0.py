#!/usr/bin/env python3
"""Generate Tier0 pin manifests and LibreLane configurations from one spec."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "specs" / "tier0_profiles.json"
PDK_SPEC_PATH = ROOT / "specs" / "tier0_pdks.json"
IHP_FLOW_DIR = ROOT / "flow" / "ihp130"
SKY130_FLOW_DIR = ROOT / "flow" / "sky130"
GF180_FLOW_DIR = ROOT / "flow" / "gf180"
ICS55_FLOW_DIR = ROOT / "flow" / "ics55"
SIDES = ("south", "east", "north", "west")
INPUT_MANAGEMENT = {
    "mgmt_clk",
    "mgmt_rst_n",
    "jtag_tck",
    "jtag_tms",
    "jtag_tdi",
    "uart_rx",
}
OUTPUT_MANAGEMENT = {"jtag_tdo", "uart_tx"}
MANAGEMENT_INSTANCES = {
    "mgmt_clk": "u_mgmt_clk_pad",
    "mgmt_rst_n": "u_mgmt_rst_n_pad",
    "jtag_tck": "u_jtag_tck_pad",
    "jtag_tms": "u_jtag_tms_pad",
    "jtag_tdi": "u_jtag_tdi_pad",
    "jtag_tdo": "u_jtag_tdo_pad",
    "uart_rx": "u_uart_rx_pad",
    "uart_tx": "u_uart_tx_pad",
}
POWER_INSTANCE_PREFIX = {
    "IOVDD": "u_iovdd_pads",
    "IOVSS": "u_iovss_pads",
    "VDD": "u_vdd_pads",
    "VSS": "u_vss_pads",
}
POWER_CELLS = {
    "IOVDD": "sg13g2_IOPadIOVdd",
    "IOVSS": "sg13g2_IOPadIOVss",
    "VDD": "sg13g2_IOPadVdd",
    "VSS": "sg13g2_IOPadVss",
}
SKY130_PAD_MASTERS = {
    "GPIO": ("sky130_ef_io__gpiov2_pad_wrapped", 80.0, 210.965),
    "IOVDD": ("sky130_ef_io__vddio_hvc_pad", 75.0, 197.965),
    "IOVSS": ("sky130_ef_io__vssio_hvc_pad", 75.0, 197.965),
    "VDD": ("sky130_ef_io__vccd_hvc_pad", 75.0, 197.965),
    "VSS": ("sky130_ef_io__vssd_hvc_pad", 75.0, 197.965),
}


def pad_path(instance: str) -> str:
    """Return the Tcl-escaped hierarchical instance name expected by LibreLane."""
    escaped = instance.replace("[", r"\\[").replace("]", r"\\]")
    return f'"u_reference.u_padframe.{escaped}"'


def management_record(pin: int, side: str, slot: int, signal: str) -> dict:
    if signal in INPUT_MANAGEMENT:
        direction = "input"
        cell = "sg13g2_IOPadIn"
        suffix = "_i"
    elif signal in OUTPUT_MANAGEMENT:
        direction = "output"
        cell = "sg13g2_IOPadOut30mA"
        suffix = "_o"
    else:
        raise ValueError(f"Unknown management signal: {signal}")
    return {
        "pin": pin,
        "side": side,
        "slot": slot,
        "function": signal,
        "direction": direction,
        "cell": cell,
        "rtl_signal": f"{signal}{suffix}",
        "instance": MANAGEMENT_INSTANCES[signal],
    }


def build_records(spec: dict, profile: dict) -> list[dict]:
    leads = profile["package_leads"]
    side_size = leads // 4
    pads_per_rail = profile["pads_per_rail"]
    if leads % 4 != 0:
        raise ValueError(
            f"{profile['id']}: package lead count must be divisible by four"
        )

    power_slots = set(range(1, side_size + 1, 4))
    if len(power_slots) != pads_per_rail:
        raise ValueError(
            f"{profile['id']}: side power slots do not match pads_per_rail"
        )

    management = spec["management_by_side"]
    power_order = spec["power_rail_order"]
    power_indices = {rail: 0 for rail in power_order}
    gpio_index = 0
    power_index = 0
    records: list[dict] = []

    for side_index, side in enumerate(SIDES):
        end_slot = side_size - 1
        while end_slot in power_slots:
            end_slot -= 1
        first_signal, last_signal = management[side]
        for slot in range(1, side_size + 1):
            pin = side_index * side_size + slot
            if slot in power_slots:
                rail = power_order[power_index % len(power_order)]
                index = power_indices[rail]
                power_indices[rail] += 1
                power_index += 1
                record = {
                    "pin": pin,
                    "side": side,
                    "slot": slot,
                    "function": rail,
                    "direction": "power" if rail.endswith("VDD") else "ground",
                    "cell": POWER_CELLS[rail],
                    "rtl_signal": rail,
                    "instance": f"{POWER_INSTANCE_PREFIX[rail]}[{index}].u_pad",
                }
            elif slot == 2:
                record = management_record(pin, side, slot, first_signal)
            elif slot == end_slot:
                record = management_record(pin, side, slot, last_signal)
            else:
                record = {
                    "pin": pin,
                    "side": side,
                    "slot": slot,
                    "function": "GPIO",
                    "direction": "inout",
                    "cell": "sg13g2_IOPadInOut30mA",
                    "rtl_signal": f"gpio[{gpio_index}]",
                    "instance": f"u_gpio_pads[{gpio_index}].u_pad",
                }
                gpio_index += 1
            records.append(record)

    expected_power = 4 * pads_per_rail
    if gpio_index != profile["gpio_count"] or len(records) != leads:
        raise ValueError(f"{profile['id']}: invalid GPIO or package-pin count")
    if sum(record["function"] in power_order for record in records) != expected_power:
        raise ValueError(f"{profile['id']}: invalid power-pad count")
    if any(power_indices[rail] != pads_per_rail for rail in power_order):
        raise ValueError(f"{profile['id']}: rail count is unbalanced")
    return records


def side_placement(records: list[dict], side: str) -> list[str]:
    selected = [record for record in records if record["side"] == side]
    if side in {"north", "west"}:
        selected.reverse()
    return [pad_path(record["instance"]) for record in selected]


def yaml_list(name: str, values: list[str]) -> str:
    lines = [f"{name}:"]
    lines.extend(f"- {value}" for value in values)
    return "\n".join(lines)


def render_ihp_config(profile: dict, records: list[dict]) -> str:
    die_side = profile["die_side_um"]
    core_offset = 365
    core_end = core_offset + profile["core_side_um"]
    placements = "\n\n".join(
        yaml_list(f"PAD_{side.upper()}", side_placement(records, side))
        for side in SIDES
    )
    return f"""# Generated by tools/generate_tier0.py. Do not edit manually.
meta:
  version: 3
  flow: Chip
  substituting_steps:
    # The IO library has intentional pad/bondpad overlap reports.
    Checker.IllegalOverlap: null

DESIGN_NAME: {profile["top"]}
VERILOG_FILES:
- dir::../../rtl/tenon_tier0_padframe.sv
- dir::../../rtl/tenon_tier0_reference.sv
- dir::../../rtl/tenon_tier0_variants.sv
VERILOG_DEFINES: [FUNCTIONAL]
PRIMARY_GDSII_STREAMOUT_TOOL: klayout

{placements}

VDD_NETS: [VDD]
GND_NETS: [VSS]
CLOCK_PORT: mgmt_clk_pad
CLOCK_NET: u_reference.u_padframe.u_mgmt_clk_pad/p2c
CLOCK_PERIOD: 20

FP_SIZING: absolute
DIE_AREA: [0, 0, {die_side}, {die_side}]
CORE_AREA: [{core_offset}, {core_offset}, {core_end}, {core_end}]
PL_TARGET_DENSITY_PCT: 5
GRT_ALLOW_CONGESTION: true

PDN_CORE_RING: true
PDN_ENABLE_RAILS: true
PDN_ENABLE_PINS: false
PDN_CORE_RING_CONNECT_TO_PADS: true
PDN_CORE_RING_VWIDTH: 15
PDN_CORE_RING_HWIDTH: 15
PDN_CORE_RING_VSPACING: 5
PDN_CORE_RING_HSPACING: 5

PAD_BONDPAD_NAME: bondpad_70x70_novias
EXTRA_GDS:
- dir::../../ip/bondpad_70x70_novias/gds/bondpad_70x70_novias.gds
EXTRA_LEFS:
- dir::../../ip/bondpad_70x70_novias/lef/bondpad_70x70_novias.lef
IGNORE_DISCONNECTED_MODULES:
- bondpad_70x70_novias
MAGIC_EXT_UNIQUE: notopports
"""


def pdk_entry(pdk_spec: dict, pdk_id: str) -> dict:
    for pdk in pdk_spec["pdks"]:
        if pdk["id"] == pdk_id:
            return pdk
    raise ValueError(f"PDK {pdk_id} is not defined")


def gf180_die_side(profile: dict, gf180: dict) -> int:
    floorplan = gf180["floorplan"]
    side_pads = profile["package_leads"] // 4
    return (
        2 * floorplan["corner_width_um"]
        + 2 * floorplan["edge_spacing_um"]
        + side_pads * floorplan["pad_width_um"]
        + (side_pads + 1) * floorplan["pad_spacing_um"]
    )


def helper_pad_side_placement(records: list[dict], side: str) -> list[str]:
    """Return PDK physical PadCell paths below the Tenon helper wrappers."""
    selected = [record for record in records if record["side"] == side]
    if side in {"north", "west"}:
        selected.reverse()

    paths = []
    for record in selected:
        instance = record["instance"]
        if record["function"] not in POWER_INSTANCE_PREFIX:
            instance = f"{instance}.u_pad"
        paths.append(pad_path(instance))
    return paths


def sky130_physical_pad_instance(record: dict) -> str:
    """Return the OpenDB hierarchy of a Sky130 physical pad cell."""
    instance = record["instance"]
    if record["function"] not in POWER_INSTANCE_PREFIX:
        instance = f"{instance}.u_pad"
    return f"u_reference.u_padframe.{instance}".replace("[", r"\[").replace("]", r"\]")


def sky130_side_records(records: list[dict], side: str) -> list[dict]:
    selected = [record for record in records if record["side"] == side]
    if side in {"north", "west"}:
        selected.reverse()
    return selected


def sky130_tcl_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def sky130_origin(
    side: str, scalar: float, die_side: float, height: float
) -> tuple[float, float]:
    if side == "PAD_SOUTH":
        return scalar, 0.0
    if side == "PAD_EAST":
        return die_side - height, scalar
    if side == "PAD_NORTH":
        return scalar, die_side - height
    if side == "PAD_WEST":
        return 0.0, scalar
    raise ValueError(f"Unknown Sky130 side: {side}")


def render_sky130_placement(profile: dict, records: list[dict]) -> str:
    """Render fixed placement for native Sky130 FD IO pad cells."""
    die_side = float(profile["die_side_um"])
    side_orient = {
        "PAD_SOUTH": "R180",
        "PAD_EAST": "R270",
        "PAD_NORTH": "R0",
        "PAD_WEST": "R90",
    }
    lines = [
        "# Generated by tools/generate_tier0.py. Do not edit manually.",
        f"# Fixed Sky130 EF-IO hard-macro placement for {profile['id']}.",
    ]

    def place_cell(instance_name: str, height: float, side: str, scalar: float) -> None:
        x, y = sky130_origin(side, scalar, die_side, height)
        lines.append(
            f"place_inst -name {instance_name} "
            f"-location [list {sky130_tcl_number(x)} {sky130_tcl_number(y)}] "
            f"-orientation {side_orient[side]} -status FIRM"
        )

    for side_name, spec_side in zip(
        ("PAD_SOUTH", "PAD_EAST", "PAD_NORTH", "PAD_WEST"), SIDES, strict=True
    ):
        selected = sky130_side_records(records, spec_side)
        pad_widths = sum(
            SKY130_PAD_MASTERS[
                record["function"]
                if record["function"] in POWER_INSTANCE_PREFIX
                else "GPIO"
            ][1]
            for record in selected
        )
        side_start = 200.0
        side_end = die_side - 200.0
        available = side_end - side_start
        if pad_widths > available:
            raise ValueError(
                f"{profile['id']}: Sky130 {side_name} pads exceed the die edge"
            )
        gap = int((available - pad_widths) // (len(selected) + 1))
        edge_space = available - pad_widths - gap * (len(selected) - 1)
        position = side_start + int(edge_space // 2)
        for record in selected:
            _, width, height = SKY130_PAD_MASTERS[
                record["function"]
                if record["function"] in POWER_INSTANCE_PREFIX
                else "GPIO"
            ]
            place_cell(
                f"{{{sky130_physical_pad_instance(record)}}}",
                height,
                side_name,
                position,
            )
            position += width + gap

    lines.append("")
    return "\n".join(lines)


def render_sky130_pdn_bridge(profile: dict) -> str:
    """Render OpenDB special-net bridges visible to PSM after PDN generation."""
    die_side = profile["die_side_um"]
    core_offset = 365
    core_end = core_offset + profile["core_side_um"]
    return (
        "# Generated by tools/generate_tier0.py. Do not edit manually.\n"
        f"# Sky130 package-to-PDN bridges for {profile['id']}.\n"
        "# The four rails are routed as separate special nets.\n"
        f"set ::tenon_sky130_bridge_die_area [list 0 0 {die_side} {die_side}]\n"
        f"set ::tenon_sky130_bridge_core_area [list {core_offset} {core_offset} {core_end} {core_end}]\n"
        "\n"
        "proc tenon_sky130_bridge_box {swire layer x0 y0 x1 y1} {\n"
        "    set xlo [expr {min($x0, $x1)}]\n"
        "    set xhi [expr {max($x0, $x1)}]\n"
        "    set ylo [expr {min($y0, $y1)}]\n"
        "    set yhi [expr {max($y0, $y1)}]\n"
        "    odb::dbSBox_create $swire $layer $xlo $ylo $xhi $yhi STRIPE\n"
        "}\n"
        "\n"
        "proc tenon_sky130_bridge_horizontal {swire layer width x0 x1 y} {\n"
        "    set half [expr {$width / 2}]\n"
        "    tenon_sky130_bridge_box $swire $layer $x0 [expr {$y - $half}] $x1 [expr {$y + $half}]\n"
        "}\n"
        "\n"
        "proc tenon_sky130_bridge_vertical {swire layer width x y0 y1} {\n"
        "    set half [expr {$width / 2}]\n"
        "    tenon_sky130_bridge_box $swire $layer [expr {$x - $half}] $y0 [expr {$x + $half}] $y1\n"
        "}\n"
        "\n"
        "proc tenon_sky130_bridge_edge {x y die_x0 die_y0 die_x1 die_y1} {\n"
        "    set edge SOUTH\n"
        "    set distance [expr {$y - $die_y0}]\n"
        "    foreach {candidate candidate_distance} [list \\\n"
        "        EAST [expr {$die_x1 - $x}] \\\n"
        "        NORTH [expr {$die_y1 - $y}] \\\n"
        "        WEST [expr {$x - $die_x0}]] {\n"
        "        if {$candidate_distance < $distance} {\n"
        "            set edge $candidate\n"
        "            set distance $candidate_distance\n"
        "        }\n"
        "    }\n"
        "    return $edge\n"
        "}\n"
        "\n"
        "proc tenon_sky130_attach_source {swire m3 m4 m5 m3m4 m4m5 width source_layer x y edge left bottom right top} {\n"
        '    if {$source_layer eq "met3"} {\n'
        "        odb::dbSBox_create $swire $m3m4 $x $y STRIPE\n"
        "    }\n"
        "    switch -- $edge {\n"
        "        SOUTH - NORTH {\n"
        '            if {$source_layer eq "met5"} {\n'
        "                odb::dbSBox_create $swire $m4m5 $x $y STRIPE\n"
        "            }\n"
        "            set ring_y $bottom\n"
        '            if {$edge eq "NORTH"} {\n'
        "                set ring_y $top\n"
        "            }\n"
        "            tenon_sky130_bridge_vertical $swire $m4 $width $x $y $ring_y\n"
        "            odb::dbSBox_create $swire $m4m5 $x $ring_y STRIPE\n"
        "        }\n"
        "        WEST - EAST {\n"
        '            if {$source_layer ne "met5"} {\n'
        "                odb::dbSBox_create $swire $m4m5 $x $y STRIPE\n"
        "            }\n"
        "            set ring_x $left\n"
        '            if {$edge eq "EAST"} {\n'
        "                set ring_x $right\n"
        "            }\n"
        "            tenon_sky130_bridge_horizontal $swire $m5 $width $x $ring_x $y\n"
        "            odb::dbSBox_create $swire $m4m5 $ring_x $y STRIPE\n"
        "        }\n"
        "    }\n"
        "}\n"
        "\n"
        "proc tenon_sky130_attach_bterms {block net_name swire m3 m4 m5 m3m4 m4m5 width left bottom right top die_x0 die_y0 die_x1 die_y1} {\n"
        "    foreach bterm [odb::dbBlock_getBTerms $block] {\n"
        "        set bterm_net [$bterm getNet]\n"
        '        if {$bterm_net eq "NULL" || [$bterm_net getName] ne $net_name} {\n'
        "            continue\n"
        "        }\n"
        "        foreach bpin [$bterm getBPins] {\n"
        "            foreach box [$bpin getBoxes] {\n"
        "                set layer [$box getTechLayer]\n"
        '                if {$layer eq "NULL" || [$layer getName] ne "met5"} {\n'
        "                    continue\n"
        "                }\n"
        "                set x [expr {int(([$box xMin] + [$box xMax]) / 2)}]\n"
        "                set y [expr {int(([$box yMin] + [$box yMax]) / 2)}]\n"
        "                set edge [tenon_sky130_bridge_edge $x $y $die_x0 $die_y0 $die_x1 $die_y1]\n"
        "                tenon_sky130_attach_source $swire $m3 $m4 $m5 $m3m4 $m4m5 $width met5 $x $y $edge $left $bottom $right $top\n"
        "            }\n"
        "    }\n"
        "        }\n"
        "}\n"
        "\n"
        "proc tenon_sky130_attach_hvc {block net_name swire m3 m4 m5 m3m4 m4m5 width left bottom right top die_x0 die_y0 die_x1 die_y1} {\n"
        "    set net [$block findNet $net_name]\n"
        "    foreach iterm [$net getITerms] {\n"
        "        set term_name [$iterm getName]\n"
        '        if {![string match "*/DRN_HVC" $term_name] && ![string match "*/SRC_BDY_HVC" $term_name]} {\n'
        "            continue\n"
        "        }\n"
        "        set instance_name [[$iterm getInst] getName]\n"
        "        set is_hvc 0\n"
        "        foreach pad_array {u_iovdd_pads u_iovss_pads u_vdd_pads u_vss_pads} {\n"
        "            if {[string first $pad_array $instance_name] >= 0} {\n"
        "                set is_hvc 1\n"
        "            }\n"
        "        }\n"
        "        if {!$is_hvc} {\n"
        "            continue\n"
        "        }\n"
        "        foreach geometry [odb::dbITerm_getGeometries $iterm] {\n"
        "            set layer [lindex $geometry 0]\n"
        '            if {[$layer getName] ne "met3"} {\n'
        "                continue\n"
        "            }\n"
        "            set rect [lindex $geometry 1]\n"
        "            set x [expr {int(([$rect xMin] + [$rect xMax]) / 2)}]\n"
        "            set y [expr {int(([$rect yMin] + [$rect yMax]) / 2)}]\n"
        "            set edge [tenon_sky130_bridge_edge $x $y $die_x0 $die_y0 $die_x1 $die_y1]\n"
        "            tenon_sky130_attach_source $swire $m3 $m4 $m5 $m3m4 $m4m5 $width met3 $x $y $edge $left $bottom $right $top\n"
        "            break\n"
        "        }\n"
        "    }\n"
        "}\n"
        "\n"
        "proc tenon_sky130_attach_io_met5 {block net_name swire m3 m4 m5 m3m4 m4m5 width left bottom right top die_x0 die_y0 die_x1 die_y1} {\n"
        "    set net [$block findNet $net_name]\n"
        "    foreach iterm [$net getITerms] {\n"
        "        set instance_name [[$iterm getInst] getName]\n"
        "        set is_io 0\n"
        '        if {[string first "u_reference.u_padframe." $instance_name] == 0} {\n'
        "            set is_io 1\n"
        "        }\n"
        "        if {!$is_io} {\n"
        "            continue\n"
        "        }\n"
        "        foreach geometry [odb::dbITerm_getGeometries $iterm] {\n"
        "            set layer [lindex $geometry 0]\n"
        "            set layer_name [$layer getName]\n"
        '            if {$layer_name ne "met4" && $layer_name ne "met5"} {\n'
        "                continue\n"
        "            }\n"
        "            set rect [lindex $geometry 1]\n"
        "            set x [expr {int(([$rect xMin] + [$rect xMax]) / 2)}]\n"
        "            set y [expr {int(([$rect yMin] + [$rect yMax]) / 2)}]\n"
        "            set edge [tenon_sky130_bridge_edge $x $y $die_x0 $die_y0 $die_x1 $die_y1]\n"
        "            tenon_sky130_attach_source $swire $m3 $m4 $m5 $m3m4 $m4m5 $width $layer_name $x $y $edge $left $bottom $right $top\n"
        "        }\n"
        "    }\n"
        "}\n"
        "\n"
        "\n"
        "proc tenon_sky130_build_rail {block tech net_name margin_um connect_core} {\n"
        "    set units [$block getDefUnits]\n"
        "    set core $::tenon_sky130_bridge_core_area\n"
        "    set die $::tenon_sky130_bridge_die_area\n"
        "    set core_x0 [expr {int([lindex $core 0] * $units)}]\n"
        "    set core_y0 [expr {int([lindex $core 1] * $units)}]\n"
        "    set core_x1 [expr {int([lindex $core 2] * $units)}]\n"
        "    set core_y1 [expr {int([lindex $core 3] * $units)}]\n"
        "    set die_x0 [expr {int([lindex $die 0] * $units)}]\n"
        "    set die_y0 [expr {int([lindex $die 1] * $units)}]\n"
        "    set die_x1 [expr {int([lindex $die 2] * $units)}]\n"
        "    set die_y1 [expr {int([lindex $die 3] * $units)}]\n"
        "    set margin [expr {int($margin_um * $units)}]\n"
        "    set width [expr {int(3.2 * $units)}]\n"
        "    set left [expr {$core_x0 - $margin}]\n"
        "    set bottom [expr {$core_y0 - $margin}]\n"
        "    set right [expr {$core_x1 + $margin}]\n"
        "    set top [expr {$core_y1 + $margin}]\n"
        "    set net [$block findNet $net_name]\n"
        '    if {$net eq "NULL"} {\n'
        '        error "Missing Sky130 rail $net_name"\n'
        "    }\n"
        "    set swire [odb::dbSWire_create $net FIXED]\n"
        "    set m3 [$tech findLayer met3]\n"
        "    set m4 [$tech findLayer met4]\n"
        "    set m5 [$tech findLayer met5]\n"
        "    set m3m4 [$tech findVia M3M4_PR]\n"
        "    set m4m5 [$tech findVia M4M5_PR]\n"
        "    foreach object [list $m3 $m4 $m5 $m3m4 $m4m5] {\n"
        '        if {$object eq "NULL"} {\n'
        '            error "Sky130 bridge requires M3M4_PR and M4M5_PR technology vias"\n'
        "        }\n"
        "    }\n"
        "    set io_clearance [expr {int(220 * $units)}]\n"
        "    set io_x0 [expr {$die_x0 + $io_clearance}]\n"
        "    set io_y0 [expr {$die_y0 + $io_clearance}]\n"
        "    set io_x1 [expr {$die_x1 - $io_clearance}]\n"
        "    set io_y1 [expr {$die_y1 - $io_clearance}]\n"
        "    tenon_sky130_bridge_horizontal $swire $m5 $width $io_x0 $io_x1 $bottom\n"
        "    tenon_sky130_bridge_horizontal $swire $m5 $width $io_x0 $io_x1 $top\n"
        "    tenon_sky130_bridge_vertical $swire $m4 $width $left $io_y0 $io_y1\n"
        "    tenon_sky130_bridge_vertical $swire $m4 $width $right $io_y0 $io_y1\n"
        "    foreach x [list $left $right] {\n"
        "        foreach y [list $bottom $top] {\n"
        "            odb::dbSBox_create $swire $m4m5 $x $y STRIPE\n"
        "        }\n"
        "    }\n"
        "    tenon_sky130_attach_bterms $block $net_name $swire $m3 $m4 $m5 $m3m4 $m4m5 $width $left $bottom $right $top $die_x0 $die_y0 $die_x1 $die_y1\n"
        "    tenon_sky130_attach_hvc $block $net_name $swire $m3 $m4 $m5 $m3m4 $m4m5 $width $left $bottom $right $top $die_x0 $die_y0 $die_x1 $die_y1\n"
        "    tenon_sky130_attach_io_met5 $block $net_name $swire $m3 $m4 $m5 $m3m4 $m4m5 $width $left $bottom $right $top $die_x0 $die_y0 $die_x1 $die_y1\n"
        "    if {$connect_core} {\n"
        "        set actual_core [$block getCoreArea]\n"
        "        set actual_core_x0 [$actual_core xMin]\n"
        "        set actual_core_y0 [$actual_core yMin]\n"
        '        if {$net_name eq "vccd"} {\n'
        "            set grid_x [expr {$actual_core_x0 + int(16.32 * $units)}]\n"
        "            set grid_y [expr {$actual_core_y0 + int(16.65 * $units)}]\n"
        "            tenon_sky130_bridge_horizontal $swire $m5 $width $left $grid_x $grid_y\n"
        "            odb::dbSBox_create $swire $m4m5 $left $grid_y STRIPE\n"
        "            odb::dbSBox_create $swire $m4m5 $grid_x $grid_y STRIPE\n"
        "        } else {\n"
        "            set grid_x [expr {$actual_core_x0 + int((16.32 + 3.2 + 1.7) * $units)}]\n"
        "            set grid_y [expr {$actual_core_y0 + int((16.65 + 3.2 + 1.7) * $units)}]\n"
        "            tenon_sky130_bridge_horizontal $swire $m5 $width $left $grid_x $grid_y\n"
        "            odb::dbSBox_create $swire $m4m5 $left $grid_y STRIPE\n"
        "            odb::dbSBox_create $swire $m4m5 $grid_x $grid_y STRIPE\n"
        "        }\n"
        "    }\n"
        "\n"
        "}\n"
        "set tenon_sky130_bridge_block [ord::get_db_block]\n"
        "set tenon_sky130_bridge_tech [[ord::get_db] getTech]\n"
        "foreach {net_name margin_um connect_core} {vccd 20 1 vssd 40 1 vddio 60 0 vssio 80 0} {\n"
        "    tenon_sky130_build_rail $tenon_sky130_bridge_block $tenon_sky130_bridge_tech $net_name $margin_um $connect_core\n"
        "}\n"
    )


def render_sky130_config(profile: dict, records: list[dict], sky130: dict) -> str:
    die_side = profile["die_side_um"]
    core_offset = 365
    core_end = core_offset + profile["core_side_um"]
    placements = "\n\n".join(
        yaml_list(f"PAD_{side.upper()}", helper_pad_side_placement(records, side))
        for side in SIDES
    )
    return f"""# Generated by tools/generate_tier0.py. Do not edit manually.
# Sky130A / sky130_ef_io Tier0 reference configuration.
meta:
  version: 3
  flow: Chip
  substituting_steps:
    # The PDK power-connection utility cannot represent unused HVC pad pins.
    Odb.SetPowerConnections: null
DESIGN_NAME: {sky130["package_top_prefix"]}{profile["id"]}
VERILOG_FILES:
- dir::../../rtl/tenon_tier0_padframe_sky130.sv
- dir::../../rtl/tenon_tier0_pdk_reference.sv
- dir::../../rtl/tenon_tier0_pdk_variants.sv
VERILOG_DEFINES: [USE_POWER_PINS]
EXTRA_VERILOG_MODELS:
- pdk_dir::libs.ref/sky130_fd_io/verilog/sky130_fd_io__blackbox_pp.v
- dir::sky130_ef_io_blackbox.v
EXTRA_LEFS:
- pdk_dir::libs.ref/sky130_fd_io/lef/sky130_fd_io.lef
- pdk_dir::libs.ref/sky130_fd_io/lef/sky130_ef_io.lef
EXTRA_GDS:
- pdk_dir::libs.ref/sky130_fd_io/gds/sky130_fd_io.gds
- pdk_dir::libs.ref/sky130_fd_io/gds/sky130_ef_io.gds
- pdk_dir::libs.ref/sky130_fd_io/gds/sky130_ef_io__gpiov2_pad_wrapped.gds
LIB:
  "*_tt_025C_1v80":
  - pdk_dir::libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib
  "*_ff_n40C_1v95":
  - pdk_dir::libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__ff_n40C_1v95.lib
  "*_ss_100C_1v60":
  - pdk_dir::libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__ss_100C_1v60.lib
DEFAULT_CORNER: nom_tt_025C_1v80
PAD_LIBS: null
PAD_VERILOG_MODELS: null
EXTRA_LIBS:
- pdk_dir::libs.ref/sky130_fd_io/lib/sky130_ef_io__gpiov2_pad_wrapped_tt_tt_025C_1v80_3v30.lib
PAD_CFG: dir::pad_cfg.tcl
PRIMARY_GDSII_STREAMOUT_TOOL: klayout

RUN_MAGIC_STREAMOUT: false
{placements}

# vccd/vssd are Sky130 core rails; vddio/vssio are the separate IO rails.
VDD_NETS: [vccd, vddio]
GND_NETS: [vssd, vssio]
CLOCK_PORT: mgmt_clk_pad
CLOCK_NET: u_reference.u_padframe.u_mgmt_clk_pad.u_pad/IN
CLOCK_PERIOD: 20

FP_SIZING: absolute
DIE_AREA: [0, 0, {die_side}, {die_side}]
CORE_AREA: [{core_offset}, {core_offset}, {core_end}, {core_end}]
PL_TARGET_DENSITY_PCT: 5
GRT_ALLOW_CONGESTION: true
PL_MAX_DISPLACEMENT_Y: 200

PAD_EDGE_SPACING: 0
PAD_PLACE_IO_TERMINALS:
- sky130_ef_io__gpiov2_pad_wrapped/PAD
- sky130_ef_io__vccd_hvc_pad/VCCD_PAD
- sky130_ef_io__vddio_hvc_pad/VDDIO_PAD
- sky130_ef_io__vssd_hvc_pad/VSSD_PAD
- sky130_ef_io__vssio_hvc_pad/VSSIO_PAD

PDN_CFG: dir::pdn.tcl
PDN_ENABLE_PINS: true
PDN_ENABLE_RAILS: true
PDN_SKIPTRIM: true
PDN_VERTICAL_LAYER: met4
PDN_HORIZONTAL_LAYER: met5
PDN_VWIDTH: 3.2
PDN_HWIDTH: 3.2
PDN_VSPACING: 1.7
PDN_HSPACING: 1.7
PDN_VPITCH: 153.6
PDN_HPITCH: 153.18
PDN_VOFFSET: 16.32
PDN_HOFFSET: 16.65
PDN_CORE_RING: true
PDN_CORE_RING_CONNECT_TO_PADS: false
PDN_CORE_RING_VWIDTH: 10
PDN_CORE_RING_HWIDTH: 10
PDN_CORE_RING_VSPACING: 5
PDN_CORE_RING_HSPACING: 5
PDN_CORE_RING_VOFFSET: 6
PDN_CORE_RING_HOFFSET: 6
PDN_CORE_VERTICAL_LAYER: met4
PDN_CORE_HORIZONTAL_LAYER: met5
RUN_IRDROP_REPORT: true
ERROR_ON_PDN_VIOLATIONS: true
MAGIC_EXT_UNIQUE: notopports
MAGIC_EXT_USE_GDS: false
MAGIC_EXT_ABSTRACT_CELLS:
- ^sky130_ef_io__gpiov2_pad_wrapped$
- ^sky130_ef_io__gpiov2_pad$
- ^sky130_fd_io__top_gpiov2$
- ^sky130_ef_io__v(ccd|ddio|ssd|ssio)_hvc_pad$
- ^sky130_fd_io__top_ground_hvc_wpad$
- ^sky130_fd_io__top_power_hvc_wpadv2$
- ^sky130_fd_io__hvc_clampv2$
ERROR_ON_ILLEGAL_OVERLAPS: true
ERROR_ON_LVS_ERROR: true
"""


def render_gf180_config(profile: dict, records: list[dict], gf180: dict) -> str:
    die_side = gf180_die_side(profile, gf180)
    core_offset = gf180["floorplan"]["core_offset_um"]
    core_end = die_side - core_offset
    placements = "\n\n".join(
        yaml_list(f"PAD_{side.upper()}", helper_pad_side_placement(records, side))
        for side in SIDES
    )
    return f"""# Generated by tools/generate_tier0.py. Do not edit manually.
# GF180MCU D / gf180mcu_ocd_io Tier0 reference configuration.
meta:
  version: 3
  flow: Chip
  substituting_steps:
    # Preserve the OCD IO cells' explicit four-rail RTL connectivity.
    Odb.SetPowerConnections: null

DESIGN_NAME: {gf180["package_top_prefix"]}{profile["id"]}
VERILOG_FILES:
- dir::../../rtl/tenon_tier0_padframe_gf180.sv
- dir::../../rtl/tenon_tier0_pdk_reference.sv
- dir::../../rtl/tenon_tier0_pdk_variants.sv
EXTRA_VERILOG_MODELS:
- pdk_dir::libs.ref/gf180mcu_ocd_io/verilog/gf180mcu_ocd_io__blackbox_pp.v
LIB:
  "*_tt_025C_5v00":
  - pdk_dir::libs.ref/gf180mcu_fd_sc_mcu7t5v0/lib/gf180mcu_fd_sc_mcu7t5v0__tt_025C_5v00.lib
  "*_ff_n40C_5v50":
  - pdk_dir::libs.ref/gf180mcu_fd_sc_mcu7t5v0/lib/gf180mcu_fd_sc_mcu7t5v0__ff_n40C_5v50.lib
  "*_ss_125C_4v50":
  - pdk_dir::libs.ref/gf180mcu_fd_sc_mcu7t5v0/lib/gf180mcu_fd_sc_mcu7t5v0__ss_125C_4v50.lib
DEFAULT_CORNER: nom_tt_025C_5v00
PAD_LIBS: null
PAD_VERILOG_MODELS: null
EXTRA_LIBS:
- pdk_dir::libs.ref/gf180mcu_ocd_io/lib/gf180mcu_ocd_io__tt_025C_5v00.lib
PAD_CFG: dir::pad_cfg.tcl
PRIMARY_GDSII_STREAMOUT_TOOL: klayout

{placements}

# VDD/VSS are the GF180 standard-cell core rails; iovdd/iovss feed OCD IO rails.
VDD_NETS: [vdd, iovdd]
GND_NETS: [vss, iovss]
CLOCK_PORT: mgmt_clk_pad
CLOCK_NET: u_reference.u_padframe.u_mgmt_clk_pad.u_pad/Y
CLOCK_PERIOD: 20

FP_SIZING: absolute
DIE_AREA: [0, 0, {die_side}, {die_side}]
CORE_AREA: [{core_offset}, {core_offset}, {core_end}, {core_end}]
PL_TARGET_DENSITY_PCT: 5
GRT_ALLOW_CONGESTION: true
PL_MAX_DISPLACEMENT_Y: 200

PDN_CFG: dir::pdn.tcl
PDN_ENABLE_PINS: false
PDN_VWIDTH: 5
PDN_HWIDTH: 5
PDN_VSPACING: 1
PDN_HSPACING: 1
PDN_VPITCH: 75
PDN_HPITCH: 75
PDN_CORE_RING: true
PDN_CORE_RING_CONNECT_TO_PADS: true
PDN_CORE_RING_VWIDTH: 25
PDN_CORE_RING_HWIDTH: 25
PDN_CORE_VERTICAL_LAYER: Metal4
PDN_CORE_HORIZONTAL_LAYER: Metal3

IGNORE_DISCONNECTED_MODULES:
- gf180mcu_ocd_io__bi_24t
MAGIC_EXT_UNIQUE: notopports
KLAYOUT_FILLER_OPTIONS:
  Metal2_ignore_active: true
"""


ICS55_POWER_CELLS = {"IOVDD": "PVDD2", "IOVSS": "PVSS2", "VDD": "PVDD1", "VSS": "PVSS1"}


ICS55_COMPACT_FILLERS = (
    ("PFILL50", 50.0),
    ("PFILL20", 20.0),
    ("PFILL10", 10.0),
    ("PFILL5", 5.0),
    ("PFILL2", 2.0),
    ("PFILL1", 1.0),
    ("PFILL01", 0.1),
    ("PFILL001", 0.01),
)


def ics55_geometry(profile: dict, ics55: dict, variant: dict) -> dict[str, object]:
    """Return the PDK-specific floorplan, preserving shared profile geometry."""
    floorplan = ics55["floorplan"]
    compact = variant.get("pdn_mode") == "commercial-pad-row"
    die_side = float(profile["die_side_um"])
    core_offset = float(floorplan["core_offset_um"])
    if compact:
        override = ics55.get("no_pll_floorplan_overrides", {}).get(profile["id"])
        if override is not None:
            die_side = float(override["die_side_um"])
            core_offset = float(override["core_offset_um"])
    core_side = die_side - 2 * core_offset
    if core_side <= 0:
        raise ValueError(f"{profile['id']} {variant['id']}: non-positive core area")
    return {
        "compact": compact,
        "die_side": die_side,
        "core_offset": core_offset,
        "core_side": core_side,
    }


def ics55_variant_for_profile(variant: dict, profile: dict) -> dict:
    """Apply an ICS55 physical-adapter override without changing its package API."""
    effective = dict(variant)
    effective.update(variant.get("profile_overrides", {}).get(profile["id"], {}))
    return effective


def ics55_record_width(record: dict, compact: bool) -> float:
    if compact and record["cell"] == "PVDD1":
        return 35.0
    return float(record["base_width"])


def ics55_decompose_filler(width: float) -> list[str]:
    """Use the commercial IO filler family to exactly cover an edge gap."""
    remaining = round(width, 2)
    cells: list[str] = []
    for cell, cell_width in ICS55_COMPACT_FILLERS:
        count = int((remaining + 1e-6) // cell_width)
        cells.extend([cell] * count)
        remaining = round(remaining - count * cell_width, 2)
    if abs(remaining) > 1e-6:
        raise ValueError(f"ICS55 filler gap {width} um cannot be decomposed")
    return cells


def ics55_compact_fillers(
    profile: dict, records: list[dict], ics55: dict, variant: dict, geometry: dict[str, float | bool]
) -> dict[str, dict[str, list[str]]]:
    if not geometry["compact"]:
        return {}
    die_side = float(geometry["die_side"])
    floorplan = ics55["floorplan"]
    usable_span = die_side - 2 * float(floorplan["corner_size_um"])
    filler_width = float(floorplan["filler_width_um"])
    result: dict[str, dict[str, list[str]]] = {}
    for spec_side in SIDES:
        selected = sky130_side_records(records, spec_side)
        pad_span = sum(ics55_record_width(record, True) + filler_width for record in selected)
        residual = round(usable_span - pad_span, 2)
        if residual < 0:
            raise ValueError(f"{profile['id']} {spec_side}: compact pad row exceeds die edge")
        leading = float(int(residual // 2))
        trailing = round(residual - leading, 2)
        result[spec_side] = {
            "leading": ics55_decompose_filler(leading),
            "trailing": ics55_decompose_filler(trailing),
        }
    return result


def render_ics55_no_pll_fillers(
    filler_layouts: dict[str, dict[str, dict[str, list[str]]]]
) -> str:
    lines = [
        "// Generated by tools/generate_tier0.py. Do not edit manually.",
        "`default_nettype none",
        "",
    ]
    for profile_id, fillers in filler_layouts.items():
        lines.extend(("", f"module tenon_ics55_{profile_id}_no_pll_fillers ();"))
        for side in SIDES:
            for edge in ("leading", "trailing"):
                for index, cell in enumerate(fillers[side][edge]):
                    lines.append(f'  (* keep = "true" *) {cell} u_{side}_{edge}_{index} ();')
        lines.extend(["endmodule", ""])
    lines.extend(["`default_nettype wire", ""])
    return chr(10).join(lines)


def ics55_path(relative: str) -> str:
    return f"u_reference.u_padframe.{relative}".replace("[", chr(92) + "[").replace("]", chr(92) + "]")


def ics55_gpio_reservation(records: list[dict], variant: dict) -> dict[int, str]:
    if not variant["include_pll"]:
        return {}
    reservation = variant["gpio_reservations"]
    selected: dict[int, str] = {}
    for side, functions in reservation.items():
        gpio_records = [record for record in records if record["side"] == side and record["function"] == "GPIO"]
        if len(gpio_records) < len(functions):
            raise ValueError(f"ICS55 {side} has insufficient GPIOs for PLL reservation")
        selected.update(
            {record["pin"]: function for record, function in zip(gpio_records, functions)}
        )
    return selected


def build_ics55_records(spec: dict, profile: dict, variant: dict) -> list[dict]:
    """Apply the ICS55-only PB4/PLL package contract to the shared profile."""
    records = [dict(record) for record in build_records(spec, profile)]
    reservations = ics55_gpio_reservation(records, variant)
    prefix = "u_base." if variant["include_pll"] or variant.get("pdn_mode") == "commercial-pad-row" else ""
    gpio_index = 0
    power_indices = {rail: 0 for rail in POWER_INSTANCE_PREFIX}
    special = {
        "PLL_AVSS": ("PVSS1CAP", "pll_avss", "u_pll_avss_pad", 1),
        "PLL_AVDD": ("PVDD1CAP", "pll_avdd", "u_pll_avdd_pad", 0),
        "AVSSIO": ("PVSS3AP", "pll_avssio", "u_pll_avssio_pad", 1),
        "AVDDIO": ("PVDD3AP", "pll_avddio", "u_pll_avddio_pad", 0),
    }
    for record in records:
        reserved = reservations.get(record["pin"])
        if reserved in special:
            cell, signal, instance, use_padi = special[reserved]
            record.update({
                "function": reserved,
                "direction": "ground" if "VSS" in reserved else "power",
                "cell": cell,
                "rtl_signal": signal,
                "instance": f"{instance}.u_pad",
                "physical_instance": f"{instance}.u_pad",
                "bond_instance": f"{instance}.u_package_overlay.u_physical.u_package_pad",
                "filler_instance": f"{instance}.u_filler",
                "bond_cell": "PADI30" if use_padi else "PADO30",
                "base_width": 35.0 if cell == "PVDD1CAP" else 30.0,
            })
            continue
        if reserved in {"XIN", "XOUT"}:
            record.update({
                "function": reserved,
                "direction": "inout",
                "cell": "PXWE1",
                "rtl_signal": "pll_xin_pad" if reserved == "XIN" else "pll_xout_pad",
                "instance": "u_pll_osc_pad.u_pad",
                "physical_instance": "u_pll_osc_pad.u_pad",
                "bond_instance": "u_pll_osc_pad.u_xin_overlay" if reserved == "XIN" else "u_pll_osc_pad.u_xout_overlay",
                "filler_instance": "u_pll_osc_pad.u_filler",
                "bond_cell": "PADO30" if reserved == "XIN" else "PADI30",
                "oscillator": True,
                "base_width": 60.0,
            })
            continue
        if record["function"] in ICS55_POWER_CELLS:
            rail = record["function"]
            index = power_indices[rail]
            power_indices[rail] += 1
            stem = record["instance"].replace(".u_pad", ".u_cell")
            record.update({
                "cell": ICS55_POWER_CELLS[rail],
                "instance": f"{prefix}{stem}.u_pad",
                "physical_instance": f"{prefix}{stem}.u_pad",
                "bond_instance": f"{prefix}{stem}.u_package_overlay.u_physical.u_package_pad",
                "filler_instance": f"{prefix}{stem}.u_filler",
                "bond_cell": "PADI30" if index % 2 else "PADO30",
                "base_width": 30.0,
            })
            continue
        if record["function"] == "GPIO":
            stem = f"{prefix}u_gpio_pads[{gpio_index}].u_cell"
            record.update({
                "cell": "PB4",
                "rtl_signal": f"gpio[{gpio_index}]",
                "instance": f"{stem}.u_pad",
                "physical_instance": f"{stem}.u_pad",
                "bond_instance": f"{stem}.u_package_overlay.u_physical.u_package_pad",
                "filler_instance": f"{stem}.u_filler",
                "bond_cell": "PADI30" if gpio_index % 2 else "PADO30",
                "base_width": 30.0,
            })
            gpio_index += 1
            continue
        stem = f"{prefix}{record['instance']}"
        record.update({
            "cell": "PB4",
            "instance": f"{stem}.u_pad",
            "physical_instance": f"{stem}.u_pad",
            "bond_instance": f"{stem}.u_package_overlay.u_physical.u_package_pad",
            "filler_instance": f"{stem}.u_filler",
            "bond_cell": "PADI30" if record["pin"] % 2 else "PADO30",
            "base_width": 30.0,
        })
    expected_gpio = profile["gpio_count"] - (6 if variant["include_pll"] else 0)
    if gpio_index != expected_gpio or len(records) != profile["package_leads"]:
        raise ValueError(f"{profile['id']} {variant['id']}: invalid package contract")
    return records


def ics55_side_pad_placement(records: list[dict], side: str) -> list[str]:
    paths = []
    seen = set()
    for record in sky130_side_records(records, side):
        path = ics55_path(record["physical_instance"])
        if path not in seen:
            paths.append(path)
            seen.add(path)
    return paths


def ics55_origin(side: str, scalar: float, die_side: float, height: float) -> tuple[float, float]:
    if side == "PAD_SOUTH":
        return scalar, 0.0
    if side == "PAD_EAST":
        return die_side - height, scalar
    if side == "PAD_NORTH":
        return scalar, die_side - height
    return 0.0, scalar


def render_ics55_placement(
    profile: dict,
    records: list[dict],
    ics55: dict,
    variant: dict,
    geometry: dict[str, float | bool],
    compact_fillers: dict[str, dict[str, list[str]]],
) -> str:
    """Render commercial PB4, bond-pad overlays, fillers, corners, and PLL macro."""
    die_side = float(geometry["die_side"])
    compact = bool(geometry["compact"])
    floorplan = ics55["floorplan"]
    pad_height = float(floorplan["pad_height_um"])
    filler_width = float(floorplan["filler_width_um"])
    corner_size = float(floorplan["corner_size_um"])
    filler_widths = dict(ICS55_COMPACT_FILLERS)
    side_orient = {"PAD_SOUTH": "R0", "PAD_EAST": "R90", "PAD_NORTH": "R180", "PAD_WEST": "R270"}
    wrapper_prefix = "u_base." if variant["include_pll"] or variant.get("pdn_mode") == "commercial-pad-row" else ""
    filler_hierarchy = f"u_{profile['id']}_no_pll_fillers.u_fillers" if compact else ""
    lines = [
        "# Generated by tools/generate_tier0.py. Do not edit manually.",
        f"# Commercial ICS55 PB4 package-pad placement for {profile['id']} {variant['id']}.",
    ]

    def place(instance: str, x: float, y: float, orientation: str) -> None:
        lines.append(
            f"place_inst -name {{{ics55_path(instance)}}} -location [list {sky130_tcl_number(x)} {sky130_tcl_number(y)}] -orientation {orientation} -status FIRM"
        )

    for side_name, spec_side in zip(("PAD_SOUTH", "PAD_EAST", "PAD_NORTH", "PAD_WEST"), SIDES, strict=True):
        selected = sky130_side_records(records, spec_side)
        orientation = side_orient[side_name]
        if compact:
            scalar = corner_size
            for index, cell in enumerate(compact_fillers[spec_side]["leading"]):
                x, y = ics55_origin(side_name, scalar, die_side, pad_height)
                place(f"{wrapper_prefix}{filler_hierarchy}.u_{spec_side}_leading_{index}", x, y, orientation)
                scalar += filler_widths[cell]
        else:
            spans = []
            index = 0
            while index < len(selected):
                record = selected[index]
                if record.get("oscillator"):
                    if index + 1 >= len(selected) or not selected[index + 1].get("oscillator"):
                        raise ValueError("PXWE1 package pins must be adjacent")
                    spans.append(70.0)
                    index += 2
                else:
                    spans.append(ics55_record_width(record, False) + filler_width)
                    index += 1
            scalar = (die_side - sum(spans)) / 2
        index = 0
        while index < len(selected):
            record = selected[index]
            if record.get("oscillator"):
                pair = selected[index : index + 2]
                x, y = ics55_origin(side_name, scalar, die_side, pad_height)
                place(record["physical_instance"], x, y, orientation)
                for offset, bond_record in zip((0.0, 30.0), pair, strict=True):
                    bx, by = ics55_origin(side_name, scalar + offset, die_side, 228.0)
                    overlay_height = 63.8 if bond_record["bond_cell"] == "PADO30" else 139.8
                    if side_name == "PAD_NORTH":
                        by = die_side - overlay_height
                    elif side_name == "PAD_EAST":
                        bx = die_side - overlay_height
                    place(bond_record["bond_instance"], bx, by, orientation)
                fx, fy = ics55_origin(side_name, scalar + 60.0, die_side, pad_height)
                place(record["filler_instance"], fx, fy, orientation)
                scalar += 70.0
                index += 2
                continue
            pad_width = ics55_record_width(record, compact)
            x, y = ics55_origin(side_name, scalar, die_side, pad_height)
            place(record["physical_instance"], x, y, orientation)
            overlay_height = 63.8 if record["bond_cell"] == "PADO30" else 139.8
            bx, by = x, y
            if side_name == "PAD_NORTH":
                by = die_side - overlay_height
            elif side_name == "PAD_EAST":
                bx = die_side - overlay_height
            if compact and record["cell"] == "PVDD1":
                if side_name in {"PAD_SOUTH", "PAD_NORTH"}:
                    bx += 2.5
                else:
                    by += 2.5
            place(record["bond_instance"], bx, by, orientation)
            fx, fy = ics55_origin(side_name, scalar + pad_width, die_side, pad_height)
            place(record["filler_instance"], fx, fy, orientation)
            scalar += pad_width + filler_width
            index += 1
        if compact:
            for fill_index, cell in enumerate(compact_fillers[spec_side]["trailing"]):
                x, y = ics55_origin(side_name, scalar, die_side, pad_height)
                place(f"{wrapper_prefix}{filler_hierarchy}.u_{spec_side}_trailing_{fill_index}", x, y, orientation)
                scalar += filler_widths[cell]
            if abs(scalar - (die_side - corner_size)) > 1e-6:
                raise ValueError(f"{profile['id']} {spec_side}: compact edge does not close")
    base_prefix = wrapper_prefix
    for instance_name, x, y, orientation in (
        ("u_corner_sw", 0.0, 0.0, "R0"),
        ("u_corner_se", die_side - corner_size, 0.0, "R90"),
        ("u_corner_ne", die_side - corner_size, die_side - corner_size, "R180"),
        ("u_corner_nw", 0.0, die_side - corner_size, "R270"),
    ):
        place(f"{base_prefix}{instance_name}", x, y, orientation)
    if variant["include_pll"]:
        core_end = float(geometry["core_offset"]) + float(geometry["core_side"])
        pll_x = (die_side - float(floorplan["pll_width_um"])) / 2
        pll_y = core_end - float(floorplan["pll_height_um"]) - float(floorplan["pll_halo_um"])
        place("u_pll", pll_x, pll_y, "R0")
    lines.append("")
    return "\n".join(lines)

def render_ics55_config(
    profile: dict, records: list[dict], ics55: dict, variant: dict, geometry: dict[str, float | bool]
) -> str:
    die_side = float(geometry["die_side"])
    core_offset = float(geometry["core_offset"])
    core_end = core_offset + float(geometry["core_side"])
    placements = "\n\n".join(
        yaml_list(f"PAD_{side.upper()}", ics55_side_pad_placement(records, side))
        for side in SIDES
    )
    compact_rtl = ""
    compact_filler_ignores = ""
    compact_filler_abstracts = ""
    if geometry["compact"]:
        compact_rtl = "- dir::../../rtl/tenon_tier0_padframe_ics55_no_pll_fillers.sv\n"
        compact_filler_ignores = "\n- PFILL50\n- PFILL20\n- PFILL5\n- PFILL2\n- PFILL1\n- PFILL01\n- PFILL001"
        compact_filler_abstracts = "\n- ^PFILL50$\n- ^PFILL20$\n- ^PFILL5$\n- ^PFILL2$\n- ^PFILL1$\n- ^PFILL01$\n- ^PFILL001$"
    pll_assets = ""
    pll_rails = ""
    pll_terminals = "\n"
    pll_disconnected_ignores = ""
    pdn_cfg = "pdn.tcl"
    pad_cfg = "pad_cfg.tcl"
    if variant["include_pll"]:
        pll_assets = """- pdk_dir::libs.ref/ics55_pll/lef/PLL_TOP.lef\n"""
        pll_assets += """EXTRA_GDS:\n- pdk_dir::libs.ref/ics55_io_3p3/gds/SP55NLLD2P_3P3V_V0p5_ics.gds\n- pdk_dir::libs.ref/ics55_pll/gds/PLL_TOP.gds\n"""
        pll_rails = ", pll_avdd, pll_avddio]\nGND_NETS: [vss, iovss, pll_avss, pll_avssio"
        pll_terminals = """\n- PVDD1CAP/SVDD1CAP\n- PVSS1CAP/SVSS1CAP\n- PVDD3AP/SAVDD\n- PVSS3AP/SAVSS\n- PXWE1/XIN\n- PXWE1/XOUT\n"""
        # These physical supply pads intentionally expose only their ground pin.
        # PSM remains the connectivity authority for the corresponding PLL rails.
        pll_disconnected_ignores = """\n- PVSS1CAP\n- PVSS3AP"""
        pdn_cfg = "pdn_pll.tcl"
    elif variant.get("pdn_mode") == "commercial-pad-row":
        pll_assets = """EXTRA_GDS:\n- pdk_dir::libs.ref/ics55_io_3p3/gds/SP55NLLD2P_3P3V_V0p5_ics.gds\n"""
        pll_rails = "]\nGND_NETS: [vss, iovss"
        pdn_cfg = "pdn_pb4_no_pll.tcl"
        pad_cfg = "pad_cfg_pb4_no_pll.tcl"
    else:
        pll_assets = """EXTRA_GDS:\n- pdk_dir::libs.ref/ics55_io_3p3/gds/SP55NLLD2P_3P3V_V0p5_ics.gds\n"""
        pll_rails = "]\nGND_NETS: [vss, iovss"
    extra_libs = "- pdk_dir::libs.ref/ics55_io_3p3/lib/SP55NLLD2P_3P3V_V0p3_tt_v1p20_25C.lib"
    if variant["include_pll"]:
        extra_libs += "\n- pdk_dir::libs.ref/ics55_pll/lib/PLL_TOP_typ.lib"
    return f"""# Generated by tools/generate_tier0.py. Do not edit manually.
# ICS55 H7CR commercial PB4 {variant['id']} Tier0 reference configuration.
meta:
  version: 3
  flow: Chip
  substituting_steps:
    Odb.SetPowerConnections: null

DESIGN_NAME: {ics55['package_top_prefix']}{profile['id']}_{variant['top_suffix']}
VERILOG_FILES:
- dir::../../rtl/tenon_tier0_padframe_ics55.sv
- dir::../../rtl/tenon_tier0_pdk_reference.sv
- dir::../../rtl/tenon_tier0_pdk_variants.sv
{compact_rtl}EXTRA_VERILOG_MODELS:
- dir::ics55_io_blackbox.v
EXTRA_LEFS:
- pdk_dir::libs.ref/ics55_io_3p3/lef/SP55NLLD2P_3P3V_V0p4a_6MT_1TM.lef
{pll_assets}LIB:
  "*_tt_1p2_25C":
  - pdk_dir::libs.ref/ics55_LLSC_H7CR/lib/ics55_LLSC_H7CR_typ_tt_1p2_25_nldm.lib
  "*_ff_1p32_m40C":
  - pdk_dir::libs.ref/ics55_LLSC_H7CR/lib/ics55_LLSC_H7CR_ff_rcbest_1p32_m40_nldm.lib
  "*_ss_1p08_125C":
  - pdk_dir::libs.ref/ics55_LLSC_H7CR/lib/ics55_LLSC_H7CR_ss_rcworst_1p08_125_nldm.lib
DEFAULT_CORNER: nom_tt_1p2_25C
PAD_LIBS: null
PAD_VERILOG_MODELS: null
EXTRA_LIBS:
{extra_libs}
PAD_CFG: dir::{pad_cfg}
PRIMARY_GDSII_STREAMOUT_TOOL: klayout
RUN_MAGIC_STREAMOUT: false

{placements}

VDD_NETS: [vdd, iovdd{pll_rails}]
CLOCK_PORT: mgmt_clk_pad
CLOCK_NET: u_reference.u_padframe.u_mgmt_clk_pad.u_pad/C
CLOCK_PERIOD: 20

FP_SIZING: absolute
DIE_AREA: [0, 0, {sky130_tcl_number(die_side)}, {sky130_tcl_number(die_side)}]
CORE_AREA: [{sky130_tcl_number(core_offset)}, {sky130_tcl_number(core_offset)}, {sky130_tcl_number(core_end)}, {sky130_tcl_number(core_end)}]
PL_TARGET_DENSITY_PCT: 5
GRT_ALLOW_CONGESTION: true
PL_MAX_DISPLACEMENT_Y: 200
DRT_THREADS: {ics55['detailed_routing_threads']}
DRT_OPT_ITERS: {ics55['detailed_routing_optimization_iterations']}

PAD_PLACE_IO_TERMINALS:
- PVDD1/VDD
- PVSS1/VSS
- PVDD2/VDD25
- PVSS2/VSSD
- PB4/PAD{pll_terminals}PDN_CFG: dir::{pdn_cfg}
PDN_ENABLE_GLOBAL_CONNECTIONS: false
PDN_ENABLE_PINS: false
PDN_ENABLE_RAILS: true
PDN_RAIL_LAYER: MET1
PDN_RAIL_WIDTH: 0.09
PDN_VERTICAL_LAYER: MET4
PDN_HORIZONTAL_LAYER: MET5
PDN_VWIDTH: 1
PDN_HWIDTH: 1
PDN_VSPACING: 5
PDN_HSPACING: 5
PDN_VPITCH: 12
PDN_HPITCH: 12
PDN_VOFFSET: 0.5
PDN_HOFFSET: 0.5
PDN_CORE_RING: true
PDN_CORE_RING_CONNECT_TO_PADS: true
PDN_CORE_RING_VWIDTH: 8
PDN_CORE_RING_HWIDTH: 8
PDN_CORE_RING_VSPACING: 2
PDN_CORE_RING_HSPACING: 2
PDN_CORE_RING_VOFFSET: 4
PDN_CORE_RING_HOFFSET: 4
PDN_CORE_VERTICAL_LAYER: T4M2
PDN_CORE_HORIZONTAL_LAYER: RDL
# These commercial mechanical cells intentionally have no power pins. PB4,
# regular supply pads, PXWE1 and PLL_TOP retain disconnected-pin checking.
# PLL-only PVSS supply pads are ground-only by construction and remain subject
# to their dedicated PSM connectivity checks.
IGNORE_DISCONNECTED_MODULES:
- PADI30
- PADO30
- PFILL10{compact_filler_ignores}
- PCORNER{pll_disconnected_ignores}
RUN_TAP_ENDCAP_INSERTION: false
RUN_FILL_INSERTION: false
MAGIC_EXT_UNIQUE: notopports
MAGIC_EXT_USE_GDS: false
MAGIC_EXT_ABSTRACT: true
MAGIC_EXT_ABSTRACT_CELLS:
- ^PB4$
- ^PADI30$
- ^PADO30$
- ^PFILL10${compact_filler_abstracts}
- ^PVDD.*$
- ^PVSS.*$
- ^PXWE1$
- ^PCORNER$
- ^PLL_TOP$
ERROR_ON_LVS_ERROR: true
"""




def render_csv(records: list[dict]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "package_pin",
            "side",
            "slot",
            "function",
            "direction",
            "cell",
            "rtl_signal",
            "instance",
        ],
        lineterminator="\n",
    )
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                "package_pin": f"P{record['pin']}",
                **{key: record[key] for key in writer.fieldnames[1:]},
            }
        )
    return output.getvalue()


def render_markdown(spec: dict, profile: dict, records: list[dict]) -> str:
    rows = [
        f"# {profile['id'].upper()} Pin Manifest",
        "",
        f"{spec['pin_orientation']} QFN lead count excludes an exposed pad.",
        "",
        "| Pin | Side | Slot | Function | Direction | PDK cell | Core-facing signal |",
        "|---|---|---:|---|---|---|---|",
    ]
    for record in records:
        rows.append(
            f"| P{record['pin']} | {record['side']} | {record['slot']} | "
            f"{record['function']} | {record['direction']} | {record['cell']} | {record['rtl_signal']} |"
        )
    rows.append("")
    return "\n".join(rows)


def expected_outputs(spec: dict, pdk_spec: dict) -> dict[Path, str]:
    outputs: dict[Path, str] = {}
    sky130 = pdk_entry(pdk_spec, "sky130A")
    gf180 = pdk_entry(pdk_spec, "gf180mcuD")
    ics55 = pdk_entry(pdk_spec, "ics55")
    no_pll_filler_layouts: dict[str, dict[str, dict[str, list[str]]]] = {}
    for profile in spec["profiles"]:
        records = build_records(spec, profile)
        profile_id = profile["id"]
        outputs[IHP_FLOW_DIR / f"{profile_id}.yaml"] = render_ihp_config(profile, records)
        outputs[SKY130_FLOW_DIR / f"{profile_id}.yaml"] = render_sky130_config(
            profile, records, sky130
        )
        outputs[SKY130_FLOW_DIR / "generated" / f"{profile_id}_placement.tcl"] = (
            render_sky130_placement(profile, records)
        )
        outputs[SKY130_FLOW_DIR / "generated" / f"{profile_id}_pdn_bridge.tcl"] = (
            render_sky130_pdn_bridge(profile)
        )
        outputs[GF180_FLOW_DIR / f"{profile_id}.yaml"] = render_gf180_config(
            profile, records, gf180
        )
        for base_variant in ics55["variants"]:
            variant = ics55_variant_for_profile(base_variant, profile)
            variant_id = variant["id"]
            ics55_records = build_ics55_records(spec, profile, variant)
            geometry = ics55_geometry(profile, ics55, variant)
            filler_layout = ics55_compact_fillers(
                profile, ics55_records, ics55, variant, geometry
            )
            if variant_id == "no-pll":
                if not geometry["compact"]:
                    raise ValueError(f"{profile_id}: SP55 no-PLL pad row must be compact")
                no_pll_filler_layouts[profile_id] = filler_layout
            outputs[ICS55_FLOW_DIR / f"{profile_id}-{variant_id}.yaml"] = (
                render_ics55_config(profile, ics55_records, ics55, variant, geometry)
            )
            outputs[
                ICS55_FLOW_DIR / "generated" / f"{profile_id}-{variant_id}_placement.tcl"
            ] = render_ics55_placement(
                profile, ics55_records, ics55, variant, geometry, filler_layout
            )
            outputs[
                ROOT / "docs" / "pinout" / "ics55" / variant_id / f"{profile_id}.csv"
            ] = render_csv(ics55_records)
            outputs[
                ROOT / "docs" / "pinout" / "ics55" / variant_id / f"{profile_id}.md"
            ] = render_markdown(spec, profile, ics55_records)
        outputs[ROOT / "docs" / "pinout" / f"{profile_id}.csv"] = render_csv(records)
        outputs[ROOT / "docs" / "pinout" / f"{profile_id}.md"] = render_markdown(
            spec, profile, records
        )
    outputs[ROOT / "rtl" / "tenon_tier0_padframe_ics55_no_pll_fillers.sv"] = (
        render_ics55_no_pll_fillers(no_pll_filler_layouts)
    )
    return outputs


def obsolete_outputs(spec: dict) -> list[Path]:
    """Return generated configuration paths superseded by the PDK split."""
    paths = [ROOT / "flow" / f"{profile['id']}.yaml" for profile in spec["profiles"]]
    paths.extend((
        ROOT / "rtl" / "tenon_tier0_padframe_ics55_qfn32_no_pll_fillers.sv",
        ICS55_FLOW_DIR / "generated" / "qfn32-no-pll_io_pdn.tcl",
    ))
    for profile in spec["profiles"]:
        paths.extend(
            (
                ICS55_FLOW_DIR / f"{profile['id']}.yaml",
                ICS55_FLOW_DIR / "generated" / f"{profile['id']}_placement.tcl",
            )
        )
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="fail when generated files differ"
    )
    args = parser.parse_args()

    spec = json.loads(SPEC_PATH.read_text())
    pdk_spec = json.loads(PDK_SPEC_PATH.read_text())
    outputs = expected_outputs(spec, pdk_spec)
    mismatches = []
    for path, content in outputs.items():
        if args.check:
            if not path.exists() or path.read_text() != content:
                mismatches.append(path.relative_to(ROOT))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    for path in obsolete_outputs(spec):
        if args.check:
            if path.exists():
                mismatches.append(path.relative_to(ROOT))
        elif path.exists():
            path.unlink()
    if mismatches:
        print("Generated files are stale:", *mismatches, sep="\n  ", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
