# Copyright 2026, SoC Labs (www.soclabs.org)
# `nanosoc_gen` test suite

Pytest-based unit + golden/snapshot regression suite for the `nanosoc_gen`
SoC generator (distribution `nanosoc-gen`, **import name `soc_model`**). It
tests the generator at the **model seam** (`SoCParser → SoCBuilder → Module`)
and snapshots at the **artifact seam** (one backend → emitted text/file). It
asserts on emitted *text* only — no simulator, no synthesis, no functional RTL
verification.

Design rationale and the full per-test coverage matrix live in:
- `tests/TEST_PLAN.md` — the master coverage matrix (every test row, grounded
  against `soc_model/`).
- `nanosoc_arch_tech/docs/roadmap/01-unit-testing-nanosoc-gen.md` (in the
  multicore superproject) — the design doc this suite implements.

---

## 1. Layout

```
tests/
  README.md                # this file
  TEST_PLAN.md             # master coverage matrix (the test contract)
  conftest.py              # sys.path shim, tier markers, --snapshot-update opt, fixtures
  helpers.py               # build_module_from_yaml / build_module_inline / assert_matches_snapshot
  fixtures/                # hermetic, self-contained SoC YAMLs (no external --lib-dir)
    minimal_soc.yaml             # smallest valid SoC: 1 region behind 1 gen:True interconnect
    two_region_soc.yaml          # address map + 2 targets + 1 initiator
    apb_bridge_soc.yaml          # AHB→APB target (protocol: apb, apb_config)
    passthrough_soc.yaml         # initiator-boundary passthrough heuristic
    firmware_soc.yaml            # firmware linker_profiles + build_info
    firmware_multicore_soc.yaml  # multicore firmware (per-core linker/memmap)
    nested_subsystem_soc.yaml    # subsystem backend (SoCSubsystemBackend.generate_all)
    regions/                     # tiny self-contained region modules
    register_maps/               # tiny register maps (RDL/discovery source)
    subsystems/                  # tiny subsystem module(s)
  unit/                    # -m unit : pure-Python, in-memory or tmp_path, sub-ms each
    test_utils.py                # resolve_param_ref, parse_bit_slice/conn_ref, write_if_changed, _strip_timestamps
    test_parser.py               # discovery, name/stem caching collision, missing-file warnings
    test_builder.py              # Module construction, instance resolution, param passthrough
    test_validator.py            # one class per validator rule
    test_backends_misc.py        # html/python/sram/chip/build_info/text/lint backend units
    test_toplevel_connections.py # toplevel connection/wiring units
    test_toplevel_coverage_gaps.py
    test_sanity.py               # import + collection smoke
    test_main_cli.py             # python -m soc_model CLI: arg parsing, --validate-only exit codes, full generate
  golden/                  # -m golden : snapshot of a rendered artifact
    test_toplevel_sv.py          # <top>.sv
    test_flists.py               # *_toplevel.flist / *_interconnect.flist / *_system.flist / *_chip.flist
    test_firmware.py             # *_memory.ld / *_memmap.{h,mk,cmake} / *_adp.{vh,py}
    test_firmware_multicore.py
    test_firmware_coverage.py
    test_config_pkg.py           # *_soc_config_pkg.sv / *_soc_config.vh
    test_discovery_yaml.py       # *_discovery.yaml (the demo-GUI device_model contract)
    test_text_reports.py         # memory_map.txt / hierarchy.txt
    test_subsystem_sv.py         # subsystem .sv + flist
    test_rdl.py                  # .rdl text (no toolchain needed)
    test_docs.py                 # <top>_design_doc.md (date-line stripped) + visualizer shim
    __snapshots__/<fixture>/...  # committed golden artifacts, one subdir per fixture
  tooling/                 # -m tooling : external tool paths (SKIP if absent)
    test_ahb_perl.py             # AHB perl/BuildBusMatrix path + pure AHB helpers
    test_rdl_regblock.py         # systemrdl-compiler + peakrdl-regblock RTL emission
  slow/                    # -m slow : drives the real multicore SoC YAML / submodules
    test_real_soc_smoke.py
```

`conftest.py` prepends the worktree root (the dir containing the `soc_model/`
package) to `sys.path`, so the suite runs **without installing** the package —
mirroring `python/tests/conftest.py` in the superproject.

