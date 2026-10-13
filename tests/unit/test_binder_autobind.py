# Copyright 2026, SoC Labs (www.soclabs.org)
"""Regression tests for the top-level binder's cross-instance auto-bind.

Downstream provenance: nanosoc_m0_soc PYNQ-Z2 bring-up
(doc/reports/pynq_z2_build.md §8.5). Analysis of the pre-fix nanosoc.sv
found the binder (a) auto-binds a cross-referenced instance port only when
it is an OUTPUT of the referenced instance — referenced INPUTS are left
dangling on the referenced instance, and (b) never auto-binds axis_byte /
apb interface SUB-SIGNALS (member ports), and (c) sizes referenced
axis_byte TDATA sub-signals as 1-bit. Boot-critical casualties before the
project-side workaround:

  * DEBUG_PRDATA/PREADY/PSLVERR (apb members) unbound -> USRT PREADY stuck
    low -> the first stage-0 UART write hung the AHB->APB bridge;
  * hostio4's FT-ADP sub-signals + FT_CLK/FT_SSN/FT_MIOSIO inputs dangling
    -> no ADP drain path;
  * USRT TDATA cross wires generated 1-bit wide.

These tests build small in-memory Module graphs (the established
test_toplevel_connections.py style) that reproduce each failure shape and
assert the binder now binds BOTH endpoints onto one flat xref wire with
the correct width.
"""

import pytest

from soc_model.backends.toplevel import SoCTopLevelBackend
from soc_model.model import Connection, Instance, Interface, Module, Param

pytestmark = pytest.mark.unit


def _wire(name, direction="in", width=1):
    return Interface(name=name, type="wire", direction=direction,
                     params={"WIDTH": width})


def _inst(name, module, connections=()):
    u = Instance(instance_name=name, module_name=module.name)
    u.connections = list(connections)
    u.resolved_module = module
    return u


def _ctx(top):
    """Full context build (runs _build_xref_wires then _build_instances)."""
    return SoCTopLevelBackend(top)._build_context()


def _conns(ctx, inst_name):
    inst = next(i for i in ctx["instances"] if i["instance_name"] == inst_name)
    return {c["port"]: c["signal"] for c in inst["connections"]}


def _xrefs(ctx):
    return {w["name"]: w for w in ctx["xref_wires"]}


# ===========================================================================
# Defect (a): one-way auto-bind — referenced INPUT ports never bound.
# ===========================================================================

class TestReferencedInputAutoBind:
    def _top(self):
        """ft1248-style producer drives a consumer's declared-but-unconnected
        INPUT via a cross-instance reference (the FT_CLK_O shape)."""
        prod = Module(name="ft_ctrl", gen=False)
        prod.interfaces = [_wire("ft_clk_o", "out", 1)]
        cons = Module(name="hostio", gen=False)
        cons.interfaces = [_wire("FT_CLK_O", "in", 1)]
        top = Module(name="soc", gen=True)
        top.instances = [
            _inst("u_ft", prod,
                  [Connection(port="ft_clk_o", conn="u_hostio.FT_CLK_O")]),
            _inst("u_hostio", cons),
        ]
        return top

    def test_referenced_input_gets_consumer_connection(self):
        """REGRESSION (defect a): u_hostio.FT_CLK_O is an INPUT the instance
        does not otherwise connect. The producer drives the flat xref wire,
        so the referenced instance must CONSUME it — previously no
        connection was injected and the input floated (no FT1248 SSN/SCLK
        on the PYNQ-Z2 MISO drain loop)."""
        ctx = self._top() and _ctx(self._top())
        assert _conns(ctx, "u_ft")["ft_clk_o"] == "u_hostio_FT_CLK_O"
        # the fix: the referenced instance now consumes the same wire
        assert _conns(ctx, "u_hostio")["FT_CLK_O"] == "u_hostio_FT_CLK_O"
        assert "u_hostio_FT_CLK_O" in _xrefs(ctx)

    def test_referenced_connected_input_is_not_reinjected(self):
        """When the referenced instance ALREADY connects the port, the
        reference resolves through to that net and no injection happens
        (no duplicate port connection)."""
        top = self._top()
        top.internal_wires = [
            Interface(name="ft_clk_net", type="wire", direction="internal",
                      params={"WIDTH": 1}),
        ]
        hostio = top.get_instance("u_hostio")
        hostio.connections = [Connection(port="FT_CLK_O", conn="ft_clk_net")]
        ctx = _ctx(top)
        conns = [c for i in ctx["instances"]
                 if i["instance_name"] == "u_hostio"
                 for c in i["connections"] if c["port"] == "FT_CLK_O"]
        assert len(conns) == 1
        assert conns[0]["signal"] == "ft_clk_net"
        # producer resolved through to the same real net
        assert _conns(ctx, "u_ft")["ft_clk_o"] == "ft_clk_net"

    def test_render_binds_both_endpoints(self, tmp_path):
        out = SoCTopLevelBackend(self._top()).generate(
            tmp_path / "rtl", tmp_path / "flist")
        sv = out.read_text()
        assert sv.count("(u_hostio_FT_CLK_O)") == 2   # driver + consumer


