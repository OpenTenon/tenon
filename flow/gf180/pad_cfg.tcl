# SPDX-License-Identifier: Apache-2.0
# GF180 OCD IO padring placement for Tenon generated PAD_* lists.

source $::env(SCRIPTS_DIR)/openroad/common/set_global_connections.tcl
set_global_connections

set block [ord::get_db_block]
set units [$block getDefUnits]
set die_height [expr {[lindex $::env(DIE_AREA) 3] - [lindex $::env(DIE_AREA) 1]}]
set die_width [expr {[lindex $::env(DIE_AREA) 2] - [lindex $::env(DIE_AREA) 0]}]
set pad_site [pad::find_site $::env(PAD_SITE_NAME)]
set corner_site [pad::find_site $::env(PAD_CORNER_SITE_NAME)]

if {$pad_site == "NULL" || $corner_site == "NULL"} {
    puts stderr "\[ERROR\] Missing GF180 IO pad or corner site."
    exit 1
}

set pad_site_width [expr {double([$pad_site getWidth]) / $units}]
set corner_width [expr {double([$corner_site getWidth]) / $units}]

make_io_sites \
    -horizontal_site $::env(PAD_SITE_NAME) \
    -vertical_site $::env(PAD_SITE_NAME) \
    -corner_site $::env(PAD_CORNER_SITE_NAME) \
    -offset $::env(PAD_EDGE_SPACING)

set row_names [dict create PAD_SOUTH IO_SOUTH PAD_EAST IO_EAST PAD_NORTH IO_NORTH PAD_WEST IO_WEST]
set horizontal_sides {PAD_SOUTH PAD_NORTH}

foreach side {PAD_SOUTH PAD_EAST PAD_NORTH PAD_WEST} {
    set side_length $die_height
    if {[lsearch -exact $horizontal_sides $side] >= 0} {
        set side_length $die_width
    }
    set available [expr {$side_length - 2 * $::env(PAD_EDGE_SPACING) - 2 * $corner_width}]
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

    if {$edge_gap != $aligned_edge_gap} {
        puts stderr "\[ERROR\] $side edge spacing is not aligned to $::env(PAD_SITE_NAME)."
        exit 1
    }

    set position [expr {$::env(PAD_EDGE_SPACING) + $corner_width + $edge_gap}]
    foreach instance_name $::env($side) {
        set instance [$block findInst $instance_name]
        set master [[$instance getMaster] getName]
        set width [expr {[[$instance getMaster] getWidth] / $units}]
        place_pad -row [dict get $row_names $side] -location $position $instance_name -master $master
        set position [expr {$position + $width + $gap}]
    }
}

place_corners $::env(PAD_CORNER)
place_io_fill -row IO_NORTH {*}$::env(PAD_FILLERS)
place_io_fill -row IO_SOUTH {*}$::env(PAD_FILLERS)
place_io_fill -row IO_WEST {*}$::env(PAD_FILLERS)
place_io_fill -row IO_EAST {*}$::env(PAD_FILLERS)

# The four rail nets are explicitly separate in RTL. Abutment only joins like-named pins.
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
