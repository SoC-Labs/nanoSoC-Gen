# SoC Configurator — feature brainstorm

## Shipped (the safety-net slice)

- **#16 Unresolved-`$PARAM` linter** — `GuiService.param_lint`, tier-1, wired into
  `validate()`. Flags `$NAME` typos that survive into RTL as literal text + the
  `auto_base` literals-only rule.
- **#17 Lib-dir shadowing auditor** — `SoCParser.module_shadows` +
  `GuiService.lib_shadow_report`; in `catalogs()` and `/api/project` (`lib_shadows`).
- **#13 Pre-commit hook** — `scripts/hooks/pre-commit` (+ `install.sh`) runs the
  headless `check` on staged top/lib YAML, blocking on error-level findings.
- **#3 Diff-vs-built staleness** — `GuiService.record_build_manifest` /
  `build_freshness`; the app stamps a manifest after a successful `soc` build and
  `/api/project` reports `build_fresh` (fresh/stale/unknown), surfaced as a
  sidebar-footer chip. Catches the lib-dir region edit the top-mtime auto-regen
  misses.

## Shipped (the power tools)

- **#20 Checklist fix-it quick actions** — `GuiService.fix_rule` +
  `fixable` checklist flag + an "apply fix" button (builder); deterministic
  dict mutations for `external_wires` (clock/reset) and `target_role`.
  `POST /api/draft/{id}/fix`.
- **#9 PARAM_OVERRIDES panel** — `params_info` / `override_flags` (injection-safe)
  / `apply_overrides_to_cmd`; Build panel editor; `GET .../params`,
  `/api/build {overrides}` with a `{overrides}` slot in the make command.
- **#1 IRQ-map editor (read-only)** — `GuiService.irq_map` traces `concat` glue
  to its NVIC consumer; new IRQ Map panel renders the bit→signal matrix and
  flags unconsumed outputs. `GET .../irqmap`. (Editing layer = phase 2.)

## Shipped (export / share / reuse)

- **#15 Artifact zip** — `GET /api/artifacts/zip` streams the curated artifact
  dirs; "⬇ Download .zip" in the Artifacts panel.
- **#12 Save-as-template** — `POST /api/draft/{id}/save-as-template` promotes a
  draft into the adapter's templates dir (path-guarded, 409 on clash); builder
  footer button; reusable via `New from template`.
- **#7 Snapshots + shareable JSON** — `POST /api/draft/{id}/snapshot` (named
  copy) + `GET /api/draft/{id}/share` (single-blob yaml+validation+adapter);
  builder footer buttons (Share JSON copies to clipboard).

- **#5 Deep-dive D3 iframe** — `GuiService.deepdive_html` renders the real
  SoCVisualizer connectivity page from the built draft; `GET /api/draft/{id}/deepdive`
  + an “Open deep-dive ↗” link in the Diagram panel (the always-correct fallback).

- **#14 Multi-draft A/B compare** — `GET /api/draft/{a}/compare?other={b}`
  (unified YAML diff + validation deltas); new Compare panel with two draft
  pickers + colour-coded diff.

Remaining next: #2 register-map table editor; #4 field tooltips; #6
import-from-discovery; #8 undo/redo; #10 rules editor; #11 sim/fpga stages;
#19/#21 lints.

Grounded in the current code (`soc_model/gui_service.py`,
`soc_model/completeness.py`, `soc_model/configurator/{app,jobs,project}.py`),
the doc-06a plan (§8 brainstorm, §10 risks), and the USER_GUIDE footguns
(§4b field reference, §8 troubleshooting). Effort: S = a day-ish, M = a few
days, L = a week+. Pri H = detailed below.

## Idea table

