<!-- Copyright 2026, SoC Labs (www.soclabs.org) -->
# `ci/matrix.py` — SoC system-validity matrix (roadmap doc 03)

Sweeps a set of named SoC configurations and asserts each is *valid* through a
cheapest-first tier ladder (`parse → validate → generate → elaborate → smoke`),
then prints a pass/fail matrix and exits non-zero if any config fails.

The cheapest tier (`validate`) runs **in-process** — it wraps the same
`SoCValidator` that `python -m soc_model … --validate-only` runs, via
`GuiService.validate`, with no subprocess. Deeper tiers (`generate` and beyond)
shell out to `python -m soc_model … --config-override … --build-dir …`, exactly
the invocation `sys_desc/Makefile` and the existing pipeline use.

## Files

| File | Purpose |
|---|---|
| `ci/matrix.py` | the runner. Importable seam: `run_matrix(matrix, base_yaml, base_dir, lib_dirs, tier=…, in_process=…) -> list[TierResult]` |
| `ci/example_matrix.yaml` | example matrix spec (runs against `tests/fixtures/`, no EDA license) |
| `ci/fixtures/relayout_ok.yaml`, `ci/fixtures/relayout_overlap.yaml` | `yaml:`-fixture configs for the base/window-relayout overlap test (positive + negative) |
| `tests/unit/test_ci_matrix.py` | unit tests for the in-process core |

## Run it (laptop, no EDA license)

```bash
source ./set_env.sh
python3 ci/matrix.py --matrix ci/example_matrix.yaml --tier validate
```

`base_yaml`/`base_dir`/`lib_dirs` come from the matrix's `defaults:` block; the
example points at the hermetic test fixtures. Override on the CLI for another
SoC:

```bash
python3 ci/matrix.py --matrix ci/example_matrix.yaml --tier validate \
    --base-yaml tests/fixtures/two_region_soc.yaml \
    --base-dir tests/fixtures --lib-dir tests/fixtures \
    --junit ci/matrix_results.xml
```

Useful flags: `--select name=default` / `--select tier=generate` (filter),
`--subprocess` (run `validate` through `python -m soc_model` too, not
in-process), `--tier generate` (emit RTL per config into `build_soc/_mtx/<name>/`).

### Matrix spec

```yaml
defaults:
  tier: validate
  base_yaml: tests/fixtures/two_region_soc.yaml
  base_dir: tests/fixtures
  lib_dirs: [tests/fixtures]
configs:
  - name: default
    overrides: {}
  - name: bigger_sram
    overrides: { SRAM_RAM_ADDR_W: 16 }      # 1:1 with --config-override KEY=VALUE
  - name: relayout_overlap
    yaml: ci/fixtures/relayout_overlap.yaml  # alt to overrides: an edited top YAML
    expect_fail: validate                    # driver inverts the verdict
axes:                                        # optional cartesian sugar
  - name: sram_width_sweep
    base: default
    grid: { SRAM_RAM_ADDR_W: [12, 14, 16] }
```

`expect_fail` configs are the matrix's self-test: they are green **because** they
fail exactly as declared, and flip the run red if a check silently regresses and
they unexpectedly pass.

## Superproject deployment

In the superproject (`nanosoc-multicore-system/`), set `base_yaml` to
`sys_desc/nanosoc_multicore_soc.yaml` and `lib_dirs` to the 8 `sys_desc`
`LIB_DIRS`. Per doc 03 §5.2, fetch them from the canonical
`make -C sys_desc print-libdirs` target (one absolute path per line) rather than
scraping `make -p`. The same override keys (`CC_IMEM_RAM_ADDR_W`,
`QSPI_FLASH_ADDR_W`, `DMAC_*_TYPE`, `NUMIRQ`, …) apply unchanged.

## GitLab CI stage

Add a `gen_matrix` stage **after** `soc_gen` (do not remove existing stages):

```yaml
# .gitlab-ci.yml — new stage between soc_gen and lint
stages: [setup, soc_gen, gen_matrix, lint]   # ...keep the rest

matrix_validate:
  stage: gen_matrix
  needs: [clone, preflight]            # parse/validate needs only the source tree
  script:
    - cd "$WORK_DIR" && source set_env.sh
    - python3 ci/matrix.py --matrix ci/example_matrix.yaml --tier validate
        --junit ci/matrix_results.xml
  artifacts:
    when: always
    reports:
      junit: ci/matrix_results.xml
    paths: [ci/matrix_results.xml]
    expire_in: 30 days

matrix_elaborate:
  stage: gen_matrix
  needs: [clone, preflight, soc_gen]
  tags: [xcelium]
  allow_failure: true                  # gated; flip off once stable
  script:
    - cd "$WORK_DIR" && source set_env.sh
    - python3 ci/matrix.py --matrix ci/example_matrix.yaml --tier elaborate
        --select tier=elaborate
  artifacts:
    when: always
    paths: [build_soc/_mtx/*/matrix_lint_*.log]
    expire_in: 30 days
```

`matrix_validate` is license-free and runs on every push, blocking the pipeline
on any unexpectedly-red config (with the validator message in the GitLab test
report). `matrix_elaborate` runs the gated `tier: elaborate` subset on an
xcelium runner.

## Tiers

| Tier | What | Mechanism | Cost |
|---|---|---|---|
| `parse` / `validate` | YAML→model + semantic checks (overlaps, widths, drivers) | in-process `GuiService.validate` (or `--validate-only`) | seconds, no license |
| `generate` | emit RTL/flist/memmap per config | `python -m soc_model … --build-dir build_soc/_mtx/<name>` | seconds–minute, no license |
| `elaborate` | structural elaboration / lint | `make -C lint …` (HAL/slang) | minutes, needs license |
| `smoke` | boot-to-marker sim | `make -C cocotb soc_smoke` | 10s of minutes, needs VCS |

In this generator-only worktree `elaborate`/`smoke` are recognised but reported
`SKIP (needs EDA / superproject build tree)`; they are wired in the superproject
deployment.
