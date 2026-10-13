# Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
# SPDX-License-Identifier: Apache-2.0
"""The pad-ring mechanism on the bundled example technology ``generic``.

``generic`` (lib/pad_tech/generic.yaml) uses the SoC Labs generic library pad
models, so it carries no foundry data. These tests cover what the foundry
rings in the pad packs rely on, with no pack present:

  * HERMETIC: tests/fixtures/pin_map/generic_mixedfix.yaml has one pad of every
    kind (single + bus), both OE polarities, a pad-name override and a small
    power ring. They pin down cell selection, net topology, OE inversion and
    placeholder coverage of the mixed-I/O ring.
  * BUNDLED: lib/pin_map/generic_multicore.yaml wraps nanosoc_multicore_chip
    (39 bonded inner ports -> 30 logical pads -> 43 pin-bits) with 16 power
    pads; it also carries the ring sides, so the .io plan is emitted.
  * UPF: the generic upf: block drives the power-intent emitter.
"""

import re
from collections import Counter

import pytest

from soc_model.model import Module
from soc_model.parser import SoCParser
from soc_model.builder import SoCBuilder
from soc_model.backends.pad_ring import SoCPadRingBackend
from soc_model.backends.upf import SoCUpfBackend

from tests.conftest import FIXTURES
from tests.helpers import build_module_from_yaml

pytestmark = pytest.mark.unit

CELL = "PAD_INOUT8MA_NOE"


def _backend(pin_map_name, sys_name="mixedfix_soc"):
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    builder = SoCBuilder(parser)
    pad_tech = builder._build_pad_tech(parser.parse_pad_tech("generic")["pad_tech"])
    pm_raw = parser.parse_pin_map(pin_map_name)
    assert pm_raw is not None, f"pin-map must resolve: {pin_map_name}"
    pin_map = builder._build_pin_map(pm_raw["pin_map"])
    return SoCPadRingBackend(Module(name=sys_name), None, pad_tech, pin_map)


def _gen(pin_map_name, tmp_path, sys_name="mixedfix_soc"):
    outs = _backend(pin_map_name, sys_name).generate(tmp_path / "rtl")
    v_path = next(p for p in outs if p.suffix == ".v")
    return v_path.read_text(), outs


def _pad_block(text, inst):
    m = re.search(re.escape(inst) + r"\s*\((.*?)\);", text, re.S)
    assert m, f"pad instance not found: {inst}"
    return m.group(1)


# ---------------------------------------------------------------------------
# the example technology
# ---------------------------------------------------------------------------
def test_generic_tech_is_bundled_and_complete():
    be = _backend("generic_mixedfix")
    pt = be.pad_tech
    assert pt.name == "generic"
    for cell in (pt.bidir_cell, pt.input_cell, pt.output_cell, pt.bidir_io_cell):
        assert cell is not None and cell.module == CELL
        assert list(cell.port_map) == ["PAD", "O", "I", "NOE"]   # order is emission order
    assert set(pt.power_cells) == {"PAD_VDDIO", "PAD_VSSIO", "PAD_VDDSOC", "PAD_VSS"}
    assert pt.upf and pt.upf["pst"]["supplies"] == ["VDDACC", "VDD", "VSS"]


def test_mixed_mode_selected_by_pin_map():
    be = _backend("generic_mixedfix")
    assert be._is_mixed() is True
    assert be.pin_map.chip_module == "mixedfix_chip"
    assert len(be.pin_map.pads) == 7


# ---------------------------------------------------------------------------
# hermetic ring
# ---------------------------------------------------------------------------
def test_generate_returns_v_and_flist(tmp_path):
    text, outs = _gen("generic_mixedfix", tmp_path)
    names = sorted(p.name for p in outs)
    assert names == ["nanosoc_chip_pads_generic_multicore.flist",
                     "nanosoc_chip_pads_generic_multicore.v"]
    assert "module nanosoc_chip_pads_generic_multicore (" in text
    assert "mixedfix_chip u_mixedfix_chip (" in text
    for ph in ("{i}", "{o}", "{e}", "{z}", "{c}", "{pad}"):
        assert ph not in text, f"unresolved placeholder {ph}"


def test_boundary_ports_by_kind(tmp_path):
    text, _ = _gen("generic_mixedfix", tmp_path)
    hdr = text.split("module nanosoc_chip_pads_generic_multicore (")[1].split("  );")[0]
    assert "input  wire          CLK_IN," in hdr
    assert "output wire          LED," in hdr            # pad-name override
    assert "input  wire [1:0]    RXD," in hdr
    assert "output wire [1:0]    TXD," in hdr
    assert "inout  wire          TDO," in hdr            # tristate -> inout pin
    assert "inout  wire          SDA," in hdr
    assert "inout  wire [3:0]    IO" in hdr


