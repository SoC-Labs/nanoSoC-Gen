# SoC Configurator

A web GUI (plus headless CLI) for authoring, validating, and building
nanosoc_gen system YAML. It edits the **raw YAML dict** — the exact shape the
parser loads — builds a transient model from it for validation and
diagramming, and drives the project's real `make` flow as a staged, streamed
build pipeline. The same validation that gates the GUI runs headless in CI
(`check`), so GUI-green == CI-green. Design spec: roadmap doc
`06a-soc-configurator-implementation-plan.md` (arch_tech, roadmap branch).

## Architecture — three layers

| Layer | Imports | Files |
|---|---|---|
| 1. Pure-python core (pytest-covered, no web deps) | yaml only | `soc_model/gui_service.py` (lint/validate/diagram/memmap/pack/catalogs/YAML I/O), `soc_model/completeness.py` (tier-3 rules engine), `soc_model/configurator/jobs.py` (asyncio staged runner), `soc_model/configurator/project.py` (`configurator.yaml` adapter) |
| 2. FastAPI shell (optional extra) | fastapi, uvicorn | `soc_model/configurator/app.py` (endpoints + WS hub + DraftStore), `soc_model/configurator/__main__.py` (`serve` / `check` CLI) |
| 3. Frontend (vanilla JS, no frameworks/CDNs) | — | `templates/base.html`, `static/css/app.css`, `static/js/core.js` (panel registry, `App.api`, WS), `static/js/panels/*.js` |

Drafts are stored as raw YAML **text** under `<project>/.configurator/drafts/`
(comments survive until a structural edit rewrites them). Bundled default
completeness rules: `lib/rules/nanosoc_completeness.yaml`.

## Quickstart

```sh
# Option A: install the extra
pip install 'nanosoc-gen[configurator]'      # fastapi>=0.100, uvicorn>=0.20
# Option B: any python >= 3.10 that already has fastapi and uvicorn
# (a python 3.8 without them does NOT work)

# Serve against a project (binds 127.0.0.1 by default)
python -m soc_model.configurator serve --project <repo> [--host H] [--port 8090]
```

### The project adapter — `<repo>/configurator.yaml`

Binds the configurator to a concrete repo. The real multicore project's file
(7 lib dirs):

```yaml
top_yaml: sys_desc/nanosoc_multicore_soc.yaml
lib_dirs:
  - sys_desc
  - nanosoc_arch_tech/sys_desc
  - nanosoc_arch_tech/sys_desc/regions
  - nanosoc_arch_tech/sys_desc/subsystems
  - ethernet-subsystem-ahb/sys_desc
  - ethernet-subsystem-ahb/sys_desc/regions
  - ahb_qspi/sys_desc
```

Optional keys (see `project.py`): `build_dir` (default `build_soc`), `rules`
(completeness rules file), `templates` (draft template dirs), `env_script`
(e.g. `set_env.sh`, sourced before each build stage), and `make:` — per-stage
shell commands for `validate` / `soc` / `firmware`. If the file is absent,
defaults are inferred (`sys_desc/*_soc.yaml` as top, `sys_desc` as lib dir) —
enough to browse/validate read-only. A missing `make.validate` falls back to
`python3 -m soc_model <top> <lib-dirs> --validate-only`.

### Headless check (CI) — no web deps

```sh
python -m soc_model.configurator check sys_desc/nanosoc_multicore_soc.yaml \
    --project .            # or explicit: [--lib-dir DIR ...] [--rules FILE]
```

Prints every tier-1..3 message plus the completeness checklist score; exits 1
on any error-level message (failed error-level rules included), 0 otherwise.
The real multicore top validates clean: `0 error(s), 0 warning(s)`, 8/9
checks (one info-level rule advisory).

## API

