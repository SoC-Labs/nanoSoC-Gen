# nanosoc_gen — NanoSoC Generation and Debug Tooling

This repository contains the Python tooling for the NanoSoC project. It provides two main tools:

1. **soc_model** — SoC generation from YAML system descriptions
2. **sim_debug** — Automated post-simulation debug and analysis

Both tools are pure Python with no heavy external dependencies and follow the same conventions: streaming/generator-based processing, dataclass models, and coloured terminal output.

---

## Tool 1: soc_model — SoC Generator

Parses YAML system descriptions and generates all RTL, firmware configuration, interconnect logic, register maps, and documentation for the NanoSoC.

### Usage

```bash
# From nanosoc_m0_soc/:
make all    # Runs the full generation pipeline

# Or directly:
python3 -m soc_model sys_desc/nanosoc_m0_soc.yaml \
    --lib-dir nanosoc_arch_tech/sys_desc \
    --build-dir build_soc \
    --system-yaml sys_desc/nanosoc_m0_system.yaml
```

### What It Generates

| Output | Directory | Description |
|--------|-----------|-------------|
| Top-level RTL | `build_soc/rtl/nanosoc.sv` | Structural SoC module from YAML instances, connections, glue logic |
| System wrapper | `build_soc/rtl/nanosoc_system.sv` | Core + expansion region wrapper |
| Chip wrapper | `build_soc/rtl/nanosoc_chip.v` | Pad-level wrapper for synthesis |
| CPU subsystem | `build_soc/rtl/nanosoc_ss_cpu.sv` | Auto-generated CPU subsystem (Cortex-M0 + memories + interconnect) |
| AHB interconnect | `build_soc/rtl/nanosoc_ahb_interconnect/` | Bus matrix via ARM BuildBusMatrix |
| Config package | `build_soc/rtl/nanosoc_soc_config_pkg.sv` | SystemVerilog parameter package |
| Memory map header | `build_soc/firmware/nanosoc_memmap.h` | C header with base addresses, sizes, clock frequency |
| Linker scripts | `build_soc/firmware/*.ld` | MEMORY blocks for each firmware profile |
| ADP config | `build_soc/firmware/nanosoc_adp.{vh,py}` | Upload address for testbench and Python tools |
| Register descriptions | `build_soc/rdl/*.rdl` | SystemRDL for all peripherals |
| Register RTL | `build_soc/rtl/nanosoc_*_discovery/` | APB register blocks from PeakRDL-regblock |
| Discovery tables | `build_soc/discovery/` | Auto-discovery YAML for bus topology |
| Build info | `build_soc/rtl/nanosoc_build_info/` | Timestamp and git hash APB peripheral |
| Documentation | `build_soc/docs/nanosoc_design_doc.md` | Design document with parameters, address map, hierarchy |
| Reports | `build_soc/reports/` | Memory map text, connectivity HTML, Python model |

### Architecture

```
soc_model/
├── __main__.py              CLI entry point and pipeline orchestration
├── parser.py                Multi-directory YAML parser with module discovery
├── builder.py               Converts parsed YAML dicts into OO model
├── model.py                 Dataclass model (Module, Instance, Interconnect, etc.)
├── validator.py             Connectivity and parameter validation
├── utils.py                 Shared utilities (param resolution, bit slicing)
└── backends/
    ├── toplevel.py          Top-level structural SV generation (Jinja2)
    ├── subsystem.py         Subsystem SV generation (extends toplevel)
    ├── system.py            System wrapper generation
    ├── chip.py              Chip wrapper with pad ring
    ├── ahb.py               AHB bus matrix (XML → BuildBusMatrix → SV wrapper)
    ├── soc_config_pkg.py    SystemVerilog config package
    ├── firmware.py           Linker scripts, C headers, ADP config
    ├── rdl.py               SystemRDL register descriptions + PeakRDL RTL
    ├── discovery.py         Bus topology auto-discovery tables
    ├── build_info.py        Build timestamp/git hash peripheral
    ├── docs.py              Markdown design documentation
    ├── html.py              Interactive connectivity visualisation
    ├── text.py              Text memory map and hierarchy
    ├── python.py            Python model export
    ├── lint.py              slang-based RTL lint
    ├── protocol_utils.py    AHB/AXIS/SWD/GPIO signal definitions
    └── templates/           Jinja2 templates for all backends
```

