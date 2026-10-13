# Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
# SPDX-License-Identifier: Apache-2.0
"""Golden snapshots for the multicore MIXED-I/O pad ring in the bundled example
technology ``generic`` (lib/pin_map/generic_multicore.yaml): the ring .v and its
Innovus .io plan, byte for byte (timestamp/copyright lines stripped on both
sides by assert_matches_snapshot). Foundry rings are pinned by their own
goldens in the pad packs.

Regenerate with:  pytest tests/golden/test_pad_ring_generic_golden.py --snapshot-update
"""

import pytest

from soc_model.model import Module
from soc_model.parser import SoCParser
from soc_model.builder import SoCBuilder
from soc_model.backends.pad_ring import SoCPadRingBackend

from tests.conftest import FIXTURES, SNAPSHOTS
from tests.helpers import assert_matches_snapshot

pytestmark = pytest.mark.golden


def _render(tmp_path):
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    builder = SoCBuilder(parser)
    pad_tech = builder._build_pad_tech(parser.parse_pad_tech("generic")["pad_tech"])
    pin_map = builder._build_pin_map(parser.parse_pin_map("generic_multicore")["pin_map"])
    outs = SoCPadRingBackend(
        Module(name="nanosoc_multicore_soc"), None, pad_tech, pin_map
    ).generate(tmp_path / "rtl")
    return {p.suffix: p.read_text() for p in outs}


def test_generic_multicore_ring_golden(tmp_path, request):
    out = _render(tmp_path)
    text = out[".v"]
    assert "module nanosoc_chip_pads_generic_multicore (" in text
    assert "nanosoc_multicore_chip u_nanosoc_multicore_chip (" in text
    assert "PAD_INOUT8MA_NOE uPAD_HOSTIO4_6 (" in text
    assert "wire  [3:0]    qspi_io_z = ~qspi_io_e;" in text
    assert_matches_snapshot(
        text, SNAPSHOTS / "generic" / "nanosoc_chip_pads_generic_multicore.v", request)


def test_generic_multicore_io_plan_golden(tmp_path, request):
    text = _render(tmp_path)[".io"]
    assert "Technology/variant:  generic multicore" in text
    assert_matches_snapshot(
        text, SNAPSHOTS / "generic" / "nanosoc_io_plan_generic_multicore.io", request)
