# SPDX-License-Identifier: Apache-2.0
# Native P65 package-ring bridges. Every contact originates at an actual M5
# rectangle retained from the commercial P65 LEF abstraction. The four rings
# are on separate metal layers; VDD/VSS alone bridge to the core PDN.

proc tenon_ics55_p65_box {swire layer x0 y0 x1 y1} {
    odb::dbSBox_create $swire $layer [expr {min($x0, $x1)}] [expr {min($y0, $y1)}] \
        [expr {max($x0, $x1)}] [expr {max($y0, $y1)}] STRIPE
}

proc tenon_ics55_p65_horizontal {swire layer width x0 x1 y} {
    set half [expr {$width / 2}]
    tenon_ics55_p65_box $swire $layer $x0 [expr {$y - $half}] $x1 [expr {$y + $half}]
}

proc tenon_ics55_p65_vertical {swire layer width x y0 y1} {
    set half [expr {$width / 2}]
    tenon_ics55_p65_box $swire $layer [expr {$x - $half}] $y0 [expr {$x + $half}] $y1
}

proc tenon_ics55_p65_edge {x y die} {
    set edge SOUTH
    set distance [expr {$y - [$die yMin]}]
    foreach {candidate candidate_distance} [list \
        EAST [expr {[$die xMax] - $x}] \
        NORTH [expr {[$die yMax] - $y}] \
        WEST [expr {$x - [$die xMin]}]] {
        if {$candidate_distance < $distance} {
            set edge $candidate
            set distance $candidate_distance
        }
    }
    return $edge
}

proc tenon_ics55_p65_m5_geometry {iterm} {
    foreach geometry [odb::dbITerm_getGeometries $iterm] {
        set layer [lindex $geometry 0]
        if {[$layer getName] eq "MET5"} {
            return [lindex $geometry 1]
        }
    }
    return ""
}

proc tenon_ics55_p65_add_transition {swire tech net_name x y} {
    if {$net_name eq "vdd"} {
        set t4v2 [$tech findVia T4M2_MET5]
        set rv [$tech findVia RDL_T4M2]
        foreach via [list $t4v2 $rv] {
            if {$via eq "NULL"} { error "Missing P65 VDD bridge via" }
            odb::dbSBox_create $swire $via $x $y STRIPE
        }
    } elseif {$net_name eq "vss"} {
        set t4v2 [$tech findVia T4M2_MET5]
        if {$t4v2 eq "NULL"} { error "Missing P65 VSS bridge via" }
        odb::dbSBox_create $swire $t4v2 $x $y STRIPE
    } elseif {$net_name eq "iovdd"} {
        set via4 [$tech findVia MET5_MET4_VIA4_0]
        if {$via4 eq "NULL"} { error "Missing P65 IOVDD bridge via" }
        odb::dbSBox_create $swire $via4 $x $y STRIPE
    } elseif {$net_name eq "iovss"} {
        set via4 [$tech findVia MET5_MET4_VIA4_0]
        set via3 [$tech findVia MET4_MET3_VIA3_0]
        foreach via [list $via4 $via3] {
            if {$via eq "NULL"} { error "Missing P65 IOVSS bridge via" }
            odb::dbSBox_create $swire $via $x $y STRIPE
        }
    }
}

proc tenon_ics55_p65_ring_layer {tech net_name} {
    switch -- $net_name {
        vdd { return [$tech findLayer RDL] }
        vss { return [$tech findLayer T4M2] }
        iovdd { return [$tech findLayer MET4] }
        iovss { return [$tech findLayer MET3] }
    }
    error "Unknown P65 power net $net_name"
}

