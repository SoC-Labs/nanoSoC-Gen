# Copyright 2026, SoC Labs (www.soclabs.org)
"""A linker region's LENGTH must be positive (review R-TRAIN5, LOW).

Before this, a region with `origin:` and `phys_size: 0` was written as
`LENGTH = 0x0`, and one whose size_adjust exceeded its size as
`LENGTH = 0x-300` (no linker parses it); a region with no phys_size of its
own takes its target's, so a target sized 0 did the same. The firmware
backend now refuses LENGTH <= 0 for every region, with or without `origin:`,
and the CLI refuses before anything is written.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from soc_model.backends.firmware import FirmwareError, SoCFirmwareBackend

from helpers import build_module_from_yaml
from test_linker_origin_refusals import _soc
from tests.helpers import arm_free_env

pytestmark = pytest.mark.unit

PKG_ROOT = Path(__file__).resolve().parents[2]


def _render(fixtures_dir, tmp_path, region):
    top = build_module_from_yaml(tmp_path, _soc(fixtures_dir, tmp_path, region).name)
    out = tmp_path / "fw"
    SoCFirmwareBackend(top).generate(out)
    return (out / "firmware_soc_tester_memory.ld").read_text()


@pytest.mark.parametrize("region, why", [
    # origin + phys_size 0: was `LENGTH = 0x0`
    ("{ target: dmem_0, linker_name: T, origin: 0x20000000, phys_size: 0 }", "LENGTH would be 0 "),
    # origin + size_adjust past the size: was `LENGTH = 0x-300`
    ("{ target: dmem_0, linker_name: T, origin: 0x20000000, phys_size: 0x100, size_adjust: -0x400 }",
     "LENGTH would be -768 "),
    # origin, no phys_size: the target's (0x4000) minus more than it
    ("{ target: dmem_0, linker_name: T, origin: 0x20000000, size_adjust: -0x4000 }",
     "LENGTH would be 0 \\(target 'dmem_0' phys_size 0x4000 \\+ size_adjust -16384\\)"),
    # no origin at all: the same rule
    ("{ target: dmem_0, address_select: default, size_adjust: -0x5000 }", "LENGTH would be -4096 "),
])
def test_non_positive_length_refused(fixtures_dir, tmp_path, region, why):
    with pytest.raises(FirmwareError, match=why):
        _render(fixtures_dir, tmp_path, region)


@pytest.mark.parametrize("region, want", [
    ("{ target: dmem_0, linker_name: T, origin: 0x20000000, phys_size: 1 }", "LENGTH = 0x1"),
    ("{ target: dmem_0, linker_name: T, origin: 0x20000000, size_adjust: -0x3FFF }", "LENGTH = 0x1"),
    ("{ target: dmem_0, linker_name: T, origin: 0x20000000 }", "LENGTH = 0x4000"),
])
def test_positive_length_still_written(fixtures_dir, tmp_path, region, want):
    assert want in _render(fixtures_dir, tmp_path, region)


def test_cli_refuses_before_writing(fixtures_dir, tmp_path):
    top = _soc(fixtures_dir, tmp_path,
               "{ target: dmem_0, linker_name: T, origin: 0x20000000, phys_size: 0 }")
    build = tmp_path / "build_soc"
    env = arm_free_env()
    env.update(PYTHONPATH=str(PKG_ROOT), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-m", "soc_model", str(top), "--build-dir", str(build),
                        "--lib-dir", str(tmp_path), "--lib-dir", str(fixtures_dir)],
                       cwd=str(PKG_ROOT), env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True, timeout=300)
    assert r.returncode == 1, r.stdout[-2000:] + r.stderr[-2000:]
    assert "LENGTH would be 0" in r.stderr and "Nothing was generated." in r.stderr
    assert not build.exists() or not any(p.is_file() for p in build.rglob("*"))
