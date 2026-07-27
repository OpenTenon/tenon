# SPDX-License-Identifier: Apache-2.0
# Connect ICS55 core-supply pad terminals to the corresponding core rings.

proc tenon_ics55_largest_t4m2_geometry {iterm} {
    set selected ""
    set selected_area -1
    foreach geometry [odb::dbITerm_getGeometries $iterm] {
        set layer [lindex $geometry 0]
        if {[$layer getName] ne "T4M2"} {
            continue
        }
        set rect [lindex $geometry 1]
        set area [expr {([$rect xMax] - [$rect xMin]) * ([$rect yMax] - [$rect yMin])}]
        if {$area > $selected_area} {
            set selected $rect
            set selected_area $area
        }
    }
    return $selected
}

proc tenon_ics55_core_ring_x {core net_name edge width spacing offset} {
    set half [expr {$width / 2}]
    set shift 0
    if {$net_name eq "vss"} {
        set shift [expr {$width + $spacing}]
    }
    if {$edge eq "WEST"} {
        return [expr {[$core xMin] - $offset - $half - $shift}]
    }
    return [expr {[$core xMax] + $offset + $half + $shift}]
}

proc tenon_ics55_core_ring_y {core net_name edge width spacing offset} {
    set half [expr {$width / 2}]
    set shift 0
    if {$net_name eq "vss"} {
        set shift [expr {$width + $spacing}]
    }
    if {$edge eq "SOUTH"} {
        return [expr {[$core yMin] - $offset - $half - $shift}]
    }
    return [expr {[$core yMax] + $offset + $half + $shift}]
}
proc tenon_ics55_nearest_vss_met5_y {core edge units} {
    set origin [expr {[$core yMin] + ($::env(PDN_HOFFSET) + $::env(PDN_HWIDTH) + $::env(PDN_HSPACING)) * $units}]
    if {$edge eq "SOUTH"} {
        return [expr {int($origin)}]
    }
    set pitch [expr {int($::env(PDN_HPITCH) * $units)}]
    set count [expr {int(([$core yMax] - $origin) / $pitch)}]
    return [expr {int($origin + $count * $pitch)}]
}


proc tenon_ics55_connect_core_sources {block tech net_name pad_prefix} {
    set units [$block getDefUnits]
    set die [$block getDieArea]
    set core [$block getCoreArea]
    set width [expr {int($::env(PDN_CORE_RING_VWIDTH) * $units)}]
    set spacing [expr {int($::env(PDN_CORE_RING_VSPACING) * $units)}]
    set offset [expr {int($::env(PDN_CORE_RING_VOFFSET) * $units)}]
    set rdl_width [expr {int(35 * $units)}]
    set t4m2_width [expr {int($::env(PDN_CORE_RING_VWIDTH) * $units)}]
    set net [$block findNet $net_name]
    set t4m2 [$tech findLayer T4M2]
    set rdl [$tech findLayer RDL]
    set rv [$tech findVia RDL_T4M2]
    set t4v2 [$tech findVia T4M2_MET5]
    foreach object [list $net $t4m2 $rdl $rv $t4v2] {
        if {$object eq "NULL"} {
            error "Missing ICS55 core source connection object for $net_name"
        }
    }

    set swire [odb::dbSWire_create $net FIXED]
    foreach iterm [$net getITerms] {
        set instance_name [[$iterm getInst] getName]
        if {[string first $pad_prefix $instance_name] < 0} {
            continue
        }
        set rect [tenon_ics55_largest_t4m2_geometry $iterm]
        if {$rect eq ""} {
            error "Missing T4M2 source geometry for $instance_name"
        }
        set x [expr {int(([$rect xMin] + [$rect xMax]) / 2)}]
        set y [expr {int(([$rect yMin] + [$rect yMax]) / 2)}]
        set edge [tenon_ics55_io_edge $x $y [$die xMin] [$die yMin] [$die xMax] [$die yMax]]
        switch -- $edge {
            WEST -
            EAST {
                set ring_x [tenon_ics55_core_ring_x $core $net_name $edge $width $spacing $offset]
                odb::dbSBox_create $swire $rv $x $y STRIPE
                tenon_ics55_io_horizontal $swire $rdl $rdl_width $x $ring_x $y
                odb::dbSBox_create $swire $rv $ring_x $y STRIPE
            }
            SOUTH -
            NORTH {
                if {$net_name eq "vss"} {
                    set mesh_y [tenon_ics55_nearest_vss_met5_y $core $edge $units]
                    tenon_ics55_io_vertical $swire $t4m2 $t4m2_width $x $y $mesh_y
                    odb::dbSBox_create $swire $t4v2 $x $mesh_y STRIPE
                } else {
                    set ring_y [tenon_ics55_core_ring_y $core $net_name $edge $width $spacing $offset]
                    tenon_ics55_io_vertical $swire $t4m2 $t4m2_width $x $y $ring_y
                    odb::dbSBox_create $swire $rv $x $ring_y STRIPE
                }
            }
            default {
                error "Unable to classify ICS55 core source $instance_name"
            }
        }
    }
}

set tenon_ics55_core_block [ord::get_db_block]
set tenon_ics55_core_tech [[ord::get_db] getTech]
tenon_ics55_connect_core_sources $tenon_ics55_core_block $tenon_ics55_core_tech \
    vdd "u_vdd_pads"
tenon_ics55_connect_core_sources $tenon_ics55_core_block $tenon_ics55_core_tech \
    vss "u_vss_pads"
