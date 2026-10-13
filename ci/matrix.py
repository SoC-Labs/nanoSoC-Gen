#!/usr/bin/env python3
# Copyright 2026, SoC Labs (www.soclabs.org)
"""CI system-validity matrix runner for nanosoc_gen (roadmap doc 03).

Sweeps a set of named SoC configurations and asserts each is *valid* through a
cheapest-first tier ladder:

    parse -> validate -> generate -> elaborate -> smoke

and reports a pass/fail matrix. The driver is a thin orchestrator: it does not
reimplement the tiers, it reuses the generator's existing entry points.

This module exposes TWO ways to run the cheapest tier (``validate``):

  * **in-process** (the default for the testable core): loads each config's
    YAML into a dict, applies its ``overrides`` onto the top-level ``params``
    block, and runs :meth:`soc_model.gui_service.GuiService.validate` directly.
    Identical results to ``python -m soc_model ... --validate-only`` (the GUI
    service wraps the same :class:`~soc_model.validator.SoCValidator`), but with
    no subprocess — fast, hermetic, unit-testable.

  * **subprocess** (``tier='generate'`` or ``in_process=False``): shells out to
    ``python3 -m soc_model <top> --lib-dir ... --config-override K=V ...
    [--validate-only | --build-dir <dir>]`` per config, exactly the invocation
    that ``sys_desc/Makefile`` and ``.gitlab-ci.yml`` already use, and collects
    the return code + the ``N error(s)`` summary. Used for ``generate`` (and as
    a faithful alternative for ``validate``) where actual RTL emission matters.

The public, importable seam is :func:`run_matrix` — call it directly from a
pytest without any subprocess (see ``tests/unit/test_ci_matrix.py``).

Matrix spec format (``ci/example_matrix.yaml``)::

    defaults:
      tier: validate            # cheapest tier every config must clear
    configs:
      - name: default
        desc: "Golden — fixture defaults"
        overrides: {}
      - name: fpga_small
        overrides: { SRAM_RAM_ADDR_W: 14 }
      - name: overlap_neg
        desc: "must FAIL Tier-1 overlap"
        yaml: ci/fixtures/overlap.yaml    # alt to overrides: an edited top YAML
        expect_fail: validate             # driver inverts the verdict
    axes:                                 # optional cartesian sugar
      - name: width_sweep
        base: default
        grid: { SRAM_RAM_ADDR_W: [12, 14, 16] }

Decisions / deviations from doc 03:
  * ``overrides`` are applied to the dict's ``params[*].default`` for the
    in-process tier (the GUI editing model), and as ``--config-override`` flags
    for the subprocess tier — both honoured by the post-M1 generator.
  * ``libdirs_from: sys_desc`` / ``make -C sys_desc print-libdirs`` is a
    superproject concern; this generator-tree driver takes ``lib_dirs``
    explicitly (the matrix runs against any base/lib set). The CLI keeps a
    ``--lib-dir`` flag and an optional ``--libdirs-from-make`` hook for the
    superproject deployment.
  * ``elaborate`` / ``smoke`` tiers are recognised but require EDA licenses and
    the superproject build tree; they are stubbed as "skipped (needs EDA)" in
    this license-free generator worktree so the matrix stays runnable on a
    laptop. The MVP (validate / generate) is fully implemented.

GitLab CI stage snippet: see ``ci/README.md``.

CLI::

    python3 ci/matrix.py --matrix ci/example_matrix.yaml --tier validate \\
        --base-dir tests/fixtures --lib-dir tests/fixtures \\
        --junit ci/matrix_results.xml
"""

import argparse
import dataclasses
import copy
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Make ``import soc_model`` work when this file is run directly as a script
# (the package root is the parent of ci/). Harmless when soc_model is already
# importable (pytest / installed).
_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

# Tier order, cheapest first. A request for tier T runs every tier up to and
# including T (stopping at the first failure).
TIERS = ["parse", "validate", "generate", "elaborate", "smoke"]