---

## 2. The four tiers (markers)

Markers are registered in `conftest.py` and declared in `pyproject.toml`.

| Marker | Meaning | Tools required | In default run? |
|---|---|---|---|
| `unit` | Pure-Python, in-memory or `tmp_path`; the parser/builder/model/utils/validator + per-backend data logic. | none | **yes** |
| `golden` | Snapshot of a rendered artifact, compared timestamp/copyright-stripped against a committed golden under `__snapshots__/`. | `jinja2`, `pyyaml` (installed) | **yes** |
| `tooling` | External-tool paths: the AHB `BuildBusMatrix.pl` perl path (needs `ARM_IP_LIBRARY_PATH` + `perl`), and `systemrdl`/`peakrdl-regblock` RTL emission. **Skips, never fails, when a prereq is absent.** | external | no (`-m tooling`) |
| `slow` | Drives the real `nanosoc_multicore_soc.yaml` end-to-end (parse/build/validate parity/artifact emit). **Skips, never fails, when the real YAML or its lib-dirs are absent.** | real-SoC submodules | no (`-m slow`) |

Default `addopts = -m 'not tooling and not slow'`, so a bare `pytest` runs the
**unit + golden** tiers only.

### Skip-not-fail guarantees (important)
- `tooling/test_ahb_perl.py`: the bulk of its assertions are pure AHB helpers and
  `monkeypatch`-ed perl error paths that need **no** external tooling, so they run
  in any worktree. The one test that would invoke the **real** `BuildBusMatrix.pl`
  is **deliberately skipped** — the backend sets `cwd` inside the read-only vendor
  IP tree (`$ARM_IP_LIBRARY_PATH`), which tests must never write to. It never
  exports or writes under `ARM_IP_LIBRARY_PATH`.
- `tooling/test_rdl_regblock.py`: guarded by `pytest.importorskip("systemrdl")` /
  `importorskip("peakrdl_regblock")` — skips cleanly if either codegen package is
  missing. (Both are installed in this worktree, so they run.)
- `slow/test_real_soc_smoke.py`: a module-level `skipif` checks the real YAML and
  every required lib-dir exist; if any is missing the whole module skips with a
  clear reason. (In this worktree the real multicore tree resolves, so they run.)

---

## 3. How to run

Always run from the worktree root. `PYTHONDONTWRITEBYTECODE=1` keeps the tree
free of `__pycache__`; `-p no:cacheprovider` skips pytest's `.pytest_cache`.

```bash
cd "$SOCLABS_NANOSOC_GEN_DIR"        # your nanosoc_gen checkout

# Default (unit + golden) — the gate:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q

# Fast tier only (sub-5s, no jinja artifacts):
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -m unit -q

# Golden tier only:
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -m golden -q

# Regenerate golden snapshots (then HAND-REVIEW the diff before committing):
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -m golden --snapshot-update

# Opt-in tiers (run on an EDA host; skip cleanly otherwise):
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -m tooling -rs
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -m slow -rs

# Coverage (default suite):
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider \
    -m "unit or golden" --cov=soc_model --cov-report=term-missing -q
```

Requirements: Python ≥3.7, `pytest>=7`, plus the generator's own runtime deps
(`pyyaml`, `jinja2`, and for the tooling tier `systemrdl-compiler`,
`peakrdl-regblock`). Install the test extra with:
`pip install -e .[test]` (adds `pytest`, `pytest-cov`).

---

## 4. The snapshot (golden) mechanism

Golden tests render exactly one backend artifact and compare it against a
committed file under `tests/golden/__snapshots__/<fixture>/<artifact>`.

The comparison reuses the generator's **own** normalisation —
`soc_model.utils._strip_timestamps` (the same function `write_if_changed` uses,
driven by `_TIMESTAMP_LINE_RE` in `utils.py`). Both the freshly-rendered text and
the committed golden are stripped of timestamp/copyright lines before comparison,
so a golden that passes here is byte-stable in a real `write_if_changed` build,
and date/header churn never causes a false failure.

`helpers.assert_matches_snapshot(actual, snap_path, request)`:
- On a normal run: asserts `_strip_timestamps(actual) == _strip_timestamps(golden)`;
  a missing golden fails with a "run `--snapshot-update`" hint.
