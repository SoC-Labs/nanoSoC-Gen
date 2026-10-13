# Copyright 2026, SoC Labs (www.soclabs.org)
"""Tooling-tier tests for RTL emission in ``soc_model/backends/rdl.py``.

Per TEST_PLAN.md §5.11 (RD-13, RD-15). The ``.rdl`` *text* needs no toolchain
(that is the default ``golden`` tier, owned elsewhere). This file exercises
the actual RTL emission path — ``SoCRdlBackend._generate_rtl`` ->
``systemrdl-compiler`` + ``peakrdl-regblock`` — which is the ``tooling`` tier.

``systemrdl`` and ``peakrdl_regblock`` ARE importable in this worktree, so
these tests really run regblock and assert on the emitted SystemVerilog
(written entirely inside ``tmp_path``). When either package is unavailable the
backend's own ImportError guard returns ``False`` (rdl.py:306); we ADD a
module-level ``importorskip`` so the suite SKIPS cleanly rather than asserting
against a silently-empty RTL dir.

No EDA simulator/synthesizer is required: peakrdl-regblock is pure Python
codegen.
"""

import re
from pathlib import Path

import pytest

# SKIP the whole module cleanly if the codegen toolchain is absent.
pytest.importorskip("systemrdl", reason="systemrdl-compiler not installed")
pytest.importorskip("peakrdl_regblock", reason="peakrdl-regblock not installed")

from soc_model.backends.rdl import SoCRdlBackend  # noqa: E402
from soc_model.model import (  # noqa: E402
    Module,
    Register,
    RegisterField,
    RegisterMap,
)

pytestmark = pytest.mark.tooling


def _assert_paddr_conn_matches_regblock(wrapper_txt, regblock_txt):
    """The wrapper's .s_apb_paddr connection must be exactly as wide as the
    regblock's s_apb_paddr port declaration.

    Checked against the *emitted regblock* rather than a hard-coded width, so
    the guard stays honest if peakrdl-regblock's address sizing ever changes.
    A mismatch here is not a benign truncation: DC reports
    ``LINK-3 Width mismatch on port 's_apb_paddr'`` and black-boxes the
    regblock, silently dropping the register block from the netlist.
    """
    port = re.search(r'input wire \[(\d+):0\] s_apb_paddr', regblock_txt)
    assert port, "regblock declares no s_apb_paddr port"
    conn = re.search(r'\.s_apb_paddr\s*\(PADDR\[(\d+):0\]\)', wrapper_txt)
    assert conn, (
        "wrapper .s_apb_paddr is not a plain PADDR part-select; got:\n"
        + '\n'.join(l for l in wrapper_txt.splitlines() if 's_apb_paddr' in l)
    )
    assert conn.group(1) == port.group(1), (
        f"width mismatch: wrapper drives PADDR[{conn.group(1)}:0] into a "
        f"[{port.group(1)}:0] regblock port -- DC LINK-3 black-boxes the regblock"
    )


def _fielded_regmap(name="unit_regs", addr_width=8, gen=True):
    """An inline RegisterMap with a fielded RW reg + an RO reg.

    Built from dataclasses (no fixture YAML needed) so the test is hermetic
    and the register shapes are explicit.
    """
    return RegisterMap(
        name=name,
        module=name,
        gen=gen,
        desc="unit regblock map",
        address_width=addr_width,
        data_width=32,
        registers=[
            Register(
                name="CTRL", offset=0x0, width=32, access="RW", reset_value=0,
                fields=[
                    RegisterField(name="EN", bits="0", access="RW",
                                  reset_value=0, desc="enable"),
                    RegisterField(name="MODE", bits="2:1", access="RW",
                                  reset_value=0, desc="mode select"),
                ],
            ),
            Register(name="STATUS", offset=0x4, width=32, access="RO",
                     reset_value=0, desc="status"),
        ],
        source_file="inline",
    )


