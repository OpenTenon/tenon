# ICS55 Smoke Reports

All results are non-signoff evaluations. Magic DRC, KLayout DRC, Magic
Spice extraction, and Netgen LVS are skipped. The table retains route DRC,
OpenROAD antenna, PSM, and critical disconnected-pin results from each final
metrics file.

| Design | Status | Die (um2) | Die (mm2) | Core (um2) | Std cells | Route DRC | OpenROAD antenna | PSM | Critical disconnected | Setup WNS | Metrics | Route report |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| inverter | PASS | 9216 | 0.009216 | 4569.600 | 713 | 0 | 0 | 0 | 0 | 5.383 | [metrics](inverter/runs/ics55-smoke/final/metrics.json) | [route](inverter/runs/ics55-smoke/44-openroad-detailedrouting/openroad-detailedrouting.log) |
| and2 | PASS | 9216 | 0.009216 | 4569.600 | 716 | 0 | 0 | 0 | 0 | 5.248 | [metrics](and2/runs/ics55-smoke/final/metrics.json) | [route](and2/runs/ics55-smoke/44-openroad-detailedrouting/openroad-detailedrouting.log) |
| xor2 | PASS | 9216 | 0.009216 | 4569.600 | 725 | 0 | 0 | 0 | 0 | 5.133 | [metrics](xor2/runs/ics55-smoke/final/metrics.json) | [route](xor2/runs/ics55-smoke/44-openroad-detailedrouting/openroad-detailedrouting.log) |
| mux2 | PASS | 9216 | 0.009216 | 4569.600 | 724 | 0 | 0 | 0 | 0 | 5.147 | [metrics](mux2/runs/ics55-smoke/final/metrics.json) | [route](mux2/runs/ics55-smoke/44-openroad-detailedrouting/openroad-detailedrouting.log) |
| full_adder | PASS | 9216 | 0.009216 | 4569.600 | 743 | 0 | 0 | 0 | 0 | 4.795 | [metrics](full_adder/runs/ics55-smoke/final/metrics.json) | [route](full_adder/runs/ics55-smoke/44-openroad-detailedrouting/openroad-detailedrouting.log) |
| decoder2to4 | PASS | 9216 | 0.009216 | 4569.600 | 749 | 0 | 0 | 0 | 0 | 5.014 | [metrics](decoder2to4/runs/ics55-smoke/final/metrics.json) | [route](decoder2to4/runs/ics55-smoke/44-openroad-detailedrouting/openroad-detailedrouting.log) |
| priority_encoder4to2 | PASS | 9216 | 0.009216 | 4569.600 | 755 | 0 | 0 | 0 | 0 | 4.819 | [metrics](priority_encoder4to2/runs/ics55-smoke/final/metrics.json) | [route](priority_encoder4to2/runs/ics55-smoke/44-openroad-detailedrouting/openroad-detailedrouting.log) |
| register8 | PASS | 9216 | 0.009216 | 4569.600 | 875 | 0 | 0 | 0 | 0 | 14.548 | [metrics](register8/runs/ics55-smoke/final/metrics.json) | [route](register8/runs/ics55-smoke/44-openroad-detailedrouting/openroad-detailedrouting.log) |
| shift_register8 | PASS | 9216 | 0.009216 | 4569.600 | 870 | 0 | 0 | 0 | 0 | 14.596 | [metrics](shift_register8/runs/ics55-smoke/final/metrics.json) | [route](shift_register8/runs/ics55-smoke/44-openroad-detailedrouting/openroad-detailedrouting.log) |
| counter8 | PASS | 9216 | 0.009216 | 4569.600 | 848 | 0 | 0 | 0 | 0 | 14.331 | [metrics](counter8/runs/ics55-smoke/final/metrics.json) | [route](counter8/runs/ics55-smoke/44-openroad-detailedrouting/openroad-detailedrouting.log) |

Acceptance requires die area < 10000 um2, zero flow errors,
zero route DRC, zero OpenROAD antenna violations, zero PSM violations, and
zero critical disconnected pins.
