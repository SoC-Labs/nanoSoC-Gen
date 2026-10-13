# Copyright 2026, SoC Labs (www.soclabs.org)
"""Golden + unit tests for ``soc_model/backends/rdl.py``.

The RDL backend emits two layers:
  * the SystemRDL ``.rdl`` *text* — pure-Python string generation that needs
    **no toolchain** (default ``golden`` / ``unit`` tier), and
  * the regblock RTL (``.sv`` + APB wrapper) — needs ``systemrdl-compiler`` +
    ``peakrdl-regblock`` (opt-in ``tooling`` tier, SKIP if absent).

Per the area remit this file owns the ``.rdl`` TEXT golden plus the pure
helper units; the RTL emission rows (RD-13/RD-15) are kept in the ``tooling``
tier and ``importorskip`` cleanly when regblock is not installed.

The ``// Auto-generated SystemRDL`` / ``// Source:`` header lines are NOT
date-bearing and are NOT touched by ``_strip_timestamps`` (the timestamp regex
only matches ``// Generated: <date>`` and ``// Copyright YYYY,``), so the
``.rdl`` text is a stable golden.
"""

from pathlib import Path

import pytest

from soc_model.backends import rdl as rdl_mod
from soc_model.backends.rdl import (
    SoCRdlBackend,
    _format_reset_value,
    _needs_onread_rclr,
    _needs_onwrite_woclr,
    _parse_bits,
    _rdl_access,
    _sanitize_name,
)
from soc_model.model import (
    AddressDecode,
    AddressDecodeSlot,
    Instance,
    Module,
    Register,
    RegisterField,
    RegisterMap,
)

from tests.conftest import SNAPSHOTS
from tests.helpers import assert_matches_snapshot, build_module_from_yaml


# ---------------------------------------------------------------------------
# helpers — build a backend without going through __init__ (top unused by the
# pure helper paths) and inline RegisterMap factories.
# ---------------------------------------------------------------------------

def _backend(top=None):
    return SoCRdlBackend(top if top is not None else Module(name="_unused"))


def _fielded_regmap():
    """A RegisterMap mirroring the tiny_regs fixture but built inline so the
    .rdl golden is reproducible without depending on parser register-map
    resolution."""
    rm = RegisterMap(name="tiny_regs", module="tiny_apb_periph",
                     address_width=12, data_width=32,
                     desc="Minimal register map: CTRL + RO STATUS.",
                     source_file="register_maps/tiny_regs.yaml")
    rm.registers = [
        Register(name="CTRL", offset=0x000, width=32, access="RW",
                 reset_value=0x00000000, desc="Control register", fields=[
                     RegisterField(name="ENABLE", bits="0", access="RW",
                                   reset_value=0, desc="Enable bit"),
                     RegisterField(name="MODE", bits="2:1", access="RW",
                                   reset_value=0, desc="Mode select"),
                 ]),
        Register(name="STATUS", offset=0x004, width=32, access="RO",
                 reset_value=0x00000000, desc="Status register", fields=[
                     RegisterField(name="BUSY", bits="0", access="RO",
                                   reset_value=0, desc="Busy flag"),
                 ]),
    ]
    return rm


# ===========================================================================
# unit tier — pure helper functions
# ===========================================================================

class TestRdlAccess:
    pytestmark = pytest.mark.unit

    @pytest.mark.parametrize("yaml_access, sw", [
        ("RW", "rw"), ("RO", "r"), ("WO", "w"),
        ("W1C", "rw"), ("RC", "r"),
        ("FOO", "rw"),            # unknown -> default rw
        ("rw", "rw"),             # case-insensitive via .upper()
        ("w1c", "rw"),
    ])
    def test_rdl_access_map(self, yaml_access, sw):  # RD-03
        assert _rdl_access(yaml_access) == sw


