# Copyright 2026, SoC Labs (www.soclabs.org)
"""`origin:` on a linker-profile region: a fixed ORIGIN for a CPU that is not an
initiator on the SoC's buses (the testbench's CMSDK debug tester, RAM at
0x2000_0000 by its own RTL). Before this, the parser dropped the key silently
and the region landed on the SoC target's address.

Covers every place the key must survive: the builder (YAML -> LinkerRegion),
the firmware backend (MEMORY origin and the profile's RW base in the .mk), and
the python backend (the re-loadable model keeps it).
"""
import shutil

import pytest

from soc_model.backends.firmware import SoCFirmwareBackend
from soc_model.backends.python import SoCPythonBackend

from helpers import build_module_from_yaml

TESTER_PROFILE = """
      # a CPU off the SoC's buses: its RAM is fixed by its own RTL
      - name: tester
        regions:
          - { target: imem_0, address_select: default }
          - { target: dmem_0, linker_name: TESTER_RAM, origin: 0x20000000, phys_size: 0x4000 }
"""


@pytest.fixture
def origin_soc(fixtures_dir, tmp_path):
    src = (fixtures_dir / "firmware_soc.yaml").read_text()
    anchor = "    linker_profiles:\n"
    assert anchor in src
    (tmp_path / "firmware_soc.yaml").write_text(src.replace(anchor, anchor + TESTER_PROFILE, 1))
    for f in fixtures_dir.iterdir():            # the fixture's library, unchanged
        if f.name != "firmware_soc.yaml" and f.is_file():
            shutil.copy(f, tmp_path / f.name)
    return build_module_from_yaml(tmp_path, "firmware_soc.yaml")


def _region(top, profile, idx):
    lp = next(p for p in top.firmware.linker_profiles if p.name == profile)
    return lp.regions[idx]


def test_builder_keeps_origin(origin_soc):
    assert _region(origin_soc, "tester", 1).origin == 0x20000000
    assert _region(origin_soc, "tester", 0).origin is None
    assert all(r.origin is None for r in _region_list(origin_soc, "app_imem"))


def _region_list(top, profile):
    return next(p for p in top.firmware.linker_profiles if p.name == profile).regions


def test_firmware_backend_places_region_at_origin(origin_soc, tmp_path):
    out = tmp_path / "fw"
    SoCFirmwareBackend(origin_soc).generate(out)
    ld = (out / "firmware_soc_tester_memory.ld").read_text()
    assert "TESTER_RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 0x4000" in ld
    assert "ORIGIN = 0x10000000, LENGTH = 0x4000" in ld          # the IMEM region: target address
    mk = (out / "firmware_soc_memmap.mk").read_text()
    assert "TESTER_RW_BASE = 0x20000000" in mk
    # a profile without origin is unchanged: DMEM at its target base
    app = (out / "firmware_soc_app_imem_memory.ld").read_text()
    assert "ORIGIN = 0x18000000" in app


def test_python_backend_keeps_origin(origin_soc, tmp_path):
    out = tmp_path / "model.py"
    SoCPythonBackend(origin_soc).generate(str(out))
    src = out.read_text()
    compile(src, str(out), "exec")
    assert "origin=536870912" in src                  # 0x20000000
    assert src.count("origin=") == 1                  # only the region that has one
