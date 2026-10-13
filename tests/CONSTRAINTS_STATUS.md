<!-- Copyright 2026, SoC Labs (www.soclabs.org) -->
# Timing-constraints generator (SDC + XDC) — STATUS

A `nanosoc_gen` backend that emits **TIMING** constraints for both flows:

* **SDC** — Synopsys / ASIC (synthesis + P&R), and
* **XDC** — Xilinx / Vivado FPGA (Pynq-Z2).

Scope = clocks / clock-groups / io-delays / false-paths / multicycle / clock
uncertainty / ASIC pad-cell constraints. **Board pin placement
(`PACKAGE_PIN` / `IOSTANDARD`) is OUT OF SCOPE** — it stays project-level (a
future pin-map-driven emitter). Reported as a residual on every run.

## Files

| File | Role |
|------|------|
| `soc_model/backends/constraints.py` | The backend — `SoCConstraintsBackend` (`render_sdc()` / `render_xdc()` / `generate()` / `aggregated_constraints()` / `residuals()`). |
| `soc_model/model.py` | New dataclasses: `GeneratedClock`, `IoDelay`, `FalsePath`, `MulticyclePath`, `ClockGroup`, `ConstraintSet`; `Clock` gains `freq_hz`/`period_ns`/`port`; `Module` gains `constraints`. |
| `soc_model/builder.py` | `_build_constraints()` parses the `constraints:` block; clock parsing extended for `freq_hz`/`period_ns`/`port`. |
| `soc_model/__main__.py` | `--emit-constraints` CLI flag + the generation step. |
| `tests/unit/test_constraints.py` | 24 unit + 4 slow (real-model) tests. |
| `tests/golden/test_constraints_golden.py` | SDC + XDC byte-stable snapshots. |
| `tests/golden/__snapshots__/constraints/constraints_soc_constraints.{sdc,xdc}` | The committed goldens. |
| `tests/fixtures/constraints_soc.yaml` | Hermetic top: system clock + 2 pre-verified IPs. |
| `tests/fixtures/regions/eth_rmii_ip/eth_rmii_ip.yaml` | Pre-verified Ethernet RMII IP **with a `constraints:` block** (RMII clock, /2 MII gen-clocks, async group, io-delays, MDIO false-path). |
| `tests/fixtures/regions/qspi_flash_ip/qspi_flash_ip.yaml` | Pre-verified QSPI IP with a `constraints:` block (SCK gen-clocks + io-delays). |
| `tests/fixtures/chip_boundary/constraints_chip.yaml` | Hermetic boundary (clk / swd / hostio4 groups). |

Generated outputs land in `build_soc/constraints/<top>_constraints.sdc` and
`build_soc/constraints/<top>_constraints.xdc`.

## The `constraints:` schema (per-module, the pre-verified-IP block)

Authored on **any** module YAML so a PRE-VERIFIED IP carries the timing it
needs; the backend AGGREGATES every resolved instance's block up to the top.

```yaml
constraints:
  scope: both              # both | asic|sdc | fpga|xdc  (set default; each entry may override)

  clocks:
    - { name: rmii_ref_clk, port: rmii_ref_clk, freq_hz: 50000000 }   # or period_ns:

  generated_clocks:
    - name: mii_rx_clk
      divide_by: 2           # or multiply_by:
      source_pin: "*u_rmii_to_mii/mrx_clk_reg/C"   # FPGA hierarchical glob
      target_pin: "*u_rmii_to_mii/mrx_clk_reg/Q"   # the divided-clock reg pin

  clock_groups:
    - name: async_sys_rmii
      groups:                # list of lists: clocks WITHIN a list are synchronous,
        - [sys_clk]          # clocks in DIFFERENT lists are asynchronous
        - [rmii_ref_clk, mii_rx_clk, mii_tx_clk]

  io_delays:
    - { kind: input,  clock: rmii_ref_clk, ports: ["phy_rmii_rxd[*]"], min: 2.0, max: 14.0 }
    - { kind: output, clock: qspi_sclk,    ports: ["qspi_io[*]"],      min: -1.0, max: 3.0 }

  false_paths:
    - { to: phy_mdc }                 # any of from / to / through; hold: true for -hold only

  multicycle_paths:
    - { cycles: 2, setup: true, end: true, from: SWDCK, to: CLK }
```

Top-level `clocks:` also accept `freq_hz` / `period_ns` / `port` for the
system/external clocks. `SYS_CLK_FREQ_HZ` (a top param) feeds the primary
system clock period when no per-clock frequency is set.

## How to generate (CLI)

```bash
cd $SOCLABS_NANOSOC_GEN_DIR
python3 -m soc_model <top>.yaml \
    --lib-dir <libs...> \
    --chip-boundary nanosoc_multicore \
    --emit-constraints \
    --build-dir <build_soc>
```

`--chip-boundary` is optional but recommended: it classifies the boundary pads
(clk / swd / hostio4 / ...) so the ASIC **pad-cell block** (`uPAD_SWDIO_IO`,
`uPAD_HOSTIO4_*`), the boundary input-delays, and the SWD false-paths are
emitted. Without it, only the model clocks + per-IP `constraints:` drive output.

## Superproject Makefile hook (exact line)

In `nanosoc-multicore-system/sys_desc/Makefile`, the `soc_model` recipe already
passes `$(PAD_FLAGS)` (which carries `--chip-boundary $(CHIP_BOUNDARY)`). Add
`--emit-constraints` to that set so the constraints reuse the boundary — append
inside the `ifneq ($(strip $(PAD_TECH)),none)` block (right after the
`--fpga-wrapper` `endif` at line 131):

