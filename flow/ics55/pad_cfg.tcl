# SPDX-License-Identifier: Apache-2.0
# ICS55 SP55 pad placement and four-rail physical connectivity.

source $::env(SCRIPTS_DIR)/openroad/common/set_global_connections.tcl

set block [ord::get_db_block]
foreach {net_name inst_pattern pin_pattern sig_type} {
    vdd {.*u_vdd_pads.*} {VDD} POWER
    vss {.*u_vss_pads.*} {VSS} GROUND
    iovdd {.*u_iovdd_pads.*} {VDD25} POWER
    iovss {.*u_iovss_pads.*} {VSSD} GROUND
    pll_avdd {.*u_pll_avdd_pad.*} {SVDD1CAP} POWER
    pll_avss {.*u_pll_avss_pad.*} {SVSS1CAP} GROUND
    pll_avddio {.*u_pll_avddio_pad.*} {SAVDD} POWER
    pll_avssio {.*u_pll_avssio_pad.*} {SAVSS} GROUND
} {
    set net [$block findNet $net_name]
    if {$net eq "NULL"} {
        set net [odb::dbNet_create $block $net_name]
    }
    $net setSpecial
    $net setSigType $sig_type
    if {$sig_type eq "POWER"} {
        add_global_connection -net $net_name -inst_pattern $inst_pattern -pin_pattern $pin_pattern -power
    } else {
        add_global_connection -net $net_name -inst_pattern $inst_pattern -pin_pattern $pin_pattern -ground
    }
}
# Keep H7CR standard cells and generated tap/endcap cells on the core grid.
# The negative match deliberately excludes every SP55 instance under the
# padframe hierarchy, where only the explicit power-pad bindings above apply.
add_global_connection -net vdd -inst_pattern {^(?!u_reference\.u_padframe\.).*} \
    -pin_pattern {VDD} -power
add_global_connection -net vss -inst_pattern {^(?!u_reference\.u_padframe\.).*} \
    -pin_pattern {VSS} -ground
set_global_connections
global_connect -force

set tenon_ics55_profile [string map {tenon_tier0_ics55_ "" _no_pll -no-pll _pll -pll} $::env(DESIGN_NAME)]
set tenon_ics55_placement [file join [file dirname [info script]] generated "${tenon_ics55_profile}_placement.tcl"]
if {![file exists $tenon_ics55_placement]} {
    error "Missing generated ICS55 pad placement $tenon_ics55_placement"
}
source $tenon_ics55_placement

# The adapter LEF exposes physical ports only on dedicated supply pads. PB4
# VDD25/VSSD remain separate in RTL but are not promoted to fictional ports.
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
