# ICS55 Smoke Designs

This directory keeps small, beginner-level digital designs that can be
hardened independently with the external ICS55 LibreLane manual PDK adapter.

Each design follows the LibreLane newcomers layout:

```text
smoke/<design>/
  config.json
  <design>.sv
```

Every configuration is a standard-cell-only Classic flow with a 60%
core-utilization target. Its absolute core floorplan uses the larger of that
target and the 10 um x 14 um minimum ICS55 PDN core, so the smallest designs
report a lower realized utilization. The top-level `VDD` and `VSS` ports form
the single H7CR core power domain.
The runner stops the normal Classic flow after KLayout rendering, generates an
abstract LEF from the resulting OpenROAD database, then resumes at the LEF
antenna-property check. This avoids the manual PDK's incomplete Magic GDS layer
mapping while retaining the OpenROAD antenna, PDN, route-DRC, disconnected-pin,
and timing checks. The generated abstract LEF can warn that signal pins do not
carry gate or diffusion antenna properties; that warning remains in the run log.

These are non-signoff evaluations: Magic DRC, KLayout DRC, Magic Spice
extraction, and Netgen LVS are skipped.

Run one design from the repository root:

```sh
python3 smoke/run_all.py --design inverter
```

Run every design serially with `make smoke-run`, then regenerate the compact
result summary with `make smoke-report`. Detailed LibreLane artifacts remain
under each design's ignored `runs/` directory. `REPORTS.md` records the
post-route, pre-filler utilization and flags each die as `UNDER` or `EXCEEDS`
against the 0.001 mm2 (1000 um2) area budget.
