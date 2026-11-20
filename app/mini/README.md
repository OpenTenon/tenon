# Tenon Mini Tier2 Template

Tenon Mini is a digital-only Tier2 multi-project fabric. The checked-in v1
configuration integrates 2048 logical base units as eight identical 256-site
clusters:

The original architecture proposal and diagram are retained in
[the fabric proposal](../../tenon_hierarchy_mux_fabric.md) and
[its diagram](../../tenon_hierarchy_mux_fabric.png). The specifications and
implementation in this directory define the current interface and placement.

```text
root controller
  -> 8 clusters
    -> 16 branches per cluster
      -> 16 sites per branch
```

The 11-bit direct address is
`{cluster[2:0], branch[3:0], site[3:0]}`. There is no asynchronous selection
shift chain. Tier1 is expected to drive `project_addr_i` from its system
controller and pulse `select_commit_i` only while `select_ready_o` is high.
The controller performs break-before-make switching: it removes output enable,
stops the old branch and cluster clocks, changes the registered address, starts
the new clock path, and only then enables the selected project.

## Project Contract

One base unit is 25 by 40 um. All project signal pins are on the 40 um east
edge on MET3. Every project implements this split digital interface:

```systemverilog
input  logic [1:0]  clk_i;
input  logic [1:0]  rst_n_i;
input  logic        enable_i;
input  logic [11:0] gpio_i;
output logic [11:0] gpio_o;
output logic [11:0] gpio_oe;
inout  wire         VDD;
inout  wire         VSS;
```

The two active-low resets must already be synchronized to their corresponding
user clocks before entering this Tier2 macro. The Mini fabric does not
resynchronize them. `gpio_i/gpio_o/gpio_oe` represent 12 bidirectional digital
IOs; only the root-selected `gpio_oe` reaches Tier1. Analog projects and analog
switch paths are not supported.

A project may occupy any integer rectangular footprint from 1x1 through 16x16
base units. This includes, for example, 1x2, 2x1, 2x2, 3x5, 4x8, 8x8 and 16x16,
which is a broader set than a fixed footprint catalog. A footprint must be
contiguous and remain within one 16x16 cluster. Only its south-west anchor has
an address; continuation sites are reserved and cannot be selected.

Declare projects in `specs/projects.json`. Fixed projects provide
`location.cluster`, `location.branch` and `location.site`; projects without a
location are placed by decreasing area with a first-fit policy. The optional
`reference_fill` entry fills every unused site for full-capacity structural and
physical testing. Run `make mini-generate` after changing either specification.
Generated RTL, placement data and the address manifest live in `generated/` and
must not be edited by hand.

## Capacity Extension

The local contract remains 256 sites per cluster. Increasing
`cluster_count` adds complete clusters and changes the address width to
`8 + ceil(log2(cluster_count))`; the generator check exercises 16 clusters,
4096 sites and a 12-bit address. Tier1 must expose the corresponding wider
address when capacity exceeds 2048. Projects never cross a cluster boundary, so
existing 256-site cluster implementations and footprint rules remain stable.

## Verification

```bash
make mini-check
make mini-sim
make mini-sim-full
```

`mini-check` validates footprint allocation, overlap rejection, generated-file
freshness, future capacity and RTL compilation. The fast simulation covers
dual clocks, output isolation, disable/re-enable and break-before-make
selection. `mini-sim-full` commits all 2048 addresses and checks one-hot
selection.

## ICS55 Reference Hardening

The physical reference hierarchy is:

```text
25x40 um reference project (MET5 power interface)
  -> 865x985 um 256-site cluster (T4M2 power interface)
    -> 2755x3115 um eight-cluster Tier2 macro (RDL power interface)
```

Twenty-micron routing channels separate adjacent project sites. The reference
project exports its 41 signal pins on the east MET3 edge. Core power rises from
MET5 in each project through T4V2 into the cluster T4M2 grid, then through RV
into the top RDL grid. Only `VDD/VSS` exist in this Tier2 macro.

```bash
make harden-mini-ics55-all \
  ICS55_PDK_ROOT=$HOME/.ciel/manual
```

The targets use the repository physical-run guard, retain native run evidence,
and refuse to overwrite an existing run. Run `make verify-pdk TENON_PDK=ics55`
before physical execution. A separately authorized DRC-only run may set
`SKIP_DRC=1` and `TENON_DRC_APPROVAL` to the authorization reference; this skips
only Magic and KLayout DRC and is not signoff. Extraction and LVS skipping are
blocked by the current engineering policy. Missing extraction/LVS support is a
flow blocker, and historical runs using those skips do not qualify a new run.
PDN generation and PSM, global/detailed routing, antenna checks, route DRC,
disconnected-pin checks and STA remain enabled.

The 2048-site reference population deliberately models every
empty site as a maximum-area macro. Post-placement and post-route timing repair
and CTS NDR are disabled for this artificial maximum-load population because
they otherwise insert thousands of buffers or make GRT remove hundreds of NDR
assignments one at a time. CTS is limited to `ctrl_clk_i`; the two user clocks
remain timed, routed and connectivity-checked but are not skew-closed against
placeholder macro models. A production assembly must provide characterized
project Liberty files, restore all three CTS roots and an appropriate NDR
policy, rerun timing closure for its actual project set, and complete
DRC/LVS/signoff. This reference flow does not derive a Liberty model for the
routed cluster macro, so top-level STA treats cluster boundaries as black
boxes. Production integration must characterize that cluster interface and the
final Tier2 macro before handing its timing view to Tier1.

Native run artifacts remain under each hierarchy directory's `runs/` tree and
are ignored by Git.