class TestRdlGenFlag:
    """RD-13: gen toggles RTL emission; the .rdl text is always written."""

    def test_gen_true_emits_rtl(self, tmp_path):  # RD-13
        rm = _fielded_regmap(gen=True)
        rdl_path, rtl_generated = SoCRdlBackend(Module(name="t")) \
            .generate_single(rm, tmp_path)
        assert rdl_path.exists()
        assert rtl_generated is True

    def test_gen_false_no_rtl(self, tmp_path):  # RD-13
        rm = _fielded_regmap(gen=False)
        rdl_path, rtl_generated = SoCRdlBackend(Module(name="t")) \
            .generate_single(rm, tmp_path)
        assert rdl_path.exists()            # .rdl text still emitted
        assert rtl_generated is False       # no RTL when gen False
        assert not (tmp_path / "rtl" / rm.name).exists()


class TestRdlRegblockEmission:
    """RD-15: regblock RTL emission produces .sv + APB wrapper with PADDR
    sliced to the register map's address width."""

    def test_emits_regblock_and_wrapper(self, tmp_path):  # RD-15
        rm = _fielded_regmap(name="unit_regs", addr_width=8, gen=True)
        rdl_path, rtl_generated = SoCRdlBackend(Module(name="t")) \
            .generate_single(rm, tmp_path)
        assert rtl_generated is True

        rtl_dir = tmp_path / "rtl" / "unit_regs"
        assert rtl_dir.is_dir()

        regblock = rtl_dir / "unit_regs.sv"
        pkg = rtl_dir / "unit_regs_pkg.sv"
        wrapper = rtl_dir / "unit_regs_apb_wrapper.sv"
        for p in (regblock, pkg, wrapper):
            assert p.exists(), f"missing emitted RTL {p}"
            assert p.read_text().strip(), f"empty emitted RTL {p}"

        # Structural teeth on the regblock package/module.
        assert "module unit_regs" in regblock.read_text()

        # APB wrapper: the external PADDR port is the APB *window* (addr_width=8),
        # while the regblock instance connection is sliced to the width
        # peakrdl-regblock actually declared. pprot/pstrb are tied off.
        wrap_txt = wrapper.read_text()
        assert "module unit_regs_apb_wrapper" in wrap_txt
        assert "input  wire [7:0] PADDR," in wrap_txt
        _assert_paddr_conn_matches_regblock(wrap_txt, regblock.read_text())
        assert ".s_apb_pprot    (3'b000)," in wrap_txt   # tied off
        assert ".s_apb_pstrb    (4'b1111)," in wrap_txt   # tied off

    def test_addr_width_propagates_to_wrapper(self, tmp_path):  # RD-15
        # rm.address_width sets the wrapper's external PADDR port (the APB
        # window the region occupies) -- it must flow through unchanged.
        rm = _fielded_regmap(name="wide_regs", addr_width=12, gen=True)
        _, rtl_generated = SoCRdlBackend(Module(name="t")) \
            .generate_single(rm, tmp_path)
        assert rtl_generated is True
        rtl_dir = tmp_path / "rtl" / "wide_regs"
        wrapper = (rtl_dir / "wide_regs_apb_wrapper.sv").read_text()
        assert "input  wire [11:0] PADDR," in wrapper
        # ...but the instance connection must track the regblock port, NOT the
        # window. Driving the window width here is a hard width mismatch that
        # makes DC raise LINK-3 and black-box the regblock (it does not
        # truncate). Regression guard for that defect.
        _assert_paddr_conn_matches_regblock(
            wrapper, (rtl_dir / "wide_regs.sv").read_text())

    def test_generate_all_collects_from_address_decode(self, tmp_path):  # RD-15
        # Drive the full generate_all collection path: an inline Module whose
        # address_decode slot carries a gen:True resolved register map.
        from soc_model.model import AddressDecode, AddressDecodeSlot

        rm = _fielded_regmap(name="ad_regs", addr_width=8, gen=True)
        slot = AddressDecodeSlot(slot=0, name="ad_regs",
                                 resolved_register_map=rm)
        ad = AddressDecode(type="apb_slave_mux", slots=[slot])
        top = Module(name="t")
        top.address_decode = ad

        results = SoCRdlBackend(top).generate_all(tmp_path)
        assert len(results) == 1
        name, rdl_path, rtl_gen = results[0]
        assert name == "ad_regs"
        assert rdl_path.exists()
        assert rtl_gen is True
        assert (tmp_path / "rtl" / "ad_regs" / "ad_regs.sv").exists()
