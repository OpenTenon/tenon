# SPDX-License-Identifier: Apache-2.0
# Core-only VDD/VSS grid for a 25x40 um Tenon Mini project macro.

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
define_pdn_grid -name project_grid -starts_with POWER -voltage_domains {CORE} \
    -pins {MET4 MET5}

add_pdn_stripe -grid project_grid -layer MET4 -width 0.3 -pitch 4 -offset 1 \
    -spacing 0.2 -starts_with POWER
# T4V2 needs 0.46 um of MET5 enclosure in the orthogonal direction. Keep the
# project interface at 0.5 um so a parent T4M2 strap can insert a legal via.
add_pdn_stripe -grid project_grid -layer MET5 -width 0.5 -pitch 4 -offset 1 \
    -spacing 0.5 -starts_with POWER -extend_to_boundary
add_pdn_connect -grid project_grid -layers {MET4 MET5}

if {$::env(PDN_ENABLE_RAILS)} {
    add_pdn_stripe -grid project_grid -layer MET1 -width 0.09 -followpins
    add_pdn_connect -grid project_grid -layers {MET1 MET4}
}
