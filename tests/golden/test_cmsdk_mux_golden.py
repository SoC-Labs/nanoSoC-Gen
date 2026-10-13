# Copyright 2026, SoC Labs (www.soclabs.org)
"""Golden snapshots for ``impl: cmsdk_mux``.

* ``cmsdk_mux_soc``: the split topology (private passthrough path, boot
  remap), shaped like the Quickstart nanoSoC CPU-subsystem interconnect.
* ``cmsdk_mux_shared``: ``two_region_soc`` switched to cmsdk_mux with four
  initiators seeing different maps: a cascaded master mux and a decoder keyed
  by the address-phase HMASTER, shaped like the Quickstart main interconnect.
"""

import pytest

from soc_model.backends.ahb import SoCAhbBackend
from soc_model.model import InterconnectInitiator, InterconnectInitiatorTarget

from tests.conftest import SNAPSHOTS
from tests.helpers import build_module_from_yaml, assert_matches_snapshot

pytestmark = pytest.mark.golden


def test_cmsdk_mux_split_golden(fixtures_dir, tmp_path, request):
    top = build_module_from_yaml(fixtures_dir, "cmsdk_mux_soc.yaml")
    SoCAhbBackend(top).generate_all(tmp_path)
    ic = "cmsdk_mux_ahb_interconnect"
    for rel, snap in [
        (f"rtl/{ic}/{ic}_lite.v", f"{ic}_lite.v"),
        ("flist/cmsdk_mux_interconnect.flist", "cmsdk_mux_interconnect.flist"),
        ("flist/cmsdk_mux_components.flist", "cmsdk_mux_components.flist"),
        (f"rtl/{ic}/cmsdk_mux_interconnect.sv", "cmsdk_mux_interconnect.sv"),
        (f"interconnect/{ic}/address_maps/{ic}_address_maps.txt", f"{ic}_address_maps.txt"),
    ]:
        assert_matches_snapshot((tmp_path / rel).read_text(),
                                SNAPSHOTS / "cmsdk_mux_soc" / snap, request)


def test_cmsdk_mux_shared_cascade_golden(fixtures_dir, tmp_path, request):
    top = build_module_from_yaml(fixtures_dir, "two_region_soc.yaml")
    ic = top.interconnects[0]
    ic.impl = "cmsdk_mux"
    for k, tgts in [(1, ["sram_1"]), (2, ["sram_0", "sram_1"]), (3, ["sram_0", "sram_1"])]:
        ic.initiators.append(InterconnectInitiator(
            name=f"dma_{k}", targets=[InterconnectInitiatorTarget(name=t) for t in tgts]))
    SoCAhbBackend(top).generate_all(tmp_path)
    name = ic.name
    assert_matches_snapshot((tmp_path / f"rtl/{name}/{name}_lite.v").read_text(),
                            SNAPSHOTS / "cmsdk_mux_shared" / f"{name}_lite.v", request)
