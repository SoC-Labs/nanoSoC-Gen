# Component Packages

A **component package** turns a nanosoc_gen module into a *droppable IP block*:
a generically named, pre-described piece of IP that a user can drag into a SoC
and tweak through a small set of safe **knobs**. It is the foundation for the
configurator's drag-and-drop composition flow (doc 06a) — the catalogue of
"Cortex-M0+ CPU Subsystem", "Ethernet Subsystem", "SRAM Block", "DMA
Controller" etc. that appears in the component palette.

Packages are pure declarative YAML, loaded by `soc_model/components.py`. This
document explains the concept, the YAML schema, how *availability* works, how
to author a package for a new IP, and how the configurator and drag-and-drop
consume packages.

## Why packages exist

A bare nanosoc_gen module exposes *all* of its params as raw integers with no
guidance — easy to mis-size a memory below what its firmware needs, or to leave
a cryptic `IMEM_RAM_ADDR_W` unexplained. A package fixes that by adding the
metadata a non-expert needs to drop the block in safely:

- a **generic, friendly label** ("Cortex-M0+ CPU Subsystem") instead of the RTL
  module name (`nanosoc_ss_cpu_plus`);
- a **category** so the palette can group blocks (CPU / memory / subsystem /
  peripheral / DMA / debug);
- a curated set of **knobs** — only the params worth tweaking — each with a
  human label, description, unit, default, and **hard min/max limits**.

The hard limits are the point: a memory knob can be made bigger but never
shrunk below the floor its firmware requires. The same limits are enforced in
the loader (`check_knob`), in the drop endpoint (HTTP 400), and surfaced to the
UI so dropdowns only offer legal sizes and number inputs carry `min`/`max`.

## The YAML schema

A package file lives in a `components/` directory (see *Availability* below) and
declares exactly one `component:` mapping:

```yaml
component:
  id: ethernet_subsystem            # unique catalogue id (slug)
  module: ethernet_ss_ahb_rmii      # the underlying nanosoc_gen module
  category: subsystem               # subsystem|cpu|memory|peripheral|debug|dma|other
  label: "Ethernet Subsystem (RMII + PTP)"   # generic, friendly name
  desc: "Cortex-M0+ core, MAC + HA1588 PTP, RMII PHY boundary."
  window_size: 512M                 # default address aperture (optional)
  knobs:
    - param: IMEM_RAM_ADDR_W        # the module param this knob drives
      label: "Instruction memory"
      kind: addr_w                  # addr_w | bytes | int | enum
      default: 16
      min: 15                       # HARD minimum (32 KB) — cannot go below
      max: 18                       # HARD maximum
      desc: "CPU0 IMEM; the udp_echo app needs ~47 KB."
      unit: ""                      # optional display unit
      options: []                   # enum/bytes choices (see below)
```

Fields map 1:1 onto `ComponentSpec` and `Knob` in `soc_model/components.py`. A
file is silently ignored if it lacks `component.id` or `component.module`; an
unknown `category` falls back to `other`.

### Knob kinds

| `kind`   | Meaning | UI rendering |
|----------|---------|--------------|
| `addr_w` | The param is an **address width N**; the size it controls is `2**N` bytes. | A size dropdown of `min..max`, each labelled in KB/MB (e.g. `15 -> 32 KB`). |
| `bytes`  | A raw byte capacity chosen from `options`. | A dropdown of the listed sizes, human-labelled. |
| `int`    | A plain integer (channel count, slot depth, pulse length). | A number input with `min`/`max`. |
| `enum`   | One of a fixed list in `options`. | A dropdown of the choices. |

`addr_w` is the workhorse for memories: it keeps the param a *width* (what the
RTL wants) while the UI talks in human sizes. `Knob.size_choices()` enumerates
`min..max` as `{value, bytes, label}` so the dropdown only ever offers legal
sizes — values below `min` simply never appear.

Examples from the bundled catalogue:

```yaml
# addr_w — a sizable memory with a hard 8 KB floor
- { param: DMEM_RAM_ADDR_W, label: "Data memory", kind: addr_w,
    default: 14, min: 13, max: 16, desc: "CPU DMEM (stack/heap/BSS)." }

# bytes — a fixed menu of cache capacities
- { param: CACHE_SIZE, label: "Read cache", kind: bytes,
    default: 4096, options: [2048, 4096, 8192, 16384] }

# int — a count with a hard floor of 1
- { param: CHANNEL_NUM, label: "DMA channels", kind: int,
    default: 4, min: 1, max: 8 }

# enum — pick one of two timing modes
- { param: REGISTER_WDATA, label: "Latch write data", kind: enum,
    default: 0, options: [0, 1] }
```

### Hard limits

`check_knob(knob, value)` enforces the limits numerically:

- below `min` -> rejected; for `addr_w` the message is sized-aware, e.g.
  `IMEM_RAM_ADDR_W = 14 (16 KB) is below the minimum 15 (32 KB)`;
- above `max` -> rejected with `... exceeds the maximum ...`;
- a non-numeric value (e.g. a `$PARAM` reference) passes the knob check and is
  left to the semantic validator;
