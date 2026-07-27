# SPDX-License-Identifier: Apache-2.0
# ICS55 PLL extension. The ordinary core/IO grids remain in pdn.tcl.

set ::env(TENON_ICS55_DISABLE_DEFAULT_MACRO_PDN) 1
source [file join [file dirname [info script]] pdn.tcl]
unset ::env(TENON_ICS55_DISABLE_DEFAULT_MACRO_PDN)

# Keep PLL_TOP AVDD/AVSS separate from the core rails. The pad sources are on
# T4M2 while the macro ports are on MET5, so add dedicated fixed straps.
proc tenon_ics55_largest_geometry_on_layer {iterm layer_name} {
    set selected ""
    set selected_area -1
    foreach geometry [odb::dbITerm_getGeometries $iterm] {
        set layer [lindex $geometry 0]
        if {[$layer getName] ne $layer_name} {
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

proc tenon_ics55_connect_pll_supply {block tech net_name pad_instance macro_pin} {
    set net [$block findNet $net_name]
    set pad_iterm ""
    set macro_iterm ""
    foreach iterm [$net getITerms] {
        set instance_name [[$iterm getInst] getName]
        if {[string first $pad_instance $instance_name] >= 0} {
            set pad_iterm $iterm
        }
        if {$instance_name eq "u_reference.u_padframe.u_pll" && [[$iterm getMTerm] getName] eq $macro_pin} {
            set macro_iterm $iterm
        }
    }
    if {$net eq "NULL" || $pad_iterm eq "" || $macro_iterm eq ""} {
        error "Missing ICS55 PLL supply connection for $net_name"
    }

    set source [tenon_ics55_largest_geometry_on_layer $pad_iterm T4M2]
    set target [tenon_ics55_largest_geometry_on_layer $macro_iterm MET5]
    set t4m2 [$tech findLayer T4M2]
    set t4v2 [$tech findVia T4M2_MET5]
    if {$source eq "" || $target eq "" || $t4m2 eq "NULL" || $t4v2 eq "NULL"} {
        error "Missing ICS55 PLL geometry or T4M2-to-MET5 via for $net_name"
    }

    set source_x [expr {int(([$source xMin] + [$source xMax]) / 2)}]
    set source_y [expr {int(([$source yMin] + [$source yMax]) / 2)}]
    set target_x [expr {int(([$target xMin] + [$target xMax]) / 2)}]
    set target_y [expr {int(([$target yMin] + [$target yMax]) / 2)}]
    set width [expr {int(8 * [$block getDefUnits])}]
    set swire [odb::dbSWire_create $net FIXED]
    tenon_ics55_io_horizontal $swire $t4m2 $width $source_x $target_x $source_y
    tenon_ics55_io_vertical $swire $t4m2 $width $target_x $source_y $target_y
    odb::dbSBox_create $swire $t4v2 $target_x $target_y STRIPE
}

set tenon_ics55_pll_block [ord::get_db_block]
set tenon_ics55_pll_tech [[ord::get_db] getTech]
tenon_ics55_connect_pll_supply $tenon_ics55_pll_block $tenon_ics55_pll_tech pll_avdd "u_reference.u_padframe.u_pll_avdd_pad.u_pad" AVDD
tenon_ics55_connect_pll_supply $tenon_ics55_pll_block $tenon_ics55_pll_tech pll_avss "u_reference.u_padframe.u_pll_avss_pad.u_pad" AVSS
tenon_ics55_connect_pll_supply $tenon_ics55_pll_block $tenon_ics55_pll_tech vdd u_reference.u_padframe.u_base.u_vdd_pads DVDD
tenon_ics55_connect_pll_supply $tenon_ics55_pll_block $tenon_ics55_pll_tech vdd u_reference.u_padframe.u_base.u_vdd_pads DVDD_DRV
tenon_ics55_connect_pll_supply $tenon_ics55_pll_block $tenon_ics55_pll_tech vss u_reference.u_padframe.u_base.u_vss_pads DVSS
tenon_ics55_connect_pll_supply $tenon_ics55_pll_block $tenon_ics55_pll_tech vss u_reference.u_padframe.u_base.u_vss_pads DVSS_DRV
