# Tenon Engineering Guide

## Architecture

Tenon is a hierarchical open-source SoC framework with three tiers.

| Tier | Name | Depends on | Responsibility |
|---|---|---|---|
| Tier0 | Foundation | None | Package-specific padframe, power domains, pin manifest and hardened reference views. |
| Tier1 | Management | Tier0 | Thin SoC wrapper for IO mapping, clock multiplication, debug and a large reserved Tier2 integration region. |
| Tier2 | Application | Tier0 and Tier1 | User design, supplied as RTL or a hardened macro, integrated through Tier1. |

Tier0's GDS/LEF is a standalone reference hardening result. It is not a normal macro that can surround a separately placed Tier1 macro. Tier1 must reuse the selected Tier0 SystemVerilog wrapper, pin manifest and physical constraints in its own chip-level hardening run so that the pad ring, PDN and center logic share one top-level route and signoff context.

## Tier0 Contract

- Supported profiles are QFN32, QFN64, QFN88 and QFN128. Their lead count equals the Tier0 package-pin count and excludes an exposed pad.
- Keep each PDK's core rail pair electrically separate from its IO rail pair: IHP `VDD/VSS` and `IOVDD/IOVSS`, Sky130 `VCCD/VSSD` and `VDDIO/VSSIO`, GF180 `VDD/VSS` and `DVDD/DVSS`, ICS55 no-PLL `VDD/VSS` and `VDDIO/VSSIO`, and ICS55 legacy PLL `VDD/VSS` and `VDD25/VSSD`. Do not merge rails in RTL, PDN Tcl, package diagrams or macro integration.
- `specs/tier0_profiles.json` is the source of truth for the common package contract. `specs/tier0_pdks.json` maps those logical rails and profiles to each PDK. Never hand-edit generated files in `flow/ihp130/qfn*.yaml`, `flow/sky130/qfn*.yaml`, `flow/gf180/qfn*.yaml`, `flow/ics55/qfn*.yaml` or `docs/pinout/qfn*`; run `make generate` instead.
- Management pins are stable: `mgmt_clk`, `mgmt_rst_n`, JTAG `TCK/TMS/TDI/TDO`, and UART `RX/TX`. GPIOs use the `gpio_i/gpio_o/gpio_oe` interface.
- Pin numbering is top-view, starts at the south-west corner, and advances counter-clockwise. The generated CSV is the package/wire-bond handoff source.

## PDK Adapters

