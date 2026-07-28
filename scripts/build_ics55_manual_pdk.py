#!/usr/bin/env python3
"""Materialize the external LibreLane-compatible ICS55 manual PDK adapter.

Commercial LEF/GDS/lib/CDL views remain outside the Tenon repository. This
script creates external symlinks and writes compatible OpenROAD and Magic abstractions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


PDK_NAME = "ics55"
IO_LEF_NAME = "SP55NLLD2P_3P3V_V0p4a_6MT_1TM.lef"
P65_IO_LEF_NAME = "ICSIOA_N55_3P3_1P6M1TM.lef"
P65_IO_GDS_NAME = "ICSIOA_N55_3P3_1P6M1TM.gds"
PLL_LEF_NAME = "PLL_TOP.lef"
OPENRCX_COMPAT_RULES = (
    Path.home()
    / ".ciel"
    / "ciel"
    / "ihp-sg13g2"
    / "versions"
    / "3b5a704ba6738aa686b08706187830e6284d2a10"
    / "ihp-sg13g2"
    / "libs.tech"
    / "librelane"
    / "openrcx"
    / "IHP_rcx_patterns.rules"
)
MAGIC_TECH_TEMPLATE = Path(__file__).with_name("ics55.magic.tech")
KLAYOUT_LYP_TEMPLATE = Path(__file__).with_name("ics55.lyp")
KLAYOUT_LYT_TEMPLATE = Path(__file__).with_name("ics55.lyt")
KLAYOUT_MAP_TEMPLATE = Path(__file__).with_name("ics55.map")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument(
        "--output-root", type=Path, default=Path.home() / ".ciel" / "manual"
    )
    return parser.parse_args()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    elif path.exists() and path.read_text() == text:
        return
    path.write_text(text)


def link(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"Missing commercial PDK view: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() and destination.resolve() == source.resolve():
        return
    if destination.exists() or destination.is_symlink():
        destination.unlink()
    destination.symlink_to(source)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strip_unsupported_taper(tech_lef: str) -> str:
    pattern = re.compile(
        r"(?ms)^NONDEFAULTRULE\s+virtuosoDefaultTaper\s*\n.*?^END\s+virtuosoDefaultTaper\s*\n"
    )
    normalized, count = pattern.subn("", tech_lef)
    if count != 1:
        raise ValueError("Expected exactly one virtuosoDefaultTaper rule")
    return normalized


def macro_block(text: str, macro: str) -> tuple[int, int, str]:
    match = re.search(
        rf"(?ms)^MACRO\s+{re.escape(macro)}\s*\n.*?^END\s+{re.escape(macro)}\s*$",
        text,
    )
    if match is None:
        raise ValueError(f"Missing macro {macro}")
    return match.start(), match.end(), match.group(0)


def t4m2_rectangles(block: str, macro: str) -> list[str]:
    obs = re.search(r"(?ms)^\s*OBS\s*\n(.*?)^\s*END\s*$", block)
    if obs is None:
        raise ValueError(f"{macro} has no OBS section")
    layer = re.search(
        r"(?ms)^\s*LAYER\s+T4M2\s*;\s*\n(.*?)(?=^\s*LAYER\s+|^\s*END\s*$)",
        obs.group(1),
    )
    if layer is None:
        raise ValueError(f"{macro} has no T4M2 geometry")
    rectangles = re.findall(r"(?m)^\s*RECT\s+([^;]+;)\s*$", layer.group(1))
    if not rectangles:
        raise ValueError(f"{macro} has no T4M2 rectangles")
    return rectangles


def add_supply_port(block: str, macro: str, pin: str) -> str:
    port = (
        "    PORT\n      LAYER T4M2 ;\n"
        + "".join(
            f"        RECT {rectangle}\n" for rectangle in t4m2_rectangles(block, macro)
        )
        + "    END\n"
    )
    pattern = re.compile(
        rf"(?ms)^(  PIN\s+{re.escape(pin)}\s*\n)(.*?)(^  END\s+{re.escape(pin)}\s*$)"
    )
    match = pattern.search(block)
    if match is None:
        raise ValueError(f"{macro} has no {pin} pin")
    if re.search(r"(?m)^\s*PORT\s*$", match.group(2)):
        raise ValueError(f"{macro}/{pin} already has a physical port")
    return (
        block[: match.start()]
        + match.group(1)
        + match.group(2)
        + port
        + match.group(3)
        + block[match.end() :]
    )


def normalize_supply_port(block: str, macro: str, pin: str) -> str:
    """Replace a multi-layer macro supply pin with a T4M2-only abstraction."""
    port = (
        "    PORT\n      LAYER T4M2 ;\n"
        + "".join(
            f"        RECT {rectangle}\n" for rectangle in t4m2_rectangles(block, macro)
        )
        + "    END\n"
    )
    pattern = re.compile(
        rf"(?ms)^(  PIN\s+{re.escape(pin)}\s*\n)(.*?)(^  END\s+{re.escape(pin)}\s*$)"
    )
    match = pattern.search(block)
    if match is None:
        raise ValueError(f"{macro} has no {pin} pin")
    body = re.sub(r"(?ms)^    PORT\s*\n.*?^    END\s*\n", "", match.group(2))
    return (
        block[: match.start()]
        + match.group(1)
        + body
        + port
        + match.group(3)
        + block[match.end() :]
    )


def simplify_pin_port(
    block: str, macro: str, pin: str, layer_names: tuple[str, ...]
) -> str:
    """Keep one routing-access layer for an IO signal pin.

    The commercial PB4 LEF describes the complete internal conductor stack as
    pin geometry. OpenROAD treats every one of those shapes as an external
    routing terminal, which produces false self-short and spacing violations.
    Retain only the outer access layer; the original OBS still protects all
    internal geometry.
    """
    pattern = re.compile(
        rf"(?ms)^(  PIN\s+{re.escape(pin)}\s*\n)(.*?)(^  END\s+{re.escape(pin)}\s*$)"
    )
    match = pattern.search(block)
    if match is None:
        raise ValueError(f"{macro} has no {pin} pin")
    port_match = re.search(r"(?ms)^    PORT\s*\n(.*?)^    END\s*$", match.group(2))
    if port_match is None:
        raise ValueError(f"{macro}/{pin} has no physical port")
    port_layers = []
    for layer_name in layer_names:
        layer = re.search(
            rf"(?ms)^      LAYER\s+{re.escape(layer_name)}\s*;\s*\n(.*?)(?=^      LAYER\s+|^    END\s*$)",
            port_match.group(1),
        )
        if layer is None:
            raise ValueError(f"{macro}/{pin} has no {layer_name} geometry")
        rectangles = re.findall(r"(?m)^        RECT\s+([^;]+;)\s*$", layer.group(1))
        if not rectangles:
            raise ValueError(f"{macro}/{pin} has no {layer_name} rectangles")
        port_layers.append("      LAYER " + layer_name + " ;\n")
        port_layers.extend(f"        RECT {rectangle}\n" for rectangle in rectangles)
    port = "    PORT\n" + "".join(port_layers) + "    END\n"
    body = re.sub(r"(?ms)^    PORT\s*\n.*?^    END\s*\n", "", match.group(2))
    return (
        block[: match.start()]
        + match.group(1)
        + body
        + port
        + match.group(3)
        + block[match.end() :]
    )


def make_io_lef(source: Path) -> str:
    content = source.read_text()
    for pin in ("IE", "C", "I", "OEN"):
        start, end, block = macro_block(content, "PB4")
        content = (
            content[:start]
            + simplify_pin_port(block, "PB4", pin, ("MET4", "MET5"))
            + content[end:]
        )
    start, end, block = macro_block(content, "PB4")
    content = (
        content[:start]
        + simplify_pin_port(block, "PB4", "PAD", ("T4M2",))
        + content[end:]
    )
    for macro, pin in (("PVDD2", "VDD25"), ("PVSS2", "VSSD")):
        start, end, block = macro_block(content, macro)
        content = content[:start] + add_supply_port(block, macro, pin) + content[end:]
    for macro, pin in (("PVDD1", "VDD"), ("PVSS1", "VSS")):
        start, end, block = macro_block(content, macro)
        content = (
            content[:start] + normalize_supply_port(block, macro, pin) + content[end:]
        )
    for macro, pin in (
        ("PVDD1CAP", "SVDD1CAP"),
        ("PVSS1CAP", "SVSS1CAP"),
        ("PVDD3AP", "SAVDD"),
        ("PVSS3AP", "SAVSS"),
    ):
        start, end, block = macro_block(content, macro)
        content = (
            content[:start] + normalize_supply_port(block, macro, pin) + content[end:]
        )
    return content


P65_IO_OPENROAD_LEF_NAME = "ICSIOA_N55_3P3_1P6M1TM_openroad.lef"
P65_IO_RAW_LEF_NAME = "ICSIOA_N55_3P3_1P6M1TM_raw.lef"
P65_SUPPLY_PINS = ("VDD", "VSS", "VDDIO", "VSSIO")
P65_FILLER_MACROS = (
    "P65_1233_FILLER50",
    "P65_1233_FILLER20",
    "P65_1233_FILLER10",
    "P65_1233_FILLER5",
    "P65_1233_FILLER2",
    "P65_1233_FILLER1",
    "P65_1233_FILLER01",
    "P65_1233_FILLER001",
)
P65_PBMUX_SIGNAL_PINS = ("C", "CS", "DS0", "DS1", "I", "IE", "OD", "OE", "PD", "PU")
P65_PG_MACROS = {
    "P65_1233_PBMUX": P65_SUPPLY_PINS,
    "P65_1233_VDD3": ("VDD",),
    "P65_1233_VSS3": ("VDD", "VSS"),
    "P65_1233_VDDIO3": ("VDDIO",),
    "P65_1233_VSSIO3": ("VDDIO", "VSSIO"),
    "P65_1233_CORNER": P65_SUPPLY_PINS,
    **{macro: P65_SUPPLY_PINS for macro in P65_FILLER_MACROS},
}
P65_OPENROAD_PIN_FILTER = {
    **P65_PG_MACROS,
    "P65_1233_PBMUX": (*P65_SUPPLY_PINS, *P65_PBMUX_SIGNAL_PINS, "PAD"),
}


def filter_macro_pins(block: str, pins: tuple[str, ...]) -> str:
    """Retain only electrically meaningful PNR terminals on a P65 macro."""
    keep = set(pins)
    pattern = re.compile(r"(?ms)^  PIN\s+(\S+)\s*\n.*?^  END\s+\1\s*(?:\n|\Z)")
    return pattern.sub(lambda match: match.group(0) if match.group(1) in keep else "", block)


def remove_macro_pins(block: str, pins: tuple[str, ...]) -> str:
    """Remove intentionally unused PNR terminals from the abstract interface."""
    remove = set(pins)
    pattern = re.compile(r"(?ms)^  PIN\s+(\S+)\s*\n.*?^  END\s+\1\s*(?:\n|\Z)")
    return pattern.sub(lambda match: "" if match.group(1) in remove else match.group(0), block)

def mark_pin_ground(block: str, macro: str, pin: str) -> str:
    """Correct a commercial physical-ground pin whose LEF USE is SIGNAL."""
    pattern = re.compile(
        rf"(?ms)^(  PIN\s+{re.escape(pin)}\s*\n)(.*?)(^  END\s+{re.escape(pin)}\s*$)"
    )
    match = pattern.search(block)
    if match is None:
        raise ValueError(f"{macro} has no {pin} pin")
    body, count = re.subn(
        r"(?m)^    USE\s+SIGNAL\s*;$", "    USE GROUND ;", match.group(2), count=1
    )
    if count != 1:
        raise ValueError(f"{macro}/{pin} does not have the expected SIGNAL use")
    return block[: match.start()] + match.group(1) + body + match.group(3) + block[match.end() :]


def replace_macro_block(content: str, macro: str, transform) -> str:
    start, end, block = macro_block(content, macro)
    return content[:start] + transform(block) + content[end:]


def simplify_pin_port_to_first_rect(block: str, macro: str, pin: str, layer_name: str) -> str:
    """Keep one actual outer routing rectangle for an OpenROAD PG terminal."""
    pattern = re.compile(
        rf"(?ms)^(  PIN\s+{re.escape(pin)}\s*\n)(.*?)(^  END\s+{re.escape(pin)}\s*$)"
    )
    match = pattern.search(block)
    if match is None:
        raise ValueError(f"{macro} has no {pin} pin")
    port_match = re.search(r"(?ms)^    PORT\s*\n(.*?)^    END\s*$", match.group(2))
    if port_match is None:
        raise ValueError(f"{macro}/{pin} has no physical port")
    layer = re.search(
        rf"(?ms)^      LAYER\s+{re.escape(layer_name)}\s*;\s*\n(.*?)(?=^      LAYER\s+|\Z)",
        port_match.group(1),
    )
    if layer is None:
        raise ValueError(f"{macro}/{pin} has no {layer_name} geometry")
    rectangles = re.findall(r"(?m)^        RECT\s+([^;]+;)\s*$", layer.group(1))
    if not rectangles:
        raise ValueError(f"{macro}/{pin} has no {layer_name} rectangles")
    port = f"    PORT\n      LAYER {layer_name} ;\n        RECT {rectangles[0]}\n    END\n"
    body = re.sub(r"(?ms)^    PORT\s*\n.*?^    END\s*\n", "", match.group(2))
    return block[: match.start()] + match.group(1) + body + port + match.group(3) + block[match.end() :]


def make_p65_openroad_lef(source: Path) -> str:
    """Retain real P65 power rails and only external PBMUX routing terminals."""
    content = source.read_text()
    for macro, pins in P65_OPENROAD_PIN_FILTER.items():
        content = replace_macro_block(
            content, macro, lambda block, pins=pins: filter_macro_pins(block, pins)
        )
    content = replace_macro_block(
        content, "P65_1233_PBMUX", lambda block: remove_macro_pins(block, ("A",))
    )
    for pin in P65_PBMUX_SIGNAL_PINS:
        content = replace_macro_block(
            content,
            "P65_1233_PBMUX",
            lambda block, pin=pin: simplify_pin_port_to_first_rect(
                block, "P65_1233_PBMUX", pin, "MET5"
            ),
        )
    content = replace_macro_block(
        content,
        "P65_1233_PBMUX",
        lambda block: simplify_pin_port_to_first_rect(
            block, "P65_1233_PBMUX", "PAD", "T4M2"
        ),
    )
    for macro, pins in P65_PG_MACROS.items():
        for pin in pins:
            start, end, block = macro_block(content, macro)
            content = (
                content[:start]
                + simplify_pin_port_to_first_rect(block, macro, pin, "MET5")
                + content[end:]
            )
        for pin in ("VSS", "VSSIO"):
            if pin not in pins:
                continue
            content = replace_macro_block(
                content,
                macro,
                lambda block, pin=pin, macro=macro: mark_pin_ground(block, macro, pin),
            )
    return content


def port_level_macro(block: str) -> str:
    name = re.search(r"(?m)^MACRO\s+(\S+)", block)
    if name is None:
        raise ValueError("Malformed LEF macro block")
    attributes = re.findall(
        r"(?m)^  (?:CLASS|FOREIGN|ORIGIN|SIZE|SYMMETRY|SITE)\b[^\n]*", block
    )
    pins = re.findall(
        r"(?ms)^  PIN\s+\S+\s*\n.*?^  END\s+\S+\s*(?=\n|\Z)", block
    )
    pins = [
        re.sub(r"(?ms)^    PORT\s*\n.*?^    END\s*\n", "", pin)
        for pin in pins
    ]
    return "\n".join([f"MACRO {name.group(1)}", *attributes, *pins, f"END {name.group(1)}"])


def make_magic_lvs_lef(std_lef: str, io_lef: str, p65_lef: str, pll_lef: str) -> str:
    header_end = re.search(r"(?m)^MACRO\s+", p65_lef)
    if header_end is None:
        raise ValueError("ICS55 P65 IO LEF has no macro definitions")
    sources = (
        (std_lef, ("TIEHIH7R", "TIELOH7R")),
        (
            io_lef,
            (
                "PB4", "PADI30", "PADO30", "PFILL10", "PCORNER",
                "PVDD1", "PVSS1", "PVDD2", "PVSS2",
                "PVDD1CAP", "PVSS1CAP", "PVDD3AP", "PVSS3AP", "PXWE1",
            ),
        ),
        (
            p65_lef,
            (
                "P65_1233_PBMUX", "P65_1233_VDD3", "P65_1233_VSS3",
                "P65_1233_VDDIO3", "P65_1233_VSSIO3", "P65_1233_CORNER",
                "P65_1233_FILLER50", "P65_1233_FILLER20", "P65_1233_FILLER10",
                "P65_1233_FILLER5", "P65_1233_FILLER2", "P65_1233_FILLER1",
            ),
        ),
        (pll_lef, ("PLL_TOP",)),
    )
    blocks = []
    for source, macros in sources:
        for macro in macros:
            blocks.append(port_level_macro(macro_block(source, macro)[2]))
    return p65_lef[: header_end.start()] + "\n\n".join(blocks) + "\nEND LIBRARY\n"


def make_openrcx_rules() -> str:
    """Return a complete OpenRCX compatibility model for feasibility runs.

    The commercial ICS55 PDK ships StarRC nxtgrd/captbl data, not an OpenRCX
    pattern model. This template is only used to keep LibreLane RCX and
    IR-drop enabled for non-signoff feasibility hardening; -lef_res supplies
    ICS55 line resistance from the materialized technology LEF.
    """
    if not OPENRCX_COMPAT_RULES.is_file():
        raise FileNotFoundError(
            f"Missing OpenRCX compatibility rules: {OPENRCX_COMPAT_RULES}"
        )
    return OPENRCX_COMPAT_RULES.read_text()


def make_magic_tech() -> str:
    if not MAGIC_TECH_TEMPLATE.is_file():
        raise FileNotFoundError(
            f"Missing ICS55 Magic technology template: {MAGIC_TECH_TEMPLATE}"
        )
    return MAGIC_TECH_TEMPLATE.read_text()


def make_magicrc() -> str:
    """Create a connectivity-only Magic setup for the external adapter."""
    return """if {[catch {set PDK_ROOT [file nativename $env(PDK_ROOT)]}]} {
    error "PDK_ROOT must be set for the ICS55 manual PDK"
}
tech load $PDK_ROOT/ics55/libs.tech/magic/ics55.tech
if {[tech name] != "ics55"} {quit -noprompt}
set VDD VDD
set GND VSS
set SUB VSS

