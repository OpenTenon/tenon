if {![info exists ::env(OUTPUT_LEF)]} {
    error "OUTPUT_LEF must be set"
}

if {[info exists ::env(INPUT_ODB)]} {
    set input_odb $::env(INPUT_ODB)
} elseif {[info exists ::env(RUN_DIR)] && [info exists ::env(DESIGN_NAME)]} {
    set pattern "*-odb-cellfrequencytables/$::env(DESIGN_NAME).odb"
    set candidates [lsort -dictionary [glob -nocomplain -directory $::env(RUN_DIR) $pattern]]
    if {[llength $candidates] == 0} {
        error "no cell-frequency ODB found below $::env(RUN_DIR)"
    }
    set input_odb [lindex $candidates end]
} else {
    error "set INPUT_ODB, or set RUN_DIR and DESIGN_NAME"
}

file mkdir [file dirname $::env(OUTPUT_LEF)]
read_db $input_odb
write_abstract_lef $::env(OUTPUT_LEF)
