# SPDX-License-Identifier: Apache-2.0
# Compact single-domain PDN for standalone ICS55 H7CR smoke designs.

# The 0.3 um rails and 0.2 um spacing satisfy the 0.18 um2 minimum enclosed
# area while fitting the smallest retained smoke cores.
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
source $::env(SCRIPTS_DIR)/openroad/common/set_global_connections.tcl

set_layer_rc -via VIA1 -resistance 2.0
set_layer_rc -via VIA2 -resistance 2.0
set_layer_rc -via VIA3 -resistance 2.0
set_layer_rc -via VIA4 -resistance 2.0

add_global_connection -net VDD -inst_pattern .* -pin_pattern VDD -power
add_global_connection -net VSS -inst_pattern .* -pin_pattern VSS -ground
set_global_connections

set_voltage_domain -name CORE -power VDD -ground VSS
define_pdn_grid -name stdcell_grid -starts_with POWER -voltage_domains {CORE} \
    -pins {MET4 MET5}

add_pdn_stripe -grid stdcell_grid -layer MET4 -width 0.3 -pitch 2 -offset 0.1 \
    -spacing 0.2 -starts_with POWER -extend_to_core_ring
add_pdn_stripe -grid stdcell_grid -layer MET5 -width 0.3 -pitch 2 -offset 0.1 \
    -spacing 0.2 -starts_with POWER -extend_to_core_ring
add_pdn_connect -grid stdcell_grid -layers {MET4 MET5}

if {$::env(PDN_ENABLE_RAILS)} {
    add_pdn_stripe -grid stdcell_grid -layer MET1 -width 0.09 -followpins
    add_pdn_connect -grid stdcell_grid -layers {MET1 MET4}
}

add_pdn_ring -grid stdcell_grid -layers {MET4 MET5} -widths {0.3 0.3} \
    -spacings {0.2 0.2} -core_offsets {0.1 0.1} -connect_to_pads