| # | Idea | What it does | Why it matters here | Builds on | Effort | Pri |
|---|---|---|---|---|---|---|
| 1 | IRQ-map editor | Matrix UI of IRQ sources → NVIC lines; emits/edits the `glue_logic: concat` blocks | The documented #1 hand-editing pain: the cpu1-periph subsystem shipped with IRQs reaching the block boundary but **not** the NVIC because the concat glue was never written. Per-bit `{bit, signal}` concat YAML is exactly a matrix | `glue_logic` schema (4b), `PUT /api/draft/{id}/dict`, structural_lint | M–L | H |
| 2 | Register-map table editor | Spreadsheet editor for `register_maps/*.yaml` (offset/width/access/fields) feeding rdl/discovery | Real maps exist today (`sys_desc/register_maps/evt_route_ctrl.yaml`, `nanosoc_spi_pl022.yaml`); the `description:`-not-`desc:` trap (4b) is invisible in a text editor, trivially enforced in a form | `register_map:` target key, rdl/discovery backends, `_safe_path` YAML I/O | M | M |
| 3 | Diff-vs-built + staleness banner | Records the YAML (hash + text) that produced the current `build_soc/`; banner goes amber when draft/top differs, with a one-click diff | `set_env.sh` auto-regen checks only the **top** YAML's mtime — editing a lib-dir region YAML doesn't retrigger it (guide §8). "Did I regenerate?" is a daily question | `/api/draft/{id}/apply` diff machinery, `/api/artifacts` (build_info dir), jobs.py stage hooks | S–M | H |
| 4 | USER_GUIDE-anchored field tooltips | Every Compose form field gets a `?` linking to its 4b anchor + a one-line extract | The guide is already code-accurate and anchor-rich (`#4b-field-reference`); EXCLUDE, `gen:` opposite defaults, `size` vs `phys_size` are exactly the fields people get wrong | docs/USER_GUIDE.md anchors; a small generated field→anchor JSON | S | H |
| 5 | Deep-dive D3 iframe | "Open in deep-dive" renders `SoCVisualizer.generate_html()` for the draft model into an iframe | The curated SVG is heuristic (kind regexes in `gui_service._KIND_RULES`); doc 06a §10.4 names the D3 view the always-correct fallback. The generator code already exists (`backends/html.py`) and `/api/artifacts/file` already serves `.html` | `SoCVisualizer.generate_html`, `build_model()`, a `GET /api/draft/{id}/deepdive` endpoint | S–M | M |
| 6 | Import-from-discovery | Reverse-engineer a starter config from a BUILT SoC's discovery YAML (bus topology table) | Lets a user adopt an existing bitstream/chip into the configurator without the original sources; discovery already encodes initiators/targets/bases ("SOCD" table) | discovery backend output in `build_soc/discovery/`, `DraftStore.create` | M | M |
| 7 | Config snapshots + shareable JSON | Named point-in-time copies of a draft + a single-blob JSON export (yaml + validation + adapter info) for issue reports | Drafts are already plain files under `.configurator/drafts/` — snapshots are a copy + metadata; the JSON blob makes "it fails for me" reproducible | DraftStore, `svc.validate`, `adapter.to_dict()` | S | M |
| 8 | Undo/redo command stack | Client-side stack of dict mutations with inverse ops | Cheap *only because* every structural edit already flows through one path (`PUT /api/draft/{id}/dict`); doc 06a flagged it post-MVP for exactly this reason | core.js single mutation path, `/dict` round-trip | M | M |
| 9 | PARAM_OVERRIDES panel | Edit top `params:` + a `--config-override` list that lands in the build stage's make command | The guide's 5d/§8 trap: overrides change **memmap/linker numbers, not RTL storage sizes** — the panel must say so inline. Today this lives only in shell history | `adapter.make` templating, jobs.py `Stage.cmd`, params schema (4b) | M | H |
| 10 | Completeness-rules editor + rule packs | Table UI over the rules YAML; per-project packs selectable in the adapter | The engine is deliberately declarative (`load_rules` validates the predicate vocabulary, so a form can enumerate it); projects already override via the adapter `rules:` key | `completeness.load_rules`/`_PREDICATES`, `/api/catalog` rules list | M | M |
| 11 | Sim-smoke / FPGA pipeline stages | Extend `_STAGE_ORDER` with `sim` and `fpga` stages; board picker backed by a board-farm service | Doc 06a §6.2 stages 6–7; jobs.py stages are already arbitrary shell + env_script, so cocotb smoke is config-only. FPGA needs care: programming through a board-farm plugin was seen to do nothing on one bench board (use hw_manager) | jobs.py `Stage`, adapter `make:` keys, the board-farm service | M (sim) / L (fpga) | M |
| 12 | Template save-as / authoring | "Save draft as template" into an adapter `templates:` dir; templates gain name/desc front-matter | Template plumbing exists (`/api/catalog` templates, `POST /api/drafts {template}` path-guarded); authoring is the missing half of the loop | catalogs(template_dirs), DraftStore | S | M |
| 13 | Pre-commit hook wrapping `check` | Ship a `pre-commit` hook (or plain git hook) running `python -m soc_model.configurator check <top> --project .` on staged sys_desc YAML | `check` already exits 1 on errors incl. failed error-level completeness rules and needs **no web deps** — CI parity for free, before the 120 s BuildBusMatrix step | `__main__._cmd_check`, project adapter | S | H |
| 14 | Multi-draft A/B compare | Pick two drafts; side-by-side memmap ruler, diagram, validation counts, unified YAML diff | All the read-side endpoints are draft-parametrised already (`/memmap`, `/diagram`, `/validate`) — the compare view is pure frontend composition | existing per-draft endpoints, difflib (server) | M | M |
| 15 | Artifact zip download | `GET /api/artifacts/zip` streaming the curated artifact dirs | The browser lists up to 500 files but hand-collecting flists/reports/constraints for a colleague is tedious; doc 06a M8 lists "artifacts browser **+ zip**" | `_ARTIFACT_DIRS` walk in app.py, `zipfile` stdlib | S | M |
| 16 | Unresolved-`$PARAM` linter (new) | Tier-1 lint walking every string value for `$REFS` that don't resolve against effective params | The nastiest silent failure in the guide (§4c/§8): a `$PARAM` typo returns the literal string unchanged and **survives into the RTL as text**. A dict walk + the existing resolver makes it a red squiggle instead | `structural_lint`, `utils.resolve_param_ref`, expand_preview | S | H |
| 17 | Lib-dir shadowing auditor (new) | Catalog-time report of duplicate `module.name`s / filename stems across lib dirs, with which file wins | Discovery is **last-writer-wins with no warning** (guide §4c) — 7 lib dirs in the real adapter make silent shadowing plausible; the parser already exposes `list_module_files()` | `SoCParser.list_module_files`, `svc.catalogs`, `/api/project` health | S | H |
| 18 | EXCLUDE advisor (new) | When editing a bus interface, offer the known-good EXCLUDE sets by role (cmsdk slave `[hburst, hmastlock]`, passthrough-target `[hready]`, initiator `[]`) with a "copy from working module" picker | EXCLUDE is load-bearing for boundary correctness and the guide's own advice is "copy from a working region rather than guessing" — make that a button | catalogs modules + parsed lib YAMLs, 4b EXCLUDE table | S–M | M |
| 19 | Adapter↔Makefile lib-dir drift check (new) | Health check comparing the adapter's `lib_dirs` against the lib dirs the Makefile passes to `python3 -m soc_model` | Doc 06a §10.1 names this duplication "the single biggest drift risk" (Makefile passes 8, adapter currently lists 7); a missed dir surfaces as a confusing "module not found" later | `_env_health`, adapter, a Makefile-grep or `make -n` parse | S | M |
| 20 | Checklist fix-it quick actions (new) | Failed completeness rules render an "apply hint" button performing the canonical dict mutation (add a clock wire, set `role: bootrom`, …) | Rules already carry machine-adjacent hints; for the simple predicates (`external_wires`, `target_role`, `min_targets`) the fix is a deterministic dict edit | rule `hint`/predicate identity, `PUT /dict`, structural_lint revalidation | M | M |
| 21 | SRAM word/byte over-report lint (new) | Warn when `srams.addr_width` equals a byte-width `$RAM_ADDR_W` on a 32-bit macro (4× over-report), and when `phys_size` convention mismatches the region's addressing | Guide §8 documents this exact trap: `ResolvedSram` treats `addr_width` as words, region YAMLs pass byte widths; the fixtures themselves embody the trap | structural_lint or a completeness predicate, srams schema (4b) | S | M |