class TestOnWriteOnRead:
    pytestmark = pytest.mark.unit

    @pytest.mark.parametrize("acc, expect", [
        ("W1C", True), ("w1c", True), ("RW", False), ("RC", False),
    ])
    def test_needs_onwrite_woclr(self, acc, expect):  # RD-04
        assert _needs_onwrite_woclr(acc) is expect

    @pytest.mark.parametrize("acc, expect", [
        ("RC", True), ("rc", True), ("RO", False), ("W1C", False),
    ])
    def test_needs_onread_rclr(self, acc, expect):  # RD-04
        assert _needs_onread_rclr(acc) is expect


class TestParseBits:
    pytestmark = pytest.mark.unit

    @pytest.mark.parametrize("s, expect", [
        ("7:0", (7, 0)),
        ("3", (3, 3)),
        ("31:16", (31, 16)),
        ("0", (0, 0)),
    ])
    def test_parse_bits(self, s, expect):  # RD-05
        assert _parse_bits(s) == expect


class TestSanitizeName:
    pytestmark = pytest.mark.unit

    @pytest.mark.parametrize("name, expect", [
        ("a-b.c", "a_b_c"),         # non-alnum -> _
        ("3x", "_3x"),              # leading digit -> _-prefixed
        ("CTRL", "CTRL"),           # already valid -> unchanged
        ("reg name", "reg_name"),   # space -> _
        ("", ""),                   # empty stays empty (no IndexError)
    ])
    def test_sanitize(self, name, expect):  # RD-06
        assert _sanitize_name(name) == expect


class TestFormatResetValue:
    pytestmark = pytest.mark.unit

    def test_none_zero(self):  # RD-07
        assert _format_reset_value(None) == "0"

    def test_int_width(self):
        assert _format_reset_value(5, 8) == "8'h5"
        assert _format_reset_value(0xABCD, 16) == "16'habcd"
        # default width is 32.
        assert _format_reset_value(1) == "32'h1"

    def test_hex_string_parsed(self):
        assert _format_reset_value("0x10", 8) == "8'h10"
        assert _format_reset_value("255", 8) == "8'hff"   # int(.., 0) decimal

    def test_bad_string_zero(self):
        assert _format_reset_value("xyz") == "0"

    def test_non_int_non_str_zero(self):
        assert _format_reset_value(1.5) == "0"


# ===========================================================================
# unit tier — _generate_register / _generate_field structure
# ===========================================================================