# ===========================================================================
# Defects (b)+(c): axis_byte / apb member ports — binding and width.
# ===========================================================================

def _hostio_module():
    m = Module(name="hostio", gen=False)
    rxd = Interface(name="USRT0_RXD", type="axis_byte", direction="out")
    m.interfaces = [rxd]
    return m


def _debug_module():
    m = Module(name="debug_ss", gen=False)
    apb = Interface(name="DEBUG_APB", type="apb", direction="target",
                    params={"ADDR_WIDTH": 10, "DATA_WIDTH": 32})
    m.interfaces = [apb]
    return m


class TestBusMemberAutoBind:
    def _axis_top(self):
        """systemctrl-style consumer references hostio's axis_byte members
        (the USRT0_RXD_TVALID/TDATA/TREADY shape, uppercase RTL spelling)."""
        sysctrl = Module(name="sysctrl", gen=False)
        sysctrl.interfaces = [
            Interface(name="USRT0_RXD", type="axis_byte", direction="in"),
        ]
        top = Module(name="soc", gen=True)
        top.instances = [
            _inst("u_sysctrl", sysctrl, [
                Connection(port="USRT0_RXD_TVALID",
                           conn="u_hostio.USRT0_RXD_TVALID"),
                Connection(port="USRT0_RXD_TDATA",
                           conn="u_hostio.USRT0_RXD_TDATA"),
                Connection(port="USRT0_RXD_TREADY",
                           conn="u_hostio.USRT0_RXD_TREADY"),
            ]),
            _inst("u_hostio", _hostio_module()),
        ]
        return top

    def test_axis_byte_members_bound_on_referenced_instance(self):
        """REGRESSION (defect b): the referenced axis_byte members must be
        bound on u_hostio — previously the sub-signals were silently
        severed (referenced-side instantiation had no such connections)."""
        ctx = _ctx(self._axis_top())
        hostio = _conns(ctx, "u_hostio")
        assert hostio.get("USRT0_RXD_TVALID") == "u_hostio_USRT0_RXD_TVALID"
        assert hostio.get("USRT0_RXD_TDATA") == "u_hostio_USRT0_RXD_TDATA"
        assert hostio.get("USRT0_RXD_TREADY") == "u_hostio_USRT0_RXD_TREADY"
        # and the consumer reads the same wires
        sysc = _conns(ctx, "u_sysctrl")
        assert sysc["USRT0_RXD_TVALID"] == "u_hostio_USRT0_RXD_TVALID"

    def test_axis_byte_tdata_width_is_eight(self):
        """REGRESSION (defect c): the referenced TDATA xref wire must be
        8-bit — previously declared 1-bit (`wire u_hostio_USRT0_RXD_TDATA;`),
        truncating the byte stream."""
        xrefs = _xrefs(_ctx(self._axis_top()))
        assert xrefs["u_hostio_USRT0_RXD_TDATA"]["width"] == 8
        assert xrefs["u_hostio_USRT0_RXD_TDATA"]["width_str"] == "[7:0]"
        assert xrefs["u_hostio_USRT0_RXD_TVALID"]["width"] == 1

    def test_apb_members_bound_with_param_widths(self):
        """REGRESSION (defect b, APB flavour): references to an APB
        completer's response members (the DEBUG_APB PRDATA/PREADY/PSLVERR
        shape that hung the USRT bridge) are bound on the referenced
        instance, with PRDATA sized from the iface DATA_WIDTH param."""
        sysctrl = Module(name="sysctrl", gen=False)
        sysctrl.interfaces = [
            _wire("USRT_PRDATA", "in", 32),
            _wire("USRT_PREADY", "in", 1),
        ]
        top = Module(name="soc", gen=True)
        top.instances = [
            _inst("u_sysctrl", sysctrl, [
                Connection(port="USRT_PRDATA", conn="u_debug.DEBUG_APB_PRDATA"),
                Connection(port="USRT_PREADY", conn="u_debug.DEBUG_APB_PREADY"),
            ]),
            _inst("u_debug", _debug_module()),
        ]
        ctx = _ctx(top)
        dbg = _conns(ctx, "u_debug")
        assert dbg.get("DEBUG_APB_PRDATA") == "u_debug_DEBUG_APB_PRDATA"
        assert dbg.get("DEBUG_APB_PREADY") == "u_debug_DEBUG_APB_PREADY"
        xrefs = _xrefs(ctx)
        assert xrefs["u_debug_DEBUG_APB_PRDATA"]["width"] == 32
        assert xrefs["u_debug_DEBUG_APB_PREADY"]["width"] == 1

    def test_member_width_resolves_param_refs(self):
        """A member width expression resolving through a $PARAM iface param
        (DATA_WIDTH: $SYS_DATA_W) picks up the top-level parameter value."""
        prod = Module(name="streamer", gen=False)
        prod.interfaces = [
            Interface(name="strm", type="axis", direction="out",
                      params={"DATA_WIDTH": "$SYS_DATA_W"}),
        ]
        cons = Module(name="sink", gen=False)
        cons.interfaces = [_wire("din", "in", 32)]
        top = Module(name="soc", gen=True)
        top.params = {"SYS_DATA_W": Param(name="SYS_DATA_W", type="int",
                                          default=32)}
        top.instances = [
            _inst("u_sink", cons,
                  [Connection(port="din", conn="u_str.strm_tdata")]),
            _inst("u_str", prod),
        ]
        xrefs = _xrefs(_ctx(top))
        assert xrefs["u_str_strm_tdata"]["width"] == 32

    def test_direction_decorated_member_reference_binds(self):
        """socdebug-style `_o`-decorated member reference
        (u_debug.ADP_RXD_TVALID_o) binds on the referenced instance using
        the SPELLED port name, so the emitted instantiation matches the
        hand-written RTL port."""
        dbg = Module(name="debug_ss", gen=False)
        dbg.interfaces = [
            Interface(name="ADP_RXD", type="axis_byte", direction="out"),
        ]
        hostio = Module(name="hostio", gen=False)
        hostio.interfaces = [
            Interface(name="ADP_RXD", type="axis_byte", direction="in"),
        ]
        top = Module(name="soc", gen=True)
        top.instances = [
            _inst("u_hostio", hostio, [
                Connection(port="ADP_RXD_TVALID",
                           conn="u_debug.ADP_RXD_TVALID_o"),
                Connection(port="ADP_RXD_TDATA",
                           conn="u_debug.ADP_RXD_TDATA_o"),
            ]),
            _inst("u_debug", dbg),
        ]
        ctx = _ctx(top)
        dbg_conns = _conns(ctx, "u_debug")
        assert dbg_conns.get("ADP_RXD_TVALID_o") == "u_debug_ADP_RXD_TVALID_o"
        assert dbg_conns.get("ADP_RXD_TDATA_o") == "u_debug_ADP_RXD_TDATA_o"
        assert _xrefs(ctx)["u_debug_ADP_RXD_TDATA_o"]["width"] == 8

    def test_bundle_reference_is_not_injected(self):
        """A reference to the WHOLE bundle (u_hostio.USRT0_RXD) is not a
        per-signal port: no member injection is fabricated for it here (the
        validator reports it — a `.USRT0_RXD(wire)` connection would be
        bogus RTL either way)."""
        sysctrl = Module(name="sysctrl", gen=False)
        sysctrl.interfaces = [
            Interface(name="USRT0_RXD", type="axis_byte", direction="in"),
        ]
        top = Module(name="soc", gen=True)
        top.instances = [
            _inst("u_sysctrl", sysctrl,
                  [Connection(port="USRT0_RXD", conn="u_hostio.USRT0_RXD")]),
            _inst("u_hostio", _hostio_module()),
        ]
        ctx = _ctx(top)
        assert "USRT0_RXD" not in _conns(ctx, "u_hostio")

    def test_render_declares_byte_wire_and_binds_both_ends(self, tmp_path):
        out = SoCTopLevelBackend(self._axis_top()).generate(
            tmp_path / "rtl", tmp_path / "flist")
        sv = out.read_text()
        assert "wire [7:0]                u_hostio_USRT0_RXD_TDATA;" in sv \
            or "wire [7:0]" in sv and "u_hostio_USRT0_RXD_TDATA;" in sv
        assert sv.count("(u_hostio_USRT0_RXD_TDATA)") == 2