- IHP SG13G2 uses `tenon_tier0_padframe`, `sg13g2_io`, and the committed LibreLane reference flows. IHP GPIOs use 30 mA bidirectional cells. Its default physical PDK is Ciel revision `3b5a704ba6738aa686b08706187830e6284d2a10`.
- Sky130A uses `tenon_tier0_padframe_sky130`, PDK-provided `sky130_ef_io__gpiov2_pad_wrapped` and HVC supply wrappers around the raw `sky130_fd_io` cells, plus `flow/sky130/` for physical reference flows. Map core rails to `VCCD/VSSD` and IO rails to `VDDIO/VSSIO`; `VDDIO_Q`, `VCCHIB`, `VDDA`, `VSWITCH`, `VSSA` and `VSSIO_Q` remain on their documented supply domains. The CORE PDN serves only `VCCD/VSSD`; `VDDIO/VSSIO` are registered secondary supplies and connect through explicit package-to-PDN bridges. GDS extraction retains top-level routing and uses LEF interfaces only for the named PDK IO hard-macro hierarchy. The default physical PDK is Ciel revision `8afc8346a57fe1ab7934ba5a6056ea8b43078e71`. Each GPIO loops its own `TIE_HI_ESD` and `TIE_LO_ESD` outputs to static controls, matching the Tiny Tapeout local loopback approach; the reference top exports the four package power nets. No PDK installation configuration belongs in this repository.
- GF180MCU D uses `tenon_tier0_padframe_gf180` and `gf180mcu_ocd_io`. Map core rails to `VDD/VSS` and IO rails to `DVDD/DVSS`; do not short either pair. Its default physical PDK is Ciel revision `f6eeac7dad085ffcc829ccfd721f7b4ce39edcf7`.
- GF180 physical reference flows use the audited OCD LEF/GDS geometry, `gf180mcu_fd_sc_mcu7t5v0` and `gf180mcu_ocd_io`. Their PDN connects the core ring only to `VDD/VSS`; `DVDD/DVSS` remain an abutted IO pad-ring domain.
- ICS55 has `no-pll` and `pll` Tier0 variants for every QFN profile. No-PLL uses H7CR standard cells and the commercial P65/ICSIOA `P65_1233_PBMUX` at DS1/DS0=`1/0` (8 mA), P65 `VDD3/VSS3/VDDIO3/VSSIO3` supply pads, P65 corners and fillers; its core and IO pairs are `VDD/VSS` and `VDDIO/VSSIO`. The legacy PB4/SP55 PLL variant retains `PB4`, `PADI30/PADO30`, supply pads, corner cells and fillers; `PB24` is not used. PLL additionally uses `PXWE1`, `PLL_TOP`, `PVDD1CAP/PVSS1CAP` and `PVDD3AP/PVSS3AP`. `PVDD1/VDD` and `PVSS1/VSS` serve the core pair, PB4 keeps `VDD25/VSSD` as the separate IO pair, and `PLL_TOP` keeps `AVDD/AVSS` separate while exporting raw controls to Tier1. Its LibreLane PDK is an external manual adapter at `ICS55_PDK_ROOT` (default `~/.ciel/manual`) and must not add PDK installation logic to this repository.
- The ICS55 P65 adapter derives OpenROAD interfaces only from commercial LEF/GDS terminal geometry. QFN32 no-PLL currently has zero PSM and disconnected-pin violations but 12 TritonRoute DRC errors, so it is not a clean hardening or signoff result. Resolve remaining routing and foundry package/RDL validation with qualified data; never suppress PSM, connectivity or route-DRC checks to label a result clean.
- Preserve every production IO pad instance with synthesis attributes. CI-only behavioral PadCell models belong under `tb/`; never substitute them into synthesizable sources.
- LibreLane run artifacts reside under `flow/ihp130/runs/`, `flow/sky130/runs/`, `flow/gf180/runs/`, and `flow/ics55/runs/`; ICS55 uses `tenon-qfn{N}-no-pll` and `tenon-qfn{N}-pll` run tags. Do not export duplicate hardening views into `build/`.

## Tier1 and Tier2 Rules

- Tier1 owns safe reset behavior, IO muxing, clock policy and debug access; Tier0 only transports signals through qualified IO cells.
- Tier1 must retain the selected Tier0 pinout and must produce the final full-chip GDS. It may reserve package pins only through a new, versioned profile.
- Tier2 RTL is hardened within Tier1. A hardened Tier2 macro must provide its GDS, LEF, timing library, power-pin names, halo and PDN connection data before physical integration. Tier2 never instantiates package pads directly.

## Implementation and Verification

- Use SystemVerilog for RTL and keep `default_nettype none` around every module.
- Run `make check-generated`, `make sim`, and `make format-check` before review. Run `make lint` and `make test` with the installed IHP PDK before IHP hardening; run `make lint-sky130`, `make lint-gf180` or `make lint-ics55` with the corresponding installed or manual PDK before using those adapters.
- Do not suppress DRC, LVS, antenna or connectivity checks. The only allowed exceptions are the IHP template's intentional `Checker.IllegalOverlap` suppression and an explicitly user-authorized `SKIP_DRC=1` non-signoff run, which skips Magic and KLayout DRC only.
- Hardening defaults to the three installed Ciel revisions and can be redirected with `PDK_ROOT` or a PDK-specific root variable; do not add Nix, Docker, PDK download or LibreLane installation/startup configuration to this repository.
