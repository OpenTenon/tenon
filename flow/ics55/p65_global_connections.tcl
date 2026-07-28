# SPDX-License-Identifier: Apache-2.0
# Exact P65 electrical supply mapping. All four foundry P65 rails propagate
# through PBMUX, supply, corner and filler cells; no macro is hidden from
# PDN or disconnected-pin checks.

proc tenon_ics55_p65_global_connect {net_name pin_pattern sig_type inst_pattern} {
    if {$sig_type eq "POWER"} {
        add_global_connection -net $net_name -inst_pattern $inst_pattern \
            -pin_pattern $pin_pattern -power
    } else {
        add_global_connection -net $net_name -inst_pattern $inst_pattern \
            -pin_pattern $pin_pattern -ground
    }
}

set tenon_ics55_stdcell_pattern {^(?!u_reference\.u_padframe\.).*}
set tenon_ics55_p65_pattern {^u_reference\.u_padframe\..*}

foreach {net_name pin_pattern sig_type} {
    vdd VDD POWER
    vss VSS GROUND
    iovdd VDDIO POWER
    iovss VSSIO GROUND
} {
    tenon_ics55_p65_global_connect $net_name $pin_pattern $sig_type $tenon_ics55_stdcell_pattern
    tenon_ics55_p65_global_connect $net_name $pin_pattern $sig_type $tenon_ics55_p65_pattern
}