## High-priority details

**1. IRQ-map editor.** The concat glue schema (`glue_logic: {type: concat,
inputs: [{bit: 3, signal: "cc_periph_irq_w[8]"}], output, pad_zero_above}`)
is literally a sparse matrix: rows = NVIC bits, cells = source signals with
optional slices. The editor reads the draft dict, finds every `concat` whose
output feeds a CPU IRQ port (follow `connections:` to the cpu subsystem
instances), and renders source-signal pickers populated from
`internal_wires` and instance IRQ outputs. Writing back is a pure dict
mutation through the existing `/dict` endpoint, so tiers 1–2 validate the
result instantly. Phase it: a **read-only IRQ matrix** (render-only, M) ships
first and already kills the "is this IRQ actually wired to the NVIC?"
archaeology that cost a debug cycle on cc_periph; the editing layer follows.

**3. Diff-vs-built + staleness banner.** On every successful `soc` stage,
jobs.py records `{top_yaml sha256, draft id, timestamp}` into
`build_soc/build_info/configurator_build.yaml` (the artifacts walker already
indexes that dir). `/api/project` health grows a `build_fresh` field comparing
the recorded hash against the current top YAML *and* the mtimes of every
`*.yaml` under the adapter's lib dirs — precisely the case `set_env.sh`'s
top-mtime check misses. The frontend banner reuses the `apply` endpoint's
difflib output for the one-click diff. Small, read-mostly, and it neutralises
the most common "my change didn't take" support question.

