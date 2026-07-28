# SPDX-License-Identifier: Apache-2.0
# ICS55 P65 no-PLL PDN. VDD/VSS use the core grid; VDDIO/VSSIO remain an
# abutted P65 pad-ring domain and deliberately receive no core-area mesh.

source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
source $::env(SCRIPTS_DIR)/openroad/common/set_global_connections.tcl

source [file join [file dirname [info script]] p65_global_connections.tcl]

set_layer_rc -via VIA1 -resistance 2.0
set_layer_rc -via VIA2 -resistance 2.0
set_layer_rc -via VIA3 -resistance 2.0
set_layer_rc -via VIA4 -resistance 2.0
set_layer_rc -via T4V2 -resistance 0.25
set_layer_rc -via RV -resistance 0.08

set_global_connections

set_voltage_domain -name CORE -power $::env(VDD_NET) -ground $::env(GND_NET)
define_pdn_grid -name stdcell_grid -starts_with POWER -voltage_domains {CORE}
add_pdn_stripe -grid stdcell_grid -layer $::env(PDN_VERTICAL_LAYER) \
    -width $::env(PDN_VWIDTH) -pitch $::env(PDN_VPITCH) -offset $::env(PDN_VOFFSET) \
    -spacing $::env(PDN_VSPACING) -starts_with POWER -extend_to_core_ring
add_pdn_stripe -grid stdcell_grid -layer $::env(PDN_HORIZONTAL_LAYER) \
    -width $::env(PDN_HWIDTH) -pitch $::env(PDN_HPITCH) -offset $::env(PDN_HOFFSET) \
    -spacing $::env(PDN_HSPACING) -starts_with POWER -extend_to_core_ring
add_pdn_connect -grid stdcell_grid -layers "$::env(PDN_VERTICAL_LAYER) $::env(PDN_HORIZONTAL_LAYER)"

if {$::env(PDN_ENABLE_RAILS)} {
    add_pdn_stripe -grid stdcell_grid -layer $::env(PDN_RAIL_LAYER) \
        -width $::env(PDN_RAIL_WIDTH) -followpins
    add_pdn_connect -grid stdcell_grid \
        -layers "$::env(PDN_RAIL_LAYER) $::env(PDN_VERTICAL_LAYER)"
}

add_pdn_ring -grid stdcell_grid \
    -layers "$::env(PDN_CORE_VERTICAL_LAYER) $::env(PDN_CORE_HORIZONTAL_LAYER)" \
    -widths "$::env(PDN_CORE_RING_VWIDTH) $::env(PDN_CORE_RING_HWIDTH)" \
    -spacings "$::env(PDN_CORE_RING_VSPACING) $::env(PDN_CORE_RING_HSPACING)" \
    -core_offsets "$::env(PDN_CORE_RING_VOFFSET) $::env(PDN_CORE_RING_HOFFSET)" \
    -connect_to_pads
add_pdn_connect -grid stdcell_grid \
    -layers "$::env(PDN_HORIZONTAL_LAYER) $::env(PDN_CORE_VERTICAL_LAYER)"
add_pdn_connect -grid stdcell_grid \
    -layers "$::env(PDN_CORE_VERTICAL_LAYER) $::env(PDN_CORE_HORIZONTAL_LAYER)"

source [file join [file dirname [info script]] pdn_p65_pad_ring.tcl]

define_pdn_grid -macro -default -name macro -starts_with POWER \
    -halo "$::env(PDN_HORIZONTAL_HALO) $::env(PDN_VERTICAL_HALO)"
add_pdn_connect -grid macro -layers "$::env(PDN_VERTICAL_LAYER) $::env(PDN_HORIZONTAL_LAYER)"
