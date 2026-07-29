# SPDX-License-Identifier: Apache-2.0
# Sky130A sky130_ef_io pad placement for Tenon generated PAD_* lists.

source $::env(SCRIPTS_DIR)/openroad/common/set_global_connections.tcl

set block [ord::get_db_block]
set units [$block getDefUnits]

# Preserve each RTL rail as its own special net. No cross-rail global connections are made.
# Map every PDK supply pin to one of the four explicit Tier0 rails.
foreach {net_name pin_pattern sig_type} {
    vccd {VCCD|VCCD_PAD|VCCHIB} POWER
    vddio {VDDIO|VDDIO_Q|VDDIO_PAD|VDDA|VSWITCH} POWER
    vssd {VSSD|VSSD_PAD} GROUND
    vssio {VSSIO|VSSIO_Q|VSSIO_PAD|VSSA} GROUND
} {
    set net [$block findNet $net_name]
    if {$net == "NULL"} {
        set net [odb::dbNet_create $block $net_name]
    }
    $net setSpecial
    $net setSigType $sig_type
    if {$sig_type == "POWER"} {
        add_global_connection -net $net_name -inst_pattern .* -pin_pattern $pin_pattern -power
    } else {
        add_global_connection -net $net_name -inst_pattern .* -pin_pattern $pin_pattern -ground
    }
}
# EF-IO HVC wrappers expose M2/M3 drain and source support terminals. Map each
# pad class to its own core or IO rail; no rail is shared across domains.
foreach {rail inst_pattern pin_pattern sig_type} {
    vccd  {.*u_vdd_pads.*}   {DRN_HVC}     POWER
    vssd  {.*u_vdd_pads.*}   {SRC_BDY_HVC} GROUND
    vccd  {.*u_vss_pads.*}   {DRN_HVC}     POWER
    vssd  {.*u_vss_pads.*}   {SRC_BDY_HVC} GROUND
    vddio {.*u_iovdd_pads.*} {DRN_HVC}     POWER
    vssio {.*u_iovdd_pads.*} {SRC_BDY_HVC} GROUND
    vddio {.*u_iovss_pads.*} {DRN_HVC}     POWER
    vssio {.*u_iovss_pads.*} {SRC_BDY_HVC} GROUND
} {
    if {$sig_type == "POWER"} {
        add_global_connection -net $rail -inst_pattern $inst_pattern -pin_pattern $pin_pattern -power
    } else {
        add_global_connection -net $rail -inst_pattern $inst_pattern -pin_pattern $pin_pattern -ground
    }
}

set_global_connections
global_connect -force
set tenon_sky130_profile [string map {tenon_tier0_sky130_ ""} $::env(DESIGN_NAME)]
set tenon_sky130_placement_file [format "%s_placement.tcl" $tenon_sky130_profile]
set tenon_sky130_placement [file join [file dirname [info script]] generated $tenon_sky130_placement_file]
if {![file exists $tenon_sky130_placement]} {
    error "Missing generated Sky130 placement $tenon_sky130_placement"
}
source $tenon_sky130_placement

# Fixed RTL pad locations are now present; bind like-named rail pins and validate
# only physical abutment of equal rail domains.
set_global_connections
global_connect -force
connect_by_abutment
foreach side {PAD_SOUTH PAD_EAST PAD_NORTH PAD_WEST} {
    foreach instance_name $::env($side) {
        set instance [$block findInst $instance_name]
        set master [[$instance getMaster] getName]
        foreach master_pin $::env(PAD_PLACE_IO_TERMINALS) {
            set parts [split $master_pin /]
            if {$master == [lindex $parts 0]} {
                place_io_terminals $instance_name/[lindex $parts 1]
                break
            }
        }
    }
}

remove_io_rows