class TestGenerateRegister:
    pytestmark = pytest.mark.unit

    def test_no_fields_single_data_field(self):  # RD-08
        reg = Register(name="SIMPLE", offset=0x8, width=16, access="RW",
                       reset_value=0xABCD)
        lines = _backend()._generate_register(reg, 32)
        text = "\n".join(lines)
        # No field decomposition -> a single DATA[<width>] field.
        assert "DATA[16];" in text
        assert "sw = rw;" in text
        assert "reset = 16'habcd;" in text
        # Instantiated at its offset, sanitized name, 3-hex-digit @addr.
        assert "SIMPLE_t SIMPLE @0x008;" in text

    def test_fielded_register_emits_per_field_blocks(self):  # RD-08 / RD-11
        reg = _fielded_regmap().registers[0]  # CTRL with ENABLE + MODE
        lines = _backend()._generate_register(reg, 32)
        text = "\n".join(lines)
        assert "} ENABLE[0:0];" in text        # bits "0": explicit position (GEN-BACKLOG 22)
        assert "} MODE[2:1];" in text          # bits "2:1"
        assert "CTRL_t CTRL @0x000;" in text
        assert 'desc = "Control register";' in text

    def test_field_width_and_access(self):  # RD-11
        fld = RegisterField(name="SEL", bits="7:4", access="RO",
                            reset_value=0, desc="select")
        reg = Register(name="R", width=32)
        lines = _backend()._generate_field(fld, reg, 32)
        text = "\n".join(lines)
        assert "} SEL[7:4];" in text           # bits "7:4", at its position
        assert "sw = r;" in text               # RO -> r
        assert 'desc = "select";' in text

    def test_w1c_emits_onwrite_woclr(self):  # RD-09
        fld = RegisterField(name="DONE", bits="0", access="W1C", reset_value=0)
        text = "\n".join(_backend()._generate_field(fld, Register(name="R"), 32))
        assert "onwrite = woclr;" in text
        assert "sw = rw;" in text               # W1C modelled as rw

    def test_rc_emits_onread_rclr(self):  # RD-09
        fld = RegisterField(name="EVT", bits="0", access="RC", reset_value=0)
        text = "\n".join(_backend()._generate_field(fld, Register(name="R"), 32))
        assert "onread = rclr;" in text
        assert "sw = r;" in text                 # RC -> r

    def test_reset_and_desc_conditional(self):  # RD-11
        # reset_value None -> no reset line; no desc -> no desc line.
        fld = RegisterField(name="BARE", bits="0", access="RW", reset_value=None,
                            desc="")
        text = "\n".join(_backend()._generate_field(fld, Register(name="R"), 32))
        assert "reset =" not in text
        assert "desc =" not in text

    def test_no_fields_w1c_emits_onwrite_woclr(self):  # RD-09 (no-fields path)
        # A fieldless W1C register takes the single-DATA-field branch and must
        # still emit `onwrite = woclr;` (rdl.py:247) — distinct from the
        # per-field branch tested above.
        reg = Register(name="IRQSTAT", offset=0xC, width=8, access="W1C",
                       reset_value=0)
        text = "\n".join(_backend()._generate_register(reg, 32))
        assert "DATA[8];" in text            # took the no-fields branch
        assert "onwrite = woclr;" in text    # rdl.py:247 fired
        assert "onread = rclr;" not in text  # W1C is not read-clear
        assert "sw = rw;" in text            # W1C modelled as rw

    def test_no_fields_rc_emits_onread_rclr(self):  # RD-09 (no-fields path)
        # A fieldless RC (read-clear) register takes the single-DATA-field
        # branch and must emit `onread = rclr;` (rdl.py:249), with no woclr.
        reg = Register(name="EVTLOG", offset=0x10, width=16, access="RC",
                       reset_value=0)
        text = "\n".join(_backend()._generate_register(reg, 32))
        assert "DATA[16];" in text           # took the no-fields branch
        assert "onread = rclr;" in text      # rdl.py:249 fired
        assert "onwrite = woclr;" not in text
        assert "sw = r;" in text             # RC -> r


# ===========================================================================
# unit tier — _generate_rdl addrmap / regfile framing
# ===========================================================================

class TestGenerateRdlFraming:
    pytestmark = pytest.mark.unit

    def test_addrmap_and_regblock_names(self):  # RD-10
        rm = _fielded_regmap()
        text = _backend()._generate_rdl(rm)
        assert "regfile tiny_regs_regs {" in text
        assert "addrmap tiny_regs {" in text
        assert "default accesswidth = 32;" in text   # == data_width
        assert "tiny_regs_regs regs;" in text

    def test_data_width_drives_accesswidth(self):
        rm = _fielded_regmap()
        rm.data_width = 16
        text = _backend()._generate_rdl(rm)
        assert "default accesswidth = 16;" in text

    def test_header_lines_present_and_not_stripped(self):
        # RD-12 precondition: header is deterministic (no date) and survives
        # _strip_timestamps untouched.
        from soc_model.utils import _strip_timestamps
        rm = _fielded_regmap()
        text = _backend()._generate_rdl(rm)
        assert "// Auto-generated SystemRDL for tiny_regs" in text
        assert "// Source: register_maps/tiny_regs.yaml" in text
        # Both header lines survive normalisation.
        stripped = _strip_timestamps(text)
        assert "// Auto-generated SystemRDL for tiny_regs" in stripped
        assert "// Source: register_maps/tiny_regs.yaml" in stripped


# ===========================================================================
# unit tier — generate_all / collection
# ===========================================================================

