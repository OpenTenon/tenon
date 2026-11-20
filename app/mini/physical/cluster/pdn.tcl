# SPDX-License-Identifier: Apache-2.0
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
source $::env(SCRIPTS_DIR)/openroad/common/set_global_connections.tcl

set_layer_rc -via VIA1 -resistance 2.0
set_layer_rc -via VIA2 -resistance 2.0
set_layer_rc -via VIA3 -resistance 2.0
set_layer_rc -via VIA4 -resistance 2.0
set_layer_rc -via T4V2 -resistance 0.25

add_global_connection -net VDD -inst_pattern .* -pin_pattern VDD -power
add_global_connection -net VSS -inst_pattern .* -pin_pattern VSS -ground
set_global_connections

set_voltage_domain -name CORE -power VDD -ground VSS
define_pdn_grid -name stdcell_grid -starts_with POWER -voltage_domains {CORE} \
    -pins {T4M2}
add_pdn_stripe -grid stdcell_grid -layer MET4 -width 1.0 -pitch 12.0 \
    -offset 2.0 -spacing 5.0 -starts_with POWER
# The first pair is centered at y=22.4/22.9 um. Repeating it every 60 um
# overlaps one VDD/VSS MET5 pair in every 40 um project macro row.
add_pdn_stripe -grid stdcell_grid -layer MET5 -width 0.5 -pitch 60.0 \
    -offset 11.2 -spacing 0.5 -starts_with POWER -extend_to_boundary
add_pdn_connect -grid stdcell_grid -layers {MET4 MET5}
# One T4M2 VDD/VSS pair crosses every 45 um project column. T4M2 has no
# project-macro obstruction and therefore provides the parent-level bridge to
# every horizontal MET5 power pin while leaving MET1..MET5 signal routing local.
add_pdn_stripe -grid stdcell_grid -layer T4M2 -width 6.0 -pitch 45.0 \
    -offset 18.6 -spacing 2.0 -starts_with POWER -extend_to_boundary
add_pdn_connect -grid stdcell_grid -layers {MET5 T4M2}

if {$::env(PDN_ENABLE_RAILS)} {
    add_pdn_stripe -grid stdcell_grid -layer MET1 -width 0.09 -followpins
    add_pdn_connect -grid stdcell_grid -layers {MET1 MET4}
}

define_pdn_grid -macro -default -name project_macro_grid -starts_with POWER \
    -grid_over_pg_pins -halo {0 0}
add_pdn_connect -grid project_macro_grid -layers {MET5 T4M2}
