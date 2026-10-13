# NanoSoC Simulation Debug Tool

Automated post-simulation analysis for NanoSoC Cortex-M0 firmware tests. Parses TARMAC execution traces, ADP logs, firmware ELF binaries, and VCD waveforms to diagnose common failures.

## Quick Start

```bash
# From the nanosoc_arch_tech directory:
cd nanosoc_m0_soc/nanosoc_arch_tech

# Analyse a simulation that just ran
PYTHONPATH=nanosoc_gen python3 -m sim_debug ../../simulate/sim/hello

# Verbose output with firmware section layout
PYTHONPATH=nanosoc_gen python3 -m sim_debug ../../simulate/sim/hello --verbose

# Analyse with VCD waveform (must run sim with VCD_SIM=yes first)
PYTHONPATH=nanosoc_gen python3 -m sim_debug ../../simulate/sim/hello --vcd ../../simulate/sim/hello/waves.vcd

# Output as markdown (for CI reports or issue filing)
PYTHONPATH=nanosoc_gen python3 -m sim_debug ../../simulate/sim/hello --markdown > debug_report.md
```

## What It Detects

### CPU Faults
- **HardFault** — identifies the faulting instruction, accessed address, and memory region
- **Data/Alignment Abort** — shows what memory access caused the fault
- **Lockup** — CPU entered lockup state (double fault)
- **NMI/BusFault** — other exception events

### Simulation Hangs
- **Branch-to-self loops** — distinguishes expected end-of-test from unexpected hangs
- **Tight polling loops** — detects CPU stuck in a small instruction range
- **No progress** — no new unique PC addresses being executed

### Memory Issues
- **Write to ROM** — firmware trying to write to read-only memory (e.g., IMEM with `RAM_PRELOAD`)
- **Unmapped access** — address doesn't fall in any known memory region
- **Wrong stack pointer** — initial SP not in writable RAM

### Firmware Layout
- **Vector table validation** — checks initial SP and reset handler point to valid regions
- **Section layout** — shows `.text`, `.data`, `.bss` addresses and sizes
- **.data in wrong region** — catches linker misconfiguration

### UART/ADP Output
- **Garbled output** — detects non-printable characters (baud rate mismatch)
- **Missing boot banner** — bootloader didn't start
- **Missing REMAP** — bootloader started but didn't hand off to firmware
- **Baud rate calculation** — reads BAUDDIV register writes from TARMAC and computes actual baud rate

### VCD Bus Analysis (when available)
- **AHB bus errors** — HRESP=1 responses
- **Signal extraction** — filtered signal values around fault events

## Example Output

### Passing Test
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

### Failing Test (HardFault — Write to ROM)
```
============================================================
  NanoSoC Simulation Debug Report
  Test: hello  |  Dir: simulate/sim/hello
============================================================

--- Findings ---
  [FATAL] HARDFAULT_REQ at 375 ns
    Time: 375 ns
    Faulting PC: Reset_Handler+0xE (0x00000196)
    Instruction: STR r0,[r2,r3]
    Write to: 0x100004C4 (IMEM_0 + 0x4C4)
    CAUSE: Write to read-only region IMEM_0
    Registers: r0=0x000000C1  r1=0x00001EAC  r2=0x10000000  r3=0x000004C4
    Last instructions before fault:
           315 ns  IT 0x00000192  SUBS r3,#4
           325 ns  IT 0x00000194  LDR  r0,[r1,r3]
           345 ns  IT 0x00000196  STR  r0,[r2,r3]

  [ERROR] Write to read-only memory (1 occurrences)
    First write: 0x100004C4 (IMEM_0) at 375 ns

------------------------------------------------------------
  RESULT: FAIL (1 fatal, 1 error, 0 warning)
```

## Command Reference

```
python3 -m sim_debug <sim_dir> [options]
```

### Positional Arguments