@pytest.mark.unit
def test_generate_all_empty_when_no_register_maps(fixtures_dir, tmp_path):
    # RD-01: a SoC with no address_decode / register maps returns [] and does
    # NOT create the rdl/ dir.
    top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
    out = SoCRdlBackend(top).generate_all(tmp_path)
    assert out == []
    assert not (tmp_path / "rdl").exists()


@pytest.mark.unit
def test_collect_register_maps_dedup_and_nested(tmp_path):
    # RD-02: register maps are deduped by name across slots and collected
    # recursively through nested address decodes.
    rm_a = RegisterMap(name="dup_regs", module="m")
    rm_a.registers = [Register(name="R0", offset=0)]
    rm_b = RegisterMap(name="dup_regs", module="m2")   # same name -> deduped
    rm_b.registers = [Register(name="R1", offset=0)]
    rm_nested = RegisterMap(name="inner_regs", module="m3")
    rm_nested.registers = [Register(name="R2", offset=0)]

    nested_ad = AddressDecode(type="apb_slave_mux", slots=[
        AddressDecodeSlot(slot=0, name="inner", offset=0,
                          resolved_register_map=rm_nested),
    ])
    ad = AddressDecode(type="ahb_slave_mux", slots=[
        AddressDecodeSlot(slot=0, name="s0", offset=0, resolved_register_map=rm_a),
        AddressDecodeSlot(slot=1, name="s1", offset=0x1000,
                          resolved_register_map=rm_b),       # dup name
        AddressDecodeSlot(slot=2, name="s2", offset=0x2000,
                          address_decode=nested_ad),         # nested
    ])
    top = Module(name="t")
    top.address_decode = ad

    collected = SoCRdlBackend(top)._collect_register_maps(top, set())
    names = [rm.name for rm in collected]
    assert names.count("dup_regs") == 1          # deduped
    assert "inner_regs" in names                  # nested recursion reached
    # First-writer-wins on the dup name (rm_a kept, not rm_b).
    dup = next(rm for rm in collected if rm.name == "dup_regs")
    assert dup.module == "m"


@pytest.mark.unit
def test_collect_recurses_into_child_instances():
    # _collect_register_maps recurses through resolved child instances.
    rm = RegisterMap(name="child_regs", module="c")
    rm.registers = [Register(name="R", offset=0)]
    child = Module(name="child")
    child.address_decode = AddressDecode(type="apb_slave_mux", slots=[
        AddressDecodeSlot(slot=0, name="s", offset=0, resolved_register_map=rm),
    ])
    top = Module(name="t")
    top.instances = [Instance(instance_name="u_c", module_name="child",
                              resolved_module=child)]
    collected = SoCRdlBackend(top)._collect_register_maps(top, set())
    assert [rm.name for rm in collected] == ["child_regs"]


@pytest.mark.unit
def test_generate_all_gen_true_routes_to_rtl_via_address_decode(
        tmp_path, monkeypatch):
    # RD-13 (default-tier): a gen:True register map discovered through an
    # address_decode slot drives generate_all's `if rm.gen:` branch
    # (rdl.py:121-123). The peakrdl/systemrdl toolchain is the tooling tier, so
    # here we stub _generate_rtl to assert the WIRING: that generate_all (a)
    # discovers the slot's register map, (b) calls _generate_rtl with the
    # rtl_out path build_dir/rtl/<name>, and (c) propagates the returned flag
    # into the result tuple.
    rm = RegisterMap(name="ad_gen_regs", module="m", gen=True,
                     address_width=8, data_width=32)
    rm.registers = [Register(name="R0", offset=0, width=32, access="RW",
                             reset_value=0)]
    slot = AddressDecodeSlot(slot=0, name="ad_gen_regs", offset=0,
                             resolved_register_map=rm)
    ad = AddressDecode(type="apb_slave_mux", slots=[slot])
    top = Module(name="t")
    top.address_decode = ad

    captured = {}

    def _fake_rtl(self, rdl_path, regmap, rtl_out):
        captured["rdl_path"] = rdl_path
        captured["rm_name"] = regmap.name
        captured["rtl_out"] = rtl_out
        return True

    monkeypatch.setattr(SoCRdlBackend, "_generate_rtl", _fake_rtl)

    backend = SoCRdlBackend(top)
    generated = backend.generate_all(tmp_path)

    # Discovery reached the address_decode-slot register map.
    assert [name for name, _, _ in generated] == ["ad_gen_regs"]
    # The gen branch fired and routed the rtl_out path correctly (rdl.py:122).
    assert captured["rm_name"] == "ad_gen_regs"
    assert captured["rtl_out"] == tmp_path / "rtl" / "ad_gen_regs"
    assert captured["rdl_path"] == tmp_path / "rdl" / "ad_gen_regs.rdl"
    # And the rtl_generated flag from _generate_rtl propagated (rdl.py:123,125).
    name, rdl_path, rtl_generated = generated[0]
    assert rtl_generated is True
    # The .rdl text itself was still written (no toolchain needed for that).
    assert rdl_path.exists()
    assert "addrmap ad_gen_regs {" in rdl_path.read_text()