### YAML System Description

The YAML files are the single source of truth. They define:

- **Modules** — parameters, interfaces (wire, AHB, AXIS, SWD, GPIO), instances
- **Interconnects** — initiators, targets, address map, remap rules, APB bridges
- **Register maps** — fields, access types, reset values
- **Firmware config** — linker profiles, ADP addresses, boot configuration
- **Glue logic** — passthrough, OR-reduce, OR-combine, AND-gate, constant drivers

Example instance declaration:
```yaml
instances:
  - instance_name: u_ss_cpu
    module: nanosoc_ss_cpu
    params: { NUMIRQ: $NUMIRQ, BOOTROM_ADDR_W: $BOOTROM_ADDR_W }
    connections:
      - { port: sys_fclk, conn: u_ss_systemctrl.SYS_FCLK }
      - { port: cpu_0, conn: nanosoc_ahb_interconnect.cpu_0 }
```

### Module Discovery

The parser scans for YAML module definitions in:
- `lib_dir/subsystems/*/` — subsystem modules (cpu, debug, systemctrl, etc.)
- `lib_dir/regions/*/` — memory region modules (bootrom, imem, dmem, etc.)
- `lib_dir/../rtl/*/sys_desc/` — IP-local descriptions (slcorem0, slcorem0p)
- `lib_dir/modules/` — legacy flat layout

Multiple `--lib-dir` paths can be specified for multi-repo projects.

---

## Tool 2: sim_debug — Simulation Debug Analyser

Automated post-simulation analysis that parses TARMAC execution traces, ADP logs, firmware ELF binaries, and VCD waveforms to diagnose common simulation failures.

### Usage

```bash
# From nanosoc_arch_tech/:
PYTHONPATH=nanosoc_gen python3 -m sim_debug ../../simulate/sim/hello

# Verbose with firmware section layout
PYTHONPATH=nanosoc_gen python3 -m sim_debug ../../simulate/sim/hello --verbose

# With VCD waveform analysis
PYTHONPATH=nanosoc_gen python3 -m sim_debug ../../simulate/sim/hello \
    --vcd ../../simulate/sim/hello/waves.vcd

# Markdown output for CI reports
PYTHONPATH=nanosoc_gen python3 -m sim_debug ../../simulate/sim/hello --markdown
```

### What It Detects

| Category | Checks |
|----------|--------|
| **CPU Faults** | HardFault, data/alignment abort, lockup, NMI — with faulting PC, instruction, accessed address, register dump |
| **Hangs** | Branch-to-self loops (expected vs unexpected), tight polling loops, no-progress detection |
| **Memory** | Write to ROM, unmapped access, initial SP in wrong region |
| **Firmware** | Vector table validation (SP, reset handler), section layout (.text/.data/.bss addresses) |
| **UART/ADP** | Garbled output, missing boot banner, missing REMAP, baud rate calculation from BAUDDIV writes |
| **VCD Bus** | AHB bus errors (HRESP=1), signal extraction around fault events |

### Example Output (Passing Test)

```
============================================================
  NanoSoC Simulation Debug Report
  Test: hello  |  Dir: simulate/sim/hello
============================================================

--- Simulation Outcome ---
  Banner: SoCLabs NanoSoC'25 ARM-CM0+ADP+EXTIO-DMA 20250412
  Remap: REMAP->IMEM0
  Sim time: 186515 ns
  Firmware output:
    Hello world

--- Findings ---
  [INFO] Initial SP: 0x1800FC00 (DMEM_0 + 0xFC00)
  [INFO] Reset handler: 0x00000189 (Reset_Handler)
  [INFO] UART2 BAUDDIV=2604 (baud rate ~38402 at 100 MHz)
  [INFO] ** TEST PASSED **

------------------------------------------------------------
  RESULT: PASS
```

### Example Output (HardFault)

