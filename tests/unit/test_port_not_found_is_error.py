# Copyright 2026, SoC Labs (www.soclabs.org)
"""GEN-BACKLOG 23: a connection to a port the resolved module lacks is an ERROR.

It was a WARNING with exit 0. The parser resolves a module from the LAST lib dir
that has it, so the QuickStart's reversed --lib-dir order (the project's
sys_desc first, arch_tech's last) took arch_tech's nanosoc_ss_cpu, which has no
cpu_0_swd, and rendered a SoC WITHOUT SWD while printing one warning.

Fixture libdir_order/: dut_child in two lib dirs; only proj/'s copy has swdio.
Bundle members of a hand-written module (GEN-BACKLOG 20) stay recognised, so
they never become errors; a real typo among them does.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from tests.helpers import arm_free_env

pytestmark = pytest.mark.unit

PKG_ROOT = Path(__file__).resolve().parents[2]
FIX = PKG_ROOT / "tests" / "fixtures"
LIBDIR = FIX / "libdir_order"
REPRO = FIX / "bundle_ports"
NOT_FOUND = "Port 'swdio' not found on module 'dut_child'"


def _cli(top, lib_dirs, *extra):
    env = arm_free_env()
    env.update(PYTHONPATH=str(PKG_ROOT), PYTHONDONTWRITEBYTECODE="1")
    cmd = [sys.executable, "-m", "soc_model", str(top)]
    for d in lib_dirs:
        cmd += ["--lib-dir", str(d)]
    return subprocess.run(cmd + list(extra), cwd=str(PKG_ROOT), env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=300)


def test_right_lib_dir_order_validates_clean():
    r = _cli(LIBDIR / "lib_top.yaml", [LIBDIR / "arch", LIBDIR / "proj"], "--validate-only")
    assert r.returncode == 0, r.stdout[-2000:]
    assert "0 error(s), 0 warning(s)" in r.stdout


def test_reversed_lib_dir_order_fails_validation():
    r = _cli(LIBDIR / "lib_top.yaml", [LIBDIR / "proj", LIBDIR / "arch"], "--validate-only")
    assert r.returncode == 1, r.stdout[-2000:]
    assert f"[ERROR] (reference) u_child.swdio: {NOT_FOUND}" in r.stdout
    assert "the LAST lib dir that has a module wins" in r.stdout
    assert "1 error(s), 0 warning(s)" in r.stdout


def test_reversed_lib_dir_order_refuses_the_render(tmp_path):
    build = tmp_path / "build"
    r = _cli(LIBDIR / "lib_top.yaml", [LIBDIR / "proj", LIBDIR / "arch"], "--build-dir", str(build))
    assert r.returncode == 1, r.stdout[-2000:]
    assert "refusing to generate: 1 validation error(s)" in r.stderr
    assert NOT_FOUND in r.stderr
    assert not build.exists()


def test_a_named_waiver_passes_it():
    r = _cli(LIBDIR / "lib_top.yaml", [LIBDIR / "proj", LIBDIR / "arch"], "--validate-only",
             "--validation-waive", "reference:u_child.swdio=tied off in the wrapper")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "WAIVED validation error reference:u_child.swdio (1)" in r.stdout


def test_hand_written_bundle_members_are_not_errors(tmp_path):
    # The repro without its planted typo: 7 hand-written member spellings, 0 errors.
    y = tmp_path / "repro_top.yaml"
    y.write_text("".join(l for l in (REPRO / "repro_top.yaml").read_text().splitlines(True)
                         if "USRT0_TXD_TVALIX" not in l))
    r = _cli(y, [REPRO / "lib"], "--validate-only")
    assert r.returncode == 0, r.stdout[-3000:]
    assert "0 error(s)" in r.stdout


def test_a_typo_among_bundle_members_is_an_error():
    r = _cli(REPRO / "repro_top.yaml", [REPRO / "lib"], "--validate-only")
    assert r.returncode == 1, r.stdout[-3000:]
    errs = re.findall(r"\[ERROR\] \(reference\) u_periph\.(\S+): Port", r.stdout)
    assert errs == ["USRT0_TXD_TVALIX"]
    assert "1 error(s), 0 warning(s)" in r.stdout