**4. USER_GUIDE-anchored tooltips.** Generate (once, checked-in) a
`field_help.json` mapping `module.interfaces[].type` →
`USER_GUIDE.md#4b-field-reference` + a one-liner, etc. Compose panels render a
`?` per field. Zero server work beyond serving the static file; highest
value on the fields with opposite defaults (`module.gen` False vs
`interconnect.gen` True), `EXCLUDE`, `size`/`phys_size`/`RAM_ADDR_W`, and
`auto_base`'s literals-only rule.

**9. PARAM_OVERRIDES panel.** Surface the top module's `params:` (dict-form
with `desc`) plus a free list of `NAME=VALUE` overrides that the build stage
appends as `--config-override` flags (the adapter's `make.soc` template gains
a `{overrides}` placeholder; jobs.py untouched). The panel must carry the
guide's caveat verbatim: overrides move **memmap/linker/aperture numbers**,
not the RTL's physical storage — shrinking `CC_IMEM_RAM_ADDR_W` without
re-generating/re-synthesising memories is a classic silent mismatch.

**13. Pre-commit hook.** A 5-line hook script (shipped under
`scripts/hooks/`) that runs the headless `check` on any staged file under the
adapter's lib dirs and blocks the commit on exit 1. Because `check` needs no
fastapi and finishes in seconds (no BuildBusMatrix), it is the cheapest
possible enforcement of "GUI-green == CI-green" — and it catches the
lib-dir-region edits that the mtime-based auto-regen misses (§8).

**16. Unresolved-`$PARAM` linter.** Walk the expanded dict
(`expand_preview`), collect effective params per scope (module defaults +
instance overrides, mirroring the lazy `resolve_param_ref` rules), and flag
any `$NAME` whose name resolves nowhere — plus the special case of `$`/
expressions inside an `auto_base` block, which the expander rejects outright.
This converts the guide's "grep your RTL for stray `$`" advice into a tier-1
message with instance/port coordinates, before RTL is ever emitted.

**17. Lib-dir shadowing auditor.** During `catalogs()`, instead of collapsing
`list_module_files()` to a stem set, keep the full path list and report any
stem or `module.name` claimed by more than one file, in scan order, marking
the winner. Surfaced both in `/api/project` health (count) and as a catalog
panel table. With 7 adapter lib dirs plus their `regions/`/`subsystems/`
subtrees, this is the difference between a one-glance answer and a
last-writer-wins mystery that today produces no warning at all.

## Recommended next 3

1. **#3 Diff-vs-built + staleness banner (S–M)** — highest
   footgun-kill-per-effort: it addresses the daily "did I regenerate?"
   confusion that `set_env.sh` only partially covers, touches only read-side
   code plus one stage hook, and needs no schema or frontend-framework work.
2. **#13 + #16 as one "trust the check" slice (S)** — the pre-commit hook and
   the `$PARAM` linter both deepen the already-shipped headless `check`
   command; together they make tier-1..3 validation the enforced gate
   everywhere YAML is edited, including outside the GUI. Pure layer-1 python,
   fully pytest-coverable, no fastapi dependency.
3. **#1 IRQ-map editor, read-only phase first (M)** — the single biggest
   authoring pain with a real incident behind it (cc_periph IRQs never
   reached the NVIC). The render-only matrix is achievable with existing
   endpoints and immediately diagnostic; it also forces the
   connections-tracing groundwork the full editor (and #20 fix-it actions)
   will reuse.

Rationale for the ordering: ship the safety net before the power tools — #3
and #13/#16 prevent regressions silently entering builds, which raises the
confidence floor for the heavier editing features (#1, #2, #9) that mutate
the dict.