proc tenon_ics55_p65_connect_ring {block tech net_name margin_um} {
    set units [$block getDefUnits]
    set die [$block getDieArea]
    set margin [expr {int($margin_um * $units)}]
    set left [expr {[$die xMin] + $margin}]
    set bottom [expr {[$die yMin] + $margin}]
    set right [expr {[$die xMax] - $margin}]
    set top [expr {[$die yMax] - $margin}]
    set width [expr {int(6 * $units)}]
    set layer [tenon_ics55_p65_ring_layer $tech $net_name]
    set net [$block findNet $net_name]
    if {$net eq "NULL" || $layer eq "NULL"} {
        error "Missing P65 package-ring object for $net_name"
    }
    set swire [odb::dbSWire_create $net FIXED]
    tenon_ics55_p65_horizontal $swire $layer $width $left $right $bottom
    tenon_ics55_p65_horizontal $swire $layer $width $left $right $top
    tenon_ics55_p65_vertical $swire $layer $width $left $bottom $top
    tenon_ics55_p65_vertical $swire $layer $width $right $bottom $top

    foreach iterm [$net getITerms] {
        set master [[[$iterm getInst] getMaster] getName]
        if {![regexp {^P65_1233_(PBMUX|VDD3|VSS3|VDDIO3|VSSIO3|CORNER|FILLER.*)$} $master]} {
            continue
        }
        set rect [tenon_ics55_p65_m5_geometry $iterm]
        if {$rect eq ""} {
            error "Missing actual P65 MET5 port for [[$iterm getInst] getName]/[[$iterm getMTerm] getName]"
        }
        set x [expr {int(([$rect xMin] + [$rect xMax]) / 2)}]
        set y [expr {int(([$rect yMin] + [$rect yMax]) / 2)}]
        tenon_ics55_p65_add_transition $swire $tech $net_name $x $y
        set edge [tenon_ics55_p65_edge $x $y $die]
        switch -- $edge {
            SOUTH { tenon_ics55_p65_vertical $swire $layer $width $x $y $bottom }
            NORTH { tenon_ics55_p65_vertical $swire $layer $width $x $top $y }
            WEST { tenon_ics55_p65_horizontal $swire $layer $width $x $left $y }
            EAST { tenon_ics55_p65_horizontal $swire $layer $width $right $x $y }
        }
    }
    return [list $swire $bottom $top]
}

proc tenon_ics55_p65_core_bridge {block tech vdd_swire vdd_bottom vss_swire vss_bottom} {
    set units [$block getDefUnits]
    set core [$block getCoreArea]
    set center_x [expr {int(([$core xMin] + [$core xMax]) / 2)}]
    set width [expr {int(6 * $units)}]
    set vdd_y [expr {int(([$core yMin] + $::env(PDN_HOFFSET) * $units))}]
    set vss_y [expr {int($vdd_y + $::env(PDN_HPITCH) * $units)}]
    set rdl [$tech findLayer RDL]
    set t4m2 [$tech findLayer T4M2]
    set rdl_t4m2 [$tech findVia RDL_T4M2]
    set t4m2_met5 [$tech findVia T4M2_MET5]
    foreach object [list $rdl $t4m2 $rdl_t4m2 $t4m2_met5] {
        if {$object eq "NULL"} { error "Missing P65 core-grid bridge object" }
    }
    tenon_ics55_p65_vertical $vdd_swire $rdl $width $center_x $vdd_bottom $vdd_y
    odb::dbSBox_create $vdd_swire $rdl_t4m2 $center_x $vdd_y STRIPE
    odb::dbSBox_create $vdd_swire $t4m2_met5 $center_x $vdd_y STRIPE
    tenon_ics55_p65_vertical $vss_swire $t4m2 $width $center_x $vss_bottom $vss_y
    odb::dbSBox_create $vss_swire $t4m2_met5 $center_x $vss_y STRIPE
}

set tenon_ics55_p65_block [ord::get_db_block]
set tenon_ics55_p65_tech [[ord::get_db] getTech]
lassign [tenon_ics55_p65_connect_ring $tenon_ics55_p65_block $tenon_ics55_p65_tech vdd 160] \
    tenon_ics55_p65_vdd_swire tenon_ics55_p65_vdd_bottom tenon_ics55_p65_vdd_top
lassign [tenon_ics55_p65_connect_ring $tenon_ics55_p65_block $tenon_ics55_p65_tech vss 160] \
    tenon_ics55_p65_vss_swire tenon_ics55_p65_vss_bottom tenon_ics55_p65_vss_top
tenon_ics55_p65_connect_ring $tenon_ics55_p65_block $tenon_ics55_p65_tech iovdd 160
tenon_ics55_p65_connect_ring $tenon_ics55_p65_block $tenon_ics55_p65_tech iovss 160
tenon_ics55_p65_core_bridge $tenon_ics55_p65_block $tenon_ics55_p65_tech \
    $tenon_ics55_p65_vdd_swire $tenon_ics55_p65_vdd_bottom \
    $tenon_ics55_p65_vss_swire $tenon_ics55_p65_vss_bottom
