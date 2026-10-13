# Copyright 2026, SoC Labs (www.soclabs.org)
"""What a linker-profile region's `origin:` must refuse, and what a region
refuses at all (review R-TRAIN5, 2026-09-30).

Until this, every one of these was ACCEPTED and written into the MEMORY block:
an unaligned origin, a float (truncated), YAML `true` (1), a value over 32 bits,
a negative one (written out as `0x-0000004`), a region that runs past 4 GiB,
and a misspelt key (`orign:`), dropped in silence so the region landed on the
SoC target's address.

The rules (soc_model/utils.py linker_origin, builder.LINKER_REGION_YAML_KEYS,
backends/firmware.py): an integer (or a string holding one), 0 .. 0xFFFF_FFFF,
a multiple of 4, origin + length <= 2**32; and a region takes only the keys the
builder reads. There is deliberately NO overlap check against the SoC's own
map: a profile with `origin:` describes another CPU's address space.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from soc_model.backends.firmware import SoCFirmwareBackend

from helpers import build_module_from_yaml
from tests.helpers import arm_free_env

PKG_ROOT = Path(__file__).resolve().parents[2]
ANCHOR = "    linker_profiles:\n"


def _soc(fixtures_dir, tmp_path, region):
    """firmware_soc.yaml with one extra profile whose second region is `region`."""
    src = (fixtures_dir / "firmware_soc.yaml").read_text()
    assert ANCHOR in src
    profile = ("      - name: tester\n        regions:\n"
               "          - { target: imem_0, address_select: default }\n"
               f"          - {region}\n")
    (tmp_path / "firmware_soc.yaml").write_text(src.replace(ANCHOR, ANCHOR + profile, 1))
    for f in fixtures_dir.iterdir():
        if f.name != "firmware_soc.yaml" and f.is_file():
            shutil.copy(f, tmp_path / f.name)
    return tmp_path / "firmware_soc.yaml"


def _tester(origin):
    return ("{ target: dmem_0, linker_name: TESTER_RAM, origin: %s, phys_size: 0x4000 }"
            % origin)


def _render(fixtures_dir, tmp_path, region):
    top = build_module_from_yaml(tmp_path, _soc(fixtures_dir, tmp_path, region).name)
    out = tmp_path / "fw"
    SoCFirmwareBackend(top).generate(out)
    return (out / "firmware_soc_tester_memory.ld").read_text()


# --- each accepted form still works -------------------------------------------
@pytest.mark.parametrize("origin", ["0x20000000", "0x2000_0000", '"0x20000000"',
                                    "536870912", "0xFFFFC000"])
def test_accepted_origins(fixtures_dir, tmp_path, origin):
    ld = _render(fixtures_dir, tmp_path, _tester(origin))
    want = "0xFFFFC000" if "FFFFC" in origin else "0x20000000"
    assert f"TESTER_RAM (rwx) : ORIGIN = {want}, LENGTH = 0x4000" in ld


# --- each rejection -------------------------------------------------------------
@pytest.mark.parametrize("origin, why", [
    ("0x20000003", "not word aligned"),          # unaligned
    ("536870912.7", "not float"),                # float, was truncated
    ("true", "not bool"),                        # YAML true, was 0x1
    ("0x1FFFFFFFF", "outside the 32-bit"),       # over 32 bits
    ("-4", "outside the 32-bit"),                # negative, was 0x-0000004
    ('"banana"', "is not an integer"),           # not a number at all
])
def test_rejected_origin(fixtures_dir, tmp_path, origin, why):
    with pytest.raises(ValueError, match=why):
        build_module_from_yaml(tmp_path, _soc(fixtures_dir, tmp_path, _tester(origin)).name)


def test_rejected_origin_past_4gib(fixtures_dir, tmp_path):
    """0xFFFFF000 + 0x4000 runs past 2**32: the firmware backend refuses it."""
    with pytest.raises(ValueError, match="past the 32-bit address space"):
        _render(fixtures_dir, tmp_path, _tester("0xFFFFF000"))


def test_rejected_misspelt_key(fixtures_dir, tmp_path):
    """`orign:` used to be dropped, so the region silently took dmem_0's address."""
    region = "{ target: dmem_0, linker_name: TESTER_RAM, orign: 0x20000000, phys_size: 0x4000 }"
    with pytest.raises(ValueError, match=r"unknown key 'orign' \(did you mean 'origin'\?\)"):
        build_module_from_yaml(tmp_path, _soc(fixtures_dir, tmp_path, region).name)


def test_rejected_unknown_key_without_origin(fixtures_dir, tmp_path):
    """Any linker region, not only one with origin: `size_ajust` is refused too."""
    region = "{ target: dmem_0, address_select: default, size_ajust: -0x400 }"
    with pytest.raises(ValueError, match=r"unknown key 'size_ajust' \(did you mean 'size_adjust'\?\)"):
        build_module_from_yaml(tmp_path, _soc(fixtures_dir, tmp_path, region).name)


def test_cli_refuses_before_writing(fixtures_dir, tmp_path):
    """The CLI stops before any backend writes: nothing under --build-dir."""
    top = _soc(fixtures_dir, tmp_path, _tester("0xFFFFF000"))
    build = tmp_path / "build_soc"
    env = arm_free_env()
    env.update(PYTHONPATH=str(PKG_ROOT), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-m", "soc_model", str(top), "--build-dir", str(build),
                        "--lib-dir", str(tmp_path), "--lib-dir", str(fixtures_dir)],
                       cwd=str(PKG_ROOT), env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, timeout=300)
    assert r.returncode == 1, r.stdout[-2000:] + r.stderr[-2000:]
    assert "past the 32-bit address space" in r.stderr
    assert "Nothing was generated." in r.stderr
    assert not build.exists() or not any(p.is_file() for p in build.rglob("*"))
