# SPDX-License-Identifier: Apache-2.0
# ICS55 package IO rings built from PVDD2/PVSS2 source-backed T4M2 terminals.

proc tenon_ics55_io_box {swire layer x0 y0 x1 y1} {
    set xlo [expr {min($x0, $x1)}]
    set xhi [expr {max($x0, $x1)}]
    set ylo [expr {min($y0, $y1)}]
    set yhi [expr {max($y0, $y1)}]
    odb::dbSBox_create $swire $layer $xlo $ylo $xhi $yhi STRIPE
}

proc tenon_ics55_io_horizontal {swire layer width x0 x1 y} {
    set half [expr {$width / 2}]
    tenon_ics55_io_box $swire $layer $x0 [expr {$y - $half}] $x1 [expr {$y + $half}]
}

proc tenon_ics55_io_vertical {swire layer width x y0 y1} {
    set half [expr {$width / 2}]
    tenon_ics55_io_box $swire $layer [expr {$x - $half}] $y0 [expr {$x + $half}] $y1
}

proc tenon_ics55_io_edge {x y die_x0 die_y0 die_x1 die_y1} {
    set edge SOUTH
    set distance [expr {$y - $die_y0}]
    foreach {candidate candidate_distance} [list \
        EAST [expr {$die_x1 - $x}] \
        NORTH [expr {$die_y1 - $y}] \
        WEST [expr {$x - $die_x0}]] {
        if {$candidate_distance < $distance} {
            set edge $candidate
            set distance $candidate_distance
        }
    }
    return $edge
}

proc tenon_ics55_build_io_ring {block tech net_name margin_um} {
    set units [$block getDefUnits]
    set die [$block getDieArea]
    set die_x0 [$die xMin]
    set die_y0 [$die yMin]
    set die_x1 [$die xMax]
    set die_y1 [$die yMax]
    set margin [expr {int($margin_um * $units)}]
    set left [expr {$die_x0 + $margin}]
    set bottom [expr {$die_y0 + $margin}]
    set right [expr {$die_x1 - $margin}]
    set top [expr {$die_y1 - $margin}]
    set t4m2_width [expr {int(8 * $units)}]
    set rdl_width [expr {int(35 * $units)}]

    set net [$block findNet $net_name]
    if {$net eq "NULL"} {
        error "Missing ICS55 IO rail $net_name"
    }
    set t4m2 [$tech findLayer T4M2]
    set rdl [$tech findLayer RDL]
    set rv [$tech findVia RDL_T4M2]
    foreach object [list $t4m2 $rdl $rv] {
        if {$object eq "NULL"} {
            error "ICS55 IO ring requires T4M2, RDL, and RDL_T4M2"
        }
    }

    set swire [odb::dbSWire_create $net FIXED]
    tenon_ics55_io_horizontal $swire $rdl $rdl_width $left $right $bottom
    tenon_ics55_io_horizontal $swire $rdl $rdl_width $left $right $top
    tenon_ics55_io_vertical $swire $t4m2 $t4m2_width $left $bottom $top
    tenon_ics55_io_vertical $swire $t4m2 $t4m2_width $right $bottom $top
    foreach x [list $left $right] {
        foreach y [list $bottom $top] {
            odb::dbSBox_create $swire $rv $x $y STRIPE
        }
    }

    foreach iterm [$net getITerms] {
        set instance_name [[$iterm getInst] getName]
        if {[string first "u_iov" $instance_name] < 0} {
            continue
        }
        foreach geometry [odb::dbITerm_getGeometries $iterm] {
            set layer [lindex $geometry 0]
            if {[$layer getName] ne "T4M2"} {
                continue
            }
            set rect [lindex $geometry 1]
            set x [expr {int(([$rect xMin] + [$rect xMax]) / 2)}]
            set y [expr {int(([$rect yMin] + [$rect yMax]) / 2)}]
            set edge [tenon_ics55_io_edge $x $y $die_x0 $die_y0 $die_x1 $die_y1]
            switch -- $edge {
                SOUTH {
                    tenon_ics55_io_vertical $swire $t4m2 $t4m2_width $x $y $bottom
                    odb::dbSBox_create $swire $rv $x $bottom STRIPE
                }
                NORTH {
                    tenon_ics55_io_vertical $swire $t4m2 $t4m2_width $x $top $y
                    odb::dbSBox_create $swire $rv $x $top STRIPE
                }
                WEST {
                    odb::dbSBox_create $swire $rv $x $y STRIPE
                    tenon_ics55_io_horizontal $swire $rdl $rdl_width $x $left $y
                    odb::dbSBox_create $swire $rv $left $y STRIPE
                }
                EAST {
                    odb::dbSBox_create $swire $rv $x $y STRIPE
                    tenon_ics55_io_horizontal $swire $rdl $rdl_width $right $x $y
                    odb::dbSBox_create $swire $rv $right $y STRIPE
                }
            }
            break
        }
    }
}

set tenon_ics55_io_block [ord::get_db_block]
set tenon_ics55_io_tech [[ord::get_db] getTech]
tenon_ics55_build_io_ring $tenon_ics55_io_block $tenon_ics55_io_tech iovdd 250
tenon_ics55_build_io_ring $tenon_ics55_io_block $tenon_ics55_io_tech iovss 300

