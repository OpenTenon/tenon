# ICS55 Smoke Reports

All results are non-signoff evaluations. Magic DRC, KLayout DRC, Magic
Spice extraction, and Netgen LVS are skipped. Route DRC, OpenROAD
antenna, PSM, and critical disconnected-pin results are read from the
corresponding completed flow stages. Every die uses an integer number of
25 um x 40 um layout units; actual utilization is sampled after detailed
routing and before filler insertion.

| Design | Status | Units | Grid | Die target | Die (um2) | Die (mm2) | Core (um2) | Cell (um2) | Actual util (%) | Std cells | Route DRC | OpenROAD antenna | PSM | Critical disconnected | Setup WNS | Metrics | Route report |
|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| inverter | PASS | 1 | 1x1 | FIXED | 1000 | 0.001000 | 140 | 6.440 | 4.600 | 21 | 0 | 0 | 0 | 0 | 5.438 | [metrics](inverter/runs/ics55-smoke/final/metrics.json) | [route](inverter/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| and2 | PASS | 1 | 1x1 | FIXED | 1000 | 0.001000 | 140 | 7.280 | 5.200 | 21 | 0 | 0 | 0 | 0 | 5.412 | [metrics](and2/runs/ics55-smoke/final/metrics.json) | [route](and2/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| xor2 | PASS | 1 | 1x1 | FIXED | 1000 | 0.001000 | 140 | 8.400 | 6.000 | 21 | 0 | 0 | 0 | 0 | 5.260 | [metrics](xor2/runs/ics55-smoke/final/metrics.json) | [route](xor2/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| mux2 | PASS | 1 | 1x1 | FIXED | 1000 | 0.001000 | 140 | 8.400 | 6.000 | 21 | 0 | 0 | 0 | 0 | 5.279 | [metrics](mux2/runs/ics55-smoke/final/metrics.json) | [route](mux2/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| full_adder | PASS | 1 | 1x1 | FIXED | 1000 | 0.001000 | 140 | 14.560 | 10.400 | 24 | 0 | 0 | 0 | 0 | 4.973 | [metrics](full_adder/runs/ics55-smoke/final/metrics.json) | [route](full_adder/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| decoder2to4 | PASS | 1 | 1x1 | FIXED | 1000 | 0.001000 | 140 | 13.160 | 9.400 | 26 | 0 | 0 | 0 | 0 | 4.940 | [metrics](decoder2to4/runs/ics55-smoke/final/metrics.json) | [route](decoder2to4/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| priority_encoder4to2 | PASS | 1 | 1x1 | FIXED | 1000 | 0.001000 | 140 | 11.760 | 8.400 | 24 | 0 | 0 | 0 | 0 | 4.908 | [metrics](priority_encoder4to2/runs/ics55-smoke/final/metrics.json) | [route](priority_encoder4to2/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| register8 | PASS | 1 | 1x1 | FIXED | 1000 | 0.001000 | 140 | 86.240 | 61.600 | 52 | 0 | 0 | 0 | 0 | 15.178 | [metrics](register8/runs/ics55-smoke/final/metrics.json) | [route](register8/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| shift_register8 | PASS | 1 | 1x1 | FIXED | 1000 | 0.001000 | 140 | 86.240 | 61.600 | 52 | 0 | 0 | 0 | 0 | 15.213 | [metrics](shift_register8/runs/ics55-smoke/final/metrics.json) | [route](shift_register8/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| counter8 | PASS | 1 | 1x1 | FIXED | 1000 | 0.001000 | 154 | 94.640 | 61.455 | 50 | 0 | 0 | 0 | 0 | 14.185 | [metrics](counter8/runs/ics55-smoke/final/metrics.json) | [route](counter8/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| led_chaser | PASS | 3 | 3x1 | FIXED | 3000 | 0.003000 | 420 | 251.720 | 59.933 | 132 | 0 | 0 | 0 | 0 | 11.398 | [metrics](led_chaser/runs/ics55-smoke/final/metrics.json) | [route](led_chaser/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| rgb_pwm | PASS | 2 | 2x1 | FIXED | 2000 | 0.002000 | 280 | 140.560 | 50.200 | 86 | 0 | 0 | 0 | 0 | 10.537 | [metrics](rgb_pwm/runs/ics55-smoke/final/metrics.json) | [route](rgb_pwm/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| traffic_light | PASS | 2 | 2x1 | FIXED | 2000 | 0.002000 | 280 | 213.640 | 76.300 | 115 | 0 | 0 | 0 | 0 | 13.496 | [metrics](traffic_light/runs/ics55-smoke/final/metrics.json) | [route](traffic_light/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| sevenseg_counter | PASS | 4 | 2x2 | FIXED | 4000 | 0.004000 | 560 | 403.760 | 72.100 | 198 | 0 | 0 | 0 | 0 | 11.453 | [metrics](sevenseg_counter/runs/ics55-smoke/final/metrics.json) | [route](sevenseg_counter/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| uart_tx | PASS | 3 | 3x1 | FIXED | 3000 | 0.003000 | 420 | 259 | 61.667 | 123 | 0 | 0 | 0 | 0 | 11.839 | [metrics](uart_tx/runs/ics55-smoke/final/metrics.json) | [route](uart_tx/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| spi_master | PASS | 2 | 2x1 | FIXED | 2000 | 0.002000 | 280 | 235.760 | 84.200 | 122 | 0 | 0 | 0 | 0 | 10.163 | [metrics](spi_master/runs/ics55-smoke/final/metrics.json) | [route](spi_master/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| lfsr_dice | PASS | 2 | 2x1 | FIXED | 2000 | 0.002000 | 280 | 214.760 | 76.700 | 114 | 0 | 0 | 0 | 0 | 12.664 | [metrics](lfsr_dice/runs/ics55-smoke/final/metrics.json) | [route](lfsr_dice/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| reaction_timer | PASS | 3 | 3x1 | FIXED | 3000 | 0.003000 | 420 | 346.080 | 82.400 | 172 | 0 | 0 | 0 | 0 | 11.442 | [metrics](reaction_timer/runs/ics55-smoke/final/metrics.json) | [route](reaction_timer/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| stopwatch | PASS | 5 | 5x1 | FIXED | 5000 | 0.005000 | 700 | 596.120 | 85.160 | 258 | 0 | 0 | 0 | 0 | 7.474 | [metrics](stopwatch/runs/ics55-smoke/final/metrics.json) | [route](stopwatch/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |
| led_matrix_scan | PASS | 2 | 2x1 | FIXED | 2000 | 0.002000 | 280 | 219.800 | 78.500 | 120 | 0 | 0 | 0 | 0 | 9.435 | [metrics](led_matrix_scan/runs/ics55-smoke/final/metrics.json) | [route](led_matrix_scan/runs/ics55-smoke/41-openroad-detailedrouting/openroad-detailedrouting.log) |

Acceptance requires die area = Units * 1000 um2 within 0.001 um2,
zero flow errors, zero route DRC, zero OpenROAD antenna violations, zero PSM
violations, and zero critical disconnected pins.