# Magic extraction uses a compact port-level LEF.  Physical PnR and streamout
# continue to use the complete commercial LEF and GDS views.
if {[info exists ::env(MAGIC_EXT_ABSTRACT)] && $::env(MAGIC_EXT_ABSTRACT)} {
    set lvs_lef "$PDK_ROOT/ics55/libs.ref/ics55_LLSC_H7CR/lef/ics55_lvs_abstract.lef"
    set ::env(CELL_LEFS) $lvs_lef
    set ::env(EXTRA_LEFS) ""
    set ::env(PAD_LEFS) ""
}

# LibreLane applies LEFview properties after loading the top by default.
# Mark known commercial leaves before dereferencing to retain abstract LVS.
rename load ics55_native_load
proc load {cell args} {
    if {[info exists ::env(MAGIC_EXT_ABSTRACT)] && $::env(MAGIC_EXT_ABSTRACT) &&
        [info exists ::env(DESIGN_NAME)] && $cell eq $::env(DESIGN_NAME)} {
        set available [cellname list allcells]
        foreach leaf {
            PB4 PADI30 PADO30 PFILL10 PCORNER
            PVDD1 PVSS1 PVDD2 PVSS2
            PVDD1CAP PVSS1CAP PVDD3AP PVSS3AP PXWE1 PLL_TOP
            P65_1233_PBMUX P65_1233_VDD3 P65_1233_VSS3 P65_1233_VDDIO3 P65_1233_VSSIO3
            P65_1233_CORNER P65_1233_FILLER50 P65_1233_FILLER20 P65_1233_FILLER10
            P65_1233_FILLER5 P65_1233_FILLER2 P65_1233_FILLER1 P65_1233_FILLER01
            P65_1233_FILLER001 P65_1233_FILLER0005
            TIEHIH7R TIELOH7R
        } {
            if {[lsearch -exact $available $leaf] >= 0} {
                ics55_native_load $leaf
                property LEFview true
            }
        }
        set filtered {}
        foreach arg $args {
            if {$arg ne "-dereference"} {
                lappend filtered $arg
            }
        }
        return [uplevel 1 [list ics55_native_load $cell {*}$filtered]]
    }
    return [uplevel 1 [list ics55_native_load $cell {*}$args]]
}

