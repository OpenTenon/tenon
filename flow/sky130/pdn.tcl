# SPDX-License-Identifier: Apache-2.0
# Sky130A Tier0 core PDN. VDDIO/VSSIO remain a separate IO pad-ring domain.

source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
source $::env(SCRIPTS_DIR)/openroad/common/set_global_connections.tcl
set_global_connections

# Only VCCD/VSSD are a core PDN domain. VDDIO/VSSIO are independent package
# supply domains and receive their own explicit, PSM-visible bridge geometry.
set_voltage_domain -name CORE \
    -power $::env(VDD_NET) \
    -ground $::env(GND_NET)
define_pdn_grid -name stdcell_grid -starts_with POWER -voltage_domain CORE

add_pdn_stripe \
    -grid stdcell_grid \
    -layer $::env(PDN_VERTICAL_LAYER) \
    -width $::env(PDN_VWIDTH) \
    -pitch $::env(PDN_VPITCH) \
    -offset $::env(PDN_VOFFSET) \
    -spacing $::env(PDN_VSPACING) \
    -starts_with POWER \
    -extend_to_core_ring
add_pdn_stripe \
    -grid stdcell_grid \
    -layer $::env(PDN_HORIZONTAL_LAYER) \
    -width $::env(PDN_HWIDTH) \
    -pitch $::env(PDN_HPITCH) \
    -offset $::env(PDN_HOFFSET) \
    -spacing $::env(PDN_HSPACING) \
    -starts_with POWER \
    -extend_to_core_ring
add_pdn_connect \
    -grid stdcell_grid \
    -layers "$::env(PDN_VERTICAL_LAYER) $::env(PDN_HORIZONTAL_LAYER)"

if {$::env(PDN_ENABLE_RAILS)} {
    add_pdn_stripe \
        -grid stdcell_grid \
        -layer $::env(PDN_RAIL_LAYER) \
        -width $::env(PDN_RAIL_WIDTH) \
        -followpins
    add_pdn_connect \
        -grid stdcell_grid \
        -layers "$::env(PDN_RAIL_LAYER) $::env(PDN_VERTICAL_LAYER)"
}

add_pdn_ring \
    -grid stdcell_grid \
    -layers "$::env(PDN_CORE_VERTICAL_LAYER) $::env(PDN_CORE_HORIZONTAL_LAYER)" \
    -widths "$::env(PDN_CORE_RING_VWIDTH) $::env(PDN_CORE_RING_HWIDTH)" \
    -spacings "$::env(PDN_CORE_RING_VSPACING) $::env(PDN_CORE_RING_HSPACING)" \
    -core_offsets "$::env(PDN_CORE_RING_VOFFSET) $::env(PDN_CORE_RING_HOFFSET)"

set tenon_sky130_profile [string map {tenon_tier0_sky130_ ""} $::env(DESIGN_NAME)]
set tenon_sky130_bridge_file [format "%s_pdn_bridge.tcl" $tenon_sky130_profile]
set tenon_sky130_bridge [file join [file dirname [info script]] generated $tenon_sky130_bridge_file]
if {![file exists $tenon_sky130_bridge]} {
    error "Missing generated Sky130 PDN bridge $tenon_sky130_bridge"
}
source $tenon_sky130_bridge

define_pdn_grid -macro -default -name macro -starts_with POWER \
    -halo "$::env(PDN_HORIZONTAL_HALO) $::env(PDN_VERTICAL_HALO)"
add_pdn_connect -grid macro -layers "$::env(PDN_VERTICAL_LAYER) $::env(PDN_HORIZONTAL_LAYER)"