- With `--snapshot-update`: (re)writes the golden and passes.

### Update workflow
1. Make the intended generator change (or fixture change).
2. `pytest -m golden --snapshot-update` regenerates every golden.
3. `git diff tests/golden/__snapshots__/` — **review the diff** like any source
   change; it is the human-readable evidence of what the change did to the RTL /
   flists / linker scripts / discovery tables.
4. Commit the goldens alongside the code change in the same MR.

No third-party snapshot plugin (`syrupy` / `pytest-snapshot`) is used — the
~25-line helper keeps the dependency surface minimal and the normalisation
identical to the generator.

---

## 5. Current numbers

Run on this worktree (branch `feature/nanosoc-gen-roadmap`, Python 3.8.17,
pytest 8.3.5):

- **Default suite (`-m "unit or golden"`): 713 passed, 1 skipped, 0 failed**
  (the 1 skip is a `pyslang`-not-installed lint path; skips cleanly).
- Per-marker collection: **unit 601, golden 120** (7 carry both → 714 in the
  default suite), **tooling 35, slow 3**; **751** collected across all markers.
- Opt-in tiers: `-m tooling` → 33 passed, 2 skipped, 0 failed;
  `-m slow` → 3 skipped by default (set `NANOSOC_REAL_ROOT=<superproject root>`
  to run them against the real ~117 KB SoC, then 3 passed).

### Coverage — `--cov=soc_model` on the default suite: **97%** overall (4229 stmts, 144 missed)

Core modules:

| Module | Coverage |
|---|---|
| `soc_model/utils.py` | 100% |
| `soc_model/model.py` | 99% |
| `soc_model/parser.py` | 99% |
| `soc_model/validator.py` | 99% |
| `soc_model/builder.py` | 98% |
| `soc_model/__main__.py` | 98% |

Backends:

| Backend | Coverage |
|---|---|
| `discovery.py` | 100% |
| `sram.py` | 100% |
| `lint.py` | 99% |
| `python.py` | 99% |
| `firmware.py` | 98% |
| `docs.py` | 98% |
| `rdl.py` | 98% |
| `soc_config_pkg.py` | 96% |
| `ahb.py` | 95% |
| `build_info.py` | 95% |
| `html.py` | 95% |
| `protocol_utils.py` | 94% |
| `subsystem.py` | 94% |
| `system.py` | 94% |
| `toplevel.py` | 94% |
| `chip.py` | 93% |
| `text.py` | 89% |

`soc_model/visualizer.py` (the `backends.html` re-export shim) is at **100%**.
The small residual misses are mostly the `except ImportError: Environment = None`
jinja2-fallback shims at the top of several backends (unreachable while jinja2 is
installed) plus a few defensive branches.

---

## 6. CI integration (roadmap doc 01, M6)

This suite is the cheapest gate in the verification "validity gradient" — it sits
**below** the existing `--validate-only` semantic check and the lint/compile/sim/
synth tiers, and runs in seconds on a pure-Python runner (no EDA tools, no
network, no submodules).

Per doc 01 §6 / §M6, add a `soc_gen_unit` job to `.gitlab-ci.yml` in the existing
**lint** stage (after `soc_gen`, before the expensive stages). Because no pipeline
currently installs pytest, the job is self-contained — it installs the `[test]`
extra itself:

```yaml
soc_gen_unit:
  stage: lint
  needs: ["soc_gen"]
  script:
    - source set_env.sh
    - pip install -e nanosoc_arch_tech/nanosoc_gen[test]
    - cd nanosoc_arch_tech/nanosoc_gen
    - pytest -m "unit or golden" --junitxml=report.xml
  artifacts:
    when: always
    reports: { junit: nanosoc_arch_tech/nanosoc_gen/report.xml }
```

Notes:
- The job runs the **default** tiers (`unit or golden`) only — `tooling` and
  `slow` stay opt-in (no `ARM_IP_LIBRARY_PATH`, no perl, no submodules needed).
- JUnit XML is emitted so failures surface inline in the MR.
- The `tooling`/`slow` tiers can be added later as separate jobs on EDA-tagged
  runners; they skip-not-fail elsewhere, so they are safe to run anywhere.
- A `make test-fast` / `make test` / `make test-update` convenience target
  (doc 01 §6) wraps the three common invocations above.