```
--- Findings ---
  [FATAL] HARDFAULT_REQ at 375 ns
    Faulting PC: Reset_Handler+0xE (0x00000196)
    Instruction: STR r0,[r2,r3]
    Write to: 0x100004C4 (IMEM_0 + 0x4C4)
    CAUSE: Write to read-only region IMEM_0
    Last instructions before fault:
           315 ns  IT 0x00000192  SUBS r3,#4
           325 ns  IT 0x00000194  LDR  r0,[r1,r3]
           345 ns  IT 0x00000196  STR  r0,[r2,r3]

------------------------------------------------------------
  RESULT: FAIL (1 fatal, 1 error, 0 warning)
```

### Architecture

```
sim_debug/
├── __main__.py              CLI entry point
├── config.py                Memory map, UART addresses, vector table offsets
├── sim_finder.py            Auto-discover simulation artifacts
├── report.py                Coloured text + markdown report formatter
├── parsers/
│   ├── tarmac.py            Streaming TARMAC trace parser (generator-based)
│   ├── adp_log.py           ADP/simulation log parser
│   ├── elf_symbols.py       ELF symbol resolution via arm-none-eabi-nm
│   └── vcd.py               Lightweight streaming VCD parser with signal filtering
└── analyzers/
    ├── fault_detector.py    HardFault, NMI, data abort detection
    ├── hang_detector.py     Branch-to-self, tight loop detection
    ├── memory_checker.py    ROM writes, unmapped access, SP/vector table validation
    └── uart_checker.py      Baud rate analysis, garbled output detection
```

### Auto-Detection

The tool auto-discovers simulation artifacts from the standard directory layout:

| Artifact | Location |
|----------|----------|
| TARMAC trace | `<sim_dir>/logs/tarmac0.log` |
| Simulation log | `<sim_dir>/logs/run_<testname>.log` |
| ADP capture | `<sim_dir>/logs/extio_adp_out.log` |
| VCD waveform | `<sim_dir>/waves.vcd` |
| Firmware ELF | `<project>/build/firmware/<testname>/compile/<testname>.o` |
| Memory map | `<project>/nanosoc_m0_soc/build_soc/firmware/nanosoc_memmap.h` |

### CLI Options

| Option | Description |
|--------|-------------|
| `--tarmac PATH` | Override TARMAC file |
| `--log PATH` | Override simulation log |
| `--vcd PATH` | Override VCD file |
| `--elf PATH` | Override ELF file |
| `--memmap PATH` | Override memory map header |
| `--context N` | Instructions before faults (default: 20) |
| `--no-color` | Disable ANSI colours |
| `--markdown` | Markdown output |
| `--verbose` | Show all details (sections, execution summary) |
| `--vcd-signals PAT` | Comma-separated signal patterns (e.g., `"*HADDR*,*lockup*"`) |
| `--quick` | Skip VCD analysis |

### CI Integration

Returns exit code 0 on pass, 1 on failure:

```yaml
test:
  script:
    - make run_vcs TESTNAME=hello
    - cd nanosoc_m0_soc/nanosoc_arch_tech
    - PYTHONPATH=nanosoc_gen python3 -m sim_debug ../../simulate/sim/hello --no-color
```

---

## Dependencies

| Package | Required By | Purpose |
|---------|-------------|---------|
| `jinja2` | soc_model | Template rendering for RTL/firmware generation |
| `pyyaml` | soc_model | YAML system description parsing |
| `systemrdl-compiler` | soc_model | Register description compilation |
| `peakrdl-regblock` | soc_model | APB register block RTL generation |
| *(none)* | sim_debug | Uses only Python stdlib + ARM toolchain |

## License

Licensed under the **Apache License 2.0** ([`LICENSE`](LICENSE)).
Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton.
[`NOTICE`](NOTICE) says what is not included: no Arm IP (the CMSDK components, BuildBusMatrix.pl and the
DMA-250 generator are Arm's, referenced by name and by path only), and no foundry pad data (that lives in
pad packs, loaded with `--pad-tech-dir`). Files the generator writes from your descriptions are your work.
