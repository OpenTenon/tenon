# SPDX-License-Identifier: Apache-2.0
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
source $::env(SCRIPTS_DIR)/openroad/common/set_global_connections.tcl

set_layer_rc -via VIA1 -resistance 2.0
set_layer_rc -via VIA2 -resistance 2.0
set_layer_rc -via VIA3 -resistance 2.0
set_layer_rc -via VIA4 -resistance 2.0
set_layer_rc -via T4V2 -resistance 0.25
set_layer_rc -via RV -resistance 0.08

add_global_connection -net VDD -inst_pattern .* -pin_pattern VDD -power
add_global_connection -net VSS -inst_pattern .* -pin_pattern VSS -ground
set_global_connections

set_voltage_domain -name CORE -power VDD -ground VSS
define_pdn_grid -name stdcell_grid -starts_with POWER -voltage_domains {CORE} \
    -pins {RDL}
add_pdn_stripe -grid stdcell_grid -layer MET4 -width 1.0 -pitch 12.0 \
    -offset 2.0 -spacing 5.0 -starts_with POWER
# An 8 um pitch intersects multiple 45 um-spaced MET5 pin pairs in every
# cluster macro, including clusters separated by the 1025 um row pitch.
add_pdn_stripe -grid stdcell_grid -layer MET5 -width 0.5 -pitch 8.0 \
    -offset 3.2 -spacing 0.5 -starts_with POWER -extend_to_boundary
add_pdn_connect -grid stdcell_grid -layers {MET4 MET5}
add_pdn_stripe -grid stdcell_grid -layer T4M2 -width 6.0 -pitch 30.0 \
    -offset 8.6 -spacing 2.0 -starts_with POWER -extend_to_boundary
add_pdn_connect -grid stdcell_grid -layers {MET5 T4M2}
# One RDL pair crosses each 1025 um cluster row. It connects the cluster's
# vertical T4M2 interface pins and is the final Tier2 macro power interface.
add_pdn_stripe -grid stdcell_grid -layer RDL -width 8.0 -pitch 1025.0 \
    -offset 100.0 -spacing 8.0 -starts_with POWER -extend_to_boundary
add_pdn_connect -grid stdcell_grid -layers {T4M2 RDL}

if {$::env(PDN_ENABLE_RAILS)} {
    add_pdn_stripe -grid stdcell_grid -layer MET1 -width 0.09 -followpins
    add_pdn_connect -grid stdcell_grid -layers {MET1 MET4}
}

define_pdn_grid -macro -default -name cluster_macro_grid -starts_with POWER \
    -grid_over_pg_pins -halo {0 0}
add_pdn_connect -grid cluster_macro_grid -layers {T4M2 RDL}
