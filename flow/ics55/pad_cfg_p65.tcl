# SPDX-License-Identifier: Apache-2.0
# ICS55 P65 no-PLL placement and four-rail connectivity.

source $::env(SCRIPTS_DIR)/openroad/common/set_global_connections.tcl

set block [ord::get_db_block]
source [file join [file dirname [info script]] p65_global_connections.tcl]
set_global_connections
global_connect -force

set tenon_ics55_profile [string map {tenon_tier0_ics55_ "" _no_pll -no-pll} $::env(DESIGN_NAME)]
set tenon_ics55_placement [file join [file dirname [info script]] generated "${tenon_ics55_profile}_placement.tcl"]
if {![file exists $tenon_ics55_placement]} {
    error "Missing generated ICS55 P65 pad placement $tenon_ics55_placement"
}
source $tenon_ics55_placement

foreach side {PAD_SOUTH PAD_EAST PAD_NORTH PAD_WEST} {
    foreach instance_name $::env($side) {
        set instance [$block findInst $instance_name]
        set master [[$instance getMaster] getName]
        foreach master_pin $::env(PAD_PLACE_IO_TERMINALS) {
            set parts [split $master_pin /]
            if {$master == [lindex $parts 0]} {
                place_io_terminals $instance_name/[lindex $parts 1] -allow_non_top_layer
            }
        }
    }
}
remove_io_rows
