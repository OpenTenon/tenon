# SPDX-License-Identifier: Apache-2.0
# Sky130A sky130_fd_io padring placement for Tenon generated PAD_* lists.

source $::env(SCRIPTS_DIR)/openroad/common/set_global_connections.tcl

set block [ord::get_db_block]
set units [$block getDefUnits]

# Preserve each RTL rail as its own special net. No cross-rail global connections are made.
# Map every PDK supply pin to one of the four explicit Tier0 rails.
foreach {net_name pin_pattern sig_type} {
    vccd {VCCD} POWER
    vddio {VDDIO|VDDIO_Q|VCCHIB|VDDA|VSWITCH} POWER
    vssd {VSSD} GROUND
    vssio {VSSIO|VSSIO_Q|VSSA} GROUND
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
# HVC supply pads expose M3 power and ground support terminals. Map each pad
# class to its own core or IO rail; no rail is shared across domains.
foreach {rail inst_pattern pin_pattern sig_type} {
    vccd  {.*u_vdd_pads.*}   {P_CORE|DRN_HVC|OGC_HVC} POWER
    vssd  {.*u_vdd_pads.*}   {SRC_BDY_HVC}             GROUND
    vccd  {.*u_vss_pads.*}   {DRN_HVC|OGC_HVC}         POWER
    vssd  {.*u_vss_pads.*}   {G_CORE|SRC_BDY_HVC}      GROUND
    vddio {.*u_iovdd_pads.*} {P_CORE|DRN_HVC|OGC_HVC} POWER
    vssio {.*u_iovdd_pads.*} {SRC_BDY_HVC}             GROUND
    vddio {.*u_iovss_pads.*} {DRN_HVC|OGC_HVC}         POWER
    vssio {.*u_iovss_pads.*} {G_CORE|SRC_BDY_HVC}      GROUND
} {
    if {$sig_type == "POWER"} {
        add_global_connection -net $rail -inst_pattern $inst_pattern -pin_pattern $pin_pattern -power
    } else {
        add_global_connection -net $rail -inst_pattern $inst_pattern -pin_pattern $pin_pattern -ground
    }
}

set_global_connections
global_connect -force
set die_height [expr {[lindex $::env(DIE_AREA) 3] - [lindex $::env(DIE_AREA) 1]}]
set die_width [expr {[lindex $::env(DIE_AREA) 2] - [lindex $::env(DIE_AREA) 0]}]
set pad_site [pad::find_site $::env(PAD_SITE_NAME)]
set corner_site [pad::find_site $::env(PAD_CORNER_SITE_NAME)]

if {$pad_site == "NULL" || $corner_site == "NULL"} {
    puts stderr "\[ERROR\] Missing Sky130 IO pad or corner site."
    exit 1
}

set pad_site_width [expr {double([$pad_site getWidth]) / $units}]
set corner_width [expr {double([$corner_site getWidth]) / $units}]
set corner_height [expr {double([$corner_site getHeight]) / $units}]

make_io_sites \
    -horizontal_site $::env(PAD_SITE_NAME) \
    -vertical_site $::env(PAD_SITE_NAME) \
    -corner_site $::env(PAD_CORNER_SITE_NAME) \
    -offset $::env(PAD_EDGE_SPACING)

set row_names [dict create PAD_SOUTH IO_SOUTH PAD_EAST IO_EAST PAD_NORTH IO_NORTH PAD_WEST IO_WEST]
set horizontal_sides {PAD_SOUTH PAD_NORTH}

foreach side {PAD_SOUTH PAD_EAST PAD_NORTH PAD_WEST} {
    set side_length $die_height
    set corner_extent $corner_height
    if {[lsearch -exact $horizontal_sides $side] >= 0} {
        set side_length $die_width
        set corner_extent $corner_width
    }
    set available [expr {$side_length - 2 * $::env(PAD_EDGE_SPACING) - 2 * $corner_extent}]
    set pad_widths 0

    foreach instance_name $::env($side) {
        set instance [$block findInst $instance_name]
        if {$instance == "NULL"} {
            puts stderr "\[ERROR\] No pad instance $instance_name found."
            exit 1
        }
        set pad_widths [expr {$pad_widths + [[$instance getMaster] getWidth] / $units}]
    }

    if {$pad_widths > $available} {
        puts stderr "\[ERROR\] $side pads exceed the available die edge."
        exit 1
    }

    set fill [expr {$available - $pad_widths}]
    set pad_count [llength $::env($side)]
    set gap [expr {floor($fill / ($pad_count + 1) / $pad_site_width) * $pad_site_width}]
    set edge_gap [expr {($fill - $gap * ($pad_count - 1)) / 2}]
    set aligned_edge_gap [expr {floor($edge_gap / $pad_site_width) * $pad_site_width}]

    if {abs($edge_gap - $aligned_edge_gap) > 0.005} {
        puts stderr "\[ERROR\] $side edge spacing is not aligned to $::env(PAD_SITE_NAME)."
        exit 1
    }
    set edge_gap $aligned_edge_gap

    set position [expr {$::env(PAD_EDGE_SPACING) + $corner_extent + $edge_gap}]
    foreach instance_name $::env($side) {
        set instance [$block findInst $instance_name]
        set master [[$instance getMaster] getName]
        set width [expr {[[$instance getMaster] getWidth] / $units}]
        place_pad -row [dict get $row_names $side] -location $position $instance_name -master $master
        set position [expr {$position + $width + $gap}]
    }
}

place_corners $::env(PAD_CORNER)
if {[llength $::env(PAD_FILLERS)] > 0} {
    foreach row {IO_NORTH IO_SOUTH IO_WEST IO_EAST} {
        place_io_fill -row $row {*}$::env(PAD_FILLERS)
    }
}

# The core and IO rails retain their distinct RTL net names; only like-named pins abut.
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