# _TCL_ENV_IN is sourced after this rcfile.  Intercept the subsequent common
# reader source so the compact LEF replaces full PDK and pad LEFs only for
# Magic.SpiceExtraction.
rename source ics55_native_source
proc source {file} {
    set result [uplevel 1 [list ics55_native_source $file]]
    if {[info exists ::env(MAGIC_EXT_ABSTRACT)] && $::env(MAGIC_EXT_ABSTRACT) &&
        [file tail $file] eq "read.tcl"} {
        proc read_pdk_lef {} {
            lef read "$::env(PDK_ROOT)/ics55/libs.ref/ics55_LLSC_H7CR/lef/ics55_lvs_abstract.lef"
        }
        proc read_extra_lef {} {}
        proc read_pad_lef {} {}
    }
    return $result
}
"""

def read_template(template: Path, description: str) -> str:
    if not template.is_file():
        raise FileNotFoundError(f"Missing ICS55 {description} template: {template}")
    return template.read_text()


def make_p65_pad_config() -> str:
    """Return LibreLane's per-pad-library configuration for native P65 views."""
    return """set root \"$::env(PDK_ROOT)/$::env(PDK)\"
set pad \"$root/libs.ref/ics55_io_p65\"
set ::env(PAD_LEFS) \"$pad/lef/ICSIOA_N55_3P3_1P6M1TM_openroad.lef\"
set ::env(PAD_GDS) \"$pad/gds/ICSIOA_N55_3P3_1P6M1TM.gds\"
set ::env(PAD_CDLS) \"$pad/cdl/ICSIOA_N55_3P3_0119.cdl\"
set ::env(PAD_LIBS) [dict create]
dict set ::env(PAD_LIBS) *_tt_1p2_25C \"$pad/lib/ICSIOA_N55_3P3_tt_1p2_3p3_25c.lib\"
dict set ::env(PAD_LIBS) *_ss_1p08_125C \"$pad/lib/ICSIOA_N55_3P3_ss_1p08_2p97_125c.lib\"
dict set ::env(PAD_LIBS) *_ff_1p32_m40C \"$pad/lib/ICSIOA_N55_3P3_ff_1p32_3p63v_0c.lib\"
"""


