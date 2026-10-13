# nanosoc_gen User Guide

nanosoc_gen is a YAML-to-structural-RTL SoC composer (the Python package `soc_model`). You describe a SoC as a tree of module YAMLs wired together by AHB-Lite interconnects with an address map; the generator resolves the tree, validates it, and emits the *plumbing* — the toplevel, subsystem wrappers, the bus matrix, glue logic, a config package, register/discovery blocks, a firmware memory map plus linker scripts, reports, docs, and (optionally) the chip-boundary / pad-ring / FPGA / UPF technology layers — feeding all three downstream delivery paths (simulation, FPGA, ASIC) from one build. This guide is example-first and grounded in the current code on branch `feature/nanosoc-gen-roadmap`; every command, CLI flag, Makefile variable, and YAML field below has been verified against the source. (Note up front: `patch_ahb_to_apb.py` has been **retired** — it no longer exists and is never run; the generator emits correct AHB and AHB-to-APB-bridge RTL natively.)

## Table of contents

1. [The mental model](#1-the-mental-model)
2. [Setup](#2-setup)
3. [Hello SoC — a runnable walkthrough](#3-hello-soc--a-runnable-walkthrough)
4. [Writing a YAML config](#4-writing-a-yaml-config)
   - [4a. The verbose format, by example](#4a-the-verbose-format-by-example)
   - [4b. Field reference](#4b-field-reference)
   - [4c. Param refs, EXCLUDE, and module discovery](#4c-param-refs-exclude-and-module-discovery)
   - [4d. The simplified format (preferred)](#4d-the-simplified-format-preferred)
5. [Running the generator](#5-running-the-generator)
   - [5a. The canonical way: `make`](#5a-the-canonical-way-make)
   - [5b. CLI reference](#5b-cli-reference)
   - [5c. Validate-only and config overrides](#5c-validate-only-and-config-overrides)
   - [5d. Makefile variables and disable knobs](#5d-makefile-variables-and-disable-knobs)
6. [Understanding the outputs](#6-understanding-the-outputs)
   - [6a. The `build_soc/` tree](#6a-the-build_soc-tree)
   - [6b. Artifact reference](#6b-artifact-reference)
   - [6c. The post-passes (no `patch_ahb_to_apb`)](#6c-the-post-passes-no-patch_ahb_to_apb)
7. [The three downstream paths](#7-the-three-downstream-paths)
   - [7a. Sim / cocotb (RTL only)](#7a-sim--cocotb-rtl-only)
   - [7b. FPGA / Vivado](#7b-fpga--vivado)
   - [7c. ASIC / pads (chip wrapper + pad ring + UPF)](#7c-asic--pads-chip-wrapper--pad-ring--upf)
8. [Troubleshooting / FAQ](#8-troubleshooting--faq)
9. [Glossary](#9-glossary)
10. [Where to go next](#10-where-to-go-next)

---

# 1. The mental model

nanosoc_gen is a **YAML → structural-RTL SoC composer**. You describe a SoC as a *tree of module YAMLs* — each YAML file maps to one RTL module — wired together by one or more *interconnects* (AHB-Lite bus matrices with an address map). The generator resolves the tree, validates connectivity and the address map, then runs a fixed sequence of *backends* that emit the plumbing: the top level, subsystem wrappers, the bus matrix, glue logic, a config package, register/discovery blocks, a firmware memory-map + linker scripts, reports, docs, and (optionally) the chip-boundary / pad-ring / FPGA layers.

nanosoc_gen is *purely structural*: it never invents leaf logic. Modules marked `gen: False` (SRAM regions, CPU cores, vendor IP) are hand-written and supplied by you; the generator emits only the wiring between them (`gen: True` modules — the toplevel and subsystems). You almost never invoke the tool by hand — the entry point is `make -C sys_desc`, and `source set_env.sh` runs it for you when the build is stale ([Chapter 2](#2-setup)).

```
  module YAML tree                                interconnect (address map)
  -----------------                               --------------------------
  hello_soc.yaml            (gen:True  = toplevel, generator emits RTL)
    |
    +-- u_sram_0  -> tiny_region.yaml   (gen:False = leaf, you supply the RTL)
    |
    +-- hello_ahb_interconnect          base/size decode windows -> targets
            initiator: cpu_m  ----------------------> target: sram_0 @ 0x0..0xFFF

                 |  python3 -m soc_model <top.yaml> --lib-dir ... --build-dir build_soc
                 v
  parse (find + resolve modules) -> build (dataclass tree) -> validate (6 checks)
                 v
  BACKENDS: reports -> ahb matrix -> dma250 -> subsystems -> rdl -> discovery ->
            build_info -> config_pkg -> toplevel -> firmware -> [chip/pads/fpga/upf]
                 v
  build_soc/{rtl, flist, firmware, rdl, discovery, interconnect, reports, docs, pnr}
                 v
   SIM / cocotb        FPGA / Vivado (--fpga-wrapper)       ASIC / pads (--chip-boundary + --pad-tech)
```

Three terms you will use constantly:

- **module** — one YAML file = one RTL module. `gen: True` ⇒ the generator emits its RTL (toplevel, subsystems). `gen: False` ⇒ a leaf you supply by hand (regions, cores, macros).
- **interconnect** — an AHB-Lite bus matrix with an address map. Its default is `gen: True` (note: the *opposite* of a module's default), so the matrix RTL is generated for you via Arm's `BuildBusMatrix.pl`.
  - `impl: busmatrix` (the default) fills in Arm's `cmsdk_ahb_busmatrix` templates, which ship only with Arm Academic Access.
  - `impl: cmsdk_mux` needs no Academic Access collateral and runs no Perl: it emits a SoC Labs `<name>_lite.v` with the same module name and ports, built from the Cortex-M0 Quickstart `cmsdk_ahb_master_mux`, `cmsdk_ahb_slave_mux` and `cmsdk_ahb_default_slave`, which the flist references through `$(ARM_CORSTONE_101_DIR)`. It is a shared bus (no concurrent transfers), with a remap-aware decoder derived from the same effective address maps as the bus-matrix XML. A `passthrough:` target gets a private path from its one initiator, because a shared path deadlocks against a `passthrough:` initiator. It fails loudly, in the validator and in the backend, on anything it cannot represent: more than 5 initiators (3 per master mux, one cascade into the high-priority port 2; the cascaded ones must share one address map), more than 9 shared targets (10 slave-mux ports with the default slave), a non-32-bit address, overlapping effective regions, a passthrough target reachable by more than one initiator, or a passthrough initiator reaching a passthrough target. Topology and limits: `soc_model/backends/cmsdk_mux.py`.
- **initiator / target** — bus master / addressable subordinate window inside an interconnect. Each initiator carries a `targets:` visibility list of the windows it can reach.

---

# 2. Setup

## 2.1 Prerequisites

| Need | Why | Notes |
|---|---|---|
| **Python ≥ 3.7** | runs the `soc_model` package | the Makefile uses `python3` |
| **PyYAML** | parse the YAML descriptions | required; without it parsing fails |
| **Jinja2** | render all templated RTL / wrappers / firmware / docs | required for any emitted RTL. If absent, those backends print `Skipped (jinja2 not available)` and emit nothing |
| **perl + `ARM_IP_LIBRARY_PATH`** | run `BuildBusMatrix.pl` to emit the bus-matrix Verilog | without it you still get the interconnect XML + wrapper but **no matrix `.v`** (won't elaborate). Read-only IP tree — never write under it |
| **systemrdl-compiler + peakrdl-regblock** | regblock RTL for `gen: True` register maps + discovery/build_info | optional; the `.rdl` and discovery `.yaml` are emitted regardless |
| **slang / pyslang** | only for `--lint` | optional |

Quick check on your host:

```bash
python3 --version
python3 -c "import yaml, jinja2; print(yaml.__version__, jinja2.__version__)"
echo "$ARM_IP_LIBRARY_PATH"                          # must point at the Arm IP tree
which perl peakrdl
```

## 2.2 `source set_env.sh`

From the repo root (`nanosoc-multicore-system/`):

```bash
source set_env.sh
```

This is the standard way to enter the project. It does four things:

1. **Exports the `SOCLABS_*` env** the generated flists depend on — `SOCLABS_PROJECT_DIR`, `SOCLABS_NANOSOC_SOC_DIR`, `SOCLABS_NANOSOC_ARCH_TECH_DIR`, `SOCLABS_NANOSOC_GEN_DIR` (where `python3 -m soc_model` runs from), plus per-IP dirs (`ETH_SS_HOME`, `PHC_AHB_HOME`, `AHB_QSPI_HOME`, `IPC_MAILBOX_HOME`, …). VCS compiles fail on unresolved `${SOCLABS_*}` if you skip this.
2. **Sets the Arm IP paths** — `ARM_IP_LIBRARY_PATH` (your site's Arm IP library; set it in your shell/CI), `CMSDK_DIR`, `ARM_CORTEXM0PLUS_IP_PATH`. These point at the shared, read-only vendor IP tree.
3. **Points at the EDA tools** — `VCS_HOME`, `VERDI_HOME`, `VIP_HOME` (override as needed).
4. **Auto-regenerates the SoC model when stale.** If `sys_desc/Makefile` and `sys_desc/nanosoc_multicore_soc.yaml` exist, it runs `make -C sys_desc` — but only when `build_soc/rtl` is missing or the top YAML is newer than it:

   ```bash
   if [ ! -d build_soc/rtl ] || [ sys_desc/nanosoc_multicore_soc.yaml -nt build_soc/rtl ]; then
       make -C sys_desc || echo "[warn] sys_desc make failed — fix YAML and rerun 'make soc'"
   else
       echo "[skip] build_soc/rtl is already up to date"
   fi
   ```

> **Staleness gotcha.** The auto-make is gated on the **top** YAML's mtime only. If you edit a *lib-dir* YAML (a region, subsystem, interconnect include), `source set_env.sh` will print `[skip]` and **not** regenerate. Force it with `make -C sys_desc` (or `make soc` at the repo root). See [Chapter 5](#5-running-the-generator) for the Makefile.

`set_env.sh --unset` removes the generated env/autoconfig artefacts. It also (idempotently) inits git submodules and fetches the QSPI flash VIP on first run, and provides `fpga_use` / `fpga_release` shell helpers for grabbing a board.

---

# 3. Hello SoC — a runnable walkthrough

The smallest valid SoC is one `gen: True` AHB-Lite interconnect with a single CPU initiator and a single SRAM target. A complete, self-contained copy lives in this repo at **`docs/examples/hello_soc/`** — two files:

```
docs/examples/hello_soc/
  hello_soc.yaml                         # the top module (gen:True)
  regions/
    tiny_region/
      tiny_region.yaml                   # the leaf SRAM region (gen:False)
```

## 3.1 The top module — `hello_soc.yaml`

```yaml
module:
  name: hello_soc
  gen: True               # generator emits RTL for this (the toplevel)
  desc: Minimal single-region SoC.

  params:
    SYS_CLK_FREQ_HZ: { type: int, default: 50000000, desc: "System clock (50 MHz)" }
    SYS_ADDR_W:      { type: int, default: 32, desc: "System address width" }
    SYS_DATA_W:      { type: int, default: 32, desc: "System data width" }
    SRAM_RAM_ADDR_W: { type: int, default: 12, desc: "SRAM byte-address width (4 KB)" }

  clocks:
    - { name: sys_hclk, source: external, desc: "AHB clock" }
  resets:
    - { name: sys_hresetn, active: low, source: external, desc: "AHB reset" }

  # Module boundary ports. A wire interface expands by WIDTH; an ahb interface
  # expands into the full AHB-Lite signal set (cpu_m_haddr, cpu_m_htrans, ...).
  interfaces:
    - { name: sys_hclk,    type: wire, direction: in, params: { WIDTH: 1 } }
    - { name: sys_hresetn, type: wire, direction: in, params: { WIDTH: 1 } }
    - { name: cpu_m, type: ahb, direction: initiator, params: { ADDR_WIDTH: $SYS_ADDR_W, DATA_WIDTH: $SYS_DATA_W } }

  instances:
    - instance_name: u_sram_0
      module: tiny_region          # resolved from regions/tiny_region/tiny_region.yaml
      addressable: True
      params: { SYS_ADDR_W: $SYS_ADDR_W, SYS_DATA_W: $SYS_DATA_W, RAM_ADDR_W: $SRAM_RAM_ADDR_W }
      connections:
        - { port: HCLK,      conn: sys_hclk }
        - { port: HRESETn,   conn: sys_hresetn }
        # connect this instance's AHB slave to the interconnect's sram_0 target
        - { port: ahb_slave, conn: hello_ahb_interconnect.sram_0 }

  interconnects:
    - name: hello_ahb_interconnect
      gen: True                    # interconnect default is True (opposite of module.gen)
      type: ahb_lite
      params: { SYS_ADDR_W: $SYS_ADDR_W, SYS_DATA_W: $SYS_DATA_W }
      connections:
        - { port: sys_hclk,    conn: sys_hclk }
        - { port: sys_hresetn, conn: sys_hresetn }
      targets:
        # base/size = the decode aperture; phys_size = real storage bytes (here both 4 KB).
        - { name: sram_0, instance: u_sram_0, base: 0x00000000, size: 0x00001000, sw_access: rwx, region_type: memory, phys_size: 0x00001000 }
      initiators:
        - name: cpu_m
          targets:
            - name: sram_0
```

What to notice:

- `module.gen: True` makes `hello_soc` something the generator emits. The interconnect's `gen: True` is the **default** — you can omit it, but it is shown for clarity.
- The `cpu_m` AHB *initiator* interface expands at the boundary into `cpu_m_haddr`, `cpu_m_htrans`, `cpu_m_hwrite`, … (initiator = outputs for the request signals, inputs for `hrdata`/`hready`/`hresp`).
- `conn: hello_ahb_interconnect.sram_0` wires the region's `ahb_slave` to the interconnect's `sram_0` target window; `$SYS_ADDR_W` etc. are lazily resolved param refs.
- `base`/`size` is the **decode aperture** (4 KB here); `phys_size` is the **real storage**. They are distinct numbers — see the report note in [3.4](#34-what-was-generated).

## 3.2 The leaf region — `regions/tiny_region/tiny_region.yaml`

```yaml
module:
  name: tiny_region
  gen: False              # leaf: generator does NOT emit RTL; you supply it

  params:
    SYS_ADDR_W: { type: int, default: 32 }
    SYS_DATA_W: { type: int, default: 32 }
    RAM_ADDR_W: { type: int, default: 12, desc: "RAM byte-address width (phys = 2^N bytes)" }
    RAM_DATA_W: { type: int, default: 32 }

  interfaces:
    - { name: HCLK,    type: wire, direction: in, params: { WIDTH: 1 } }
    - { name: HRESETn, type: wire, direction: in, params: { WIDTH: 1 } }
    # EXCLUDE drops these signals: this is the cmsdk AHB-Lite *slave* boundary form.
    - { name: ahb_slave, type: ahb, direction: target, params: { ADDR_WIDTH: $SYS_ADDR_W, DATA_WIDTH: $SYS_DATA_W, EXCLUDE: [hburst, hmastlock] } }

  srams:
    - { name: u_sram, addr_width: $RAM_ADDR_W, data_width: $RAM_DATA_W }
```

`gen: False` tells nanosoc_gen this module's RTL is hand-written (or a macro) — it only generates the plumbing around it. `EXCLUDE: [hburst, hmastlock]` selects the AHB-Lite *slave* boundary form (a target gets `hreadyout`); `EXCLUDE` is load-bearing for boundary correctness.

## 3.3 Generate it

From the repo root, run the generator on the example. `--lib-dir docs/examples/hello_soc` is what lets the parser find `tiny_region` under that dir's `regions/`:

```bash
cd "$SOCLABS_NANOSOC_GEN_DIR"      # your nanosoc_gen checkout
PYTHONDONTWRITEBYTECODE=1 python3 -m soc_model \
    docs/examples/hello_soc/hello_soc.yaml \
    --lib-dir docs/examples/hello_soc \
    --build-dir /tmp/hello_build
```

Real output (abridged) — **0 errors, 0 warnings, 1/1 modules resolved**:

```
Parsing hello_soc.yaml...
  Library: .../docs/examples/hello_soc
  Presets: 1 (ahb_target_region)
  Module: hello_soc
  Params: 4
  Interfaces: 3
  Instances: 1
  Interconnects: 1
  Resolved modules: 1/1

Validating...
  0 error(s), 0 warning(s)

Build directory: /tmp/hello_build
--- Analysis Reports ---            (html, memory map, hierarchy, python model, sram report)
--- AHB Interconnect Generation --- (XML, address maps, BuildBusMatrix.pl, wrapper, config pkg, flist)
--- DMA-250 Render Generation ---   No DMA-250 instances found, skipping render
--- Subsystem Module Generation --- No subsystems with gen: True found
--- RDL Register Description Generation ---  No register maps found in the design hierarchy
--- Device Discovery Table ---      (discovery yaml + rdl + regblock RTL)
--- Build Info Registers ---        No build_info section found, skipping
--- SoC Config Package ---          (hello_soc_soc_config_pkg.sv)
--- Top-Level Module Generation --- (hello_soc.sv)
--- Firmware Configuration ---      No firmware: section found, skipping
--- Design Documentation ---        (hello_soc_design_doc.md)
--- RTL timescale sweep ---
  prepended `timescale to 14 file(s)
Done.
```

(`ARM_IP_LIBRARY_PATH` must be set for the `BuildBusMatrix.pl` step. Without it you still get the XML + wrapper but no matrix `.v`. There is **no** `firmware`/`build_info`/`dma250`/`subsystem` section in this minimal SoC, so those backends print "skipping" — that is expected.)

## 3.4 What was generated

```
/tmp/hello_build/
├── rtl/                                            # all generated RTL
│   ├── hello_soc.sv                                # structural toplevel
│   ├── hello_soc_soc_config_pkg.sv                 # localparam config package (SV)
│   ├── hello_soc_soc_config.vh                     #   same, `define form for .v consumers
│   ├── hello_ahb_interconnect/
│   │   ├── hello_interconnect.sv                   #   AHB matrix wrapper (SV)
│   │   ├── hello_config_pkg.sv                     #   interconnect config package
│   │   └── hello_ahb_interconnect/                 #   raw BuildBusMatrix.pl Verilog:
│   │       ├── hello_ahb_interconnect.v            #     top matrix
│   │       ├── hello_ahb_interconnect_lite.v       #     AHB-Lite shim
│   │       ├── hello_ahb_interconnect_default_slave.v
│   │       ├── hello_arbiter.v
│   │       ├── hello_inititator_input.v            #     [Arm's spelling]
│   │       ├── hello_matrix_decode_CPU_M.v         #     per-initiator decoder
│   │       └── hello_target_output.v
│   └── hello_ahb_interconnect_discovery/           #   discovery regblock (3 SV files)
│       ├── hello_ahb_interconnect_discovery.sv
│       ├── hello_ahb_interconnect_discovery_apb_wrapper.sv
│       └── hello_ahb_interconnect_discovery_pkg.sv
├── flist/                                          # filelists for sim/synth
│   ├── hello_interconnect.flist
│   ├── hello_soc_soc_config_pkg.flist
│   └── hello_soc_toplevel.flist
├── rdl/
│   └── hello_ahb_interconnect_discovery.rdl        # SystemRDL source for the regblock
├── discovery/
│   └── hello_ahb_interconnect_discovery.yaml       # runtime device-discovery table ("SOCD")
├── interconnect/hello_ahb_interconnect/            # NON-RTL matrix inputs/outputs
│   ├── xml/…   ipxact/…   address_maps/…   logs/…
├── reports/
│   ├── hello_soc_connectivity.html                 # D3 block diagram + memmap + validation
│   ├── hello_soc_memory_map.txt                    # target/initiator address table
│   ├── hello_soc_hierarchy.txt                     # module hierarchy tree
│   ├── hello_soc_model.py                          # python model for downstream tools (cocotb/GUI)
│   └── hello_soc_sram_report.txt                   # SRAM macro list (for ASIC substitution)
└── docs/
    └── hello_soc_design_doc.md                     # generated design document
```

A few of these, read straight from the run:

**`reports/hello_soc_memory_map.txt`** — the decode windows:

```
Interconnect: hello_ahb_interconnect (ahb_lite)
  Target      Instance     Base           End        Size  Access Type
  sram_0      u_sram_0     0x00000000  0x00000FFF        4 KB  rwx   memory
  Initiator Connectivity:
    cpu_m: sram_0
```

**`rtl/hello_soc.sv`** — the structural top, with the AHB initiator flattened to ports:

```systemverilog
module hello_soc #(
    parameter SYS_CLK_FREQ_HZ = 50000000,
    parameter SYS_ADDR_W      = 32,
    parameter SYS_DATA_W      = 32,
    parameter SRAM_RAM_ADDR_W = 12
) (
    input  wire                  sys_hclk,
    input  wire                  sys_hresetn,
    output wire [SYS_ADDR_W-1:0] cpu_m_haddr,
    output wire [1:0]            cpu_m_htrans,
    output wire                  cpu_m_hwrite,
    ...
    input  wire                  cpu_m_hresp
);
```

**`reports/hello_soc_sram_report.txt`** — note the SRAM word/byte trap (see the field reference in [4a.1](#4a1-a-region-the-gen-false-leaf) and the FAQ in [Chapter 8](#8-troubleshooting--faq)): `tiny_region` documents `RAM_ADDR_W` as a *byte* width, but the SRAM model treats `addr_width` as a *word* width, so a 4 KB window reports as a 16 KB / 4096×32-b macro. For a real word-addressed macro, set `srams.addr_width = RAM_ADDR_W − 2` (for 32-bit data):

```
  SRAM Name   Hierarchy Path                Depth   Width    Size
  u_sram      hello_soc.u_sram_0.u_sram     4096    32-b    16 KB
```

> **`Presets: 1 (ahb_target_region)`** in the output simply reports that the CLI loaded one bundled preset and made it available — `hello_soc` does not use it. The simplified-YAML `uses:` / `auto_base` sugar that relies on presets is covered in [4d](#4d-the-simplified-format-preferred).

## 3.5 Where to go next

- This is the **raw-CLI** path. In the real project you never type the command — `make -C sys_desc` assembles all eight `--lib-dir` flags, the pad/chip/FPGA flags, and `--build-dir build_soc` for you ([Chapter 5](#5-running-the-generator)).
- Add a `firmware:` section (linker profiles, ADP) and a `build_info:` section to additionally emit `.ld` linker scripts, `memmap.{h,mk,cmake}`, and the build-info register block — see [4b](#4b-field-reference) and [Chapter 6](#6-understanding-the-outputs); `tests/fixtures/firmware_soc.yaml` is a worked example.
- The same `build_soc/` feeds all three downstream paths — sim/cocotb (RTL only), FPGA/Vivado (`--fpga-wrapper`), and ASIC/pads (`--chip-boundary` + `--pad-tech`) — covered in [Chapter 7](#7-the-three-downstream-paths).

---

# 4. Writing a YAML config

A nanosoc_gen SoC is described as a tree of **modules**: one YAML file = one RTL module. A top module instantiates child modules (subsystems, peripheral regions), wires them with typed **interfaces** (bus/wire ports) and one or more **interconnects** (AHB-Lite bus matrices that carry the address map). The generator is purely structural — it emits the plumbing (top level, subsystem wrappers, the bus matrix, glue, config package, memory map); the leaf RTL behind a `gen: False` module (SRAM macros, CPU cores, vendor IP) is hand-written.

This chapter teaches the YAML in two passes:

* **4a–4c — the verbose format**: every key spelled out. This is what the generator actually consumes, so you must understand it.
* **4d — the simplified format** (`uses:` presets + `auto_base` address auto-assignment): an opt-in, transparent sugar layer that desugars to the verbose form before the builder ever sees it. Prefer it for new work — but read 4a first, because it is what you are abbreviating.

> Every YAML snippet below is copy-pasteable from a real fixture in `tests/fixtures/` (the hermetic test SoCs) or from the production `sys_desc/nanosoc_multicore_soc.yaml`. The smallest complete, runnable example is `tests/fixtures/minimal_soc.yaml` — that is the spine of 4a (and the basis of the `docs/examples/hello_soc/` walkthrough in [Chapter 3](#3-hello-soc--a-runnable-walkthrough)).

---

## 4a. The verbose format, by example

### 4a.1 A region (the `gen: False` leaf)

The simplest module is a **region**: a hand-written leaf (an SRAM, bootrom, IMEM, DMEM, or a peripheral block) sitting behind a single AHB slave. The generator does **not** emit RTL for it (`gen: False`); it only needs the boundary so it can wire the region into a parent's bus matrix. Here is the complete hermetic fixture `tests/fixtures/regions/tiny_region/tiny_region.yaml`:

```yaml
module:
  name: tiny_region
  gen: False
  desc: >
    Minimal general-purpose SRAM region with one AHB slave port.

  params:
    SYS_ADDR_W: { type: int, default: 32, desc: "System address width" }
    SYS_DATA_W: { type: int, default: 32, desc: "System data width" }
    RAM_ADDR_W: { type: int, default: 12, desc: "RAM byte-address width (phys = 2^N bytes)" }
    RAM_DATA_W: { type: int, default: 32, desc: "RAM data width" }

  interfaces:
    - { name: HCLK,    type: wire, direction: in, params: { WIDTH: 1 }, desc: AHB clock }
    - { name: HRESETn, type: wire, direction: in, params: { WIDTH: 1 }, desc: AHB reset (active low) }
    - { name: ahb_slave, type: ahb, direction: target, params: { ADDR_WIDTH: $SYS_ADDR_W, DATA_WIDTH: $SYS_DATA_W, EXCLUDE: [hburst, hmastlock] }, desc: AHB slave port }

  srams:
    - { name: u_sram, addr_width: $RAM_ADDR_W, data_width: $RAM_DATA_W, desc: "General-purpose SRAM" }
```

Key by key:

* **`module:`** — every top-level file MUST have this single root key, or the builder raises `ValueError`. Everything below lives under it.
* **`name`** — the RTL module name. It is also a *discovery key* ([4c](#4c-param-refs-exclude-and-module-discovery)): the parser caches this module under both `tiny_region` and its filename stem.
* **`gen: False`** — do **not** generate RTL; this leaf is supplied by hand. `False` is the default for `module:`, so you *could* omit it, but stating it documents intent. (Watch out: `gen` defaults to `False` on a module but `True` on an interconnect — opposite — see [4a.4](#4a4-the-interconnect-where-the-address-map-lives).)
* **`desc`** — free text, carried into the HTML/Markdown reports. Optional everywhere.
* **`params:`** — module parameters. Each is `NAME: { type, default, desc }` (the verbose dict form). `type` is informational only — it is carried into the config package and docs but is never type-checked. There is also a scalar shorthand: `NAME: 32` is exactly `Param(name=NAME, default=32)`. Params are referenced elsewhere as `$NAME` ([4c](#4c-param-refs-exclude-and-module-discovery)).
* **`interfaces:`** — the module's boundary ports (full reference in [4b](#4b-field-reference)):
  * `HCLK`/`HRESETn` are `type: wire` ports. A wire's width comes from `params.WIDTH` (default 1). A 1-bit wire emits no `[W-1:0]` range.
  * `ahb_slave` is `type: ahb, direction: target`. A bus interface expands into the full set of per-signal ports (`HADDR`, `HTRANS`, `HWRITE`, `HSIZE`, `HRDATA`, …) using the AHB signal table. `ADDR_WIDTH`/`DATA_WIDTH` size those signals; `$SYS_ADDR_W` resolves against this module's params. `direction: target` means a slave (inputs on the address phase, output `HRDATA`). `EXCLUDE: [hburst, hmastlock]` **drops** those signals from the expanded bus — this matches the cmsdk AHB-Lite slave boundary. **`EXCLUDE` is load-bearing**: the wrong set produces a port-mismatch against whatever drives it. (More in [4b](#4b-field-reference)/[4c](#4c-param-refs-exclude-and-module-discovery).)
* **`srams:`** — declares memory macros inside the region. `addr_width` default 14, `data_width` default 32; both accept a `$PARAM` string. Here `addr_width: $RAM_ADDR_W` resolves to 12. These feed the SRAM report (which ASIC P&R uses for macro substitution) and the UPF backend. **Footgun**: `ResolvedSram` treats `addr_width` as a *word*-address width (`depth = 2**addr_width` words), but the region documents `RAM_ADDR_W` as a *byte* width. Passing `addr_width: $RAM_ADDR_W` (as the fixtures do) makes the SRAM report over-state size by `data_width/8`× (4× for 32-bit). For a real word-addressed macro set `srams.addr_width = RAM_ADDR_W − log2(data_width/8)` (`−2` for 32-bit).

### 4a.2 A subsystem (a `gen: True` module with its own bus)

A **subsystem** is a `gen: True` module that itself contains `instances` and `interconnects` — e.g. a CPU subsystem with its own internal bus matrix and a private memory. The generator emits a `<name>.sv` wrapper for it. The hermetic `tests/fixtures/subsystems/tiny_cpu_ss/tiny_cpu_ss.yaml` shows the shape (trimmed to the essentials):

```yaml
module:
  name: tiny_cpu_ss
  gen: True                       # subsystem -> generator emits tiny_cpu_ss.sv
  params:
    SYS_ADDR_W:      { type: int, default: 32 }
    SYS_DATA_W:      { type: int, default: 32 }
    IMEM_RAM_ADDR_W: { type: int, default: 12 }

  interfaces:
    - { name: sys_hclk,    type: wire, direction: in, params: { WIDTH: 1 } }
    - { name: sys_hresetn, type: wire, direction: in, params: { WIDTH: 1 } }
    # A module-level AHB master that an internal window forwards to (passthrough TARGET):
    - { name: sub_m, type: ahb, direction: initiator, params: { ADDR_WIDTH: $SYS_ADDR_W, DATA_WIDTH: $SYS_DATA_W } }
    # An external slave port that drives the internal matrix (passthrough INITIATOR);
    # EXCLUDE:[hready] selects the slave/target boundary form (slave gets hreadyout):
    - { name: sub_s, type: ahb, direction: target, params: { ADDR_WIDTH: $SYS_ADDR_W, DATA_WIDTH: $SYS_DATA_W, EXCLUDE: [hready] } }

  instances:
    # An RTL leaf master (rtl_module: -> no YAML lookup; inline interfaces only):
    - instance_name: u_cpu_0
      rtl_module: tiny_cpu_core
      interfaces:
        - { name: HCLK,    type: wire, direction: in, params: { WIDTH: 1 } }
        - { name: HRESETn, type: wire, direction: in, params: { WIDTH: 1 } }
        - { name: ahb_master, type: ahb, direction: initiator, params: { ADDR_WIDTH: $SYS_ADDR_W, DATA_WIDTH: $SYS_DATA_W } }
      connections:
        - { port: HCLK,       conn: sys_hclk }
        - { port: HRESETn,    conn: sys_hresetn }
        - { port: ahb_master, conn: tiny_cpu_ss_ahb_interconnect.cpu_0 }
    # A YAML region (module: -> discovered by name/stem):
    - instance_name: u_imem_0
      module: tiny_region
      addressable: True
      params: { SYS_ADDR_W: $SYS_ADDR_W, SYS_DATA_W: $SYS_DATA_W, RAM_ADDR_W: $IMEM_RAM_ADDR_W }
      connections:
        - { port: HCLK,      conn: sys_hclk }
        - { port: HRESETn,   conn: sys_hresetn }
        - { port: ahb_slave, conn: tiny_cpu_ss_ahb_interconnect.imem_0 }

  interconnects:
    - name: tiny_cpu_ss_ahb_interconnect
      gen: True
      type: ahb_lite
      params: { SYS_ADDR_W: $SYS_ADDR_W, SYS_DATA_W: $SYS_DATA_W }
      connections:
        - { port: sys_hclk,    conn: sys_hclk }
        - { port: sys_hresetn, conn: sys_hresetn }
      targets:
        - { name: imem_0, instance: u_imem_0, base: 0x10000000, size: 0x08000000, sw_access: rwx, region_type: memory, phys_size: 0x00001000 }
        # A passthrough window: forwards to module-level master 'sub_m', no instance:
        - { name: system, base: 0x20000000, size: 0xE0000000, sw_access: rw, region_type: ~, role: ~, passthrough: sub_m }
      initiators:
        - name: cpu_0
          targets: [ { name: imem_0 }, { name: system } ]
        # A passthrough initiator: external slave 'sub_s' drives the matrix:
        - name: sub_s
          passthrough: sub_s
          targets: [ { name: imem_0 } ]
```

What is new versus a region:

* **`gen: True`** — emit RTL for this module (the structural `.sv` wrapper).
* **`instances:`** — child modules wired inside the subsystem. Each has:
  * **`instance_name`** — the HDL instance name (required in practice).
  * **`module:`** vs **`rtl_module:`** — `module: tiny_region` references a YAML module discovered by name or filename stem ([4c](#4c-param-refs-exclude-and-module-discovery)). `rtl_module: tiny_cpu_core` is a hand-written RTL leaf with *no* YAML lookup — you must declare its ports inline in `interfaces:`. If both are present, `rtl_module:` wins. (Instance-level `interfaces:` are **only** used for `rtl_module:` instances; on a `module:` instance they are parsed but ignored.)
  * **`addressable: True`** — marks an address-map participant (a slave behind the matrix).
  * **`params:`** — per-instance parameter overrides; `$REF`/expressions allowed.
  * **`connections:`** — `{ port, conn, desc }`. `port` is a child port (may carry a `[h:l]` slice). `conn` is what drives/receives it: a flat net (`sys_hclk`), an `instance.port` reference, an interconnect endpoint (`name.target` / `name.initiator`, e.g. `tiny_cpu_ss_ahb_interconnect.imem_0`), a sized literal (`1'b1`, `1'b0`), or the sentinel `unconnected` (deliberately open).
* **`interconnects:`** — the internal bus matrix ([4a.4](#4a4-the-interconnect-where-the-address-map-lives)). Note `passthrough:` windows and passthrough initiators, which forward to/from a *module-level* AHB interface rather than an instance — that is how a subsystem re-exposes a bus to its parent.

### 4a.3 The top module (instances + the address map)

The top module ties everything together. `tests/fixtures/minimal_soc.yaml` is the smallest complete SoC: one region behind one AHB-Lite matrix with one CPU initiator. This is the canonical worked example (`docs/examples/hello_soc/`). Verbatim:

```yaml
module:
  name: minimal_soc
  gen: True
  desc: Minimal single-region SoC.

  params:
    SYS_CLK_FREQ_HZ: { type: int, default: 50000000, desc: "System clock (50 MHz)" }
    SYS_ADDR_W:      { type: int, default: 32, desc: "System address width" }
    SYS_DATA_W:      { type: int, default: 32, desc: "System data width" }
    SRAM_RAM_ADDR_W: { type: int, default: 12, desc: "SRAM byte-address width (4 KB)" }

  clocks:
    - { name: sys_hclk, source: external, desc: "AHB clock" }
  resets:
    - { name: sys_hresetn, active: low, source: external, desc: "AHB reset" }

  interfaces:
    - { name: sys_hclk,    type: wire, direction: in, params: { WIDTH: 1 }, desc: "AHB clock in" }
    - { name: sys_hresetn, type: wire, direction: in, params: { WIDTH: 1 }, desc: "AHB reset in" }
    - { name: cpu_m, type: ahb, direction: initiator, params: { ADDR_WIDTH: $SYS_ADDR_W, DATA_WIDTH: $SYS_DATA_W }, desc: "CPU AHB master-out" }

  instances:
    - instance_name: u_sram_0
      module: tiny_region
      addressable: True
      params: { SYS_ADDR_W: $SYS_ADDR_W, SYS_DATA_W: $SYS_DATA_W, RAM_ADDR_W: $SRAM_RAM_ADDR_W }
      connections:
        - { port: HCLK,      conn: sys_hclk }
        - { port: HRESETn,   conn: sys_hresetn }
        - { port: ahb_slave, conn: minimal_ahb_interconnect.sram_0 }

  interconnects:
    - name: minimal_ahb_interconnect
      gen: True
      type: ahb_lite
      params: { SYS_ADDR_W: $SYS_ADDR_W, SYS_DATA_W: $SYS_DATA_W }
      connections:
        - { port: sys_hclk,    conn: sys_hclk }
        - { port: sys_hresetn, conn: sys_hresetn }
      targets:
        - { name: sram_0, instance: u_sram_0, base: 0x00000000, size: 0x00001000, sw_access: rwx, region_type: memory, phys_size: 0x00001000, desc: "On-chip SRAM" }
      initiators:
        - name: cpu_m
          targets:
            - name: sram_0
```

Notable top-level keys:

* **`clocks:` / `resets:`** — *pure metadata* (documentation only). Actual clock/reset wiring is done with `interfaces` (type `wire`) plus `connections` (here `sys_hclk` / `sys_hresetn`). Declaring `clocks:`/`resets:` does **not** create ports.
* **`interfaces:`** — the chip-edge ports of the whole SoC: clock/reset wires plus `cpu_m` (`direction: initiator`, i.e. an AHB *master* output bundle). The toplevel backend flattens this into `cpu_m_haddr`, `cpu_m_htrans`, … on the generated top.
* **`instances:`** — same shape as in a subsystem. Here a single `u_sram_0` region whose `ahb_slave` connects to the matrix endpoint `minimal_ahb_interconnect.sram_0`.

### 4a.4 The interconnect (where the address map lives)

The `interconnects:` list defines AHB-Lite bus matrices. Each entry is the most information-dense block in the file:

* **`name`** — bus name; this is what `name.target` / `name.initiator` connections reference.
* **`gen: True`** — emit the matrix RTL (via Arm `BuildBusMatrix.pl`). **This defaults to `True`** — the opposite of `module.gen`. Set `gen: False` only to model an externally-built matrix.
* **`type: ahb_lite`** — the matrix type.
* **`params:`** — matrix params (e.g. `SYS_ADDR_W`, `SYS_DATA_W`); `$refs` allowed.
* **`connections:`** — clk/reset/scan/remap wiring for the matrix itself (`sys_hclk`, `sys_hresetn`, …).
* **`targets:`** — the subordinate windows (the *address map*). Each target is either a bare string (name only) or a dict. The dict fields you will use most:
  * `name` — window name (referenced by initiators).
  * `instance` — the module instance this window routes to.
  * `base` — base address (hex `0x…` accepted).
  * `size` — the **decode aperture** in bytes (used for overlap validation and the memory-map `End`). This is the address footprint, *not* the physical store size — in the production SoC a 64 KB RAM commonly sits in a 128 MB or 16 MB aperture.
  * `phys_size` — the **physical storage** in bytes. Accepts an int *or* an expression string such as `"2 ** $RAM_ADDR_W"` or `"4 * (2 ** $N)"` (resolved later against params). Feeds the memmap `_SIZE`, the linker `LENGTH`, and ADP.
  * `sw_access` — `rw` / `rwx` / `rx` / `ro` / etc.
  * `region_type` — `memory`, `periph`, or `~`/None (a passthrough/admin window).
  * `role` — e.g. `bootrom`; `~`/None for plain.
  * `protocol` — `ahb` (default) or `apb`. `apb` means the target is reached through an AHB-to-APB bridge ([4a.5](#4a5-an-apb-bridged-target-no-patch-script-needed)).
  * `passthrough` — a module-level AHB interface name this window forwards to (no instance; used in subsystems, [4a.2](#4a2-a-subsystem-a-gen-true-module-with-its-own-bus)).
  * `subordinate_bus: True` — this target hands off to *another* bus matrix / NoC (an admin window). The production SoC uses this for the ethernet-subsystem admin window.
  * `register_map` — path to a register-map file (read by the rdl/discovery backends).
* **`initiators:`** — the bus masters. Each `{ name, instance, passthrough, targets }`. The per-initiator `targets:` is that master's *visibility list*: which windows it can reach. An initiator referencing a target **not** declared in the interconnect's `targets:` is a validation error. (A `targets:` item may also be `{ name, visibility: [...] }` to give that one initiator a private aliased view of the window.)

> **`size` ≠ `phys_size` ≠ `RAM_ADDR_W` — three distinct numbers.** `size` is the decode aperture, `phys_size` is the real storage, and `RAM_ADDR_W` is the macro's address width (with the word-vs-byte twist of [4a.1](#4a1-a-region-the-gen-false-leaf)). The real nanosoc regions use `phys = 4 * 2^N` (word-addressed, default `RAM_ADDR_W=14` → 64 KB); the hermetic `tiny_region` uses `phys = 2^N` (byte). Match your `phys_size:` formula to the region's convention or the linker silently over/under-flows.

A real production target (from `sys_desc/nanosoc_multicore_soc.yaml`) shows the same fields with `subordinate_bus`/`periph` in play:

```yaml
- { name: eth_ss_slave, instance: u_eth_ss_0,           base: 0x00000000, size: 0x20000000, sw_access: rwx, region_type: ~,      role: ~, subordinate_bus: True, desc: "Ethernet subsystem admin window" }
- { name: dma_230_0,    instance: u_dma_230_apb_bridge, base: 0x20000000, size: 0x01000000, sw_access: rw,  region_type: periph, role: None, desc: "DMA-230 APB configuration registers" }
```

### 4a.5 An APB-bridged target (no patch script needed)

To put a peripheral on APB behind the AHB matrix, mark its target `protocol: apb` and supply an `apb_config:` describing the bridge. The generator emits the AHB-to-APB bridge RTL **natively** — there is no `patch_ahb_to_apb.py` step (that script has been retired and does not exist). From `tests/fixtures/apb_bridge_soc.yaml`:

```yaml
targets:
  - { name: sram_0, instance: u_sram_0, base: 0x00000000, size: 0x00001000, sw_access: rwx, region_type: memory, phys_size: 0x00001000 }
  - name: apb_periph_0
    instance: u_apb_periph_0
    base: 0x40000000
    size: 0x00001000
    sw_access: rw
    region_type: periph
    protocol: apb
    register_map: register_maps/tiny_regs.yaml
    apb_config:
      bridge: cmsdk_ahb_to_apb
      params: { ADDRWIDTH: 12, REGISTER_RDATA: 1 }
    desc: "APB peripheral via AHB-to-APB bridge"
```

The matching APB region (`tiny_apb_periph`) declares `PCLK`/`PRESETn`/`PCLKEN` wires and the APB passthrough signals; on the instance you tie `PCLKEN` to `1'b1`:

```yaml
- instance_name: u_apb_periph_0
  module: tiny_apb_periph
  addressable: True
  connections:
    - { port: PCLKEN,    conn: 1'b1 }
    - { port: ahb_slave, conn: apb_bridge_ahb_interconnect.apb_periph_0 }
```

> **Bare `rtl_module: cmsdk_ahb_to_apb` footgun**: if you instantiate the bridge as a raw RTL module (rather than via `apb_config`), you MUST declare `PCLKEN`, `PREADY`, `PSLVERR` as inline `interfaces:` and tie them — floating APB inputs hang the bus and the validator errors.

---

## 4b. Field reference

The authoritative readers are `builder.py` (which keys each `_build_*` method consumes) and `model.py` (dataclass types/defaults). **A key the builder does not read is silently ignored** — `parser.py` only loads raw dicts; the builder defines the schema.

### Top module — `module:`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `name` | str | `'unknown'` | Module/RTL name; also a discovery key ([4c](#4c-param-refs-exclude-and-module-discovery)) |
| `gen` | bool | **`False`** | Emit RTL for this module (toplevel/subsystem). Regions are `gen:False`. |
| `desc` | str | `''` | Free text (docs/HTML) |
| `params` | map | `{}` | Module parameters (below) |
| `clocks` | list | `[]` | Metadata only; wiring is via `interfaces`+`connections` |
| `resets` | list | `[]` | Metadata only |
| `interfaces` | list | `[]` | Module boundary ports (below) |
| `instances` | list | `[]` | Child module instances (below) |
| `assigns` | list | `[]` | Legacy combinational assigns — prefer `glue_logic` |
| `glue_logic` | list | `[]` | Typed structural glue (below) |
| `internal_wires` | list | `[]` | Internal nets connecting instances/glue |
| `interconnects` | list | `[]` | AHB-Lite bus matrices + address map (below) |
| `address_decode` | map | `None` | Single nested slave mux (rdl/discovery hook) |
| `firmware` | map | `None` | Linker profiles / memmap / ADP |
| `build_info` | map | `None` | Build-id register block |
| `srams` | list | `[]` | Memory macros (below) |
| `uses` | str/list | — | **Sugar** ([4d](#4d-the-simplified-format-preferred)) — preset mixins, consumed before build |

`source_file` is set by the builder, never from YAML.

### `params:`

Two forms per key: dict `NAME: { type, default, desc }` (defaults `type='int'`, `default=None`, `desc=''`), or scalar shorthand `NAME: <value>` → `Param(name, default=value)`. `type` is informational only (never type-checked).

### `interfaces:` (boundary ports)

Each `{ name, type, direction, params, desc }`.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `name` | str | `''` | Port/bus name; prefix for expanded bus signals |
| `type` | str | `'wire'` | `wire`, `ahb`, `apb`, `axis`/`axis_stream`, `axis_byte`, `swd`, `gpio`, `dbg_ahb` |
| `direction` | str | `'in'` | `in`, `out`, `inout`, `initiator`, `target`, `receiver`, `sender` |
| `params` | map | `{}` | Width/protocol params (below) |
| `desc` | str | `''` | |

* **wire**: width from `params.WIDTH` (default 1). A 1-bit wire emits no `[W-1:0]`.
* **Bus types** (`ahb`/`apb`/`axis*`/`swd`/`gpio`/`dbg_ahb`): expanded into per-signal ports. Bus params read `ADDR_WIDTH`/`ADDR_W` and `DATA_WIDTH`/`DATA_W` (`axis_byte` data defaults 8). `initiator`/`out`/`sender` ⇒ output direction; `in`/`target`/`receiver` ⇒ input.
* **`EXCLUDE`** (inside `params:`): a list of signal-suffixes dropped from the expanded bus, e.g. `EXCLUDE: [hburst, hmastlock]` for a cmsdk AHB-Lite slave; `EXCLUDE: [hready]` selects the **slave/target boundary** form of a passthrough initiator (the slave gets `hreadyout`). Load-bearing for boundary correctness.

> Note: *interface-definition files* under `lib/interfaces/*.yaml` are a **separate** schema (`interface: { name, protocol, role, parameters, signals }` with long-form `parameters`/`description`). That is not the module-interface schema above — don't confuse the two.

### `instances:`

| YAML key | Type | Default | Meaning |
|---|---|---|---|
| `instance_name` | str | `''` | HDL instance name (required in practice) |
| `rtl_module:` **or** `module:` | str | `''` | `rtl_module:` ⇒ RTL leaf, no YAML lookup, inline `interfaces:` required. `module:` ⇒ discovered YAML module (by name OR filename stem). `rtl_module` wins if both present. |
| `addressable` | bool | `False` | Marks an address-map participant |
| `condition` | str | `None` | Verilog `generate if (...)` guard; raw text passed through |
| `params` | map | `{}` | Parameter overrides (`$REF`/expressions OK) |
| `connections` | list | `[]` | `{ port, conn, desc }` (below) |
| `interfaces` | list | `[]` | **Inline interfaces — used ONLY for `rtl_module:` instances**; parsed but ignored on a `module:` instance |

**connections** `{ port, conn, desc }`: `port` = child port (may carry `[h:l]` slice). `conn` = a flat net / `instance.port` / interconnect `name.target` or `name.initiator` / sized literal (`1'b1`, `1'b0`) / sentinel `unconnected` (intentionally open).

### `interconnects:`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `name` | str | `''` | Bus name; referenced by `name.target`/`name.initiator` |
| `gen` | bool | **`True`** | Emit the matrix RTL (**opposite default to `module.gen`**) |
| `type` | str | `'ahb_lite'` | Matrix type |
| `desc` | str | `''` | |
| `params` | map | `{}` | e.g. `SYS_ADDR_W`, `SYS_DATA_W` (`$refs` OK) |
| `connections` | list | `[]` | clk/reset/scan/remap wiring |
| `targets` | list | `[]` | Subordinate windows (below) |
| `initiators` | list | `[]` | Masters (below) |
| `auto_base` | map | — | **Sugar** ([4d](#4d-the-simplified-format-preferred)) — auto-assign un-pinned bases |

**targets** — a bare string (name only) OR a dict:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `name` | str | `''` | Window name |
| `instance` | str | `None` | Associated module instance |
| `base` | int | `0` | Base address (`0x`-hex OK) |
| `size` | int | `0` | Decode aperture (bytes) — overlap validation + memmap End |
| `phys_size` | int OR expr-str | `None` | Physical storage bytes; accepts `"2 ** $RAM_ADDR_W"`, `"4 * (2 ** $N)"`. Feeds memmap `_SIZE`, linker `LENGTH`, ADP |
| `sw_access` | str | `'rw'` | `rw`, `rwx`, `rx`, `ro`, … |
| `region_type` | str | `None` | `memory`, `periph`, or `~`/None |
| `role` | str | `None` | e.g. `bootrom`; `None`/`~` for plain |
| `subordinate_bus` | bool | `False` | Target connects to another bus matrix/NoC (admin window) |
| `protocol` | str | `'ahb'` | `'ahb'`/`'apb'` (lowercased). `apb` ⇒ AHB-to-APB bridge target |
| `apb_config` | map | `None` | `{ bridge: cmsdk_ahb_to_apb, params: { ADDRWIDTH, REGISTER_RDATA, ... } }` |
| `passthrough` | str | `None` | Module-level AHB iface this window forwards to (no instance) |
| `register_map` | str | — | Filepath; read by rdl/discovery backends |
| `desc` | str | `''` | |

**initiators** `{ name, instance, passthrough, targets }`. Each `targets` entry is a string (target name) OR `{ name, visibility }` where `visibility` is a list of per-initiator window aliases `[{ base, aperture }]`. Referencing a target not in the interconnect's `targets:` ⇒ validation **error**.

### `glue_logic:` (typed structural glue)

Each `{ name, type, output, ... }`. `type` ∈ `passthrough | or_reduce | or_combine | and_gate | constant | concat`.

| `type` | Required fields | Meaning |
|---|---|---|
| `passthrough` | `input`, `output` | Wire input→output |
| `or_reduce` | `input`, `output` | Reduction-OR multi-bit input → 1-bit |
| `or_combine` | `inputs:[...]`, `output` | N-input OR of equal-width operands |
| `and_gate` | `inputs:[a,b]`, `output` | 2-input AND |
| `constant` | `output`, `value`, opt `width` | Drive output to a fixed value |
| `concat` | `inputs:[{bit,signal}...]`, `output`, opt `pad_zero_above` | Per-bit concat of 1-bit signals; unmapped/≥pad bits ⇒ `1'b0` |

`output`/`input` may carry bit slices. `concat` signals may carry slices (`{ bit: 3, signal: "cc_periph_irq_w[8]" }`). Glue is emitted as `soc_glue` helper instances.

### `internal_wires:`

`[{ name, type, params, desc }]` — built with `direction='internal'` (hardcoded), `type` default `wire`, width from `params.WIDTH`. For IRQ buses, `*_nc` sinks, etc.

### `srams:`

`[{ name, addr_width, data_width, desc }]`. `addr_width` default **14**, `data_width` default **32** (int OR `$PARAM`). Word/byte over-report footgun: see [4a.1](#4a1-a-region-the-gen-false-leaf).

### `firmware:`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `cpu_initiator` | str | `''` | CPU initiator firmware links against |
| `cpu` | str | `''` | `cortex-m0`/`cortex-m0plus` → `NanoSoC_DEFAULT_CPU` |
| `linker_profiles` | list | `[]` | Named MEMORY layouts (below) |
| `adp` | map | `None` | `{ upload_target, address_select }` |
| `hex_adjust` | map | `None` | Per-image hex placement |

**linker_profiles** `{ name, regions:[...] }`; **regions** `{ target, address_select, linker_name, software_access, size_adjust, phys_size }`. `address_select` ∈ `default`/`remapped`/`alias`. `size_adjust` (e.g. `-0x400`) shrinks the linker `LENGTH` only — *not* the memmap.h `_SIZE`. (Verified on `tests/fixtures/firmware_soc.yaml`: DMEM `_SIZE=0x4000` in memmap.h but `LENGTH=0x3C00` in the `.ld`.)

### `build_info:`

`{ apb_slot(3), gen(True), fields }`. **fields** `{ name, type, value, source, max_length, desc }`; `type` ∈ `int32`/`timestamp`/`git_hash`/`string`; `source` `'tool'` ⇒ generator repo git hash, else project repo.

### `register_map:` (separate file)

Referenced via `register_map:` on a target/decode-slot. Top key `register_map:`. **`description:` (not `desc:`) on the register-map, its registers, and its fields** — everything else uses `desc:`.

### `address_decode:` (nested slave mux)

`{ type, module, bridge, slots }` where slots are `{ slot, name, module, offset, size, register_map, desc, address_decode }` (recurses). Lets the rdl/discovery backends find register maps inside child regions.

---

## 4c. Param refs, EXCLUDE, and module discovery

### Param references `$NAME` and expressions

Most string values may reference params with `$NAME`. Resolution (`utils.resolve_param_ref`) is **lazy** — backends evaluate against an effective-params dict (child defaults overlaid with resolved instance overrides):

* **`"$FOO"`** (whole string) → look up `FOO`. **If `FOO` is absent the literal string is returned unchanged** — typos do *not* error; they survive into the RTL as text. Watch the generator's "Resolved modules" line and grep your RTL for stray `$` if a width looks wrong.
* **Expression strings** containing `$` but not a pure ref, e.g. `"2 ** $RAM_ADDR_W"` or `"4 * (2 ** $N)"`: every `$param` is text-substituted, then if no `$` remains the result is evaluated. Only arithmetic is allowed (`+ - * / ( ) **`); anything else is returned verbatim. This is how `phys_size: "2 ** $IMEM_RAM_ADDR_W"` becomes `0x4000`.
* **Exception**: inside an `auto_base` interconnect, `$PARAM`/expressions are **rejected** — literals only ([4d](#4d-the-simplified-format-preferred)). It is the one place in nanosoc_gen where `$PARAM` does not resolve.

### EXCLUDE semantics

`EXCLUDE` (inside an interface's `params:`) drops named signal-suffixes from an expanded bus and **selects the boundary form** of an AHB-Lite port:

* `EXCLUDE: [hburst, hmastlock]` — the cmsdk AHB-Lite **slave** form (used on every region's `ahb_slave`).
* `EXCLUDE: [hready]` — the slave/**target** boundary form of a passthrough initiator (the slave endpoint gets `hreadyout` instead of `hready`).
* No `hready` in EXCLUDE on a passthrough initiator endpoint — the cmsdk **initiator** boundary form (no `hsel`/`hreadyout`; bare `hready` is a subsystem output).

The wrong EXCLUDE set produces a port-mismatch error against whatever drives or receives the bus. Copy the EXCLUDE from a working region/subsystem of the same role rather than guessing.

### Module discovery (by name or filename stem)

When an instance says `module: tiny_region`, the parser resolves it by scanning each `--lib-dir` (the Makefile passes 8 of them) plus their `regions/`, `subsystems/`, `../rtl/*/sys_desc/`, and `modules/` subtrees. Every discovered module YAML is cached under **both** its `module.name` **and** its filename stem, so `module: tiny_region` matches whether the key came from the `name:` field or the file `tiny_region.yaml`.

Consequences to know:

* **`--lib-dir` is mandatory** for any non-self-contained SoC. The hermetic fixtures need only `--lib-dir tests/fixtures`; the production SoC needs all 8. Miss one and the module is unresolved → validator error.
* **Last-writer-wins.** Duplicate `module.name`s or filename-stem collisions silently shadow each other — no warning. Scan order is lib-dir order, then an unsorted glob within each dir.
* **A missing/typo'd referenced file loads as `{}`** with only a printed Warning; it surfaces later as a validator "module not found", not a parse exception.
* **`rtl_module:`** bypasses discovery entirely — it is a raw RTL leaf whose ports you declare inline ([4a.2](#4a2-a-subsystem-a-gen-true-module-with-its-own-bus)).

---

## 4d. The simplified format (preferred)

The simplified format is an **opt-in, transparent** desugaring pre-pass (`soc_model/expand.py`, roadmap doc-05). It rewrites a raw `{'module': {...}}` dict into the exact canonical shape the builder already consumes, so **the builder and every backend stay sugar-ignorant**. Two guarantees:

* **Strict identity**: a module with no recognised sugar key is returned *unchanged* — sugar-free YAML expands to byte-identical canonical output (the non-breakage guarantee).
* **Opt-in**: the parser only applies it when constructed with an expander. The CLI always installs one (so sugar works through `make` for free, in the top YAML and in any discovered region/subsystem). The library/test default is *no* expander — sugar off.

Sugar triggers are: a module-level **`uses:`** key, OR any interconnect carrying an **`auto_base:`** key. When sugar fires, the CLI prints e.g. `Presets: 1 (ahb_target_region)`.

Two features are implemented today: `uses:` presets (Feature 1) and `auto_base` + size-shorthand (Feature 2). Two more are **roadmap-only and NOT in the code — do not try them**: terse `bus:` wiring and derived `phys_size`.

### Feature 1 — `uses:` presets

A **preset** is a reusable partial-module mixin stored in a `presets/` directory. Presets are discovered from the bundled `lib/presets/` plus each `<lib-dir>/presets/`. A preset file has the shape `{ preset: { name: <id>, <module keys...> } }` — the `name:` is the library key and is stripped from the merge body.

The bundled `lib/presets/ahb_target_region.yaml` captures the boilerplate every AHB target region repeats (the four width params plus `HCLK`/`HRESETn`/`ahb_slave`):

```yaml
preset:
  name: ahb_target_region
  params:
    SYS_ADDR_W: { type: int, default: 32, desc: "System address width" }
    SYS_DATA_W: { type: int, default: 32, desc: "System data width" }
    RAM_ADDR_W: { type: int, default: 14, desc: "RAM address width (phys = 2^N bytes)" }
    RAM_DATA_W: { type: int, default: 32, desc: "RAM data width" }
  interfaces:
    - { name: HCLK,    type: wire, direction: in, params: { WIDTH: 1 }, desc: AHB clock }
    - { name: HRESETn, type: wire, direction: in, params: { WIDTH: 1 }, desc: AHB reset (active low) }
    - name: ahb_slave
      type: ahb
      direction: target
      params: { ADDR_WIDTH: $SYS_ADDR_W, DATA_WIDTH: $SYS_DATA_W, EXCLUDE: [hburst, hmastlock] }
      desc: AHB slave port
```

A region then pulls it in with `uses:` and states only what is unique to it. The real fixture `tests/fixtures/expand/tiny_uses_region.yaml`:

```yaml
# AFTER (simplified) — the whole region is a few lines of body
module:
  name: tiny_uses_region
  gen: False
  uses: [ahb_target_region]      # pulls params + HCLK/HRESETn/ahb_slave
  srams:
    - { name: u_sram, addr_width: $RAM_ADDR_W, data_width: $RAM_DATA_W, desc: "Test SRAM" }
```

Compare with the verbose `tiny_region.yaml` from [4a.1](#4a1-a-region-the-gen-false-leaf), which restates all four params plus the three interfaces — roughly 13 lines of boilerplate replaced by one `uses:` line. `uses:` takes a single name (`uses: ahb_target_region`, normalised to a list) or a list (`uses: [a, b]`).

**Merge rules** (`_deep_merge`):

* The preset is merged *under* the module: **the module's own keys win over all presets.**
* With multiple presets, **later wins over earlier**.
* **dict + dict → recursive field-merge.** Overlapping sub-keys merge; the overlay value wins on a scalar conflict.
* **list + list → identity-keyed merge.** An overlay item whose `name` / `instance_name` / `instance` matches a base item **replaces that item whole** (not field-merged); items with a non-matching or no id are appended; order is preserved.

  > The easy trap: **dicts field-merge, lists whole-item-replace.** If your module's `interfaces:` list contains an item named `ahb_slave`, it replaces the preset's `ahb_slave` entirely (you don't get a partial merge of just the changed sub-fields).

**Errors**: an unknown preset name (the error lists what is available); a preset that itself declares `uses:` (recursive presets are not supported in v1); a `uses:` value that is neither a string nor a list.

### Feature 2 — `auto_base` + size shorthand

`auto_base` removes hand-counted hex bases from an interconnect's address map. Add it to an interconnect:

```yaml
auto_base: { from: <addr>, align: <stride> }
```

Targets with **no** explicit `base:` are packed in YAML order starting at `from`, respecting `align`; the expander writes back concrete integer `base:` values. Targets that **do** carry `base:` stay **pinned** (mixed mode). Packing is:

```
base   = align_up(cursor, align)
cursor = base + align_up(size, align)
```

so a target larger than `align` consumes several stride slots and the next auto target lands *past the hole*. `align: 0` ⇒ no rounding. The `auto_base` key is consumed from the output.

**Size shorthand** applies to `from`, `align`, and every `size:`/`base:`: an int, a `0x`-hex string, decimal string, or `NN[K|M|G|T]` (1024-based, case-insensitive, fractions allowed if they yield an integer byte count). So `16M` = `0x01000000`, `512M` = `0x20000000`.

Here is the production peripheral band written as `auto_base` sugar (this is the exact map the generator's own test reproduces, including the 64M XiP aperture eating the `0x25..0x27` gap):

```yaml
# AFTER (simplified)
interconnects:
  - name: multicore_interconnect
    gen: True
    auto_base: { from: 0x20000000, align: 16M }
    targets:
      - { name: eth_ss_slave,   base: 0x00000000, size: 512M }   # pinned
      - { name: dma_230_0,        size: 16M }
      - { name: qspi_flash_0,     size: 16M }
      - { name: phc_0,            size: 16M }
      - { name: ipc_mailbox_0,    size: 16M }
      - { name: qspi_flash_xip,   size: 64M }    # spans 0x24..0x27, eats the gap
      - { name: cc_periph_0,      size: 16M }    # resumes at 0x28000000
      - { name: cpu1_remap_0,     size: 16M }
      - { name: reset_ctrl_0,     size: 16M }
      - { name: evt_route_0,      size: 16M }
      - { name: cpu_ss_1_slave, base: 0x80000000, size: 512M }   # pinned
```

Expanded (canonical) bases:

```
dma_230_0      = 0x20000000      qspi_flash_xip = 0x24000000  (64M -> spans 0x24..0x27)
qspi_flash_0   = 0x21000000      cc_periph_0    = 0x28000000  (past the 64M hole)
phc_0          = 0x22000000      cpu1_remap_0   = 0x29000000
ipc_mailbox_0  = 0x23000000      reset_ctrl_0   = 0x2A000000
                                 evt_route_0    = 0x2B000000
# pinned 512M windows untouched at 0x00000000 and 0x80000000.
```

**Hard errors** inside an `auto_base` interconnect:

* a bool where a size is expected;
* **`$PARAM` or an expression in any size/base** — `auto_base` is literals-only (the single place `$PARAM` does not resolve);
* bad units (`16MB`), an empty string, or a non-integer fractional byte count;
* an un-pinned target with neither `base:` nor `size:`;
* an auto-packed window overlapping a **pinned** window (auto-vs-auto never overlaps by construction; pinned-vs-pinned is not checked here).

> **Reality check**: the production `sys_desc/nanosoc_multicore_soc.yaml` does **not** use `auto_base` yet — it hand-writes hex `base:`/`size:` literals (verified). `auto_base` is a working, available convenience the SoC simply has not been migrated to; both forms are fully supported.

### Mixing the two formats

`uses:` and `auto_base` are independent and compose freely: a region can `uses:` a preset, and the same SoC's interconnect can use `auto_base`. Because the pass is a strict identity when no sugar key is present, you can migrate file-by-file — verbose and simplified modules coexist in the same build with no flag. Sugar applies in the top YAML *and* in any discovered region/subsystem, since the CLI installs the expander for the whole parse.

---

# 5. Running the generator

The generator is a Python package, `soc_model`, invoked as `python3 -m soc_model`. You *can* call it by hand, but for any real SoC you should drive it through the project Makefile, which assembles the eight `--lib-dir` flags, the chip/pad/FPGA flags, and the output directory for you, then applies the small set of post-passes the flow needs. This chapter covers the canonical entry point, the full CLI, and the Makefile knobs.

## 5a. The canonical way: `make`

The entry point is the `sys_desc` Makefile:

```bash
# from the project root, once per shell — exports SOCLABS_*, ARM_IP_LIBRARY_PATH,
# EDA-tool paths, AND auto-regenerates build_soc/ if it is stale:
source set_env.sh

# explicit regeneration (the canonical command):
make -C sys_desc          # == make -C sys_desc soc_model (default target is `all: soc_model`)

# equivalent shortcut from the repo root:
make soc                  # the root Makefile just runs `$(MAKE) -C sys_desc`
```

`source set_env.sh` does more than set variables. After initialising submodules it runs a **staleness-gated** regeneration: if `build_soc/rtl` is missing, or `sys_desc/nanosoc_multicore_soc.yaml` is newer than `build_soc/rtl`, it runs `make -C sys_desc` for you; otherwise it prints `[skip] build_soc/rtl is already up to date`.

> **Gotcha — staleness is keyed on the top YAML only.** The auto-make checks the mtime of the *top-level* YAML against `build_soc/rtl`. If you edit a **lib-dir** YAML (a region, a subsystem, an interface definition) the auto-make will **not** fire. After editing anything other than the top YAML, run `make soc` explicitly.

### Why not bare `python3 -m soc_model`

Running the module directly is fully supported (and is exactly what the fixtures and the test suite do), but for the real multicore SoC it is error-prone:

- **`--lib-dir` is mandatory and there are eight of them.** The parser resolves every `module:` reference by scanning each `--lib-dir` (plus its `regions/`, `subsystems/`, `modules/` subdirs). Miss one and you get an unresolved-module validation error. The Makefile builds the full list (see [5d](#5d-makefile-variables-and-disable-knobs)).
- **The chip/pad/FPGA flags must be passed explicitly and consistently.** The Makefile derives them from `PAD_TECH`/`PIN_COUNT`/`CHIP_BOUNDARY`/`FPGA_WRAPPER` and gates them with the disable logic in [5d](#5d-makefile-variables-and-disable-knobs).
- **The CLI flags default to environment variables** (`PAD_TECH`, `PIN_COUNT`, `CHIP_BOUNDARY`, `FPGA_WRAPPER`, `EMIT_UPF`). A stray exported variable silently changes what a bare `python3 -m soc_model` does. The Makefile always passes explicit flags, so it is deterministic.
- **The generator alone does not finish the job.** Four post-passes run *after* `python3 -m soc_model` (DMA-250 q-channel patch, memmap symlinks, the memmap compat header, and `.ld` wrapping — see [6c](#6c-the-post-passes-no-patch_ahb_to_apb)). The Makefile applies them; a raw CLI run does not.

> **`patch_ahb_to_apb.py` is gone.** The generator emits correct AHB and AHB→APB-bridge RTL natively (APB targets use `protocol: apb` + an `apb_config:` block, see [4a.5](#4a5-an-apb-bridged-target-no-patch-script-needed)). The only RTL post-pass is `patch_dma250_qctrl.py` (vendor DMA-250 collateral) plus `.ld`/memmap file-wrapping. Do not look for, or run, `patch_ahb_to_apb.py` — it does not exist.

### The full raw invocation (for power users)

If you must drive the generator directly — e.g. to point it at a throwaway build dir, or to reproduce exactly what the Makefile runs — this is the shape, with the exact eight lib-dirs. The Makefile `cd`s into `$(SOCLABS_NANOSOC_GEN_DIR)` first (so `python3 -m soc_model` resolves the package) and the lib-dirs are all relative to `$SOCLABS_PROJECT_DIR`:

```bash
source set_env.sh        # you still need the SOCLABS_* + ARM_IP_LIBRARY_PATH env

cd "$SOCLABS_NANOSOC_GEN_DIR"
python3 -m soc_model \
    "$SOCLABS_PROJECT_DIR/sys_desc/nanosoc_multicore_soc.yaml" \
    --lib-dir "$SOCLABS_PROJECT_DIR/sys_desc" \
    --lib-dir "$SOCLABS_NANOSOC_ARCH_TECH_DIR/sys_desc" \
    --lib-dir "$SOCLABS_NANOSOC_ARCH_TECH_DIR/sys_desc/regions" \
    --lib-dir "$SOCLABS_NANOSOC_ARCH_TECH_DIR/sys_desc/subsystems" \
    --lib-dir "$ETH_SS_HOME/sys_desc" \
    --lib-dir "$ETH_SS_HOME/sys_desc/regions" \
    --lib-dir "$ETHMAC_AHB_HOME/sys_desc" \
    --lib-dir "$AHB_QSPI_HOME/sys_desc" \
    --chip-boundary nanosoc_multicore \
    --pad-tech "$PAD_TECH" --pad-tech-dir "$NANOSOC_PAD_TECH_DIR" \
    --pin-count multicore \
    --fpga-wrapper \
    --build-dir "$SOCLABS_PROJECT_DIR/build_soc"
```

This emits the RTL/flists/reports but **stops short of the four post-passes** — the memmap symlinks, the compat `nanosoc_memmap.h`, and the wrapped `<profile>.ld` scripts are written by the Makefile recipe, not the generator. Use this only when you understand that, or run `make -C sys_desc` instead.

For the smallest self-contained case (a single region behind one bus matrix, the worked example in [Chapter 3](#3-hello-soc--a-runnable-walkthrough)), only one lib-dir is needed:

```bash
cd "$SOCLABS_NANOSOC_GEN_DIR"      # your nanosoc_gen checkout
PYTHONDONTWRITEBYTECODE=1 python3 -m soc_model \
    tests/fixtures/minimal_soc.yaml \
    --lib-dir tests/fixtures \
    --build-dir /tmp/hello_min_build
```

> **`ARM_IP_LIBRARY_PATH` + `perl` are required for the AHB bus-matrix RTL.** The matrix is generated by Arm's `BuildBusMatrix.pl` (≈120 s). Without the env var (or without perl) you still get the XML, the wrapper SV, and everything else — but no matrix `.v` files, so the design will not elaborate. `set_env.sh` sets `ARM_IP_LIBRARY_PATH` to the site's Arm IP library (read-only: never write under it).

## 5b. CLI reference

One positional argument (the top-level YAML) plus the optional flags below. Defaults shown are the code defaults; several read an environment variable.

| Flag | Short | Default | Effect |
|---|---|---|---|
| `yaml_file` (positional) | — | **required** | Top-level YAML. Resolved to an absolute path; its parent directory becomes the base dir for relative includes. |
| `--build-dir DIR` | `-b` | `<yaml_dir>/build_soc` | Output tree root. |
| `--validate-only` | — | off | Parse + validate, then exit `1` if there are errors else `0`. Emits **no** files. |
| `--no-validate` | — | off | Skip validation entirely (still generates RTL). |
| `--lib-dir DIR` | `-l` | none | **Repeatable.** Each dir is scanned for component YAMLs (modules / interfaces / register maps). Also adds `<dir>/presets/` to the preset search path. |
| `--arm-ip-library-path PATH` | — | `$ARM_IP_LIBRARY_PATH` | Override the env var; handed to the AHB interconnect and DMA-250 backends. |
| `--lint` | — | off | After generation, run slang/pyslang over the generated RTL. |
| `--lint-flist FILE` | — | none | `.flist` of design RTL to lint (used with `--lint`). |
| `--slang-bin PATH` | — | auto-detect | slang binary for `--lint`. |
| `--slang-args "…"` | — | none | Extra slang arguments (space-separated). |
| `--allow-unread-hsel` | — | off | Report, but do not fail on, the HSEL-reader check (below). Only for reproducing a frozen render. |
| `--fail-on-unread-hsel` | — | on | Fail the run on an HSEL-reader finding (the default; the opposite of `--allow-unread-hsel`). |
| `--system-yaml FILE` | — | none | Generate a *system* wrapper (expansion region) + a GPIO chip wrapper. Enables the GPIO-bus pad-ring path. **Not used by the multicore flow** — the multicore SoC is wrapped directly by `--chip-boundary`. |
| `--config-override KEY=VALUE` | — | `[]` | **Repeatable.** Value parsed as `int(v, 0)` (accepts `0x…` or decimal) else kept as a string. Feeds the config package and the memmap/linker sizing — **not** the physical SRAM macro count. |
| `--pad-tech NAME` | — | `$PAD_TECH` | Selects `pad_tech/<NAME>.yaml` (bundled: `generic`; others from a pad pack); triggers the pad ring (and UPF if that tech has a `upf:` block). Not found = error, exit 1. `none` = no pad ring. |
| `--pad-tech-dir DIR` | — | `$NANOSOC_PAD_TECH_DIR` | A pad pack (`pad_tech/`, `pin_map/`, `templates/`); repeatable. See [7c](#7c-asic--pads-chip-wrapper--pad-ring--upf). |
| `--pin-count N\|name` | — | `$PIN_COUNT` | Numeric → `pin_map/<tech>_<N>pin.yaml`; a name (e.g. `multicore`) → `pin_map/<tech>_<name>.yaml`. Required with `--pad-tech`. |
| `--chip-boundary NAME` | — | `$CHIP_BOUNDARY` | Selects `lib/chip_boundary/<NAME>.yaml`; emits `<chip_module>.v` (bonded ports re-exposed, tied inputs constant-driven, opens left open). |
| `--emit-upf` | — | `bool($EMIT_UPF)` | Force UPF emission. A `upf:` block in the pad-tech auto-enables it anyway; if you force it but the tech has no `upf:` block, the generator prints a skip notice. |
| `--fpga-wrapper [NAME]` | — | `$FPGA_WRAPPER` | Emit the Vivado IP wrapper. Optional value: bare flag reuses `--chip-boundary`; a NAME overrides the profile/boundary (`lib/fpga_profile/<name>.yaml` + `lib/chip_boundary/<name>.yaml`). |

> **`--fpga-wrapper` argument quirk.** It is declared `nargs='?'`, so `--fpga-wrapper hello.yaml` greedily consumes the next token as the *profile name*, not as the positional `yaml_file`. Always put the positional YAML first, or use `--fpga-wrapper=<name>`. A bare `--fpga-wrapper` with no `--chip-boundary` prints `Skipped (--fpga-wrapper needs a name or --chip-boundary)`.

The console output is a running log of each stage (`Parsing …`, `Resolved modules: X/N`, `Validating…`, then a `---` banner per backend). Watch two lines in particular:

- `Resolved modules: X/N` — if `X < N`, a `module:` reference did not resolve (almost always a missing `--lib-dir`).
- `N error(s), M warning(s)` — non-zero errors mean the model is invalid (and would fail `--validate-only`).

## 5c. Validate-only and config overrides

**Validate without generating.** For fast bring-up feedback (schema and reference errors) without paying for the full render:

```bash
make -C sys_desc validate
# == python3 -m soc_model <yaml> <8x --lib-dir> --validate-only
```

`--validate-only` runs the parser, builder, and the validator, prints every message, then exits `1` if there are any errors (else `0`). It writes **nothing** — no `build_soc/`. Use it in CI or in an edit/check loop. (`--no-validate` is the opposite: it skips the validator but still generates RTL — use only when you know the model is good and want to bypass a false-positive check.)

**Config overrides.** `--config-override KEY=VALUE` injects values into the SoC config package and the memmap/linker sizing. It is repeatable and the value is parsed as `int(v, 0)` (so `0x…` and decimal both work) or, failing that, kept as a string:

```bash
make -C sys_desc soc_model \
  PARAM_OVERRIDES="--config-override CC_IMEM_RAM_ADDR_W=14 --config-override CC_DMEM_RAM_ADDR_W=12"
```

> **Override scope.** `--config-override` changes the generated **config package, memory map, and linker scripts** — i.e. the address-width *numbers* firmware sees. It does **not** change the physical SRAM macro instantiation in the region RTL (that follows the region YAML's own params). This is exactly why the FPGA target shrinks the *memmap* without touching the RTL store size — see `soc_model_fpga` in [5d](#5d-makefile-variables-and-disable-knobs).

## 5d. Makefile variables and disable knobs

The `sys_desc/Makefile` is the canonical front-end. Targets:

| Target | What it does |
|---|---|
| `all` (default) | → `soc_model` |
| `soc_model` | Runs `python3 -m soc_model <yaml> <lib-dirs> $(PARAM_OVERRIDES) $(PAD_FLAGS) --build-dir build_soc`, then the four post-passes ([6c](#6c-the-post-passes-no-patch_ahb_to_apb)). |
| `soc_model_fpga` | `soc_model` with the Pynq-Z2 memmap-shrink overrides baked in (see below). |
| `validate` | `python3 -m soc_model … --validate-only` (no RTL). |
| `clean` | `rm -rf $(BUILD_SOC_DIR)`. |
| `help` | Prints the targets, the resolved `PAD_FLAGS`, and the `LIB_DIRS`. |

Override these on the command line (`make -C sys_desc VAR=value`):

| Variable | Default | Effect |
|---|---|---|
| `SOCLABS_PROJECT_DIR` | `$(abspath $(CURDIR)/..)` | Repo root; all paths derive from it. |
| `SOCLABS_NANOSOC_GEN_DIR` | `<arch_tech>/nanosoc_gen` | Where `python3 -m soc_model` is run from. |
| `BUILD_SOC_DIR` | `$(SOCLABS_PROJECT_DIR)/build_soc` | Passed as `--build-dir`. |
| `SYS_DESC_YAML` | `$(CURDIR)/nanosoc_multicore_soc.yaml` | The top-level input. |
| `PYTHON` | `python3` | Interpreter (needs ≥ 3.7). |
| `PARAM_OVERRIDES` | empty | Pass-through string, e.g. `PARAM_OVERRIDES="--config-override CC_IMEM_RAM_ADDR_W=14"`. |
| `PAD_TECH` | the project's (a foundry tech from a pad pack, or `generic`) | Pad technology. **`PAD_TECH=none` (or empty) disables ALL chip/pad/UPF/FPGA-wrapper generation → RTL only.** |
| `PIN_COUNT` | `multicore` | Pin-map variant → `pin_map/<PAD_TECH>_multicore.yaml`. |
| `CHIP_BOUNDARY` | `nanosoc_multicore` | Chip-boundary spec name; also the FPGA-profile name. |
| `FPGA_WRAPPER` | `1` | `1` appends `--fpga-wrapper`; **`FPGA_WRAPPER=0` skips just the Vivado wrapper** (chip wrapper + pads + UPF still generated). |

The **eight** `LIB_DIRS` the Makefile assembles (relative to `SOCLABS_PROJECT_DIR`):

```
sys_desc/                                         (= $(CURDIR), the project's own YAML + wrappers)
nanosoc_arch_tech/sys_desc
nanosoc_arch_tech/sys_desc/regions
nanosoc_arch_tech/sys_desc/subsystems
ethernet-subsystem-ahb/sys_desc
ethernet-subsystem-ahb/sys_desc/regions
ethernet-subsystem-ahb/ethernet-mac-ahb/sys_desc
ahb_qspi/sys_desc
```

(The IPC-mailbox submodule is **not** a lib-dir — its wrapper YAML lives in `sys_desc/` itself.)

### The chip/pad/FPGA flag assembly (and how to disable it)

The Makefile turns the four tech variables into `--chip-boundary/--pad-tech/--pin-count` (+ optional `--fpga-wrapper`) with this gate:

```make
PAD_FLAGS :=
ifneq ($(strip $(PAD_TECH)),)
ifneq ($(strip $(PAD_TECH)),none)
PAD_FLAGS := --chip-boundary $(CHIP_BOUNDARY) --pad-tech $(PAD_TECH) --pin-count $(PIN_COUNT)
ifeq ($(strip $(FPGA_WRAPPER)),1)
PAD_FLAGS += --fpga-wrapper
endif
endif
endif
```

The two disable knobs in practice:

```bash
make -C sys_desc PAD_TECH=none        # RTL only — no chip wrapper, no pads, no UPF, no FPGA wrapper
make -C sys_desc FPGA_WRAPPER=0       # chip wrapper + pad ring + UPF, but NO Vivado wrapper
```

> **UPF is automatic.** There is no `--emit-upf` in the Makefile. UPF is emitted because the pad-tech carries a `upf:` block (the bundled `generic.yaml` does); any pad-tech with a `upf:` block auto-enables it.

### The pre-canned FPGA target

`soc_model_fpga` is just `soc_model` with a fixed override set that shrinks the CPU1 (chip-control) IMEM/DMEM/bootrom so the memory map fits Pynq-Z2 BRAM:

```bash
make -C sys_desc soc_model_fpga
# == make soc_model PARAM_OVERRIDES="--config-override CC_IMEM_RAM_ADDR_W=14 \
#                                    --config-override CC_DMEM_RAM_ADDR_W=12 \
#                                    --config-override CC_BOOTROM_ADDR_W=11"
```

As with all `--config-override`s, this changes only the **memmap and linker** numbers; the RTL store size still follows the YAML defaults.

---

# 6. Understanding the outputs

Everything lands under `build_soc/` (or whatever `--build-dir` you pass). One backend owns each kind of artifact; the build order is fixed.

## 6a. The `build_soc/` tree

This is the real tree produced by the multicore SoC build (verified):

```
build_soc/
  rtl/                          # ALL generated RTL (.sv/.v) + config pkg + .vh + .upf
    <ic_name>/                  #   per-interconnect: wrapper .sv + per-IC config pkg
      <ic_name>/                #     BuildBusMatrix.pl raw Verilog (arbiters/decoders/…)
    <ic_name>_discovery/        #   discovery regblock RTL (regblock + _apb_wrapper + pkg)
    dma250/rendered_<CFG>/…     #   DMA-250 rendered core + logs (only if a dma_250 instance exists)
    <top>.sv                    #   structural SoC top
    <top>_soc_config_pkg.sv     #   config localparams (SystemVerilog)
    <top>_soc_config.vh         #   same as `define for .v consumers
    <top>_chip.v                #   chip-functional wrapper      (only with --chip-boundary)
    nanosoc_chip_pads_<tech>_<variant>.v    # pad ring          (only with --pad-tech)
    nanosoc_chip_pads_<tech>_<variant>.upf  # power intent       (auto when pad-tech has upf:)
    <top>_vivado_wrapper.v      #   FPGA Vivado wrapper          (only with --fpga-wrapper)
  flist/                        # ALL .flist filelists
  firmware/                     # .ld .h .mk .cmake .vh .py + build_info .h
  rdl/                          # SystemRDL .rdl files
  discovery/                    # <ic>_discovery.yaml device-discovery tables
  build_info/                   # <top>_build_info.yaml
  interconnect/<ic>/            # NON-RTL interconnect artifacts: xml/ address_maps/ logs/ ipxact/
  reports/                      # HTML, text maps, python model, sram report
  docs/                         # <top>_design_doc.md
  pnr/                          # Innovus .io floorplan (only when the pin-map carries side data)
```

A real `firmware/` directory for the multicore SoC, showing both the prefixed generator output and the Makefile-wrapped `<profile>.ld` scripts side by side:

```
firmware/
  nanosoc_multicore_soc_memmap.{h,mk,cmake}          # generator output (prefixed)
  nanosoc_memmap.{h,mk,cmake}                         # post-pass symlinks/compat (unprefixed)
  nanosoc_multicore_soc_adp.{vh,py}                   # ADP upload address constants
  nanosoc_multicore_soc_<profile>_memory.ld          # generator: MEMORY block only
  <profile>.ld                                        # post-pass: complete linker script
  nanosoc_multicore_soc_build_info.h                  # build-info header
```

The build runs backends in a fixed order (`__main__.py`):

> reports → ahb (BuildBusMatrix.pl) → dma250 → subsystem → rdl → discovery → build_info → config_pkg → toplevel → firmware → [system] → [chip] → chip_wrapper → [fpga_wrapper] → [pad_ring (+upf)] → docs → **`timescale sweep** → [lint]

The bracketed stages run only when the matching flag/section is present. The final **`timescale sweep** walks `rtl/` and prepends `` `timescale 1ns/1ps `` to any `.sv`/`.v` that lacks one in its first 400 chars — this is how the externally-rendered interconnect and DMA-250 files (which never pass through the templating layer) end up with a timescale natively. It prints e.g. `prepended `timescale to 14 file(s)`.

> **jinja2 dependency.** Every *templated* output (all wrappers, the toplevel, subsystems, the config pkg, firmware files, docs, regblock RTL) needs jinja2; if it is missing the backend prints `Skipped (jinja2 not available)` and emits nothing. The non-templated outputs (HTML, text reports, python model, SRAM report, `.rdl`, the discovery/build-info YAML, the config `.vh`, UPF, the DMA-250 flist) are produced regardless.

## 6b. Artifact reference

What each backend emits, where, who consumes it, and what it needs.

| Artifact | Backend | Path under `build_soc/` | Consumer | Needs |
|---|---|---|---|---|
| Connectivity HTML | html | `reports/<top>_connectivity.html` | human/browser (block diagram + memmap + validation msgs) | — |
| Memory-map text | text | `reports/<top>_memory_map.txt` | human (target/initiator table) | — |
| Hierarchy text | text | `reports/<top>_hierarchy.txt` | human | — |
| Python model | python | `reports/<top>_model.py` | downstream Python tools (cocotb device models, GUI) | — |
| SRAM report | sram | `reports/<top>_sram_report.txt` | ASIC macro substitution | — |
| AHB matrix RTL | ahb | `rtl/<ic>/<ic>/*.v` (`_lite`, `_default_slave`, `_arbiter`, `_inititator_input` [sic — Arm's spelling], `_matrix_decode_<INIT>`, `_target_output`) | sim/synth | **`ARM_IP_LIBRARY_PATH` + perl** (BuildBusMatrix.pl, ≈120 s) |
| AHB wrapper SV | ahb | `rtl/<ic>/<prefix>_interconnect.sv` (inlines `cmsdk_ahb_to_apb` + `cmsdk_apb_slave_mux` for APB targets) | sim/synth | jinja2 |
| AHB flist / per-IC config pkg | ahb | `flist/<prefix>_interconnect.flist`, `rtl/<ic>/<prefix>_config_pkg.sv` | flow | jinja2 |
| AHB XML / address maps / IP-XACT / log | ahb | `interconnect/<ic>/{xml,address_maps,ipxact,logs}/…` | BuildBusMatrix input + human | — |
| DMA-250 core | dma250 | `rtl/dma250/rendered_<CFG>/…`, `flist/dma250_rendered.flist`, log | sim/synth | `ARM_IP_LIBRARY_PATH`/`SOCLABS_DMA250_DIR` + python3 (only if a `dma_250` instance exists) |
| Subsystem RTL | subsystem | `rtl/<module_name>.sv` + flist | sim/synth (per `gen:True` module with internal instances/interconnects) | jinja2 |
| RDL `.rdl` | rdl | `rdl/<name>.rdl` | docs / SystemRDL tools | — |
| Regblock RTL | rdl | `rtl/<name>/<name>{,_pkg}.sv` + `<name>_apb_wrapper.sv` | sim/synth (only `gen:True` register maps) | systemrdl-compiler + peakrdl-regblock |
| Discovery table | discovery | `discovery/<ic>_discovery.yaml` (+ rdl/rtl) | runtime device-discovery firmware (magic `0x534F4344` "SOCD") | — / regblock for RTL |
| Build info | build_info | `build_info/<top>_build_info.yaml`, `firmware/<top>_build_info.h` (+ rdl/rtl) | runtime/firmware (magic `0x42494E46` "BINF") | — / regblock for RTL |
| SoC config pkg | soc_config_pkg | `rtl/<top>_soc_config_pkg.sv`, `rtl/<top>_soc_config.vh`, `flist/<top>_soc_config_pkg.flist` | sim/synth; `.vh` for Verilog consumers | jinja2 (SV only; `.vh` is plain) |
| Top-level module | toplevel | `rtl/<top>.sv`, `flist/<top>_toplevel.flist` | sim/synth (the structural SoC top) | jinja2 |
| Linker MEMORY block | firmware | `firmware/<top>_<profile>_memory.ld` (one per `linker_profiles` entry) | linker (after Makefile wrapping) | jinja2 |
| Memmap header | firmware | `firmware/<top>_memmap.h` (`<TOP>_<REGION>_BASE/_SIZE/_ALIAS/_REMAP`, `SYS_CLK_FREQ_HZ`, `NUMIRQ`) | C firmware | jinja2 |
| Memmap mk/cmake | firmware | `firmware/<top>_memmap.{mk,cmake}` | NanoSoCFirmware CMake | jinja2 |
| ADP vh/py | firmware | `firmware/<top>_adp.{vh,py}` | sim backdoor / ADP upload | jinja2 |
| System wrapper | system | `rtl/<system>.sv` + flist | sim/synth (only with `--system-yaml`) | jinja2 |
| Chip wrapper (GPIO) | chip | `rtl/<…_chip>.v` + `<…_pads>.v` + flist | legacy single-core GPIO path (auto when a system module is present) | jinja2 |
| Chip-functional wrapper | chip_wrapper | `rtl/<chip_module>.v` + flist | ASIC seam (only with `--chip-boundary`) — in/out/oe nets | jinja2 |
| FPGA Vivado wrapper | fpga_wrapper | `rtl/<module_name>.v` (default `<chip>_vivado_wrapper`) + flist | Vivado IP-Integrator (only with `--fpga-wrapper`) — IOBUF `_i/_o/_t` triples | jinja2 |
| Pad ring | pad_ring | `rtl/nanosoc_chip_pads_<tech>_<variant>.v` + inline flist | ASIC P&R (only with `--pad-tech`) — true `inout` pads | jinja2 |
| Innovus floorplan | pad_ring | `pnr/nanosoc_io_plan_<tech>_<variant>.io` | Innovus (only when the pin-map carries `ring`/side data) | jinja2 |
| UPF | upf | `rtl/nanosoc_chip_pads_<tech>_<variant>.upf` + flist | ASIC P&R power intent (auto when `pad_tech.upf`, or `--emit-upf`) | — (pure python) |
| Design docs | docs | `docs/<top>_design_doc.md` | human | jinja2 |
| Lint report | lint | (stdout only, no file) | CI/dev (only with `--lint`) | slang/pyslang |
| HSEL-reader check | hsel_lint | (stdout only, no file) | always on; **fails the run** when a bus-matrix `<tgt>_hsel` has no reader outside the matrix (a child subsystem link that is not gated), or a generated module's input `*_hsel` is never read while its `*_htrans` is used. Also `python3 -m soc_model.backends.hsel_lint <rtl_dir>` on any past render | — (pure python) |

> **Generated tech-flist content footgun.** The chip/pad/FPGA/UPF flists hard-code lines of the form `$SOCLABS_NANOSOC_SOC_DIR/build_soc/rtl/<file>` — the `build_soc/rtl/` subpath is fixed regardless of `--build-dir`. The toplevel flist additionally hard-codes a single-core-layout path (`$(SOCLABS_PROJECT_DIR)/nanosoc_m0_soc/build_soc/rtl/…`). The downstream environment must define those variables (which `set_env.sh` does). Also keep `--build-dir` / worktree paths free of `+` characters — a `+` in a path breaks VCS `+incdir+`/`+define+`/`$readmemh` handling.

## 6c. The post-passes (no `patch_ahb_to_apb`)

After `python3 -m soc_model` returns, the `sys_desc/Makefile` runs **four** post-passes. None of them is the retired `patch_ahb_to_apb.py` — the generator already emits correct AHB and AHB→APB-bridge RTL. They are:

1. **`patch_dma250_qctrl.py`** — `python3 scripts/patch_dma250_qctrl.py build_soc/rtl/dma250`. Forces the DMA-250 q-channel power/clock controller ALWAYS-POWERED from reset (there are no real power domains on the Pynq FPGA; the rendered reset-capture otherwise latches the DMAC off and hangs CPU0's first DMA APB write). The DMA-250 backend re-renders the qctrl every build, so this **must** run post-render. It is idempotent and a no-op when no DMA-250 is rendered. **This is vendor DMA-250 collateral — it is not, and has nothing to do with, the retired `patch_ahb_to_apb`.**

2. **memmap symlinks** — in `build_soc/firmware/`, symlink the prefixed `nanosoc_multicore_soc_memmap.{cmake,mk}` → unprefixed `nanosoc_memmap.{cmake,mk}` (the `NanoSoCFirmwareFunctions.cmake` helper expects the unprefixed names).

3. **`nanosoc_memmap.h` compat wrapper** — a generated header that `#include`s the prefixed `nanosoc_multicore_soc_memmap.h` and re-exports `NANOSOC_SYS_CLK_FREQ_HZ` for older CMSIS/retarget sources that reference the unprefixed macro.

4. **`.ld` linker wrappers** — the generator emits only MEMORY blocks (`<top>_<profile>_memory.ld`); the recipe writes a complete `<profile>.ld` that `INCLUDE`s `lib-nosys.ld` + the generated memory block + a sections fragment. Profiles `eth_ss_cm0`, `stage1_imem`, `cc_stage1_imem`, `eth_stage1_imem` get `sections.ld`; `stage0_bootrom` and `eth_ss_bootloader` get `sections_bootloader.ld`. (The wrap loop is hard-coded to those profile names. Fixture profiles like `app_imem` are not auto-wrapped — but fixtures are run via the raw CLI, not the Makefile.)

These four are the entire post-`soc_model` story. The generator's own final stage (the `` `timescale `` sweep, [6a](#6a-the-build_soc-tree)) is *inside* `python3 -m soc_model`, not a Makefile post-pass — it replaces what used to be a separate patch script.

---

# 7. The three downstream paths

The same `build_soc/` tree feeds **all three** delivery paths — simulation, FPGA, and ASIC. You do not run three different generators; you run **one** generator and turn on more backends with flags. The Makefile encodes this as a few knobs:

| Path | Makefile knobs | Extra backends that fire |
|---|---|---|
| Sim / cocotb | `PAD_TECH=none` (RTL only) | none — just the RTL/flist/firmware backends |
| FPGA / Vivado | `FPGA_WRAPPER=1` (default) + a `PAD_TECH` | `--chip-boundary` + `--fpga-wrapper` |
| ASIC / pads | a `PAD_TECH` (a foundry tech from a pad pack) | `--chip-boundary` + `--pad-tech` + `--pin-count` + UPF |

The default `make -C sys_desc soc_model` actually turns on the FPGA *and* ASIC layers at once (`PAD_TECH=<tech>`, `PIN_COUNT=multicore`, `CHIP_BOUNDARY=nanosoc_multicore`, `FPGA_WRAPPER=1`), because emitting the chip wrapper, pad ring, FPGA wrapper, and UPF is cheap and the downstream flows each pick the file set they need. To get **RTL only** (the leanest sim build):

```bash
make -C sys_desc soc_model PAD_TECH=none
```

## 7a. Sim / cocotb (RTL only)

This is the default consumption mode and needs no chip/pad/FPGA flags at all. The simulator compiles the structural RTL via the generated `.flist` files:

```bash
source set_env.sh          # MANDATORY — flists reference $SOCLABS_* env vars
make -C sys_desc soc_model PAD_TECH=none
```

`source set_env.sh` does two things that matter here:

1. It exports `SOCLABS_PROJECT_DIR`, `SOCLABS_NANOSOC_SOC_DIR`, `SOCLABS_NANOSOC_GEN_DIR`, `ARM_IP_LIBRARY_PATH`, and the EDA-tool paths. **The generated flists reference these by name** — e.g. each line is `$SOCLABS_NANOSOC_SOC_DIR/build_soc/rtl/<file>`. VCS/xmelab compiles fail with unresolved `${SOCLABS_*}` if you skip it.
2. It **auto-runs `make -C sys_desc`** when `build_soc/rtl` is missing or older than `sys_desc/nanosoc_multicore_soc.yaml`. That staleness check is **mtime-gated on the top YAML only** — editing a *lib-dir* region/subsystem YAML will NOT trip it. After editing a region YAML, run `make -C sys_desc soc_model` by hand.

What the sim path consumes from `build_soc/`:

- **RTL flists** — `flist/<top>_toplevel.flist`, `flist/<ic>_interconnect.flist`, `flist/<subsystem>_toplevel.flist`, `flist/<top>_soc_config_pkg.flist`, `flist/dma250_rendered.flist` (if any DMA-250), plus the inline discovery/build-info/regblock RTL.
- **The structural top** `rtl/<top>.sv` and every `gen:True` subsystem `rtl/<name>.sv`.
- **The bus matrix** `rtl/<ic>/<ic>/*.v` (BuildBusMatrix.pl output) + its wrapper `rtl/<ic>/<prefix>_interconnect.sv`.
- **The config package** `rtl/<top>_soc_config_pkg.sv` for SV consumers and `rtl/<top>_soc_config.vh` (\`define`s) for plain-Verilog consumers.
- **Firmware glue** for backdoor/ADP loads: `firmware/<top>_memmap.{h,mk,cmake}`, the `.ld` linker scripts, and `firmware/<top>_adp.{vh,py}`.
- **The Python device model** `reports/<top>_model.py` — used by cocotb device models / the demo GUI to know the bus topology and address map.

Tool requirements for the RTL itself: **jinja2** (all templated RTL), and **`ARM_IP_LIBRARY_PATH` + perl** for the bus-matrix `.v` (BuildBusMatrix.pl, ~120 s). Without `ARM_IP_LIBRARY_PATH` you still get the XML and the wrapper, but no matrix `.v`, so the design will not elaborate. `peakrdl`/`systemrdl-compiler` are only needed if you want the discovery / build-info / register-block RTL (the `.rdl` and YAML descriptions are emitted regardless).

> Firmware preload: regions that carry `MEM_FPGA_IMG`/`IMEM_MEM_FPGA_IMG` (default `"image.hex"`) `$readmemh` their contents at elaboration. The file must be on the include path. A `Cannot open image.hex` warning is **by design** on cores that DMA-boot from flash instead of preloading — see [Chapter 8](#8-troubleshooting--faq).

## 7b. FPGA / Vivado (the generated Vivado wrapper)

The FPGA path adds one synthesizable wrapper on top of the inner SoC: a **Vivado IP-Integrator wrapper** that converts the chip boundary's bidir/tristate pads into Vivado IOBUF triples and renames ports to board names. It is produced by `--fpga-wrapper`, which reuses the **same** `--chip-boundary` spec the ASIC seam uses.

The Makefile turns it on with the default `FPGA_WRAPPER=1`:

```bash
make -C sys_desc soc_model               # FPGA_WRAPPER=1 -> adds --fpga-wrapper
make -C sys_desc soc_model FPGA_WRAPPER=0 # chip + pads + UPF, but NO Vivado wrapper
```

For the smaller FPGA memory profiles, use the dedicated target — it shrinks the CPU1 IMEM / DMEM / bootrom linker+memmap sizing (the RTL store size still follows the YAML):

```bash
make -C sys_desc soc_model_fpga
# == make soc_model PARAM_OVERRIDES="--config-override CC_IMEM_RAM_ADDR_W=14 \
#       --config-override CC_DMEM_RAM_ADDR_W=12 --config-override CC_BOOTROM_ADDR_W=11"
```

What `--fpga-wrapper` emits (`fpga_wrapper.py`):

- `rtl/<module_name>.v` — default `<chip_module>_vivado_wrapper.v` (`nanosoc_multicore_vivado_wrapper.v`); instantiates the inner SoC top, presents IP-Integrator board ports, and for every bonded **bidir** pad emits the Vivado triple `<base>_i / <base>_o / <base>_t` with `_t = ~oe` (active-high oe) or `_t = oe` (active-low). **No pad cells** — Vivado synthesises the IOBUF.
- `rtl/<module_name>.flist` — inline file list.

The bespoke board layer (renames, FPGA-fit param overrides, debug taps, the SYSRESETREQ self-loop, `rtc_clk <- sys_fclk`, the IP-XACT lint contract) is **not model-derivable** — it lives in `lib/fpga_profile/<name>.yaml`. The bundled `nanosoc_multicore.yaml` profile renames `sys_sysresetn -> nrst`, applies the Zynq-fit param overrides (e.g. `CC_IMEM_RAM_ADDR_W=14`), and curates the IP-XACT `physical_names` (`[sys_fclk, rmii_ref_clk, nrst, uart_rxd, uart_txd]`). Each applied bespoke item is echoed on the generator console as `residual (bespoke): ...` so you can audit it.

The `--fpga-wrapper` flag is `nargs='?'`: bare `--fpga-wrapper` reuses `--chip-boundary`; `--fpga-wrapper <name>` overrides both the boundary and the profile to `lib/{chip_boundary,fpga_profile}/<name>.yaml`. (Raw-CLI footgun: bare `--fpga-wrapper` then a YAML token will consume the YAML as the profile NAME — see [Chapter 8](#8-troubleshooting--faq).)

Consumer: **Vivado IP-Integrator** packages `rtl/<top>_vivado_wrapper.v` as IP using the profile's `ip_xact` bus-interface metadata.

## 7c. ASIC / pads (chip wrapper + pad ring + UPF)

The ASIC path is the richest: it layers the inner SoC into a bonded chip, then a pad ring with real `inout` pads and power cells, plus a UPF power-intent file and an Innovus floorplan. Four data-driven backends do the work, all fed by editable YAML in `lib/` (no code changes to add a tech — see "How to add a tech" below).

**The layering (inner -> outer):**

```
nanosoc_multicore_soc.sv          inner SoC top (functional ports)
        |  --chip-boundary
        v
nanosoc_multicore_chip.v          chip wrapper: bonded nets re-exposed 1:1
        |                          (in/out/oe seam), ties driven, opens left open
        |  --pad-tech + --pin-count
        v
nanosoc_chip_pads_<tech>_multicore.v     pad ring: true `inout` pads + power ring
nanosoc_chip_pads_<tech>_multicore.upf   UPF (beside the ring, when the tech has upf:)
pnr/nanosoc_io_plan_<tech>_multicore.io  Innovus floorplan (when side data present)
```

The multicore Makefile builds all of this:

```bash
export NANOSOC_PAD_TECH_DIR=<your pad pack>   # foundry pad data (not in this repository)
make -C sys_desc soc_model
# PAD_FLAGS = --chip-boundary nanosoc_multicore --pad-tech $(PAD_TECH) --pin-count multicore
#             --fpga-wrapper            (FPGA wrapper rides along)
```

Without a pad pack, `PAD_TECH=generic` renders the same ring with the bundled example cells (the SoC Labs generic library pads), and `PAD_TECH=none` renders no pad ring. There is **no `--emit-upf` in the Makefile**: UPF auto-emits when the pad tech carries a `upf:` block.

### Pad packs: where foundry pad data lives

The generator carries the pad-ring mechanism and one example technology, `generic` (`lib/pad_tech/generic.yaml`, `lib/pin_map/generic_multicore.yaml`). Foundry pad-cell names, port maps and pin maps are not in this repository. A **pad pack** is a directory with `pad_tech/<tech>.yaml`, `pin_map/<tech>_<token>.yaml` and, optionally, `templates/*.j2`; name it with `--pad-tech-dir <dir>` (repeatable) or `$NANOSOC_PAD_TECH_DIR` (an `os.pathsep` list). Search order for a pad tech or pin map: the top YAML's directory, each `--lib-dir`, the pad packs, then the bundled `lib/`. A pack's templates are searched after the bundled ones, so a pack can add a template but not replace one. The fixed-pin (GPIO-bus) ring template is not bundled: a pack that supports fixed-pin rings provides `templates/nanosoc_chip_pads_asic.v.j2`. A `--pad-tech` or pin map that is not found, or a fixed-pin ring without that template, is an error (exit 1) that names the directories searched.

### The four descriptors

These live in `lib/<kind>/` (resolved relative to the package root; a project can override any bundled file via `--lib-dir`, and pad techs and pin maps also come from pad packs):

1. **`lib/chip_boundary/<name>.yaml`** — *tech-independent*. Classifies **every** inner SoC port as exactly one of: a **bonded** pad, a **tied** input (driven to a constant inside the wrapper), or an **open** output (left unconnected). The bundled `nanosoc_multicore.yaml` classifies **39 bonded + 17 tied + 24 open = 80** ports. A port classified twice raises an error. Pad rows give `pad_kind` (`input` / `output` / `tristate_out` / `bidir`), `width`, the in/out/oe nets, and `oe_polarity`. **Tie constants must be self-contained sized literals** (`"32'd0"`, NOT `{SYS_ADDR_W{1'b0}}` — the wrapper has no inner-param scope).

2. **`lib/pad_tech/<tech>.yaml`** — *tech-constant cells*. The pad-cell catalogue: `bidir_cell` / `input_cell` / `output_cell` / `bidir_io_cell`, `power_cells`, `constants{tiehi,tielo}`, `supply_connect`, optional `upf:` and `align_ports`. Each cell has a `module` and an **ordered** `port_map` — the insertion order **is** the port emission order and must match the vendor hand file. Placeholders `{i}{o}{e}{z}` = per-bit nets, `{pad}` = boundary port, `{c}` = core/sink, `{ren}` = pull-enable. Bundled tech: `generic` (the SoC Labs generic library pads `PAD_INOUT8MA_NOE` / `PAD_VDDIO` / `PAD_VSSIO` / `PAD_VDDSOC` / `PAD_VSS`, `align_ports: true`, has `upf:`). Foundry techs come from a pad pack.

3. **`pin_map/<tech>_<token>.yaml`** — the pad-ring variant (bundled: `generic_multicore`). Two modes:
   - **mixed-I/O** (`mode: mixed` OR a non-empty `pads:` list): an explicit per-signal pad list naming chip-top nets + pad kinds. Needs **no** system module. Output `nanosoc_chip_pads_<tech>_multicore.v`. This is the multicore path.
   - **GPIO-bus** (no `pads:`): the ring is driven by the system module's `type: gpio` interfaces, so it needs `--system-yaml`. Output `<chip>_pads_<tech>_<token>.v`. Its template comes from a pad pack (`templates/nanosoc_chip_pads_asic.v.j2`).

4. **`lib/fpga_profile/<name>.yaml`** — the FPGA board layer (used by `--fpga-wrapper`, [7b](#7b-fpga--vivado)), not part of the ASIC ring.

### The generate flags

| Flag | Selects | Makefile var / default |
|---|---|---|
| `--chip-boundary <name>` | `lib/chip_boundary/<name>.yaml` | `CHIP_BOUNDARY=nanosoc_multicore` |
| `--pad-tech <tech>` | `pad_tech/<tech>.yaml` (+ pad ring) | `PAD_TECH=<tech>` |
| `--pad-tech-dir <dir>` | a pad pack to search | `$NANOSOC_PAD_TECH_DIR` |
| `--pin-count <N\|name>` | numeric `N` -> `<tech>_<N>pin.yaml`; name -> `<tech>_<name>.yaml` | `PIN_COUNT=multicore` |
| `--emit-upf` | force UPF (auto if `pad_tech.upf` present) | not passed (auto via the tech's `upf:`) |

`make PAD_TECH=none` disables **all** chip/pad/UPF/FPGA generation (RTL only). `make FPGA_WRAPPER=0` keeps chip+pads+UPF but drops just the Vivado wrapper.

### UPF

`upf.py` renders `rtl/nanosoc_chip_pads_<tech>_<variant>.upf` from the pad-tech `upf:` block (supply sets, power domains TOP/ACCEL, ports/nets, a single AON PST at the tech's stated voltage) plus a `connect_supply_net` line per memory macro, derived by walking the **inner** SoC top's `srams` (the same walk the SRAM backend uses). It auto-emits when `pad_tech.upf` exists, or when forced with `--emit-upf` (if forced without a `upf:` block, it prints a skip). It is pure Python — no jinja2 needed. (Banked-memory footgun: an `addr_width > 15` macro is physically `2^(AW-15)` banks but the model carries one `SramEntry`, so only one connect pair is emitted — under-counted, flagged as a residual.)

### How to add a tech or a pin-count (1-2 YAML files, no code)

Everything is data-driven. To extend:

- **New pin-count on an existing tech (GPIO):** add one `pin_map/<tech>_<N>pin.yaml` (in the tech's pad pack), then build with `--pin-count <N>` (`PIN_COUNT=<N>`). No code.
- **New mixed-I/O ring on an existing tech:** add one `pin_map/<tech>_<name>.yaml` (`mode: mixed`, `chip_module:`, `pads:`), build with `--pin-count <name>`. No code.
- **A whole new technology:** add **two** files to a pad pack: `pad_tech/<tech>.yaml` (the 4 cells with correct `port_map` order, `power_cells`, `supply_connect`, optional `upf:` / `align_ports`) **plus** at least one `pin_map/<tech>_<token>.yaml`. No backend code changes.
- **New chip boundary** (different inner port set): one `lib/chip_boundary/<name>.yaml` — must classify every inner port exactly once.
- **New FPGA board:** one `lib/fpga_profile/<name>.yaml` (defaults make it inert).

Keep pad techs and pin maps in a pad pack (`--pad-tech-dir`); chip boundaries and FPGA profiles go in your project, passed via `--lib-dir` (a project copy overrides the bundled file).

### Reproduced variants

The bundled `lib/pin_map/generic_multicore.yaml` is the **mixed-I/O** ring (30 pads -> 43 cell instances) wrapping `nanosoc_multicore_chip`, in the example `generic` technology; it needs **no** `--system-yaml`. The foundry variants (fixed-pin GPIO-bus rings and the production multicore ring) are in the SoC Labs pad pack, with their own tests and golden files.

### DMA-250 generation (ASIC + FPGA)

If the SoC instantiates a DMA-250 (any instance whose `module_name` contains `dma_250`), the `SoCDma250Backend` renders the Arm DMA-250 core into `rtl/dma250/rendered_<CFG>/...` and emits `flist/dma250_rendered.flist`. It is a **no-op** when no such instance exists. Because the DMA-250 power/clock q-channel controller is re-rendered every build, the Makefile applies a single post-pass after the generator:

```
python3 scripts/patch_dma250_qctrl.py build_soc/rtl/dma250
```

This forces the q-channel **always-powered from reset** (FPGA has no power domains; otherwise the reset capture latches the DMAC OFF and CPU0's first DMA APB write hangs). It is idempotent and a no-op when no DMA-250 was rendered. **This is the only RTL post-pass** — it is vendor DMA-250 collateral, NOT the retired `patch_ahb_to_apb.py` (see [Chapter 8](#8-troubleshooting--faq)).

---

# 8. Troubleshooting / FAQ

Real footguns, each verified against the code. Symptom -> cause -> fix.

**"Module not found" / "Unresolved module" in validation.**
You missed a `--lib-dir`. The parser scans each `--lib-dir` (and its `regions/`, `subsystems/`, `modules/`, `../rtl/*/sys_desc/` subdirs) to resolve every `module:` reference. Watch the `Resolved modules: X/N` line. The hermetic fixtures need only `--lib-dir tests/fixtures`; the real multicore SoC needs **8** lib-dirs — which is exactly why you should drive it with `make`, not a hand-rolled CLI. Note also: a missing/typo'd YAML file loads as `{}` with only a printed Warning, then surfaces *downstream* as this validator error, not as a parse exception.

**Linker / memmap sizes are wrong by 4x (or the RAM is too small).**
`size`, `phys_size`, and `RAM_ADDR_W` are **three different numbers** and easy to conflate:
- `size:` = the address-decode **aperture** (e.g. a 128 MB window for a 16 KB RAM).
- `phys_size:` = the **physical storage** in bytes (int or an expression string).
- `RAM_ADDR_W` semantics **differ per region**. Real nanosoc regions are *word*-addressed (`phys = 4 * 2^N`, default 14 -> 64 KB); the hermetic `tiny_region` is *byte*-addressed (`phys = 2^N`). Match the `phys_size:` formula to the region's convention.

Also the **SRAM report over-reports** for word-addressed macros: `ResolvedSram` treats `addr_width` as a word width (`depth = 2**addr_width` words), but the region YAMLs pass `addr_width: $RAM_ADDR_W` where `RAM_ADDR_W` is a *byte* width — so a 32-bit macro reports 4x too large. For a real macro set `srams.addr_width = RAM_ADDR_W - log2(data_width/8)` (`-2` for 32-bit data). The fixtures pass `$RAM_ADDR_W` directly — that is the trap, not a template you should copy verbatim into production.

**VCS compile fails on unresolved `${SOCLABS_NANOSOC_GEN_DIR}` / `${SOCLABS_*}`.**
You forgot `source set_env.sh`. The generated flists reference those env vars verbatim. Source it first. (It also auto-runs `make -C sys_desc` when `build_soc/rtl` is stale — but only vs the **top** YAML's mtime; editing a lib-dir region YAML won't re-trigger it, so run `make -C sys_desc soc_model` by hand after such edits.)

**The chip wrapper / pad ring / UPF didn't generate.**
The pad/chip layers run only when their flags are present:
- Chip wrapper needs `--chip-boundary` (Makefile var `CHIP_BOUNDARY`).
- Pad ring + UPF need `--pad-tech` + `--pin-count` (`PAD_TECH`, `PIN_COUNT`).
If you ran `make ... PAD_TECH=none`, all of these are intentionally skipped. The mixed-I/O multicore ring needs **no** `--system-yaml`; the GPIO-bus pin-maps **do**.
A pad tech or pin map that is not found is an error (exit 1, naming the pad packs searched): set `--pad-tech-dir` / `$NANOSOC_PAD_TECH_DIR`.

**"`--system-yaml` needed for the chip?" — usually NO.**
`--system-yaml` is for the legacy single-core system/chip **GPIO** path. The multicore SoC *is* the SoC and is wrapped directly by `--chip-boundary` (chip-functional wrapper) + `--pin-count multicore` (mixed-I/O ring). The Makefile does **not** pass `--system-yaml`. You only need it for the GPIO-bus pad-ring variants.

**"Cannot open image.hex" at elaboration.**
Regions with `MEM_FPGA_IMG` / `IMEM_MEM_FPGA_IMG` (default `"image.hex"`) `$readmemh` at elaboration. Either the file isn't on the include path, **or** — by design — that core DMA-boots from flash and has no preload image. Don't wire up a preload just to silence the warning on a DMA-boot core (e.g. CPU1).

**Bare `python -m soc_model` behaves differently than expected (pads appear/disappear).**
`--pad-tech / --pin-count / --chip-boundary / --fpga-wrapper / --emit-upf` all **default to env vars** (`PAD_TECH`, `PIN_COUNT`, ...). A stray exported `PAD_TECH` changes raw-CLI behaviour. Use `make` (which always passes explicit flags) for deterministic builds.

**`--fpga-wrapper hello.yaml` "ate" my positional YAML.**
`--fpga-wrapper` is `nargs='?'`, so the next token becomes its profile **name**, not the positional `yaml_file`. Put the positional first, or use `--fpga-wrapper=<name>`. Bare `--fpga-wrapper` with no `--chip-boundary` prints `Skipped (--fpga-wrapper needs a name or --chip-boundary)`.

**A `cmsdk_ahb_to_apb` (`rtl_module:`) instance fails validation.**
A bare `rtl_module: cmsdk_ahb_to_apb` must declare `PCLKEN`, `PREADY`, `PSLVERR` as inline `interfaces:` and tie them (`PCLKEN -> 1'b1`, etc.), or validation **errors** — floating APB inputs hang the bus. (Inline `interfaces:` are honoured **only** for `rtl_module:` instances; on a `module:` instance they're parsed but ignored for boundary expansion.) The clean way to add an APB peripheral is `protocol: apb` + `apb_config:` on the target — see [4a.5](#4a5-an-apb-bridged-target-no-patch-script-needed).

**Port-mismatch errors around an AHB slave/master boundary.**
`EXCLUDE` is load-bearing. `EXCLUDE: [hburst, hmastlock]` selects the cmsdk AHB-Lite slave boundary; `EXCLUDE: [hready]` selects the slave/target form of a passthrough (slave gets `hreadyout`). Wrong `EXCLUDE` -> wrong boundary form -> port mismatch.

**A `$PARAM` typo silently reached the RTL as text.**
`resolve_param_ref` returns the literal string unchanged when the param is absent — typos don't error, they survive into RTL. And inside an `auto_base` interconnect, `$PARAM` / expressions are **rejected** (literals only) — the one place `$PARAM` does not resolve.

**A `+` in my build/worktree path broke the flists.**
A `+` mangles `+incdir+` / `+define+` / `$readmemh` in VCS. Keep `--build-dir` and worktree paths free of `+`.

**The bus matrix `.v` is missing (design won't elaborate).**
The AHB matrix is generated by Arm `BuildBusMatrix.pl`, which needs `ARM_IP_LIBRARY_PATH` + perl (~120 s). Without it you get the XML and wrapper but no matrix `.v`. **Never write under the Arm IP library** — it is read-only, shared vendor IP.

**A templated file printed "Skipped (jinja2 not available)".**
`jinja2` is required for all templated RTL / firmware / docs / wrappers. Install it; the plain-text reports, `.rdl`, discovery/build-info YAML, config `.vh`, and UPF do not need it.

**"Do I need to run `patch_ahb_to_apb.py`?" — NO. It no longer exists.**
`patch_ahb_to_apb.py` has been **fully retired** — it is not in the active `scripts/` tree and is never run. The generator emits correct AHB and AHB->APB-bridge RTL **natively**: an APB target uses `protocol: apb` plus `apb_config: { bridge: cmsdk_ahb_to_apb, params: {...} }`. The **only** RTL post-pass is `patch_dma250_qctrl.py` (vendor DMA-250 q-channel), and the Makefile additionally wraps the `.ld` linker scripts and symlinks the memmap files. (The generator itself also runs a final \`timescale sweep that prepends `` `timescale 1ns/1ps `` to any RTL lacking one — this is how the externally-rendered interconnect/DMA-250 files get a timescale, replacing an old patch.)

---

# 9. Glossary

- **module** — one YAML file mapping to one RTL module. `gen: True` -> the generator emits its RTL (toplevel / subsystem); `gen: False` -> a leaf supplied by hand (regions, CPU cores, vendor IP). Note the opposite defaults: `module.gen` defaults **False**, `interconnect.gen` defaults **True**.
- **region** — a `gen: False` leaf module (SRAM / bootrom / IMEM / DMEM) sitting behind one AHB slave port.
- **subsystem** — a `gen: True` module that itself contains internal `instances` and/or `interconnects` (e.g. a CPU subsystem with its own bus matrix and remap logic).
- **interconnect** — an AHB-Lite bus matrix with an address map. `gen: True` (default) emits the matrix RTL via Arm `BuildBusMatrix.pl`.
- **initiator / target** — a bus master / a subordinate address window in an interconnect. Each initiator carries its own `targets:` visibility list (plus optional `visibility:` per-initiator address aliases).
- **interface** — a typed boundary port: a `wire`, or a bus (`ahb` / `apb` / `axis*` / `swd` / `gpio` / `dbg_ahb`) that expands into per-signal ports.
- **EXCLUDE** — an interface param that drops bus signals from the expansion; selects the AHB-Lite slave vs initiator boundary form (load-bearing for boundary correctness).
- **passthrough** — an interconnect window or initiator that forwards to a module-level AHB interface instead of an instance.
- **subordinate_bus** — a target that hands off to another bus matrix / NoC (an admin / hierarchical window).
- **glue_logic** — typed structural primitives (`passthrough` / `or_reduce` / `or_combine` / `and_gate` / `constant` / `concat`) emitted as `soc_glue` helper-module instances.
- **preset / `uses:`** — a reusable partial-module mixin from `lib/presets/*.yaml`, deep-merged into the module (the module's own keys win over presets).
- **auto_base** — interconnect sugar that auto-packs un-pinned targets from `from` by `align`, with `NN[K|M|G|T]` size shorthand. (Literals only — `$PARAM` is rejected here.)
- **size vs phys_size** — the address-decode **aperture** (address-map footprint) vs the **physical storage** in bytes.
- **chip_boundary** — the tech-independent classification of every inner SoC port as bonded / tied / open: the ASIC seam spec (`lib/chip_boundary/<name>.yaml`).
- **pad_tech** — the tech-constant pad-cell catalogue (`port_map` placeholders, `power_cells`, `supply_connect`, optional `upf:`) in `lib/pad_tech/<tech>.yaml`.
- **pin_map** — a pad-ring variant: GPIO-bus mode (driven by `type: gpio` ifaces) or mixed-I/O mode (`mode: mixed`, explicit `pads:` list) in `lib/pin_map/<tech>_<token>.yaml`.
- **fpga_profile** — the bespoke FPGA board layer (IOBUF triples, port renames, FPGA param overrides, debug taps, features, IP-XACT contract) in `lib/fpga_profile/<name>.yaml`.
- **UPF** — Synopsys Unified Power Format power intent: supply sets + power domains + per-memory `connect_supply_net` lines.
- **discovery table** — a read-only register block encoding the bus topology (magic `0x534F4344` "SOCD") for runtime device discovery.
- **build_info** — a read-only register block carrying build id / timestamp / git hash (magic `0x42494E46` "BINF").
- **config package** — `<top>_soc_config_pkg.sv` (and `.vh`) of `localparam`s; replaces the legacy `gen_defines.v`.
- **flist** — a generated file list (`.flist`) of RTL paths for the simulator / synthesizer; lines reference `$SOCLABS_*` env vars, so `source set_env.sh` first.
- **timescale sweep** — the final generator post-pass that prepends `` `timescale 1ns/1ps `` to any RTL file lacking one (covers externally-rendered interconnect / DMA-250 files).
- **ADP** — Arm Debug Protocol upload: the sim/HW image-load address constants emitted in `<top>_adp.{vh,py}`.
- **soc_model / nanosoc_gen** — the Python generator package, invoked as `python3 -m soc_model`.

---

# 10. Where to go next

**The roadmap design docs** (`nanosoc_arch_tech/docs/roadmap/`) are the deeper design rationale behind each subsystem of the generator — read them when you want the *why*, not just the *how*:

| Doc | Topic |
|---|---|
| `00-overview.md` | Roadmap index & overview of the nanosoc_gen improvement program |
| `01-unit-testing-nanosoc-gen.md` | The pytest unit + golden/snapshot test suite (713 tests, 97% cov) |
| `02-clean-architecture-adapters-backends.md` | Adapter/backend clean-architecture design |
| `03-ci-system-validity-matrix.md` | CI system-validity tests across a parameter range |
| `04-testbench-generation-default-tests.md` | Generated testbenches + a default test set |
| `05-simplified-yaml-format.md` | The simplified YAML format (`uses:` presets, `auto_base`, size shorthand) — implemented in `soc_model/expand.py` (Chapter 4d) |
| `06-web-gui-yaml-builder.md` | Web-app GUI for interactive `sys_desc` YAML authoring |
| `07-toplevel-wrapper-generation-fpga-asic.md` | The tech-dependent top-level wrapper system: chip boundary, pad tech, pin map, FPGA profile, UPF (Chapter 7c) |
| `08-vivado-block-diagram-generation.md` | Vivado block-diagram generation with typed interconnect ports |

> `09-user-guide.md` is the *outline* this guide was built from; it predates the simplified YAML, the doc-07 pad/tech flow, the DMA-250 backend, and the `patch_ahb_to_apb` retirement, so trust **this** guide for current behaviour.

**The package README** — `README.md` at the repo root — is the quick-reference for both `soc_model` (the generator) and `sim_debug` (the post-simulation debug tool), with the artifact table and the per-output directory map.

**The runnable hello-SoC example** referenced throughout sits in `docs/examples/hello_soc/` (a copy of `tests/fixtures/minimal_soc.yaml` + `regions/tiny_region/tiny_region.yaml`). Run the command in [Chapter 3](#3-hello-soc--a-runnable-walkthrough) and read the generated `build_soc/` tree.

**To go deeper into the code itself**, the authoritative readers are: `soc_model/parser.py` (YAML loading + lib-dir scanning), `soc_model/builder.py` (which keys each `_build_*` method consumes — the real schema), `soc_model/model.py` (dataclass types + defaults), `soc_model/expand.py` (the simplified-YAML desugar), `soc_model/__main__.py` (the CLI + backend ordering), and `soc_model/backends/*.py` (one file per artifact).