| Argument | Description |
|----------|-------------|
| `sim_dir` | Simulation output directory (e.g., `simulate/sim/hello`) |

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--tarmac PATH` | auto | Override TARMAC trace file path |
| `--log PATH` | auto | Override simulation log file path |
| `--vcd PATH` | auto | Override VCD waveform file path |
| `--elf PATH` | auto | Override firmware ELF file path |
| `--memmap PATH` | auto | Override memory map header (`.h`) path |
| `--context N` | 20 | Number of instructions to show before faults |
| `--no-color` | | Disable ANSI colour output |
| `--markdown` | | Output as markdown |
| `--verbose` | | Show all analysis details |
| `--vcd-signals PAT` | `*haddr*,...` | Comma-separated VCD signal filter patterns |
| `--quick` | | Skip VCD analysis (faster for large sims) |

### Auto-Detection

The tool auto-discovers simulation artifacts based on the standard directory layout:

| Artifact | Expected Location |
|----------|-------------------|
| TARMAC trace | `<sim_dir>/logs/tarmac0.log` |
| Simulation log | `<sim_dir>/logs/run_<testname>.log` |
| ADP capture | `<sim_dir>/logs/extio_adp_out.log` |
| VCD waveform | `<sim_dir>/waves.vcd` |
| Firmware ELF | `<project>/build/firmware/<testname>/compile/<testname>.o` |
| Memory map | `<project>/nanosoc_m0_soc/build_soc/firmware/nanosoc_memmap.h` |

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | No errors or warnings only |
| 1 | Fatal or error findings detected |

## Generating VCD Waveforms

VCD dumps are disabled by default for performance. To enable:

```bash
# Run simulation with VCD enabled
make run_vcs TESTNAME=hello VCD_SIM=yes

# Then analyse with VCD
PYTHONPATH=nanosoc_gen python3 -m sim_debug ../../simulate/sim/hello
```

The VCD parser streams the file and only tracks filtered signals, so it handles large files efficiently. Default signals tracked:
- `*haddr*` — AHB address bus
- `*htrans*` — AHB transfer type
- `*hresp*` — AHB response (bus errors)
- `*lockup*` — CPU lockup signal
- `*sleeping*` — CPU sleep status

### Custom Signal Filtering

```bash
# Track specific signals
PYTHONPATH=nanosoc_gen python3 -m sim_debug ../../simulate/sim/hello \
  --vcd-signals "*HADDR*,*HWRITE*,*cpu_0_irq*"
```

## Integration with CI

The tool returns exit code 1 on failure, making it suitable for CI pipelines:

```yaml
test_hello:
  script:
    - make run_vcs TESTNAME=hello
    - cd nanosoc_m0_soc/nanosoc_arch_tech && PYTHONPATH=nanosoc_gen python3 -m sim_debug ../../simulate/sim/hello --no-color
```

For CI artifact reports:

```yaml
  script:
    - PYTHONPATH=nanosoc_gen python3 -m sim_debug ../../simulate/sim/hello --markdown > debug_report.md
  artifacts:
    paths:
      - debug_report.md
```

## Architecture

```
sim_debug/
├── __main__.py          # CLI entry point
├── config.py            # Memory map, UART addresses, vector table offsets
├── sim_finder.py        # Auto-discover simulation artifacts
├── report.py            # Coloured text + markdown report formatter
├── parsers/
│   ├── tarmac.py        # Streaming TARMAC trace parser (generator-based)
│   ├── adp_log.py       # ADP/simulation log parser
│   ├── elf_symbols.py   # ELF symbol resolution via arm-none-eabi-nm
│   └── vcd.py           # Lightweight streaming VCD parser
└── analyzers/
    ├── fault_detector.py    # HardFault, NMI, data abort detection
    ├── hang_detector.py     # Branch-to-self, tight loop detection
    ├── memory_checker.py    # ROM writes, unmapped access, SP validation
    └── uart_checker.py      # Baud rate analysis, garbled output detection
```

### Design Principles

- **Streaming parsers** — TARMAC and VCD files are parsed as generators, never loaded fully into memory. Handles 100MB+ files efficiently.
- **No external dependencies** — uses only Python stdlib and the ARM toolchain (`arm-none-eabi-nm`). Degrades gracefully if the toolchain is unavailable (no symbol names, but analysis still works).
- **Online analysis** — fault and memory checkers process events as they arrive, maintaining only a rolling context window.
- **Separation of concerns** — parsers produce structured events, analyzers consume them and produce findings, the report formatter renders them.
