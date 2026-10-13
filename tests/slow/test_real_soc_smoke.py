# Copyright 2026, SoC Labs (www.soclabs.org)
"""Slow-tier smoke test driving the REAL multicore SoC YAML end-to-end.

Per TEST_PLAN.md §5.14 (SL-01, SL-02, SL-04). This is the ``slow`` tier
(``pytest -m slow``); it must SKIP — never FAIL — when the real
``nanosoc_multicore_soc.yaml`` or any of its required ``--lib-dir``
component trees / submodules are absent (which is the normal state of the
generator-only worktree).

When everything resolves it:
  * builds the real ~117 KB SoC through the full SoCParser -> SoCBuilder
    seam (SL-01),
  * checks SoCValidator parity with ``python -m soc_model --validate-only``
    (the CLI exits 1 iff there are errors) (SL-02), and
  * emits the toplevel / discovery / config-pkg artifacts into tmp_path and
    asserts they exist with real content (SL-04).

No EDA tools are required: only YAML parsing + jinja2 codegen (text only).
The AHB perl path is NOT exercised here (arm_ip_library_path=None), so the
read-only vendor IP tree is never touched.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Worktree root holding the importable ``soc_model`` package (parents[2] of
# this file: tests/slow/ -> tests/ -> worktree root). Used to set PYTHONPATH
# for the CLI subprocess, which does not inherit conftest's in-process
# sys.path shim.
_PKG_ROOT = Path(__file__).resolve().parents[2]

from soc_model.backends.soc_config_pkg import SoCConfigPkgBackend
from soc_model.backends.discovery import SoCDiscoveryBackend
from soc_model.backends.toplevel import SoCTopLevelBackend
from soc_model.builder import SoCBuilder
from soc_model.parser import SoCParser
from soc_model.validator import SoCValidator

# ---------------------------------------------------------------------------
# Real-config location (the multicore SoC superproject), and its lib-dir set
# mirrored from sys_desc/Makefile's LIB_DIRS. Resolved PORTABLY (no hardcoded
# home path): an explicit NANOSOC_REAL_ROOT env override wins; otherwise the
# normal submodule layout <root>/nanosoc_arch_tech/nanosoc_gen places the
# package two levels below the superproject root. When neither yields the real
# YAML the whole module skips (the normal state of a sibling/standalone
# generator worktree — set NANOSOC_REAL_ROOT to run it here). All paths are
# absolute so the test is independent of pytest's cwd.
# ---------------------------------------------------------------------------
def _resolve_real_root():
    candidates = []
    env = os.environ.get("NANOSOC_REAL_ROOT")
    if env:
        candidates.append(Path(env))
    # Normal checkout: <superproject>/nanosoc_arch_tech/nanosoc_gen/
    candidates.append(_PKG_ROOT.parents[1])
    for c in candidates:
        if (c / "sys_desc" / "nanosoc_multicore_soc.yaml").is_file():
            return c
    # Well-defined fallback so the path constants below resolve; the
    # _missing_prereqs() guard then produces the skip reason.
    return candidates[0]


_REAL_ROOT = _resolve_real_root()
_REAL_YAML_DIR = _REAL_ROOT / "sys_desc"
_REAL_YAML = _REAL_YAML_DIR / "nanosoc_multicore_soc.yaml"

# Required component library directories (subset of Makefile LIB_DIRS that the
# top YAML actually needs to fully resolve). If any is missing we SKIP.
_REQUIRED_LIB_DIRS = [
    _REAL_YAML_DIR,
    _REAL_ROOT / "nanosoc_arch_tech" / "sys_desc",
    _REAL_ROOT / "nanosoc_arch_tech" / "sys_desc" / "regions",
    _REAL_ROOT / "nanosoc_arch_tech" / "sys_desc" / "subsystems",
    _REAL_ROOT / "ethernet-subsystem-ahb" / "sys_desc",
    _REAL_ROOT / "ethernet-subsystem-ahb" / "sys_desc" / "regions",
    _REAL_ROOT / "ahb_qspi" / "sys_desc",
]


def _missing_prereqs():
    """Return a human-readable reason string if anything is absent, else ''."""
    if not _REAL_YAML.is_file():
        return f"real SoC YAML not found: {_REAL_YAML}"
    missing = [str(d) for d in _REQUIRED_LIB_DIRS if not d.is_dir()]
    if missing:
        return ("required lib-dir(s)/submodule(s) absent: "
                + ", ".join(missing))
    return ""


_SKIP_REASON = _missing_prereqs()
# Module-level guard: every test below skips with the same clear reason when
# the real config or its component trees are not initialised here.
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON
                       or "real SoC present"),
]


@pytest.fixture(scope="module")
def real_top():
    """Build the real multicore SoC Module once for the module."""
    parser = SoCParser(str(_REAL_YAML_DIR),
                       [str(d) for d in _REQUIRED_LIB_DIRS])
    return SoCBuilder(parser).build_system(_REAL_YAML.name)


def test_real_soc_builds(real_top):  # SL-01
    """The real SoC parses + builds into a resolved Module."""
    assert real_top.name == "nanosoc_multicore_soc"
    assert real_top.instances, "real SoC should have instances"
    assert real_top.interconnects, "real SoC should have at least one interconnect"
    # Most instances should resolve (the build is meaningless if none do).
    resolved = sum(1 for i in real_top.instances if i.resolved_module)
    assert resolved > 0, "no instances resolved — lib-dir set is incomplete"


def test_validate_only_parity(real_top):  # SL-02
    """In-process SoCValidator agrees with the CLI --validate-only verdict.

    The CLI exits 1 iff there are errors (``__main__`` :116). We assert the
    in-process validator's error/warning shape, then confirm the CLI exit
    code matches (errors -> 1, clean -> 0).
    """
    validator = SoCValidator(real_top)
    validator.validate_all()
    n_err = len(validator.errors)
    n_warn = len(validator.warnings)
    assert n_err >= 0 and n_warn >= 0  # shape sanity

    # Drive the real CLI; base_dir is the yaml's parent (matches __main__).
    cmd = [sys.executable, "-m", "soc_model", str(_REAL_YAML)]
    for d in _REQUIRED_LIB_DIRS:
        cmd += ["--lib-dir", str(d)]
    cmd.append("--validate-only")
    # The subprocess does not inherit conftest's in-process sys.path shim, so
    # make `soc_model` importable via PYTHONPATH (the worktree root).
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_PKG_ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    proc = subprocess.run(
        cmd, cwd=str(_REAL_YAML_DIR), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, timeout=180,
    )
    expected_exit = 1 if n_err else 0
    assert proc.returncode == expected_exit, (
        f"CLI exit {proc.returncode} != expected {expected_exit} "
        f"(in-process errors={n_err}); stderr tail:\n{proc.stderr[-2000:]}"
    )
    # The CLI prints its own count line; sanity-check the error count agrees.
    assert f"{n_err} error(s)" in proc.stdout, (
        "CLI error-count line did not match in-process validator; "
        f"stdout tail:\n{proc.stdout[-2000:]}"
    )


def test_real_artifacts_emit(real_top, tmp_path):  # SL-04
    """Toplevel / discovery / config-pkg artifacts emit for the real config."""
    rtl_dir = tmp_path / "rtl"
    flist_dir = tmp_path / "flist"
    rtl_dir.mkdir()
    flist_dir.mkdir()

    # Toplevel SV.
    sv_path = SoCTopLevelBackend(real_top).generate(rtl_dir, flist_dir)
    assert sv_path is not None and sv_path.exists()
    sv_txt = sv_path.read_text()
    assert f"module {real_top.name}" in sv_txt
    assert "endmodule" in sv_txt

    # Config package.
    pkg_path = SoCConfigPkgBackend(real_top).generate(rtl_dir, flist_dir)
    assert pkg_path is not None and pkg_path.exists()
    assert f"package {real_top.name}_soc_config_pkg;" in pkg_path.read_text()

    # Discovery YAML(s) — at least one gen:True interconnect table.
    disc = SoCDiscoveryBackend(real_top).generate(tmp_path)
    assert disc, "no discovery tables emitted for real SoC"
    for _ic_name, _rm, path in disc:
        assert Path(path).exists()
        assert Path(path).read_text().strip()
