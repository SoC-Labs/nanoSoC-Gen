# Copyright 2026, SoC Labs (www.soclabs.org)
"""GEN-BACKLOG 22: every RDL field sits at its YAML `bits:` position.

`_generate_field` parsed `bits:` into (high, low) but emitted `} NAME[width];`.
SystemRDL then packs such fields from bit 0 in declaration order, so a register
with a gap, or with its fields listed high-to-low, came out wrong and nothing
failed. Proven on the AES-128 accelerator (CTRL BYPASS/ENCODE at 4/5, not 6/7:
every encrypt ran as a decrypt) and on cmsdk_apb_dualtimers Timer{1,2}Control.
Now `} NAME[high:low];`.

Two registers here: AES_CTRL has a gap (bits 4-5 unused), PID1 lists its fields
high nibble first (as coresight_rom_table does). Each check runs on the
generated text and on the old `[width]` form, which must FAIL it:
  * a toolchain-free model of SystemRDL placement (implicit `[w]` = the next bit
    after the previous field; explicit `[h:l]` = there), and
  * the real compiler (systemrdl-compiler), when installed.
"""
import re

import pytest

from soc_model.backends.rdl import SoCRdlBackend
from soc_model.model import Module, Register, RegisterField, RegisterMap

pytestmark = pytest.mark.golden

YAML_BITS = {
    "AES_CTRL": {"MODE": (3, 0), "BYPASS": (6, 6), "ENCODE": (7, 7)},
    "PID1": {"JEP106_ID_3_0": (7, 4), "PART_1": (3, 0)},
}
ORDER = {"AES_CTRL": [("MODE", "3:0"), ("BYPASS", "6"), ("ENCODE", "7")],
         "PID1": [("JEP106_ID_3_0", "7:4"), ("PART_1", "3:0")]}

GOLDEN_FIELD_LINES = {
    "AES_CTRL": ["} MODE[3:0];", "} BYPASS[6:6];", "} ENCODE[7:7];"],
    "PID1": ["} JEP106_ID_3_0[7:4];", "} PART_1[3:0];"],
}


def _regmap():
    rm = RegisterMap(name="pos_regs", module="pos_periph", address_width=12, data_width=32,
                     desc="GEN-BACKLOG 22: gapped and descending field lists",
                     source_file="register_maps/pos_regs.yaml", gen=False)
    rm.registers = [
        Register(name=r, offset=4 * i, width=32, access="RW", reset_value=0, desc=r,
                 fields=[RegisterField(name=n, bits=b, access="RW", reset_value=0, desc=n)
                         for n, b in ORDER[r]])
        for i, r in enumerate(ORDER)
    ]
    return rm


@pytest.fixture
def rdl_text(tmp_path):
    path, rtl = SoCRdlBackend(Module("t")).generate_single(_regmap(), tmp_path)
    assert rtl is False
    return path.read_text()


def _old_form(text):
    """The pre-fix emitter's output: `} NAME[high:low];` -> `} NAME[width];`."""
    return re.sub(r"\} (\w+)\[(\d+):(\d+)\];",
                  lambda m: f"}} {m.group(1)}[{int(m.group(2)) - int(m.group(3)) + 1}];", text)


def _placed(text):
    """{reg: {field: (msb, lsb)}} as SystemRDL places them (lsb0, default packing)."""
    out = {}
    for reg, body in re.findall(r"reg (\w+)_t \{(.*?)\n    \};", text, re.S):
        nxt, fields = 0, {}
        for name, a, b in re.findall(r"\} (\w+)\[(\d+)(?::(\d+))?\];", body):
            if b:
                hi, lo = int(a), int(b)
            else:
                lo, hi = nxt, nxt + int(a) - 1
            fields[name] = (hi, lo)
            nxt = hi + 1
        out[reg] = fields
    return out


def _compiled(tmp_path, text):
    systemrdl = pytest.importorskip("systemrdl")
    from systemrdl.node import FieldNode
    p = tmp_path / "t.rdl"
    p.write_text(text)
    rdlc = systemrdl.RDLCompiler()
    rdlc.compile_file(str(p))
    root = rdlc.elaborate(top_def_name="pos_regs")
    out = {}
    for node in root.descendants():
        if isinstance(node, FieldNode):
            out.setdefault(node.parent.inst_name, {})[node.inst_name] = (node.msb, node.lsb)
    return out


def test_golden_field_lines(rdl_text):
    for reg, lines in GOLDEN_FIELD_LINES.items():
        body = re.search(rf"reg {reg}_t \{{(.*?)\n    \}};", rdl_text, re.S).group(1)
        assert [l.strip() for l in body.splitlines() if l.strip().startswith("} ")] == lines


def test_positions_match_yaml(rdl_text):
    assert _placed(rdl_text) == YAML_BITS


def test_old_form_fails_positions(rdl_text):
    # The negative control: the old emitter's text mis-places both registers.
    old = _placed(_old_form(rdl_text))
    assert old != YAML_BITS
    assert old["AES_CTRL"]["BYPASS"] == (4, 4) and old["AES_CTRL"]["ENCODE"] == (5, 5)
    assert old["PID1"] == {"JEP106_ID_3_0": (3, 0), "PART_1": (7, 4)}   # nibbles swapped


@pytest.mark.parametrize("form", ["new", "old"])
def test_compiler_places_fields(rdl_text, tmp_path, form):
    text = rdl_text if form == "new" else _old_form(rdl_text)
    got = _compiled(tmp_path, text)
    if form == "new":
        assert got == YAML_BITS
    else:
        assert got != YAML_BITS
        assert got["AES_CTRL"]["ENCODE"] == (5, 5)
