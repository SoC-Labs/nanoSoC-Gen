"""The SRAM report sizes a RAM from the unit of its address width.

sl_ahb_sram / sl_sram slice HADDR[RAM_ADDR_W-1:0]: RAM_ADDR_W is a BYTE width,
so a region with RAM_ADDR_W=14 holds 2**14 bytes = 4096 32-bit words (16 KB).
The report used 2**RAM_ADDR_W as a WORD depth and printed 16384 x 32-b = 64 KB
for each of the Quickstart's 16 KB RAMs (Total 256 KB for four).
"""
import pytest

from soc_model.backends.sram import SoCSramBackend
from soc_model.builder import SoCBuilder
from soc_model.model import Instance, Module, Param, SramEntry
from soc_model.parser import SoCParser


def _region(name, aw=14):
    m = Module(name=name, gen=False)
    m.params = {"RAM_ADDR_W": Param(name="RAM_ADDR_W", type="int", default=aw),
                "RAM_DATA_W": Param(name="RAM_DATA_W", type="int", default=32)}
    m.srams = [SramEntry(name="u_sram", addr_width="$RAM_ADDR_W", data_width="$RAM_DATA_W")]
    return m


def _row(txt, path):
    line = next(l for l in txt.splitlines() if path in l)
    return line.split()


def test_ram_addr_w_is_a_byte_width(tmp_path):
    top = Module(name="nanosoc", gen=True)
    for n in range(4):
        i = Instance(instance_name=f"u_region_sram_{n}", module_name="nanosoc_region_sram")
        i.params = {"RAM_ADDR_W": 14}
        i.resolved_module = _region("nanosoc_region_sram", aw=10)
        top.instances.append(i)
    out = tmp_path / "sram.txt"
    SoCSramBackend(top).generate(str(out))
    txt = out.read_text()
    row = _row(txt, "nanosoc.u_region_sram_0.u_sram")
    assert row[2] == "4096", txt          # words: 2**14 bytes / 4
    assert row[3:6] == ["32-b", "16", "KB"], txt
    assert "Total: 4 SRAM(s), 64 KB" in txt, txt


def test_literal_width_stays_a_word_width(tmp_path):
    """eth_wishbone's BD RAM: addr_width 8, 256 x 32 (1 KB)."""
    top = Module(name="t", gen=True)
    top.srams = [SramEntry(name="bd_ram", addr_width=8, data_width=32)]
    out = tmp_path / "sram.txt"
    SoCSramBackend(top).generate(str(out))
    row = _row(out.read_text(), "t.bd_ram")
    assert row[2] == "256" and row[4:6] == ["1", "KB"]


@pytest.mark.parametrize("aw,unit,depth,size", [
    ("$AW", "byte", "1024", ["4", "KB"]),
    ("$RAM_ADDR_W", "word", "4096", ["16", "KB"]),
])
def test_explicit_addr_unit_wins(tmp_path, aw, unit, depth, size):
    top = Module(name="t", gen=True)
    top.params = {"AW": Param(name="AW", type="int", default=12),
                  "RAM_ADDR_W": Param(name="RAM_ADDR_W", type="int", default=12)}
    top.srams = [SramEntry(name="r", addr_width=aw, data_width=32, addr_unit=unit)]
    out = tmp_path / "sram.txt"
    SoCSramBackend(top).generate(str(out))
    row = _row(out.read_text(), "t.r")
    assert row[2] == depth and row[4:6] == size


def test_builder_reads_and_checks_addr_unit(tmp_path):
    b = SoCBuilder(SoCParser(str(tmp_path)))
    m = b._build_module({"name": "m", "srams": [
        {"name": "a", "addr_width": "$RAM_ADDR_W"},
        {"name": "b", "addr_width": 8, "addr_unit": "byte"}]})
    assert [s.unit for s in m.srams] == ["byte", "byte"]
    with pytest.raises(ValueError, match="addr_unit"):
        b._build_module({"name": "m2", "srams": [{"name": "c", "addr_unit": "bytes"}]})


def test_an_unknown_parameter_without_addr_unit_is_refused(tmp_path):
    """Train 7 R-LOW-4: only $RAM_ADDR_W (the sl_ahb_sram byte width) and a literal width
    have a default unit; any other parameter must say addr_unit, never a guess."""
    b = SoCBuilder(SoCParser(str(tmp_path)))
    with pytest.raises(ValueError, match=r"'\$MEM_AW' has no addr_unit"):
        b._build_module({"name": "m3", "srams": [{"name": "d", "addr_width": "$MEM_AW"}]})
    m = b._build_module({"name": "m4", "srams": [{"name": "d", "addr_width": "$MEM_AW", "addr_unit": "byte"},
                                                  {"name": "e", "addr_width": 8}]})
    assert [s.unit for s in m.srams] == ["byte", "word"]
    with pytest.raises(ValueError, match="has no addr_unit"):
        SramEntry(name="x", addr_width="$AW").unit