@pytest.mark.unit
def test_generate_single_gen_true_routes_to_rtl(tmp_path, monkeypatch):
    # RD-13 (default-tier): generate_single's `if rm.gen:` branch
    # (rdl.py:146-148) routes to _generate_rtl with build_dir/rtl/<name> and
    # returns its flag. Stub _generate_rtl to stay out of the tooling tier.
    rm = _fielded_regmap()
    rm.gen = True

    captured = {}

    def _fake_rtl(self, rdl_path, regmap, rtl_out):
        captured["rtl_out"] = rtl_out
        return True

    monkeypatch.setattr(SoCRdlBackend, "_generate_rtl", _fake_rtl)

    rdl_path, rtl_generated = SoCRdlBackend(Module("t")).generate_single(
        rm, tmp_path)
    assert rtl_generated is True                       # propagated (rdl.py:148)
    assert captured["rtl_out"] == tmp_path / "rtl" / "tiny_regs"  # rdl.py:147
    assert rdl_path.exists()


@pytest.mark.unit
def test_generate_single_writes_rdl_no_rtl_when_gen_false(tmp_path):
    rm = _fielded_regmap()
    assert rm.gen is False
    rdl_path, rtl_generated = SoCRdlBackend(Module("t")).generate_single(rm, tmp_path)
    assert rdl_path == tmp_path / "rdl" / "tiny_regs.rdl"
    assert rdl_path.exists()
    assert rtl_generated is False
    body = rdl_path.read_text()
    assert "addrmap tiny_regs {" in body


# ===========================================================================
# golden tier — full .rdl text snapshots
# ===========================================================================

@pytest.mark.golden
def test_rdl_golden_inline_fielded_regmap(tmp_path, request):
    # RD-12: a fielded register map renders a byte-stable .rdl. Built inline so
    # the golden does not depend on parser register-map resolution.
    rm = _fielded_regmap()
    rdl_path, _ = SoCRdlBackend(Module("t")).generate_single(rm, tmp_path)
    text = rdl_path.read_text()

    # Structural teeth so an empty/error golden cannot silently pass.
    assert "// Auto-generated SystemRDL for tiny_regs" in text
    assert "regfile tiny_regs_regs {" in text
    assert "reg CTRL_t {" in text
    assert "} ENABLE[0:0];" in text
    assert "} MODE[2:1];" in text
    assert "reg STATUS_t {" in text
    assert "} BUSY[0:0];" in text
    assert "addrmap tiny_regs {" in text
    assert "default accesswidth = 32;" in text

    assert_matches_snapshot(text, SNAPSHOTS / "rdl_inline" / "tiny_regs.rdl", request)