def patch_tech_config(destination: Path) -> None:
    config = destination / "libs.tech" / "librelane" / "config.tcl"
    if not config.is_file():
        raise FileNotFoundError(
            f"Missing external ICS55 adapter skeleton: {config}. "
            "Create the manual adapter support files before materializing commercial views."
        )
    content = config.read_text().replace("N551P6M_ecos.lef", "N551P6M_openroad.lef")
    pad_views = """if {$::env(PAD_CELL_LIBRARY) eq "ics55_io_p65"} {
    set ::env(PAD_LEFS) "$pad/lef/ICSIOA_N55_3P3_1P6M1TM_openroad.lef"
    set ::env(PAD_GDS) "$pad/gds/ICSIOA_N55_3P3_1P6M1TM.gds"
    set ::env(PAD_CDLS) "$pad/cdl/ICSIOA_N55_3P3_0119.cdl"
    set ::env(PAD_LIBS) [dict create]
    dict set ::env(PAD_LIBS) *_tt_1p2_25C "$pad/lib/ICSIOA_N55_3P3_tt_1p2_3p3_25c.lib"
    dict set ::env(PAD_LIBS) *_ss_1p08_125C "$pad/lib/ICSIOA_N55_3P3_ss_1p08_2p97_125c.lib"
    dict set ::env(PAD_LIBS) *_ff_1p32_m40C "$pad/lib/ICSIOA_N55_3P3_ff_1p32_3p63v_0c.lib"
} else {
    set ::env(PAD_LEFS) "$pad/lef/SP55NLLD2P_3P3V_V0p4a_6MT_1TM.lef"
    set ::env(PAD_GDS) "$pad/gds/SP55NLLD2P_3P3V_V0p5_ics.gds"
    set ::env(PAD_CDLS) "$pad/cdl/SP55NLLD2P_3P3V_V0p5.sp"
    set ::env(PAD_LIBS) [dict create]
    dict set ::env(PAD_LIBS) *_tt_1p2_25C "$pad/lib/SP55NLLD2P_3P3V_V0p3_tt_v1p20_25C.lib"
    dict set ::env(PAD_LIBS) *_ss_1p08_125C "$pad/lib/SP55NLLD2P_3P3V_V0p3_ss_v1p08_125C.lib"
    dict set ::env(PAD_LIBS) *_ff_1p32_m40C "$pad/lib/SP55NLLD2P_3P3V_V0p3_ff_v1p32_0C.lib"
}"""
    content = re.sub(
        r"(?ms)^set ::env\(PAD_LEFS\).*?(?=^set ::env\(METAL_LAYER_NAMES\))",
        pad_views + "\n",
        content,
    )
    content = re.sub(
        r"(?m)^set ::env\(RCX_RULESETS\) \[dict create\]\n(?:dict set ::env\(RCX_RULESETS\).*\n)*",
        "set ::env(RCX_RULESETS) [dict create]\n"
        'dict set ::env(RCX_RULESETS) "nom_*" "$root/libs.tech/librelane/openrcx/ics55.typ.rules"\n',
        content,
    )
    write_text(config, content)