# Tiers this license-free generator-tree driver can actually execute. Deeper
# tiers need EDA tools + the superproject build tree (see module docstring).
_RUNNABLE_TIERS = {"parse", "validate", "generate"}


@dataclasses.dataclass
class TierResult:
    """Outcome of running one config up to its requested tier."""
    config: str
    tier: str               # the deepest tier actually attempted
    passed: bool            # final verdict (expect_fail already applied)
    message: str = ""       # validator errors / triage detail
    duration: float = 0.0
    skipped: bool = False    # tier needs EDA / superproject; not run
    expected_fail: bool = False  # this config declared expect_fail


# ---------------------------------------------------------------------------
# matrix spec loading + axis expansion
# ---------------------------------------------------------------------------
def load_matrix(path) -> Dict[str, Any]:
    """Load a matrix spec YAML into a dict."""
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def expand_axes(matrix: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Expand ``configs`` + the optional ``axes`` cartesian sugar into a flat
    list of fully-resolved config dicts, each carrying defaults merged in.

    Each returned config has at least: ``name``, ``overrides`` (dict),
    ``tier`` (str). Optional: ``yaml`` (str), ``expect_fail`` (str/bool),
    ``desc`` (str).
    """
    defaults = matrix.get("defaults") or {}
    default_tier = defaults.get("tier", "validate")
    configs: List[Dict[str, Any]] = []

    by_name: Dict[str, Dict[str, Any]] = {}
    for raw in matrix.get("configs") or []:
        cfg = _normalise_config(raw, default_tier)
        configs.append(cfg)
        by_name[cfg["name"]] = cfg

    # axes: cartesian product of a grid over a named base config.
    for axis in matrix.get("axes") or []:
        base_name = axis.get("base")
        base = by_name.get(base_name, {"overrides": {}, "tier": default_tier})
        grid = axis.get("grid") or {}
        axis_tier = axis.get("tier", base.get("tier", default_tier))
        for combo in _cartesian(grid):
            ov = dict(base.get("overrides") or {})
            ov.update(combo)
            suffix = "_".join(f"{k}{v}" for k, v in sorted(combo.items()))
            configs.append({
                "name": f"{axis.get('name', 'axis')}__{suffix}",
                "desc": f"axis {axis.get('name')} {combo}",
                "overrides": ov,
                "tier": axis_tier,
            })
    return configs


def _normalise_config(raw: Dict[str, Any], default_tier: str) -> Dict[str, Any]:
    cfg = dict(raw or {})
    cfg.setdefault("overrides", {})
    cfg.setdefault("tier", default_tier)
    if "name" not in cfg:
        raise ValueError("matrix config missing 'name': %r" % (raw,))
    return cfg


def _cartesian(grid: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
    keys = list(grid.keys())
    out: List[Dict[str, Any]] = [{}]
    for k in keys:
        vals = grid[k]
        if not isinstance(vals, (list, tuple)):
            vals = [vals]
        out = [dict(c, **{k: v}) for c in out for v in vals]
    return out


# ---------------------------------------------------------------------------
# applying overrides to a YAML dict (in-process tier; mirrors the GUI editing
# model where params[*].default is the source of truth)
# ---------------------------------------------------------------------------
def apply_overrides_to_dict(data: Dict[str, Any],
                            overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Return a deep copy of ``data`` with ``overrides`` applied onto the
    top-level ``params`` block. Mirrors the post-M1 generator semantics: an
    override sets a declared param's default; an unknown key is added (the
    generator warns + ignores unknown top-level keys, but a freshly-added
    param is harmless and keeps the in-process path simple)."""
    data = copy.deepcopy(data)
    mod = data.get("module", data) if isinstance(data, dict) else data
    params = mod.setdefault("params", {})
    if isinstance(params, list):
        # list-form params: [{name, default, ...}]
        index = {p.get("name"): p for p in params if isinstance(p, dict)}
        for k, v in overrides.items():
            if k in index:
                index[k]["default"] = v
            else:
                params.append({"name": k, "default": v})
    else:
        for k, v in overrides.items():
            spec = params.get(k)
            if isinstance(spec, dict):
                spec["default"] = v
            else:
                params[k] = {"default": v}
    return data


# ---------------------------------------------------------------------------
# in-process validate tier (the testable core)
# ---------------------------------------------------------------------------
def _resolve_config_yaml(cfg: Dict[str, Any], base_dir: Path) -> Path:
    """The top-level YAML to load for a config: an explicit ``yaml:`` fixture
    (relative to cwd or base_dir) or the matrix's default base YAML."""
    if cfg.get("yaml"):
        p = Path(cfg["yaml"])
        if not p.is_absolute() and not p.exists():
            alt = base_dir / cfg["yaml"]
            if alt.exists():
                return alt
        return p
    raise ValueError(
        "config %r has no 'yaml' and no base_yaml was supplied" % cfg["name"])


def run_validate_in_process(cfg: Dict[str, Any], base_yaml: Path,
                            base_dir: Path, lib_dirs: List[str]) -> TierResult:
    """Run T0 parse + T1 validate for one config WITHOUT a subprocess.

    Loads the config's YAML (``yaml:`` fixture or ``base_yaml``), applies its
    ``overrides`` onto the params block, and runs ``GuiService.validate`` —
    the same SoCValidator that ``--validate-only`` runs.
    """
    from soc_model.gui_service import GuiService

    t0 = time.time()
    expected = bool(cfg.get("expect_fail"))
    try:
        yaml_path = (_resolve_config_yaml(cfg, base_dir)
                     if cfg.get("yaml") else base_yaml)
        with open(yaml_path) as fh:
            data = yaml.safe_load(fh)
        data = apply_overrides_to_dict(data, cfg.get("overrides") or {})
        svc = GuiService(str(base_dir), [str(d) for d in lib_dirs])
        result = svc.validate(data)
    except Exception as exc:  # parse / load failure == Tier-0 fail
        dur = time.time() - t0
        # A parse failure is a real failure unless this config expected to fail.
        return TierResult(cfg["name"], "parse", passed=expected,
                          message="parse/build error: %s" % exc,
                          duration=dur, expected_fail=expected)

    errs = [m for m in result["messages"] if m.get("level") == "error"]
    err_text = "\n".join(
        "[%s] %s%s" % (m.get("category", "?"),
                       (m.get("instance", "") + ": ") if m.get("instance") else "",
                       m.get("message", ""))
        for m in errs)
    validate_ok = result.get("ok", not errs)

    dur = time.time() - t0
    if expected:
        # expect_fail: green iff it actually failed (at validate).
        passed = not validate_ok
        msg = (err_text if not validate_ok
               else "UNEXPECTED PASS — config declared expect_fail=%s but "
                    "validated clean" % cfg.get("expect_fail"))
    else:
        passed = validate_ok
        msg = err_text
    return TierResult(cfg["name"], "validate", passed=passed,
                      message=msg, duration=dur, expected_fail=expected)


# ---------------------------------------------------------------------------
# subprocess tiers (faithful to the real CI invocation; used for generate, and
# optionally for validate via in_process=False)
# ---------------------------------------------------------------------------
def _soc_model_cmd(yaml_path, lib_dirs, overrides,
                   build_dir=None, validate_only=False) -> List[str]:
    cmd = [sys.executable, "-m", "soc_model", str(yaml_path)]
    for d in lib_dirs:
        cmd += ["--lib-dir", str(d)]
    for k, v in (overrides or {}).items():
        cmd += ["--config-override", "%s=%s" % (k, v)]
    if validate_only:
        cmd.append("--validate-only")
    if build_dir:
        cmd += ["--build-dir", str(build_dir)]
    return cmd


def run_subprocess_tier(cfg: Dict[str, Any], base_yaml: Path, base_dir: Path,
                        lib_dirs: List[str], tier: str,
                        build_root: Optional[Path] = None,
                        gen_cwd: Optional[Path] = None) -> TierResult:
    """Run a config via ``python -m soc_model`` (validate or generate)."""
    import subprocess

    t0 = time.time()
    expected = bool(cfg.get("expect_fail"))
    yaml_path = (_resolve_config_yaml(cfg, base_dir)
                 if cfg.get("yaml") else base_yaml)
    validate_only = (tier == "validate")
    build_dir = None
    if tier == "generate":
        root = build_root or Path("build_soc/_mtx")
        build_dir = root / cfg["name"]
    cmd = _soc_model_cmd(yaml_path, lib_dirs, cfg.get("overrides") or {},
                         build_dir=build_dir, validate_only=validate_only)
    proc = subprocess.run(cmd, cwd=str(gen_cwd) if gen_cwd else None,
                          capture_output=True, text=True)
    dur = time.time() - t0
    rc_ok = (proc.returncode == 0)
    errs = [l for l in (proc.stdout or "").splitlines() if "[ERROR]" in l]
    err_text = "\n".join(errs) or (proc.stderr or "")[-600:]

    if expected:
        passed = not rc_ok
        msg = err_text if not rc_ok else "UNEXPECTED PASS (expect_fail)"
    else:
        passed = rc_ok
        msg = err_text
    return TierResult(cfg["name"], tier, passed=passed, message=msg,
                      duration=dur, expected_fail=expected)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------
def _select(configs, expr):
    """Filter configs by a ``key=value`` expression (name=… or tier=…)."""
    if not expr or "=" not in expr:
        return configs
    key, _, val = expr.partition("=")
    key, val = key.strip(), val.strip()
    return [c for c in configs if str(c.get(key, "")) == val]


def run_matrix(matrix: Dict[str, Any], base_yaml=None, base_dir=None,
               lib_dirs=None, tier: str = "validate",
               in_process: bool = True, select: Optional[str] = None,
               build_root=None, gen_cwd=None) -> List[TierResult]:
    """Run every config in ``matrix`` up to ``tier`` and return TierResults.

    This is the importable seam (callable from pytest with no subprocess when
    ``in_process=True`` and ``tier in {'parse','validate'}``).

    Args:
      matrix:     loaded matrix-spec dict (``load_matrix`` output) OR already
                  an expanded list of config dicts.
      base_yaml:  default top-level YAML for configs without a ``yaml:`` key.
      base_dir:   base dir for SoCParser / relative ``yaml:`` resolution.
      lib_dirs:   component-library dirs.
      tier:       deepest tier to attempt for each config (capped per-config
                  by that config's own ``tier``).
      in_process: True -> use GuiService for validate (no subprocess).
      select:     optional ``key=value`` filter.
    """
    if tier not in TIERS:
        raise ValueError("unknown tier %r (choose from %s)" % (tier, TIERS))
    base_dir = Path(base_dir) if base_dir else Path.cwd()
    base_yaml = Path(base_yaml) if base_yaml else None
    lib_dirs = list(lib_dirs or [])

    if isinstance(matrix, list):
        configs = matrix
    else:
        configs = expand_axes(matrix)
    configs = _select(configs, select)

    requested_idx = TIERS.index(tier)
    results: List[TierResult] = []
    for cfg in configs:
        # Cap the run at the lower of the requested tier and the config's tier.
        cfg_idx = TIERS.index(cfg.get("tier", "validate"))
        eff = TIERS[min(requested_idx, cfg_idx)]

        if eff in ("parse", "validate"):
            if in_process:
                results.append(run_validate_in_process(
                    cfg, base_yaml, base_dir, lib_dirs))
            else:
                results.append(run_subprocess_tier(
                    cfg, base_yaml, base_dir, lib_dirs, "validate",
                    gen_cwd=gen_cwd))
        elif eff == "generate":
            results.append(run_subprocess_tier(
                cfg, base_yaml, base_dir, lib_dirs, "generate",
                build_root=build_root, gen_cwd=gen_cwd))
        else:  # elaborate / smoke — need EDA + superproject; record as skipped
            results.append(TierResult(
                cfg["name"], eff, passed=True, skipped=True,
                message="tier '%s' needs EDA tools / superproject build tree "
                        "(not run in the generator worktree)" % eff,
                expected_fail=bool(cfg.get("expect_fail"))))
    return results


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def format_table(results: List[TierResult]) -> str:
    lines = []
    n = len(results)
    width = max((len(r.config) for r in results), default=10)
    for i, r in enumerate(results, 1):
        if r.skipped:
            verdict = "SKIP"
        elif r.expected_fail:
            verdict = "PASS (expected-fail)" if r.passed else "FAIL"
        else:
            verdict = "PASS" if r.passed else "FAIL"
        lines.append("[%2d/%2d] %-*s  %-8s %-22s (%.2fs)" % (
            i, n, width, r.config, r.tier, verdict, r.duration))
        if not r.passed and r.message:
            for ml in r.message.splitlines():
                lines.append("          %s" % ml)
        elif r.expected_fail and r.passed and r.message:
            # show the validator line that confirms the expected failure
            first = r.message.splitlines()[:1]
            for ml in first:
                lines.append("          (expected) %s" % ml)
    n_pass = sum(1 for r in results if r.passed)
    n_fail = n - n_pass
    n_exp = sum(1 for r in results if r.expected_fail and r.passed)
    lines.append("%d configs: %d green, %d red  (%d expected-fail)" % (
        n, n_pass, n_fail, n_exp))
    return "\n".join(lines)


def write_junit(results: List[TierResult], path) -> None:
    """Emit a JUnit XML with one <testcase> per config."""
    n_fail = sum(1 for r in results if not r.passed)
    n_skip = sum(1 for r in results if r.skipped)
    suite = ET.Element("testsuite", {
        "name": "nanosoc_gen.matrix",
        "tests": str(len(results)),
        "failures": str(n_fail),
        "skipped": str(n_skip),
    })
    for r in results:
        tc = ET.SubElement(suite, "testcase", {
            "classname": "matrix",
            "name": r.config,
            "time": "%.3f" % r.duration,
        })
        if r.skipped:
            ET.SubElement(tc, "skipped", {"message": r.message})
        elif not r.passed:
            cat = "expect_fail" if r.expected_fail else "validate"
            f = ET.SubElement(tc, "failure", {"type": cat,
                                              "message": "%s tier failed" % r.tier})
            f.text = r.message
    tree = ET.ElementTree(suite)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", default="ci/example_matrix.yaml",
                    help="matrix spec YAML")
    ap.add_argument("--tier", choices=TIERS, default="validate",
                    help="deepest tier to attempt per config")
    ap.add_argument("--base-yaml", default=None,
                    help="default top-level SoC YAML for configs without yaml:")
    ap.add_argument("--base-dir", default=None,
                    help="base dir for the parser / relative yaml: resolution")
    ap.add_argument("--lib-dir", action="append", default=[],
                    help="component-library dir (repeatable)")
    ap.add_argument("--select", default=None,
                    help="filter configs e.g. name=default or tier=generate")
    ap.add_argument("--subprocess", action="store_true",
                    help="use 'python -m soc_model' for validate too "
                         "(default: in-process GuiService)")
    ap.add_argument("--junit", default="ci/matrix_results.xml",
                    help="JUnit XML output path")
    args = ap.parse_args(argv)

    matrix = load_matrix(args.matrix)
    defaults = matrix.get("defaults") or {}
    base_yaml = args.base_yaml or defaults.get("base_yaml")
    base_dir = args.base_dir or defaults.get("base_dir") or (
        str(Path(base_yaml).parent) if base_yaml else ".")
    lib_dirs = args.lib_dir or defaults.get("lib_dirs") or [base_dir]

    results = run_matrix(
        matrix, base_yaml=base_yaml, base_dir=base_dir, lib_dirs=lib_dirs,
        tier=args.tier, in_process=not args.subprocess, select=args.select)

    print(format_table(results))
    if args.junit:
        write_junit(results, args.junit)
        print("-> %s" % args.junit)

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
