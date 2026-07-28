# SPDX-License-Identifier: Apache-2.0
# Structural completion check for the commercial SP55 pad-row power ring.

proc tenon_ics55_require_placed {block instance_name} {
    set instance [$block findInst $instance_name]
    if {$instance eq "NULL"} {
        error "Missing ICS55 pad-ring instance $instance_name"
    }
    if {[$instance getPlacementStatus] eq "NONE"} {
        error "Unplaced ICS55 pad-ring instance $instance_name"
    }
}

set tenon_ics55_ring_block [ord::get_db_block]
set tenon_ics55_pad_count 0
foreach instance [$tenon_ics55_ring_block getInsts] {
    set instance_name [$instance getName]
    if {[string match "u_reference.u_padframe.u_base.*.u_pad" $instance_name]} {
        if {[$instance getPlacementStatus] eq "NONE"} {
            error "Unplaced ICS55 functional pad $instance_name"
        }
        incr tenon_ics55_pad_count
    }
}
if {![regexp {^tenon_tier0_ics55_qfn([0-9]+)_no_pll$} $::env(DESIGN_NAME) -> tenon_ics55_expected_pads]} {
    error "Unsupported ICS55 no-PLL pad-row design $::env(DESIGN_NAME)"
}
if {$tenon_ics55_pad_count != $tenon_ics55_expected_pads} {
    error "ICS55 pad row has $tenon_ics55_pad_count functional pads, expected $tenon_ics55_expected_pads"
}

foreach {side instance_name} {
    PAD_SOUTH u_reference.u_padframe.u_base.u_mgmt_clk_pad.u_pad
    PAD_EAST  u_reference.u_padframe.u_base.u_jtag_tck_pad.u_pad
    PAD_NORTH u_reference.u_padframe.u_base.u_jtag_tdo_pad.u_pad
    PAD_WEST  u_reference.u_padframe.u_base.u_uart_tx_pad.u_pad
} {
    tenon_ics55_require_placed $tenon_ics55_ring_block $instance_name
}
foreach corner {u_corner_sw u_corner_se u_corner_ne u_corner_nw} {
    tenon_ics55_require_placed $tenon_ics55_ring_block "u_reference.u_padframe.u_base.$corner"
}

set tenon_ics55_filler_count 0
foreach instance [$tenon_ics55_ring_block getInsts] {
    if {[string match "PFILL*" [[$instance getMaster] getName]]} {
        incr tenon_ics55_filler_count
    }
}
if {$tenon_ics55_filler_count < 4} {
    error "ICS55 pad row has insufficient filler coverage"
}
puts "TENON_ICS55_IO_PAD_ROW_RING_COMPLETE sides=4 pads=$tenon_ics55_pad_count fillers=$tenon_ics55_filler_count"