- a knob with neither `min` nor `max` is always accepted.

When a package is dropped via the API, an out-of-range knob is rejected with
HTTP 400 `hard limit: <param> = .. is below the minimum ..`, so the constraint
holds even if a client bypasses the UI.

## Availability — packages light up only where the RTL exists

A package merely *references* a module by name; the module's RTL must resolve in
the active lib dirs for the block to be buildable. The configurator therefore
reports each package with an `available` flag:

- `available: true`  — the package's `module` resolves in the project's lib
  dirs (the catalogue can build it).
- `available: false` — the module is not present in this project; the block is
  shown greyed-out / non-droppable.

This is why the bundled definitions for real nanosoc modules (Ethernet, QSPI,
DMA, IPC mailbox, PHC, reset controller, CPU peripheral subsystem) light up
only when the configurator is pointed at a project that ships that RTL (the
nanosoc-multicore superproject), and appear unavailable against a bare scratch
project. Conversely, any lib module that has **no** authored package still
appears in the palette via a `bare_component()` fallback — its integer params
become limit-free knobs and its category is guessed from the module name — so
everything in the lib is droppable, packaged or not.

## Authoring a package for a new IP — worked example

Goal: make the IPC mailbox (`ipc_mbx_ahb`) droppable. Steps:

1. **Read the module YAML** to get the real param names and what they size.
   `ipc_mbx_ahb` exposes `SLOT_DEPTH` (words per mailbox slot, default 4) and
   `APB_ADDR_W` (register aperture width, default 12 = 4 KB).
2. **Pick a generic label + category.** It is a CPU-to-CPU message block, so
   `label: "IPC Mailbox"`, `category: peripheral`.
3. **Expose the size-bearing params as knobs with sane floors.** `SLOT_DEPTH`
   needs at least one word (`min: 1`); the register aperture is 4 KB minimum
   (`min: 12`, an `addr_w` knob).
4. **Drop the file in `lib/components/`** (bundled) or `<lib-dir>/components/`
   (project-local).

The resulting `lib/components/ipc_mailbox.yaml`:

```yaml
component:
  id: ipc_mailbox
  module: ipc_mbx_ahb
  category: peripheral
  label: "IPC Mailbox"
  desc: "Inter-processor communication mailbox: two message slots with per-CPU
    interrupts, on an AHB slave port."
  window_size: 4K
  knobs:
    - { param: SLOT_DEPTH, label: "Words per slot", kind: int,
        default: 4, min: 1, max: 16,
        desc: "Data words carried per mailbox slot (each direction)." }
    - { param: APB_ADDR_W, label: "Register aperture", kind: addr_w,
        default: 12, min: 12, max: 14,
        desc: "APB register address width; 4 KB (12-bit) default." }
```

Only package params you can *verify* against the module YAML — an unknown param
would just be applied blindly and could break the build. Where a param is not a
size (e.g. the PHC's `DEFAULT_NS_INCR` ns-per-cycle), expose it without
min/max so any positive value is legal.

### Bundled catalogue

The packages shipped in `lib/components/` (all reference real nanosoc modules):

| id | module | category | label |
|----|--------|----------|-------|
| `cpu_subsystem_m0plus` | `nanosoc_ss_cpu_plus` | cpu | Cortex-M0+ CPU Subsystem |
| `ethernet_subsystem` | `ethernet_ss_ahb_rmii` | subsystem | Ethernet Subsystem (RMII + PTP) |
| `cpu_peripheral_subsystem` | `cc_periph_subsystem` | subsystem | CPU Peripheral Subsystem |
| `qspi_flash_controller` | `qspi_flash_ahb` | peripheral | QSPI Flash Controller (XiP + cache) |
| `ipc_mailbox` | `ipc_mbx_ahb` | peripheral | IPC Mailbox |
| `ptp_hardware_clock` | `phc_ahb` | peripheral | PTP Hardware Clock |
| `reset_controller` | `nanosoc_reset_ctrl` | peripheral | Reset Controller |
| `dma_controller` | `dma_250_ahb` | dma | DMA Controller |

## How the configurator + drag-and-drop consume packages

- `GET /api/components` returns the catalogue (packaged + bare-fallback),
  grouped by category, each with its `available` flag and serialised knobs
  (`to_dict()` adds `choices` for addr_w/bytes/enum knobs and `default_bytes`
  for addr_w). The drag-and-drop palette renders one tile per available
  package; the knob list drives the per-block tweak form.
- `POST /api/draft/{id}/component {module, component_id, params}` drops the
  block into a draft: it instantiates the module, applies the package defaults,
  overlays any `params` knob overrides (re-checking every limit), and sizes the
  target window from the chosen size. Out-of-range knobs -> HTTP 400.
- `DELETE /api/draft/{id}/component/{instance}` removes the block and its
  targets.

Because the same `check_knob` limits gate the loader, the API, and the UI, a
memory dropped through any path can never go below its firmware floor — the
hard-limit guarantee that makes generic, droppable IP safe for non-experts.