def main() -> None:
    args = parse_args()
    source = args.source_root.resolve()
    destination = args.output_root.expanduser().resolve() / PDK_NAME
    if not source.is_dir():
        raise SystemExit(f"Commercial source root does not exist: {source}")

    h7cr = source / "ics55_STDnIO_260714" / "ics55_LLSC_H7C_V1p10C100" / "ics55_LLSC_H7CR"
    p65_source = source / "ics55_STDnIO_260714" / "ICsprout_55LLULP1233_IO_251013"
    files = {
        "tech": source / "tech" / "N551P6M.lef",
        "std_lef": h7cr / "lef" / "ics55_LLSC_H7CR_M2.lef",
        "std_gds": h7cr / "gds" / "ics55_LLSC_H7CR_M2.gds",
        "std_cdl": h7cr / "cdl" / "ics55_LLSC_H7CR.cdl",
        "std_tt": h7cr / "liberty" / "lib" / "ics55_LLSC_H7CR_typ_tt_1p2_25_nldm.lib",
        "std_ss": h7cr / "liberty" / "lib" / "ics55_LLSC_H7CR_ss_rcworst_1p08_125_nldm.lib",
        "std_ff": h7cr / "liberty" / "lib" / "ics55_LLSC_H7CR_ff_rcbest_1p32_m40_nldm.lib",
        "io_lef": source / "lef" / IO_LEF_NAME,
        "io_gds": source / "gds" / "sm2ics" / "ics" / "ics" / "SP55NLLD2P_3P3V_V0p5_ics.gds",
        "io_cdl": source / "cdl" / "SP55NLLD2P_3P3V_V0p5.sp",
        "io_tt": source / "ccslib" / "SP55NLLD2P_3P3V_V0p3_tt_v1p20_25C.lib",
        "io_ss": source / "ccslib" / "SP55NLLD2P_3P3V_V0p3_ss_v1p08_125C.lib",
        "io_ff": source / "ccslib" / "SP55NLLD2P_3P3V_V0p3_ff_v1p32_0C.lib",
        "pll_lef": source / "lef" / PLL_LEF_NAME,
        "pll_gds": source / "gds" / "PLL_TOP.gds",
        "pll_cdl": source / "cdl" / "PLL_TOP.cdl",
        "pll_tt": source / "ccslib" / "PLL_TOP_typ.lib",
        "p65_lef": p65_source / "lef" / P65_IO_LEF_NAME,
        "p65_gds": p65_source / "gds" / P65_IO_GDS_NAME,
        "p65_cdl": p65_source / "cdl" / "ICSIOA_N55_3P3_0119.cdl",
        "p65_tt": p65_source / "library" / "ICSIOA_N55_3P3_tt_1p2_3p3_25c.lib",
        "p65_ss": p65_source / "library" / "ICSIOA_N55_3P3_ss_1p08_2p97_125c.lib",
        "p65_ff": p65_source / "library" / "ICSIOA_N55_3P3_ff_1p32_3p63v_0c.lib",
        "p65_verilog": p65_source / "verilog" / "icsIOA_N55_3P3_0112.v",
    }
    missing = [str(path) for path in files.values() if not path.is_file()]
    if missing:
        raise SystemExit("Missing commercial PDK view(s):\n" + "\n".join(missing))

    io_lef = make_io_lef(files["io_lef"])
    p65_lef = files["p65_lef"].read_text()
    p65_openroad_lef = make_p65_openroad_lef(files["p65_lef"])

    scl = destination / "libs.ref" / "ics55_LLSC_H7CR"
    io = destination / "libs.ref" / "ics55_io_3p3"
    p65 = destination / "libs.ref" / "ics55_io_p65"
    pll = destination / "libs.ref" / "ics55_pll"
    destinations = {
        "std_lef": scl / "lef" / files["std_lef"].name,
        "std_gds": scl / "gds" / files["std_gds"].name,
        "std_cdl": scl / "cdl" / files["std_cdl"].name,
        "std_tt": scl / "lib" / files["std_tt"].name,
        "std_ss": scl / "lib" / files["std_ss"].name,
        "std_ff": scl / "lib" / files["std_ff"].name,
        "io_gds": io / "gds" / files["io_gds"].name,
        "io_cdl": io / "cdl" / files["io_cdl"].name,
        "io_tt": io / "lib" / files["io_tt"].name,
        "io_ss": io / "lib" / files["io_ss"].name,
        "io_ff": io / "lib" / files["io_ff"].name,
        "pll_lef": pll / "lef" / files["pll_lef"].name,
        "pll_gds": pll / "gds" / files["pll_gds"].name,
        "pll_cdl": pll / "cdl" / files["pll_cdl"].name,
        "pll_tt": pll / "lib" / files["pll_tt"].name,
        "p65_lef": p65 / "lef" / P65_IO_RAW_LEF_NAME,
        "p65_gds": p65 / "gds" / files["p65_gds"].name,
        "p65_cdl": p65 / "cdl" / files["p65_cdl"].name,
        "p65_tt": p65 / "lib" / files["p65_tt"].name,
        "p65_ss": p65 / "lib" / files["p65_ss"].name,
        "p65_ff": p65 / "lib" / files["p65_ff"].name,
        "p65_verilog": p65 / "verilog" / files["p65_verilog"].name,
    }
    for key, target in destinations.items():
        link(files[key], target)

    write_text(
        destination / "libs.tech" / "librelane" / "openrcx" / "ics55.typ.rules",
        make_openrcx_rules(),
    )
    write_text(destination / "libs.tech" / "magic" / "ics55.tech", make_magic_tech())
    write_text(destination / "libs.tech" / "magic" / "ics55.magicrc", make_magicrc())
    write_text(
        destination / "libs.tech" / "klayout" / "tech" / "ics55.lyp",
        read_template(KLAYOUT_LYP_TEMPLATE, "KLayout layer-properties"),
    )
    write_text(
        destination / "libs.tech" / "klayout" / "tech" / "ics55.lyt",
        read_template(KLAYOUT_LYT_TEMPLATE, "KLayout technology"),
    )
    write_text(
        destination / "libs.tech" / "klayout" / "tech" / "ics55.map",
        read_template(KLAYOUT_MAP_TEMPLATE, "KLayout DEF layer map"),
    )
    write_text(
        scl / "techlef" / "N551P6M_openroad.lef",
        strip_unsupported_taper(files["tech"].read_text()),
    )
    write_text(io / "lef" / IO_LEF_NAME, io_lef)
    write_text(p65 / "lef" / P65_IO_OPENROAD_LEF_NAME, p65_openroad_lef)
    write_text(
        scl / "lef" / "ics55_lvs_abstract.lef",
        make_magic_lvs_lef(
            files["std_lef"].read_text(), io_lef, p65_lef, files["pll_lef"].read_text()
        ),
    )
    patch_tech_config(destination)
    write_text(
        destination / "libs.tech" / "librelane" / "ics55_io_p65" / "config.tcl",
        make_p65_pad_config(),
    )
    manifest = {
        "schema_version": 2,
        "adapter": PDK_NAME,
        "commercial_source_root": str(source),
        "source_sha256": {key: digest(path) for key, path in files.items()},
        "derived_files": [
            "libs.ref/ics55_LLSC_H7CR/techlef/N551P6M_openroad.lef",
            "libs.ref/ics55_LLSC_H7CR/lef/ics55_lvs_abstract.lef",
            f"libs.ref/ics55_io_3p3/lef/{IO_LEF_NAME}",
            f"libs.ref/ics55_io_p65/lef/{P65_IO_OPENROAD_LEF_NAME}",
            "libs.ref/ics55_pll/lef/PLL_TOP.lef",
            "libs.tech/librelane/openrcx/ics55.typ.rules",
            "libs.tech/librelane/ics55_io_p65/config.tcl",
            "libs.tech/magic/ics55.tech",
            "libs.tech/magic/ics55.magicrc",
            "libs.tech/klayout/tech/ics55.lyp",
            "libs.tech/klayout/tech/ics55.lyt",
            "libs.tech/klayout/tech/ics55.map",
        ],
    }
    write_text(
        destination / "ADAPTER_MANIFEST.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )
    print(f"ICS55 manual adapter materialized at {destination}")


if __name__ == "__main__":
    main()