@pytest.mark.golden
def test_rdl_golden_from_apb_bridge_fixture(fixtures_dir, tmp_path, request):
    # End-to-end through the parser/builder: apb_bridge_soc resolves the
    # tiny_regs register map via the tiny_apb_periph address-decode slot, so
    # generate_all walks the hierarchy and emits rdl/tiny_regs.rdl.
    top = build_module_from_yaml(fixtures_dir, "apb_bridge_soc.yaml")
    generated = SoCRdlBackend(top).generate_all(tmp_path)
    assert [name for name, _, _ in generated] == ["tiny_regs"]

    rdl_path = tmp_path / "rdl" / "tiny_regs.rdl"
    assert rdl_path.exists()
    text = rdl_path.read_text()

    # Structural teeth.
    assert "regfile tiny_regs_regs {" in text
    assert "addrmap tiny_regs {" in text
    assert "} MODE[2:1];" in text

    assert_matches_snapshot(
        text, SNAPSHOTS / "apb_bridge_soc" / "tiny_regs.rdl", request)


# ===========================================================================
# unit tier — ImportError fallback (RD-14)
# ===========================================================================

@pytest.mark.unit
def test_generate_rtl_import_error_returns_false(tmp_path, monkeypatch, capsys):
    # RD-14: when systemrdl/peakrdl cannot be imported, _generate_rtl warns and
    # returns False without raising. Force the ImportError by blocking the
    # module imports.
    import builtins
    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name.startswith("systemrdl") or name.startswith("peakrdl"):
            raise ImportError(f"blocked {name} for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)

    rm = _fielded_regmap()
    rm.gen = True
    rdl_path = tmp_path / "tiny_regs.rdl"
    rdl_path.write_text(_backend()._generate_rdl(rm))

    result = SoCRdlBackend(Module("t"))._generate_rtl(rdl_path, rm, tmp_path / "rtl")
    assert result is False
    out = capsys.readouterr().out
    assert "WARNING" in out
    # No RTL output dir created on the import-failure path.
    assert not (tmp_path / "rtl").exists()


# ===========================================================================
# tooling tier — regblock RTL emission (SKIP if regblock absent)
# ===========================================================================

@pytest.mark.tooling
def test_rtl_emission_gen_true(tmp_path):
    # RD-13 / RD-15: a gen:True register map drives peakrdl-regblock RTL
    # emission producing the regblock .sv plus an APB wrapper with the correct
    # PADDR width and tied-off pprot/pstrb. SKIPs cleanly when regblock is not
    # installed.
    pytest.importorskip("systemrdl")
    pytest.importorskip("peakrdl_regblock")

    rm = _fielded_regmap()
    rm.gen = True
    rm.address_width = 8
    generated = SoCRdlBackend(Module("t")).generate_single(rm, tmp_path)
    rdl_path, rtl_generated = generated
    assert rdl_path.exists()
    assert rtl_generated is True, "regblock RTL emission should succeed when installed"

    rtl_dir = tmp_path / "rtl" / "tiny_regs"
    assert rtl_dir.is_dir()
    svs = list(rtl_dir.glob("*.sv"))
    assert svs, "expected at least one emitted .sv"

    wrapper = rtl_dir / "tiny_regs_apb_wrapper.sv"
    assert wrapper.exists()
    wtext = wrapper.read_text()
    assert "module tiny_regs_apb_wrapper" in wtext
    assert "[7:0] PADDR" in wtext            # address_width-1 = 7
    assert "3'b000" in wtext                  # pprot tied off
    assert "4'b1111" in wtext                 # pstrb tied off


@pytest.mark.tooling
def test_rtl_not_emitted_for_gen_false(tmp_path):
    # RD-13: gen:False does NOT emit RTL even when regblock is available.
    pytest.importorskip("systemrdl")
    pytest.importorskip("peakrdl_regblock")
    rm = _fielded_regmap()
    assert rm.gen is False
    _, rtl_generated = SoCRdlBackend(Module("t")).generate_single(rm, tmp_path)
    assert rtl_generated is False
    assert not (tmp_path / "rtl" / "tiny_regs").exists()
