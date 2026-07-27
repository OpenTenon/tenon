# OpenROAD preflight for the externally maintained ICS55 LibreLane adapter.

if {![info exists ::env(ICS55_PDK_ROOT)]} {
    puts stderr "ICS55_PDK_ROOT is required"
    exit 2
}
set root [file normalize [file join $::env(ICS55_PDK_ROOT) ics55]]
read_lef [file join $root libs.ref ics55_LLSC_H7CR techlef N551P6M_openroad.lef]
read_lef [file join $root libs.ref ics55_LLSC_H7CR lef ics55_LLSC_H7CR_M2.lef]
read_lef [file join $root libs.ref ics55_io_3p3 lef SP55NLLD2P_3P3V_V0p4a_6MT_1TM.lef]
read_lef [file join $root libs.ref ics55_pll lef PLL_TOP.lef]
read_liberty [file join $root libs.ref ics55_LLSC_H7CR lib ics55_LLSC_H7CR_typ_tt_1p2_25_nldm.lib]
read_liberty [file join $root libs.ref ics55_io_3p3 lib SP55NLLD2P_3P3V_V0p3_tt_v1p20_25C.lib]
read_liberty [file join $root libs.ref ics55_pll lib PLL_TOP_typ.lib]

foreach cell {PB4 PXWE1 PVDD1 PVSS1 PVDD2 PVSS2 PVDD1CAP PVSS1CAP PVDD3AP PVSS3AP PLL_TOP} {
    if {[llength [get_lib_cells */$cell]] == 0} {
        puts stderr "Missing expected ICS55 IO cell $cell"
        exit 3
    }
}
puts "ICS55_OPENROAD_PRECHECK_OK"
exit 0
