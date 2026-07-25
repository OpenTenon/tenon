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
SIDES = ("south", "east", "north", "west")
INPUT_MANAGEMENT = {"mgmt_clk", "mgmt_rst_n", "jtag_tck", "jtag_tms", "jtag_tdi", "uart_rx"}
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
        raise ValueError(f"{profile['id']}: package lead count must be divisible by four")

    power_slots = set(range(1, side_size + 1, 4))
    if len(power_slots) != pads_per_rail:
        raise ValueError(f"{profile['id']}: side power slots do not match pads_per_rail")

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
        yaml_list(f"PAD_{side.upper()}", side_placement(records, side)) for side in SIDES
    )
    return f"""# Generated by tools/generate_tier0.py. Do not edit manually.
meta:
  version: 3
  flow: Chip
  substituting_steps:
    # The IO library has intentional pad/bondpad overlap reports.
    Checker.IllegalOverlap: null

DESIGN_NAME: {profile['top']}
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



def render_sky130_config(profile: dict, records: list[dict], sky130: dict) -> str:
    die_side = profile["die_side_um"]
    core_offset = 365
    core_end = core_offset + profile["core_side_um"]
    placements = "\n\n".join(
        yaml_list(f"PAD_{side.upper()}", helper_pad_side_placement(records, side)) for side in SIDES
    )
    return f"""# Generated by tools/generate_tier0.py. Do not edit manually.
# Sky130A / sky130_fd_io Tier0 reference configuration.
meta:
  version: 3
  flow: Chip
  substituting_steps:
    # The PDK power-connection utility cannot represent unused HVC pad pins.
    Odb.SetPowerConnections: null
DESIGN_NAME: {sky130['package_top_prefix']}{profile['id']}
VERILOG_FILES:
- dir::../../rtl/tenon_tier0_padframe_sky130.sv
- dir::../../rtl/tenon_tier0_pdk_reference.sv
- dir::../../rtl/tenon_tier0_pdk_variants.sv
VERILOG_DEFINES: [USE_POWER_PINS]
EXTRA_VERILOG_MODELS:
- pdk_dir::libs.ref/sky130_fd_io/verilog/sky130_fd_io__blackbox_pp.v
EXTRA_LEFS:
- pdk_dir::libs.ref/sky130_fd_io/lef/sky130_fd_io.lef
EXTRA_GDS:
- pdk_dir::libs.ref/sky130_fd_io/gds/sky130_fd_io.gds
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
- pdk_dir::libs.ref/sky130_fd_io/lib/sky130_fd_io__top_gpiov2_tt_tt_025C_1v80_3v30.lib
- pdk_dir::libs.ref/sky130_fd_io/lib/sky130_fd_io__top_power_hvc_wpad_tt_025C_1v80_3v30_3v30.lib
- pdk_dir::libs.ref/sky130_fd_io/lib/sky130_fd_io__top_ground_hvc_wpad_tt_025C_1v80_3v30_3v30.lib
PAD_CFG: dir::pad_cfg.tcl
PRIMARY_GDSII_STREAMOUT_TOOL: klayout

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

PAD_SITE_NAME: tenon_sky130_io
PAD_CORNER_SITE_NAME: tenon_sky130_corner
PAD_FAKE_SITES:
  tenon_sky130_io: [0.005, 200]
  tenon_sky130_corner: [200, 203.665]
PAD_CORNER: [sky130_fd_io__corner_bus_overlay]
PAD_FILLERS: []
PAD_EDGE_SPACING: 0
PAD_PLACE_IO_TERMINALS:
- sky130_fd_io__top_gpiov2/PAD
- sky130_fd_io__top_power_hvc_wpad/P_PAD
- sky130_fd_io__top_ground_hvc_wpad/G_PAD

PDN_CFG: dir::pdn.tcl
PDN_ENABLE_PINS: false
PDN_ENABLE_RAILS: true
PDN_VWIDTH: 3.2
PDN_HWIDTH: 3.2
PDN_VSPACING: 1.7
PDN_HSPACING: 1.7
PDN_VPITCH: 153.6
PDN_HPITCH: 153.18
PDN_CORE_RING: true
PDN_CORE_RING_CONNECT_TO_PADS: true
PDN_CORE_RING_VWIDTH: 10
PDN_CORE_RING_HWIDTH: 10
PDN_CORE_RING_VSPACING: 5
PDN_CORE_RING_HSPACING: 5
PDN_CORE_VERTICAL_LAYER: met4
PDN_CORE_HORIZONTAL_LAYER: met5
MAGIC_EXT_UNIQUE: notopports
"""

def render_gf180_config(profile: dict, records: list[dict], gf180: dict) -> str:
    die_side = gf180_die_side(profile, gf180)
    core_offset = gf180["floorplan"]["core_offset_um"]
    core_end = die_side - core_offset
    placements = "\n\n".join(
        yaml_list(f"PAD_{side.upper()}", helper_pad_side_placement(records, side)) for side in SIDES
    )
    return f"""# Generated by tools/generate_tier0.py. Do not edit manually.
# GF180MCU D / gf180mcu_ocd_io Tier0 reference configuration.
meta:
  version: 3
  flow: Chip
  substituting_steps:
    # Preserve the OCD IO cells' explicit four-rail RTL connectivity.
    Odb.SetPowerConnections: null

DESIGN_NAME: {gf180['package_top_prefix']}{profile['id']}
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
        writer.writerow({"package_pin": f"P{record['pin']}", **{key: record[key] for key in writer.fieldnames[1:]}})
    return output.getvalue()


def render_markdown(spec: dict, profile: dict, records: list[dict]) -> str:
    rows = [
        f"# {profile['id'].upper()} Pin Manifest",
        "",
        f"{spec['pin_orientation']} QFN lead count excludes an exposed pad.",
        "",
        "| Pin | Side | Slot | Function | Direction | IHP cell | Core-facing signal |",
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
    for profile in spec["profiles"]:
        records = build_records(spec, profile)
        profile_id = profile["id"]
        outputs[IHP_FLOW_DIR / f"{profile_id}.yaml"] = render_ihp_config(profile, records)
        outputs[SKY130_FLOW_DIR / f"{profile_id}.yaml"] = render_sky130_config(profile, records, sky130)
        outputs[GF180_FLOW_DIR / f"{profile_id}.yaml"] = render_gf180_config(profile, records, gf180)
        outputs[ROOT / "docs" / "pinout" / f"{profile_id}.csv"] = render_csv(records)
        outputs[ROOT / "docs" / "pinout" / f"{profile_id}.md"] = render_markdown(spec, profile, records)
    return outputs


def obsolete_outputs(spec: dict) -> list[Path]:
    """Return generated configuration paths superseded by the PDK split."""
    return [ROOT / "flow" / f"{profile['id']}.yaml" for profile in spec["profiles"]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail when generated files differ")
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