def test_input_and_output_cells(tmp_path):
    text, _ = _gen("generic_mixedfix", tmp_path)
    blk = _pad_block(text, "uPAD_CLK_IN")
    assert ".O(tielo)" in blk and ".NOE(tiehi)" in blk   # driver off
    assert ".I(clk_in)" in blk and ".PAD(CLK_IN)" in blk
    blk = _pad_block(text, "uPAD_LED_OUT")
    assert ".O(led_out)" in blk and ".NOE(tielo)" in blk  # always driving
    assert ".I(led_out_nc)" in blk and ".PAD(LED)" in blk
    assert "wire           led_out_nc;" in text


def test_oe_polarity(tmp_path):
    text, _ = _gen("generic_mixedfix", tmp_path)
    assert "wire           sda_z = ~sda_oe;" in text      # active-high -> inverted
    assert "wire           tdo_z = tdo_noe;" in text      # active-low -> straight
    assert ".NOE(sda_z)" in _pad_block(text, "uPAD_SDA")
    blk = _pad_block(text, "uPAD_TDO")
    assert ".NOE(tdo_z)" in blk and ".I(tdo_nc)" in blk


def test_bus_pads_expand_per_bit(tmp_path):
    text, _ = _gen("generic_mixedfix", tmp_path)
    for b in (0, 1):
        assert f".I(rxd[{b}])" in _pad_block(text, f"uPAD_RXD_{b}")
        assert f".O(txd[{b}])" in _pad_block(text, f"uPAD_TXD_{b}")
    assert "wire  [3:0]    io_z = ~io_e;" in text
    for b in range(4):
        blk = _pad_block(text, f"uPAD_IO_{b}")
        assert f".I(io_i[{b}])" in blk and f".O(io_o[{b}])" in blk
        assert f".NOE(io_z[{b}])" in blk and f".PAD(IO[{b}])" in blk
    assert "uPAD_IO_4" not in text
    assert text.count(f"{CELL} uPAD_") == 12            # 7 pads -> 12 pin-bits


def test_power_ring(tmp_path):
    text, _ = _gen("generic_mixedfix", tmp_path)
    for inst in ("PAD_VDDIO uPAD_VDDIO_0(", "PAD_VDDIO uPAD_VDDIO_1(",
                 "PAD_VSS uPAD_VSS_0(", "PAD_VSS uPAD_VSS_1("):
        assert inst in text
    assert ".PAD(VDDIO)" in text and ".PAD(VSS)" in text


def test_no_io_plan_without_sides(tmp_path):
    _, outs = _gen("generic_mixedfix", tmp_path)
    assert not any(p.suffix == ".io" for p in outs)


# ---------------------------------------------------------------------------
# bundled multicore ring
# ---------------------------------------------------------------------------
def test_multicore_ring(tmp_path):
    text, outs = _gen("generic_multicore", tmp_path, sys_name="nanosoc_multicore_soc")
    assert "nanosoc_multicore_chip u_nanosoc_multicore_chip (" in text
    assert text.count(f"{CELL} uPAD_") == 43
    assert len(re.findall(r"^PAD_(?:VDD|VSS)\w* uPAD_", text, re.M)) == 16
    inst = text.split("u_nanosoc_multicore_chip (")[1].split("  );")[0]
    assert len(re.findall(r"\.\w+\s*\(", inst)) == 39
    assert "wire           jtag_tdo_z = dap_ntdoen;" in text
    assert "wire  [3:0]    qspi_io_z = ~qspi_io_e;" in text
    assert ".PAD(HOSTIO4[6])" in text


def test_multicore_pad_kind_tally():
    be = _backend("generic_multicore", sys_name="nanosoc_multicore_soc")
    kinds = Counter(ps.kind for ps in be.pin_map.pads)
    assert (kinds["input"], kinds["output"], kinds["tristate_out"], kinds["bidir"]) == (14, 11, 1, 4)
    assert sum(ps.width for ps in be.pin_map.pads) == 43


def test_multicore_io_plan(tmp_path):
    _, outs = _gen("generic_multicore", tmp_path, sys_name="nanosoc_multicore_soc")
    io = next(p for p in outs if p.suffix == ".io")
    assert io.name == "nanosoc_io_plan_generic_multicore.io"
    assert io.parent.name == "pnr"
    text = io.read_text()
    for side in ("(top", "(left", "(bottom", "(right"):
        assert side in text
    # every signal pin-bit and every power pad is placed exactly once
    names = re.findall(r'inst\s+name="([^"]+)"', text)
    assert len(names) == len(set(names)) == 43 + 16


# ---------------------------------------------------------------------------
# UPF from the generic upf: block
# ---------------------------------------------------------------------------
def test_upf_from_generic_block(tmp_path):
    be = _backend("generic_mixedfix")
    top = build_module_from_yaml(FIXTURES, "two_region_soc.yaml")
    upf = SoCUpfBackend(top, be.pad_tech.upf, tech="generic", variant="multicore")
    path = upf.generate(tmp_path / "rtl", tmp_path / "flist")
    assert path.name == "nanosoc_chip_pads_generic_multicore.upf"
    text = path.read_text()
    assert "create_supply_set VDD_VSS" in text
    assert "add_pst_state aon -pst all_pst -state {state1}" in text
    assert text.count("connect_supply_net VDD -ports u_") == 2   # two SRAM regions
    assert "/u_mem_macro/VDD" in text
