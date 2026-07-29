# ICS55 Smoke Designs

This directory keeps standalone beginner-level digital designs that can be
hardened independently with the external ICS55 LibreLane manual PDK adapter.
Each design uses a LibreLane newcomers-style directory:

```text
smoke/<design>/
  manifest.json  # Design metadata owned by this directory
  config.json    # LibreLane physical-flow configuration
  <design>.sv
```

All designs are standard-cell-only Classic flows. A layout unit is a fixed
`25 um x 40 um` die, or `0.001 mm2`; the core is a separate physical region
inside that die. The original small examples use one unit. Demonstration
examples use the first integer number of units that passes placement, routing,
PSM, antenna, and disconnected-pin checks. For a multi-unit design, the exact
factor pair with a die aspect ratio closest to one is selected while preserving
that unit count; for example, four units use a `2x2` grid rather than `1x4`.

The top-level `VDD` and `VSS` ports form the single H7CR core power domain.
The runner stops the normal Classic flow after KLayout rendering, generates an
abstract LEF from the resulting OpenROAD database, then resumes at the LEF
antenna-property check. This avoids the manual PDK's incomplete Magic GDS layer
mapping while retaining OpenROAD antenna, PDN, route-DRC, disconnected-pin, and
timing checks.

These are non-signoff evaluations: Magic DRC, KLayout DRC, Magic Spice
extraction, and Netgen LVS are skipped.

## Design Manifests

Every design directory owns a `manifest.json` using schema version 1. It is the
source of truth for the design name, top module, RTL source, functional
category, English description, port roles, expected behavior, required
integer count of `25 um x 40 um` layout units, and the exact `columns x rows`
layout grid. The root `smoke/manifest.json` is only the stable ordered catalog
used by smoke tooling.

`config.json` remains a strict LibreLane configuration file and must not carry
custom metadata. `make smoke-manifest-check` validates all local manifests,
their files, their `DESIGN_NAME` values, and the agreement between each layout
unit count and its configured die area. It is part of `make check`.

## Demonstrations

The demonstration examples have Tiny Tapeout-style external ports in addition to
`VDD/VSS`: `ui_in[7:0]`, `uo_out[7:0]`, `uio_in[7:0]`, `uio_out[7:0]`,
`uio_oe[7:0]`, `ena`, `clk`, and synchronous active-low `rst_n`.

| Design | Observable behavior |
|---|---|
| `led_chaser` | Directional 8-bit LED chaser |
| `rgb_pwm` | RGB PWM duty display |
| `traffic_light` | Traffic-light and pedestrian FSM |
| `sevenseg_counter` | Loadable seven-segment counter |
| `uart_tx` | UART transmit frame |
| `spi_master` | SPI clock, MOSI, and chip-select sequence |
| `lfsr_dice` | LFSR dice/random pattern |
| `reaction_timer` | Triggered reaction-time counter |
| `stopwatch` | Start/stop/lap stopwatch |
| `led_matrix_scan` | 8x8 LED row/column scan |


| Design | External control | Observable output |
|---|---|---|
| `led_chaser` | `ui_in[0]` run, `ui_in[1]` direction | One-hot LED vector on `uo_out` |
| `rgb_pwm` | `ui_in[7:5]` red, `[4:2]` green, `uio_in[2:0]` blue duty | RGB PWM on `uo_out[2:0]` |
| `traffic_light` | `ui_in[0]` walk request, `[1]` emergency | FSM state on `uo_out[2:0]` |
| `sevenseg_counter` | `ui_in[3:0]` value, `[4]` load, `[5]` run | Seven segments on `uo_out[6:0]` |
| `uart_tx` | `ui_in` byte, `uio_in[0]` start | TX on `uo_out[0]`, busy on `[3]` |
| `spi_master` | `ui_in` byte, `uio_in[0]` start, `[1]` MISO | SCLK/MOSI/CS_n/busy on `uo_out[3:0]` |
| `lfsr_dice` | `ui_in[0]` load seed, `[1]` hold | LFSR state on `uo_out` |
| `reaction_timer` | `ui_in[0]` arm, `uio_in[0]` reaction | State and elapsed count on `uo_out` |
| `stopwatch` | `ui_in[0]` run, `[1]` clear, `uio_in[0]` lap | Selected ticks/lap byte on `uo_out` |
| `led_matrix_scan` | `ui_in[0]` scan, `[3:1]` pattern, `uio_in[0]` invert | Columns on `uo_out`, row select on `uio_out` |

Each demonstration has a local `tb.sv`; `make smoke-sim` compiles and executes
its reset, external-input, and output-observation scenario. `make check` runs
both the top-level smoke compilation and these behavioral simulations.

## Running

Run one design from the repository root:

```sh
python3 smoke/run_all.py --design led_chaser
```

For greedy unit sizing, start from one unit and increase only after a physical
capacity failure. The helper preserves that exact unit count and chooses its
closest-to-square factor-pair grid automatically:

```sh
python3 smoke/layout_units.py --design led_chaser --units 2
```

Run all designs serially with `make smoke-run`, then regenerate the compact
summary with `make smoke-report`. `REPORTS.md` records each design's unit count,
post-route pre-filler utilization, and exact `units x 1000 um2` die target.
Detailed LibreLane artifacts remain under each design's ignored `runs/`
directory.