| Endpoint | Description |
|---|---|
| `GET /api/project` | `{adapter, health}` (env checks: perl/make/jinja2/systemrdl/ARM_IP_LIBRARY_PATH/top exists) |
| `GET /api/catalog` | `{modules, presets, pad_techs, pin_maps, chip_boundaries, fpga_profiles, rules, templates}` |
| `GET\|POST /api/drafts` | list / create `{name, template?\|from_top?}` → `{id}` |
| `GET /api/draft/{id}` | `{id, yaml}` (raw text) |
| `PUT /api/draft/{id}` `{yaml}` | save text → validation result |
| `GET\|PUT /api/draft/{id}/dict` | parsed dict view / write full dict (server dumps to YAML; comments lost) |
| `POST /api/draft/{id}/validate` | `{ok, errors, warnings, messages[{tier,level,category,instance,port,message,rule?,hint?}], checklist[{id,level,desc,passed,detail,hint}]}` |
| `GET /api/draft/{id}/diagram` | layout-neutral JSON: bus spines, initiator kinds (cpu/debug/dma/other), targets grouped by region_type, validation badges, ghost placeholders, counts |
| `GET /api/draft/{id}/memmap` | per-interconnect sorted address windows + overlap marking + span |
| `GET /api/components` | droppable component-package catalogue: `{components:[{id, module, category, label, desc, window_size, packaged, available, knobs[...]}]}` (see *Component packages*) |
| `POST /api/draft/{id}/component` `{module, component_id?, params?}` | drop a package into the draft (applies knob defaults/overrides, sizes the window); out-of-range knob -> 400 `hard limit: ...` |
| `DELETE /api/draft/{id}/component/{instance}` | remove a dropped block + its targets |
| `POST /api/draft/{id}/pack` | `{interconnect, from, align, repack_all?, apply?}` → auto_base packing, before/after table |
| `GET /api/draft/{id}/export?mode=raw\|canonical` | YAML text (canonical = doc-05 desugared form) |
| `POST /api/draft/{id}/apply` `{dry}` | unified diff vs the project top YAML; write-through when `dry: false` and valid |
| `POST /api/build` `{stages:[validate,soc,firmware]}` | start the staged pipeline (409 if busy) |
| `GET /api/build/status`, `POST /api/build/cancel` | job state / kill the process group |
| `GET /api/artifacts`, `GET /api/artifacts/file?path=` | browse `build_dir` (reports/constraints/docs/discovery/firmware/flist/build_info); text preview (415 otherwise) |
| `WS /ws` | `{type: 'hello'\|'line'\|'stage'\|'job', ...}` — build log lines + stage/job status |

All paths are guarded against escaping the project root; stage commands come
from the adapter file, never from the request.

## Component packages — droppable IP with tweakable knobs

The drag-and-drop palette is built from **component packages**: declarative
YAML that wraps a nanosoc_gen module with a generic label, a category, and a
set of **knobs** (the params worth exposing), each with a default and **hard
min/max limits** so memories can't be shrunk below their firmware floor.
Packages are loaded by `soc_model/components.py` from the bundled
`lib/components/*.yaml` plus every `<lib-dir>/components/`; a package whose
`module` doesn't resolve in the active lib dirs is reported `available: false`,
and any lib module with no package falls back to a limit-free bare entry, so
everything is droppable. The same `check_knob` limits gate the loader, the
`/api/draft/{id}/component` endpoint (HTTP 400 below a hard minimum), and the
UI (dropdowns offer only legal sizes). Bundled packages cover the Cortex-M0+
CPU subsystem, Ethernet subsystem, CPU peripheral subsystem, QSPI flash, IPC
mailbox, PTP hardware clock, reset controller, and DMA controller. Full concept,
schema, knob kinds, availability, and an authoring walkthrough:
[`docs/COMPONENT_PACKAGES.md`](../../docs/COMPONENT_PACKAGES.md).

## Drag & Drop canvas — shape a SoC fast

The **Drag & Drop** panel (`static/js/panels/canvas.js`) frames the design as a
bus topology: a centred AHB bus spine with grey droppable **zones** above (bus
masters) and below (memory & peripherals). Drop a palette tile into a zone — or
onto the bus, which auto-routes by category. **＋ / －** per side add/remove
lanes; placed blocks **drag between zones** to re-file (a drag ghost shows the
target). On top of that:

