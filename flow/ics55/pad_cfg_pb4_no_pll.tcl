# SPDX-License-Identifier: Apache-2.0
# Commercial asic_top-style SP55 pad binding for the QFN32 no-PLL profile.

source $::env(SCRIPTS_DIR)/openroad/common/set_global_connections.tcl

set block [ord::get_db_block]
foreach {net_name pin_pattern sig_type} {
    vdd VDD POWER
    vss VSS GROUND
    iovdd VDD25 POWER
    iovss VSSD GROUND
    fp FP POWER
    fpb FPB GROUND
} {
    set net [$block findNet $net_name]
    if {$net eq "NULL"} {
        set net [odb::dbNet_create $block $net_name]
    }
    $net setSpecial
    $net setSigType $sig_type
    if {$sig_type eq "POWER"} {
        add_global_connection -net $net_name -inst_pattern .* -pin_pattern $pin_pattern -power
    } else {
        add_global_connection -net $net_name -inst_pattern .* -pin_pattern $pin_pattern -ground
    }
}
set_global_connections
global_connect -force

set tenon_ics55_profile [string map {tenon_tier0_ics55_ "" _no_pll -no-pll _pll -pll} $::env(DESIGN_NAME)]
set tenon_ics55_placement [file join [file dirname [info script]] generated "${tenon_ics55_profile}_placement.tcl"]
if {![file exists $tenon_ics55_placement]} {
    error "Missing generated ICS55 pad placement $tenon_ics55_placement"
}
source $tenon_ics55_placement

foreach side {PAD_SOUTH PAD_EAST PAD_NORTH PAD_WEST} {
    foreach instance_name $::env($side) {
        set instance [$block findInst $instance_name]
        set master [[$instance getMaster] getName]
        foreach master_pin $::env(PAD_PLACE_IO_TERMINALS) {
            set parts [split $master_pin /]
            if {$master eq [lindex $parts 0]} {
                place_io_terminals $instance_name/[lindex $parts 1] -allow_non_top_layer
            }
        }
    }
}
remove_io_rows
