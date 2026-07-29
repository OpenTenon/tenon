# ICS55 Smoke Reports

All results are non-signoff evaluations. Magic DRC, KLayout DRC, Magic
Spice extraction, and Netgen LVS are skipped. Route DRC, OpenROAD
antenna, PSM, and critical disconnected-pin results are read from the
corresponding completed flow stages. Every die is fixed at 1000 um2; actual
utilization is sampled after detailed routing and before filler insertion.

| Design | Status | Die target | Die (um2) | Die (mm2) | Core (um2) | Cell (um2) | Actual util (%) | Std cells | Route DRC | OpenROAD antenna | PSM | Critical disconnected | Setup WNS | Metrics | Route report |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| inverter | PASS | FIXED | 1000 | 0.001000 | 140 | 6.440 | 4.600 | 21 | 0 | 0 | 0 | 0 | 5.438 | [metrics](inverter/runs/ics55-smoke/final/metrics.json) | [route](inverter/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| and2 | PASS | FIXED | 1000 | 0.001000 | 140 | 7.280 | 5.200 | 21 | 0 | 0 | 0 | 0 | 5.412 | [metrics](and2/runs/ics55-smoke/final/metrics.json) | [route](and2/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| xor2 | PASS | FIXED | 1000 | 0.001000 | 140 | 8.400 | 6.000 | 21 | 0 | 0 | 0 | 0 | 5.260 | [metrics](xor2/runs/ics55-smoke/final/metrics.json) | [route](xor2/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| mux2 | PASS | FIXED | 1000 | 0.001000 | 140 | 8.400 | 6.000 | 21 | 0 | 0 | 0 | 0 | 5.279 | [metrics](mux2/runs/ics55-smoke/final/metrics.json) | [route](mux2/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| full_adder | PASS | FIXED | 1000 | 0.001000 | 140 | 14.560 | 10.400 | 24 | 0 | 0 | 0 | 0 | 4.973 | [metrics](full_adder/runs/ics55-smoke/final/metrics.json) | [route](full_adder/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| decoder2to4 | PASS | FIXED | 1000 | 0.001000 | 140 | 13.160 | 9.400 | 26 | 0 | 0 | 0 | 0 | 4.940 | [metrics](decoder2to4/runs/ics55-smoke/final/metrics.json) | [route](decoder2to4/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| priority_encoder4to2 | PASS | FIXED | 1000 | 0.001000 | 140 | 11.760 | 8.400 | 24 | 0 | 0 | 0 | 0 | 4.908 | [metrics](priority_encoder4to2/runs/ics55-smoke/final/metrics.json) | [route](priority_encoder4to2/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| register8 | PASS | FIXED | 1000 | 0.001000 | 140 | 86.240 | 61.600 | 52 | 0 | 0 | 0 | 0 | 15.178 | [metrics](register8/runs/ics55-smoke/final/metrics.json) | [route](register8/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| shift_register8 | PASS | FIXED | 1000 | 0.001000 | 140 | 86.240 | 61.600 | 52 | 0 | 0 | 0 | 0 | 15.213 | [metrics](shift_register8/runs/ics55-smoke/final/metrics.json) | [route](shift_register8/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| counter8 | PASS | FIXED | 1000 | 0.001000 | 154 | 94.640 | 61.455 | 50 | 0 | 0 | 0 | 0 | 14.185 | [metrics](counter8/runs/ics55-smoke/final/metrics.json) | [route](counter8/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |

Acceptance requires die area = 1000 um2 within 0.001 um2,
zero flow errors,
zero route DRC, zero OpenROAD antenna violations, zero PSM violations, and
zero critical disconnected pins.