- **Connector lines** draw each block down to the bus, dotted with its base
  address (SVG overlay, recomputed on render/resize).
- **Inline size knob** — memory blocks carry a size `<select>` offering only
  sizes ≥ the hard minimum; changing it `PUT`s `/api/draft/{id}/component/{inst}`
  which re-tweaks the instance params and resizes the window
  (`GuiService.update_component`, the one model-mutating new endpoint; the
  server still enforces the floor with HTTP 400).
- **Address ribbon** renders `/memmap` to scale under the bus and flags
  overlapping windows in red.
- **Suggested-next chips** turn failed completeness checks into one-click
  "＋ add a memory / debug / peripheral" actions.
- **Right-click menu**: Duplicate / Select / Fine-tune in Compose / Remove.
- **Group into subsystem** — multi-select blocks into a collapsible container
  (a *visual* canvas grouping; it does not restructure the generated RTL).
- **Templates** seed a scratch SoC from a recipe (Minimal / MCU / Dual-core /
  Networked), resolving each step to whatever IP the project actually ships.

Zone layout, group membership and per-instance zone assignment are a
presentation layer persisted per-draft in `localStorage`; the server stays the
sole truth for the model (re-read `/dict` + `/validate` + `/diagram` + `/memmap`
after every change).

## Validation model — three tiers

1. **Structural lint** (`gui_service.structural_lint`) — instant dict-level
   schema checks the builder silently tolerates: missing/duplicate names, bad
   interface types/directions, unknown protocols, unparseable `base:`/`size:`,
   initiator references to unknown targets, unknown presets.
2. **Semantic validation** — the real `SoCValidator` over a transient built
   `Module`; identical results to `--validate-only`.
3. **Completeness rules** (`soc_model/completeness.py`) — declarative,
   per-project policy: "is this a *complete system*?" Returned both as a full
   checklist and as messages for failures. Rules file format:

```yaml
rules:
  - id: cpu-initiator
    level: error                  # error | warning | info
    desc: At least one CPU master drives an interconnect
    require: { initiator_module_matches: "(cpu|cm0|m0plus|eth_ss)" }
    hint: Add a CPU subsystem instance and list it under the initiators
  - id: memory-target
    level: error
    desc: At least one memory target exists
    require: { min_targets: { region_type: memory, count: 1 } }
    hint: Add an SRAM/IMEM region instance with an addressable target window
```

Exactly one predicate per `require:` block. Predicates:
`initiator_module_matches`, `instance_module_matches`, `target_at_address`
(+`region_type`), `target_role`, `external_wires`, `firmware_regions_resolve`,
`apb_targets_have_config`, `min_targets`.

## Testing

Markers (`pyproject.toml`): `unit` / `golden` / `tooling` / `slow`; the
default run is `-m 'not tooling and not slow'`. Layer-1 tests
(`tests/unit/test_gui_service.py`, `test_completeness.py`) are pure python;
`tests/unit/test_configurator.py` covers the adapter/CLI unconditionally and
gates the FastAPI/TestClient tests behind a `needs_fastapi = pytest.mark.skipif(...)`
so the suite passes on hosts without the extra. Current: 54 tests pass.

## Panel status

| Panel | File | Status |
|---|---|---|
| YAML | `panels/yaml.js` | **Implemented** (M4): drafts, raw editor, debounced 3-tier validation, checklist |
| Build | `panels/build.js` | **Implemented**: stage selection, WS-streamed logs, cancel |
| Compose | `panels/compose.js` | Landing now (dict-backed forms) |
| Diagram | `panels/diagram.js` | Landing now (curated SVG from `/diagram`) |
| MemMap | `panels/memmap.js` | Landing now (address ruler + auto-pack) |
| Artifacts | `panels/artifacts.js` | Landing now (build-tree browser) |