```makefile
PAD_FLAGS += --emit-constraints
```

(Equivalently, `EMIT_CONSTRAINTS=1` in the environment enables it.)

## What reproduces the hand files (timing intent, not byte-identical)

### ASIC SDC vs the hand-written 65 nm `constraints.sdc` in `nanosoc_arch_tech/asic/`
Reproduced from the real model + bundled boundary (`render_sdc()`):
* `create_clock` EXTCLK (`sys_fclk`, 10 ns from `SYS_CLK_FREQ_HZ=100 MHz`) and
  SWDCLK (`swdclk`, 40 ns = 4× EXTCLK, on the `swd` input pad), each with
  `set_clock_uncertainty 0.35`;
* inter-clock setup uncertainty (0.1) SWD↔EXT both directions;
* the async-domain multicycle block (`2 -setup -end`, `1 -hold -end`, both
  directions) + `set_false_path -hold -from sys_fclk -to swdclk`;
* the **pad-cell block**: `set_false_path -through uPAD_SWDIO_IO`,
  `set_multicycle_path 2 -through uPAD_SWDIO_IO`, and the per-control-pin
  `… uPAD_SWDIO_IO/{I,IE,DS,OEN} -to …/C`; the hand `uPAD_P0_*/uPAD_P1_*`
  GPIO-bus block is reproduced on the multicore's wide bidir bus as
  `uPAD_HOSTIO4_*/{I,IE,DS,OEN} -to …/C`;
* boundary input-delays (`SYS_SYSRESETN`, `SYS_SCANENABLE`, `SYS_TESTMODE`, …
  @ `sys_fclk`; the SWD clock pad @ `swdclk`);
* the design-rule tail `set_max_capacitance 3` / `set_max_fanout 10`.

### FPGA XDC vs `pynq/targets/pynq-z2/nanosoc_multicore_timing.xdc`
Reproduced from the per-IP `constraints:` aggregation (`render_xdc()`,
demonstrated on the hermetic fixture; the same mechanism applies to the real IPs
once authored — see residuals):
* `create_clock rmii_ref_clk` @ 50 MHz (20 ns);
* `create_generated_clock mii_rx_clk / mii_tx_clk` (÷2 of `rmii_ref_clk`) on the
  `mrx_clk_reg/Q` / `mtx_clk_reg/Q` divider pins (hierarchical-glob source);
* `create_generated_clock qspi_sclk_i` (HCLK÷2) + `qspi_sclk` (÷1 forwarded to
  the SCK output) on `QSPI_SCLK_reg`;
* `set_clock_groups -asynchronous` for `sys_clk` vs `{rmii_ref_clk mii_rx_clk
  mii_tx_clk}` (sys↔rmii async, RMII children synchronous together);
* RMII io-delays (rx `min 2.0 / max 14.0`, tx `min -1.0 / max 5.0`) and QSPI
  io-delays (`qspi_io`/`qspi_ncs` out `-1.0/3.0`, `qspi_io` in `1.0/7.0`);
* false-paths: `phy_mdc` (per-IP) + the boundary-derived SWD set
  (`swd_clk`/`swdio` from / to).

Every aggregated line carries a `;#  <instance-path>` provenance tag.

## Per-IP aggregation (the pre-verified-IP story)

`aggregated_constraints()` walks the resolved hierarchy depth-first and collects
each `module.constraints`. In the fixture, `u_eth_ss_0` contributes the RMII
clock + 2 gen-clocks + async group + 2 io-delays + 1 false-path, and
`u_qspi_flash_0` contributes 2 gen-clocks + 2 io-delays — all emitted at the top
with the right scope. Verified by `tests/unit/test_constraints.py::TestAggregation`
and the XDC golden.

## Residuals

* **Pin placement (`PACKAGE_PIN` / `IOSTANDARD`) is OUT OF SCOPE** — stays in
  the board pin XDC (`pynq/targets/pynq-z2/nanosoc_multicore.xdc`); a future
  pin-map-driven emitter could generate it.
* On the **REAL** multicore the FPGA-critical IPs (`u_eth_ss_0`,
  `u_qspi_flash_0`, …) **do not yet carry a `constraints:` block**, so the real
  XDC currently emits only the boundary-derived SWD false-paths. The backend
  reports the exact list of IP instances missing a block
  (`ips_without_constraints()`), naming what to author. The fixture IPs show the
  exact block shape to copy into the real `ethernet-subsystem-ahb` /
  `ahb_qspi` YAMLs (these live in submodules / the read-only `sys_desc`, so
  authoring them is a follow-up, coordinated change — not done here).
* The model `sys_fclk` has no FPGA frequency (it is the BD `clk_wiz` / PS FCLK
  output on the Pynq, handled by Vivado) — reported, not faked into a
  `create_clock`. Declare `freq_hz` on it if a hard FPGA system-clock
  constraint is wanted.
* Generated-clock source/target **pin globs** are vendor/RTL-instance specific
  (the divider register names) — they are authored in the IP `constraints:`
  block, not model-derived.

## Test status

```
cd "$SOCLABS_NANOSOC_GEN_DIR"        # your nanosoc_gen checkout
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -m "unit or golden" -q
# 933 passed, 1 skipped, 44 deselected

# real-model cross-check (slow):
NANOSOC_REAL_ROOT=<a nanosoc-multicore-system checkout> \
  python3 -m pytest -p no:cacheprovider -m slow \
  tests/unit/test_constraints.py::TestRealMulticoreConstraints -q
# 4 passed
```
