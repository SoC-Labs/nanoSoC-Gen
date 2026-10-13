# Copyright 2026, SoC Labs (www.soclabs.org)
"""Connection-rich coverage for the top-level structural SV backend.

The hermetic fixture SoCs (minimal / two_region / apb_bridge / passthrough /
firmware) all go through a single gen:True interconnect with instances wired to
it, so they never exercise the backend's:

  * width-inference fallback              (_infer_width)
  * cross-instance xref wire declaration  (_build_xref_wires._add_xref)
  * connection-to-wire resolution         (_resolve_conn_to_wire)
  * glue/adapter instance emission        (_build_glue_instances)
  * AXIS / SWD / bare-name-AHB expansion  (_expand_axis_port,
                                           _expand_protocol_connection,
                                           _expand_ahb_top_connection)
  * bit-select coalesce                    (_coalesce_bit_select_ports)
  * direct instance-to-instance bus wiring (_expand_bus_connection branches)

This module builds small in-memory ``Module`` graphs (with ``resolved_module``
attached by hand, exactly as ``SoCBuilder._resolve_instances`` would) that DO
hit those paths, and asserts on the concrete data structures the backend
returns (and, for one case, the fully rendered SV) — not coverage padding.

Each test PINS the generator's current behaviour: where a width cannot be
resolved (e.g. a ``$PARAM`` on a top interface, which the int-only branch
ignores) the documented fallback (1) is asserted, so a deliberate change is a
reviewable diff rather than a silent regression.
"""

import pytest

from soc_model.backends.toplevel import SoCTopLevelBackend
from soc_model.model import (
    Connection, GlueLogicEntry, Instance, Interconnect,
    InterconnectInitiator, InterconnectTarget, Interface, Module, Param,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Small model-construction helpers (no YAML, no parser).
# ---------------------------------------------------------------------------

def _wire(name, direction="in", width=1):
    return Interface(name=name, type="wire", direction=direction,
                     params={"WIDTH": width})


def _ahb(name, direction, **extra):
    params = {"ADDR_WIDTH": "$SYS_ADDR_W", "DATA_WIDTH": "$SYS_DATA_W"}
    params.update(extra)
    return Interface(name=name, type="ahb", direction=direction, params=params)


def _backend(top):
    """SoCTopLevelBackend with the xref-mapping side-table primed.

    ``_build_instances`` reads ``self._output_port_to_xref`` (normally set by
    ``_build_context`` before it runs). Tests that call ``_build_instances``
    directly must seed it; ``_build_xref_wires`` will (re)populate it.
    """
    be = SoCTopLevelBackend(top)
    be._output_port_to_xref = {}
    return be


# ===========================================================================
# _infer_width — the width-inference fallback ladder (lines 577-640).
# ===========================================================================

class TestInferWidth:
    def test_input_bit_slice_wins(self):
        be = _backend(Module(name="t", gen=True))
        assert be._infer_width("foo[7:4]", None) == 4
        assert be._infer_width("bar[3]", None) == 1   # single-bit slice

    def test_output_bit_slice_when_input_unsliced(self):
        be = _backend(Module(name="t", gen=True))
        assert be._infer_width("plain", "dst[15:8]") == 8

    def test_internal_wire_param_width_resolved(self):
        """A $PARAM width on an internal wire resolves against flat_params."""
        top = Module(name="t", gen=True)
        top.params = {"SYS_DATA_W": Param(name="SYS_DATA_W", type="int", default=32)}
        top.internal_wires = [
            Interface(name="wide_net", type="wire", direction="internal",
                      params={"WIDTH": "$SYS_DATA_W"}),
        ]
        assert _backend(top)._infer_width("wide_net", None) == 32

    def test_top_interface_int_width(self):
        top = Module(name="t", gen=True)
        top.interfaces = [_wire("byte_in", "in", 8)]
        assert _backend(top)._infer_width("byte_in", None) == 8

    def test_top_interface_param_width_is_not_resolved(self):
        """PIN: the top-level-interface branch only honours INT widths; a
        $PARAM width is ignored and the default (1) is returned."""
        top = Module(name="t", gen=True)
        top.params = {"SYS_DATA_W": Param(name="SYS_DATA_W", type="int", default=32)}
        top.interfaces = [
            Interface(name="bus_o", type="wire", direction="out",
                      params={"WIDTH": "$SYS_DATA_W"}),
        ]
        assert _backend(top)._infer_width("bus_o", None) == 1

    def test_gpio_expanded_port_width(self):
        top = Module(name="g", gen=True)
        top.interfaces = [
            Interface(name="p0", type="gpio", direction="inout",
                      params={"WIDTH": 16}),
        ]
        be = _backend(top)
        assert be._infer_width("p0_out", None) == 16
        assert be._infer_width("p0_in", None) == 16
        assert be._infer_width("p0_outen", None) == 16

    def test_hierarchical_instance_port_int_width(self):
        """A 'inst.port' reference resolves width from the child module iface."""
        child = Module(name="prod", gen=False)
        child.interfaces = [_wire("OUT", "out", 12)]
        top = Module(name="t", gen=True)
        u = Instance(instance_name="u_p", module_name="prod")
        u.resolved_module = child
        top.instances = [u]
        assert _backend(top)._infer_width("u_p.OUT", None) == 12

    def test_unknown_signal_defaults_to_one(self):
        assert _backend(Module(name="t", gen=True))._infer_width("ghost", None) == 1


# ===========================================================================
# _build_xref_wires._add_xref (447-496) + _resolve_conn_to_wire (942-1005).
# ===========================================================================

def _two_instance_xref_top():
    """producer drives a top-level OUTPUT port; consumer cross-refs the
    producer's port. This forces _resolve_conn_to_wire to fall through to the
    flat wire name (an output may only be driven once) and _add_xref to DECLARE
    that flat wire — the only shape that walks the whole _add_xref body."""
    prod = Module(name="producer", gen=False)
    prod.interfaces = [_wire("STATUS", "out", 4)]
    cons = Module(name="consumer", gen=False)
    cons.interfaces = [_wire("STATUS_IN", "in", 4)]

    top = Module(name="xref_soc", gen=True)
    top.interfaces = [_wire("status_o", "out", 4)]

    u_prod = Instance(instance_name="u_prod", module_name="producer")
    u_prod.connections = [Connection(port="STATUS", conn="status_o")]
    u_prod.resolved_module = prod

    u_cons = Instance(instance_name="u_cons", module_name="consumer")
    u_cons.connections = [Connection(port="STATUS_IN", conn="u_prod.STATUS")]
    u_cons.resolved_module = cons

    top.instances = [u_prod, u_cons]
    return top


class TestXrefWires:
    def test_xref_wire_declared_when_source_drives_top_output(self):
        be = _backend(_two_instance_xref_top())
        xref = be._build_xref_wires()
        names = {w["name"]: w for w in xref}
        assert "u_prod_STATUS" in names
        # width inferred from the producer module interface (4-bit -> [3:0])
        assert names["u_prod_STATUS"]["width"] == 4
        assert names["u_prod_STATUS"]["width_str"] == "[3:0]"
        # the source instance's drive of the top output is redirected to the xref
        assert be._output_port_to_xref == {"status_o": "u_prod_STATUS"}

    def test_no_xref_when_ref_resolves_to_real_wire(self):
        """When the cross-ref resolves to a normal internal wire (not a flat
        xref), _add_xref returns early and declares nothing."""
        prod = Module(name="producer", gen=False)
        prod.interfaces = [_wire("DATA_OUT", "out", 8)]
        cons = Module(name="consumer", gen=False)
        cons.interfaces = [_wire("DATA_IN", "in", 8)]
        top = Module(name="t", gen=True)
        top.internal_wires = [
            Interface(name="net_data", type="wire", direction="internal",
                      params={"WIDTH": 8}),
        ]
        u_prod = Instance(instance_name="u_prod", module_name="producer")
        u_prod.connections = [Connection(port="DATA_OUT", conn="net_data")]
        u_prod.resolved_module = prod
        u_cons = Instance(instance_name="u_cons", module_name="consumer")
        u_cons.connections = [Connection(port="DATA_IN", conn="u_prod.DATA_OUT")]
        u_cons.resolved_module = cons
        top.instances = [u_prod, u_cons]

        be = _backend(top)
        assert be._build_xref_wires() == []     # nothing declared
        # the consumer's cross-ref resolves through to the real internal wire
        assert be._resolve_conn_to_wire("u_prod.DATA_OUT") == "net_data"

    def test_resolve_conn_to_wire_recurses_to_top_output_flat(self):
        be = _backend(_two_instance_xref_top())
        be._build_xref_wires()  # populates state; not strictly required here
        # u_cons.STATUS_IN -> u_prod.STATUS -> drives top OUTPUT -> flat wire
        assert be._resolve_conn_to_wire("u_prod.STATUS") == "u_prod_STATUS"

    def test_resolve_conn_to_wire_passthrough_non_hier(self):
        be = _backend(Module(name="t", gen=True))
        # no dot -> returned verbatim
        assert be._resolve_conn_to_wire("sys_hclk") == "sys_hclk"

    def test_resolve_conn_to_wire_breaks_cycle(self):
        """Mutually-referencing ports (u_a.X -> u_b.Y -> u_a.X) break to a
        CANONICAL flat wire name (lexicographically smallest cycle member)
        instead of recursing forever — and, since the duplicate-link fix,
        BOTH entry points resolve to that same canonical name (previously
        each start point produced its own flat wire -> two disjoint nets)."""
        a = Module(name="a", gen=False)
        a.interfaces = [_wire("X", "out", 1)]
        b = Module(name="b", gen=False)
        b.interfaces = [_wire("Y", "out", 1)]
        top = Module(name="cyc", gen=True)
        u_a = Instance(instance_name="u_a", module_name="a")
        u_a.connections = [Connection(port="X", conn="u_b.Y")]
        u_a.resolved_module = a
        u_b = Instance(instance_name="u_b", module_name="b")
        u_b.connections = [Connection(port="Y", conn="u_a.X")]
        u_b.resolved_module = b
        top.instances = [u_a, u_b]
        be = _backend(top)
        assert be._resolve_conn_to_wire("u_a.X") == "u_a_X"
        assert be._resolve_conn_to_wire("u_a.X[0]") == "u_a_X[0]"
        # entry-point independence (the duplicate-link severed-net fix)
        assert be._resolve_conn_to_wire("u_b.Y") == "u_a_X"

    def test_build_instances_rewires_source_to_xref(self):
        """The end-to-end effect: u_prod's drive of the top output port is
        rewired to the xref wire, and u_cons reads the same xref wire."""
        be = _backend(_two_instance_xref_top())
        be._build_xref_wires()           # populate _output_port_to_xref
        insts = {i["instance_name"]: i for i in be._build_instances()}
        prod_conns = {c["port"]: c["signal"]
                      for c in insts["u_prod"]["connections"]}
        cons_conns = {c["port"]: c["signal"]
                      for c in insts["u_cons"]["connections"]}
        assert prod_conns["STATUS"] == "u_prod_STATUS"
        assert cons_conns["STATUS_IN"] == "u_prod_STATUS"

    def test_rewired_top_output_gets_boundary_assign(self):
        """REGRESSION (undriven-output defect class, cf. system_htrans): when
        the source instance's drive of a top-level OUTPUT is rewired onto the
        xref wire, the backend must ALSO emit `assign <port> = <xref>;` —
        otherwise the boundary port is declared but never driven (Z in sim,
        0 on FPGA). This is exactly the nanosoc_multicore_soc cpu_*_lockup /
        cpu_*_sysresetreq gap found by formal."""
        be = _backend(_two_instance_xref_top())
        ctx = be._build_context()
        assigns = {a["lhs"]: a["rhs"] for a in ctx["xref_output_assigns"]}
        assert assigns == {"status_o": "u_prod_STATUS"}

    def test_rewired_top_output_assign_rendered(self, tmp_path):
        """End-to-end render: the generated SV contains the boundary-output
        assign, and the source instance drives the xref wire."""
        out = _backend(_two_instance_xref_top()).generate(
            tmp_path / "rtl", tmp_path / "flist")
        sv = out.read_text()
        assert "assign status_o = u_prod_STATUS;" in sv

    def test_no_boundary_assign_when_glue_drives_port(self):
        """If explicit YAML glue logic already drives the output port (the
        passthrough the original code comment assumed), no duplicate assign
        is recorded — a second driver would be a multi-drive error."""
        top = _two_instance_xref_top()
        top.glue_logic = [
            GlueLogicEntry(name="pt_status", type="passthrough",
                           output="status_o", input="u_prod.STATUS",
                           desc="explicit copy"),
        ]
        be = _backend(top)
        ctx = be._build_context()
        assert ctx["xref_output_assigns"] == []

    def test_boundary_assign_deduped_per_lhs(self):
        """Recording the same LHS twice (defensive: only one driver may win)
        keeps a single assign."""
        be = _backend(_two_instance_xref_top())
        be._xref_output_assigns = []
        be._record_xref_output_assign("status_o", "u_prod_STATUS")
        be._record_xref_output_assign("status_o", "u_prod_STATUS")
        assert len(be._xref_output_assigns) == 1


# ===========================================================================
# _build_glue_instances (524-571) — every glue type, plus full render.
# ===========================================================================

def _glue_top():
    top = Module(name="glue_soc", gen=True)
    top.params = {"SYS_DATA_W": Param(name="SYS_DATA_W", type="int", default=32)}
    top.interfaces = [
        _wire("sys_hclk", "in", 1),
        _wire("sys_hresetn", "in", 1),
        _wire("irq_o", "out", 1),
    ]
    top.internal_wires = [
        Interface(name="irq_a", type="wire", direction="internal",
                  params={"WIDTH": 1}),
        Interface(name="irq_b", type="wire", direction="internal",
                  params={"WIDTH": 1}),
        Interface(name="wide", type="wire", direction="internal",
                  params={"WIDTH": "$SYS_DATA_W"}),
        Interface(name="cc_bus", type="wire", direction="internal",
                  params={"WIDTH": "$SYS_DATA_W"}),
    ]
    top.glue_logic = [
        GlueLogicEntry(name="pt", type="passthrough", output="irq_o",
                       input="irq_a", desc="pass"),
        GlueLogicEntry(name="orr", type="or_reduce", output="red",
                       input="wide", desc="reduce 32->1"),
        GlueLogicEntry(name="orc", type="or_combine", output="irq_o",
                       inputs=["irq_a", "irq_b"], desc="combine"),
        GlueLogicEntry(name="ag", type="and_gate", output="g",
                       inputs=["irq_a", "irq_b"], desc="and"),
        GlueLogicEntry(name="cst", type="constant", output="c",
                       value=5, width=4, desc="const"),
        GlueLogicEntry(
            name="cc", type="concat", output="cc_bus",
            concat_inputs=[{"bit": 0, "signal": "irq_a"},
                           {"bit": 1, "signal": "irq_b"}],
            pad_zero_above=2, desc="concat"),
        GlueLogicEntry(name="mx", type="mux2", output="muxed",
                       inputs=["irq_a", "irq_b"], select="$SYS_DATA_W",
                       desc="2:1 mux selected by a param ref"),
        GlueLogicEntry(name="rs", type="reset_sync", output="resetn_sync",
                       inputs=["resetn_async", "clk_dom"],
                       desc="async-assert/sync-deassert reset synchroniser"),
    ]
    return top


class TestGlueInstances:
    def test_all_glue_types_built(self):
        gi = {g["name"]: g for g in _backend(_glue_top())._build_glue_instances()}

        # passthrough: width inferred from the 1-bit internal source
        assert gi["pt"]["type"] == "passthrough"
        assert gi["pt"]["input"] == "irq_a"
        assert gi["pt"]["width"] == 1

        # or_reduce: width inferred from the $PARAM internal wire (32)
        assert gi["orr"]["width"] == 32
        assert gi["orr"]["input"] == "wide"

        # or_combine: N inputs resolved, n_inputs counted
        assert gi["orc"]["inputs"] == ["irq_a", "irq_b"]
        assert gi["orc"]["n_inputs"] == 2

        # and_gate: two inputs -> input_a / input_b
        assert gi["ag"]["input_a"] == "irq_a"
        assert gi["ag"]["input_b"] == "irq_b"

        # constant: explicit width + value carried through
        assert gi["cst"]["width"] == 4
        assert gi["cst"]["value"] == 5

        # concat: output net width resolved from the $PARAM internal wire (32);
        # bit_map placed bit0=irq_a, bit1=irq_b, all other bits 1'b0, MSB-first.
        assert gi["cc"]["width"] == 32
        bits = gi["cc"]["bits"]
        assert len(bits) == 32
        assert bits[-1] == "irq_a"      # bit 0 is the last (rightmost) element
        assert bits[-2] == "irq_b"      # bit 1
        assert set(bits[:-2]) == {"1'b0"}   # everything above pad_zero_above

        # mux2: in0/in1 from inputs[0]/[1]; select $PARAM -> the build-time
        # parameter narrowed to bit 0 (what the old 1-bit sel port kept).
        assert gi["mx"]["input0"] == "irq_a"
        assert gi["mx"]["input1"] == "irq_b"
        assert gi["mx"]["select"] == "SYS_DATA_W[0]"   # $PARAM -> PARAM[0]
        assert gi["mx"]["width"] == 1

        # reset_sync: inputs[0]=arstn, inputs[1]=clk; STAGES defaults to 3.
        assert gi["rs"]["type"] == "reset_sync"
        assert gi["rs"]["arstn"] == "resetn_async"
        assert gi["rs"]["clk"] == "clk_dom"
        assert gi["rs"]["stages"] == 3

    def test_constant_width_defaults_to_one(self):
        top = Module(name="t", gen=True)
        top.glue_logic = [
            GlueLogicEntry(name="z", type="constant", output="o", value=0),
        ]
        gi = _backend(top)._build_glue_instances()[0]
        assert gi["width"] == 1     # gl.width None -> 1

    def test_render_emits_glue_helper_instances(self, tmp_path):
        """End-to-end: the glue context renders the soc_glue_* helper module
        instantiations and the concat assign into the SV body."""
        out = _backend(_glue_top()).generate(tmp_path / "rtl", tmp_path / "flist")
        assert out is not None
        sv = out.read_text()
        assert "soc_glue_passthrough #(" in sv
        assert "soc_glue_or_reduce #(" in sv
        assert "soc_glue_or_combine #(" in sv
        assert "soc_glue_and_gate #(" in sv
        assert "soc_glue_constant #(" in sv
        assert "soc_glue_mux2 #(" in sv
        assert "assign cc_bus = {" in sv
        # mux2 select renders as the SEL parameter, bit 0 of the param ref
        assert ".SEL  (SYS_DATA_W[0])" in sv
        # reset_sync renders the soc_glue_reset_sync helper with clk + arstn
        assert "soc_glue_reset_sync #(" in sv
        assert ".arstn (resetn_async)" in sv
        assert ".clk   (clk_dom)" in sv


# ===========================================================================
# AXIS port + protocol connection (_expand_axis_port, _expand_protocol_connection)
# ===========================================================================

class TestAxisExpansion:
    def test_expand_axis_ports(self):
        top = Module(name="axis_soc", gen=True)
        top.params = {"SYS_DATA_W": Param(name="SYS_DATA_W", type="int",
                                          default=32)}
        top.interfaces = [
            Interface(name="strm_in", type="axis", direction="in",
                      params={"DATA_WIDTH": "$SYS_DATA_W", "HAS_FLUSH": 1}),
            Interface(name="strm_out", type="axis", direction="out",
                      params={"DATA_WIDTH": "$SYS_DATA_W"}),
            Interface(name="b_in", type="axis_byte", direction="in", params={}),
        ]
        ports = {p["name"]: p for p in _backend(top)._build_ports()}

        # input stream: tvalid/tdata are inputs, tready is an output (back-pressure)
        assert ports["strm_in_tvalid"]["direction"] == "input"
        assert ports["strm_in_tready"]["direction"] == "output"
        assert ports["strm_in_tdata"]["direction"] == "input"
        # HAS_FLUSH on an INPUT stream adds an output flush port
        assert ports["strm_in_flush"]["direction"] == "output"
        assert ports["strm_in_flush"]["width"] == 1

        # output stream: directions flip; no flush (output side + no HAS_FLUSH)
        assert ports["strm_out_tvalid"]["direction"] == "output"
        assert ports["strm_out_tready"]["direction"] == "input"
        assert "strm_out_flush" not in ports

        # axis_byte: the reduced 3-signal set
        assert ports["b_in_tvalid"]["direction"] == "input"
        assert ports["b_in_tdata"]["direction"] == "input"
        assert "b_in_tstrb" not in ports

    def test_expand_axis_protocol_connection_with_flush(self):
        """A child wired to a top AXIS iface by BARE NAME expands per-signal,
        and adds the flush signal because HAS_FLUSH is set."""
        top = Module(name="axis_soc", gen=True)
        top.params = {"SYS_DATA_W": Param(name="SYS_DATA_W", type="int",
                                          default=32)}
        top.interfaces = [
            Interface(name="strm_in", type="axis", direction="in",
                      params={"DATA_WIDTH": "$SYS_DATA_W", "HAS_FLUSH": 1}),
        ]
        child = Module(name="dma", gen=False)
        child.interfaces = [
            Interface(name="strm_in", type="axis", direction="in",
                      params={"DATA_WIDTH": "$SYS_DATA_W", "HAS_FLUSH": 1}),
        ]
        inst = Instance(instance_name="u_dma", module_name="dma")
        inst.connections = [Connection(port="strm_in", conn="strm_in")]
        inst.resolved_module = child
        top.instances = [inst]

        conns = {c["port"]: c["signal"]
                 for c in _backend(top)._build_instances()[0]["connections"]}
        assert conns["strm_in_tvalid"] == "strm_in_tvalid"
        assert conns["strm_in_tdata"] == "strm_in_tdata"
        assert conns["strm_in_tready"] == "strm_in_tready"
        assert conns["strm_in_flush"] == "strm_in_flush"

    def test_expand_axis_byte_protocol_connection(self):
        top = Module(name="axis_soc", gen=True)
        top.interfaces = [
            Interface(name="b_in", type="axis_byte", direction="in", params={}),
        ]
        child = Module(name="sink", gen=False)
        child.interfaces = [
            Interface(name="b_in", type="axis_byte", direction="in", params={}),
        ]
        inst = Instance(instance_name="u_sink", module_name="sink")
        inst.connections = [Connection(port="b_in", conn="b_in")]
        inst.resolved_module = child
        top.instances = [inst]
        conns = {c["port"]: c["signal"]
                 for c in _backend(top)._build_instances()[0]["connections"]}
        assert conns == {"b_in_tvalid": "b_in_tvalid",
                         "b_in_tready": "b_in_tready",
                         "b_in_tdata": "b_in_tdata"}


# ===========================================================================
# SWD protocol connection + _top_iface_signal_name (1063-1065).
# ===========================================================================

class TestSwdExpansion:
    def test_swd_strips_suffix_in_connection(self):
        top = Module(name="swd_soc", gen=True)
        top.interfaces = [Interface(name="cpu_0_swd", type="swd",
                                    direction="in", params={})]
        child = Module(name="cpu", gen=False)
        child.interfaces = [Interface(name="cpu_0_swd", type="swd",
                                      direction="in", params={})]
        inst = Instance(instance_name="u_cpu", module_name="cpu")
        inst.connections = [Connection(port="cpu_0_swd", conn="cpu_0_swd")]
        inst.resolved_module = child
        top.instances = [inst]
        conns = {c["port"]: c["signal"]
                 for c in _backend(top)._build_instances()[0]["connections"]}
        # both the instance port AND the top signal strip the _swd suffix
        assert conns == {"cpu_0_swdi": "cpu_0_swdi",
                         "cpu_0_swclk": "cpu_0_swclk",
                         "cpu_0_swdo": "cpu_0_swdo",
                         "cpu_0_swdoen": "cpu_0_swdoen"}

    def test_top_iface_signal_name_swd_and_plain(self):
        be = _backend(Module(name="t", gen=True))
        swd = Interface(name="cpu_0_swd", type="swd", direction="in", params={})
        assert be._top_iface_signal_name(swd, "swdi") == "cpu_0_swdi"
        wire = Interface(name="rst", type="wire", direction="in", params={})
        assert be._top_iface_signal_name(wire, "x") == "rst_x"


# ===========================================================================
# Bare-name AHB top connection + boundary helpers
# (_expand_ahb_top_connection 815-831, _ahb_iface_boundary_signals 797-801,
#  _iface_drives_child_passthrough_initiator, _child_port_is_pt_initiator,
#  _find_instance_interface, _get_instance_port_width).
# ===========================================================================

def _eth_pt_initiator_top():
    """A child subsystem ('eth_ss') whose AHB port 'eth_ss_1' is a passthrough
    initiator on its OWN interconnect (initiator boundary — no hready exclude).
    The top wires it by bare name { port: eth_ss_1, conn: eth_ss_0 }."""
    child = Module(name="eth_ss", gen=True)
    cic = Interconnect(name="eth_ss_ahb_interconnect", gen=True)
    cic.initiators = [InterconnectInitiator(name="eth_ss_1",
                                            passthrough="eth_ss_1")]
    child.interconnects = [cic]
    child.interfaces = [_ahb("eth_ss_1", "target")]

    top = Module(name="ahb_top_soc", gen=True)
    top.params = {
        "SYS_ADDR_W": Param(name="SYS_ADDR_W", type="int", default=32),
        "SYS_DATA_W": Param(name="SYS_DATA_W", type="int", default=32),
    }
    top.interfaces = [_ahb("eth_ss_0", "target")]
    inst = Instance(instance_name="u_eth_ss", module_name="eth_ss")
    inst.connections = [Connection(port="eth_ss_1", conn="eth_ss_0")]
    inst.resolved_module = child
    top.instances = [inst]
    return top, inst


class TestAhbTopConnection:
    def test_child_port_is_pt_initiator_and_iface_drives(self):
        top, inst = _eth_pt_initiator_top()
        be = _backend(top)
        inst_iface = be._find_instance_interface(inst, "eth_ss_1")
        assert inst_iface is not None and inst_iface.type == "ahb"
        assert be._child_port_is_pt_initiator(inst, "eth_ss_1", inst_iface) is True
        # the external top iface drives a child passthrough-initiator
        assert be._iface_drives_child_passthrough_initiator(
            top.interfaces[0]) is True
        # a non-AHB iface short-circuits to False
        assert be._iface_drives_child_passthrough_initiator(
            _wire("not_ahb")) is False

    def test_boundary_signals_initiator_form(self):
        top, inst = _eth_pt_initiator_top()
        be = _backend(top)
        inst_iface = be._find_instance_interface(inst, "eth_ss_1")
        sigs, invert = be._ahb_iface_boundary_signals(inst_iface, True)
        names = {s for s, _, _ in sigs}
        # initiator boundary: no hsel / no hreadyout, invert True
        assert invert is True
        assert "hsel" not in names and "hreadyout" not in names
        assert "haddr" in names and "hready" in names

    def test_expand_ahb_top_connection_signals(self):
        top, _ = _eth_pt_initiator_top()
        be = _backend(top)
        conns = {c["port"]: c["signal"]
                 for c in be._build_instances()[0]["connections"]}
        # initiator-boundary signal mapping: <child_port>_<sig> -> <top>_<sig>
        assert conns["eth_ss_1_haddr"] == "eth_ss_0_haddr"
        assert conns["eth_ss_1_hrdata"] == "eth_ss_0_hrdata"
        assert conns["eth_ss_1_hready"] == "eth_ss_0_hready"
        # no hsel / hreadyout in the initiator boundary
        assert "eth_ss_1_hsel" not in conns
        assert "eth_ss_1_hreadyout" not in conns

    def test_top_port_declaration_takes_initiator_boundary(self):
        """The external eth_ss_0 PORT DECLARATION must match the instantiation:
        the initiator boundary with directions INVERTED (the module is the
        slave end of an external master) — so the master-driven address-phase
        signals are module INPUTS, the response signals are OUTPUTS, and there
        is no spurious hsel/hreadyout."""
        top, _ = _eth_pt_initiator_top()
        ports = {p["name"]: p for p in _backend(top)._build_ports()}
        # master drives address phase IN -> module input
        assert ports["eth_ss_0_haddr"]["direction"] == "input"
        assert ports["eth_ss_0_hwrite"]["direction"] == "input"
        # master receives response -> module output
        assert ports["eth_ss_0_hrdata"]["direction"] == "output"
        assert ports["eth_ss_0_hready"]["direction"] == "output"
        # initiator boundary: no slave-side hsel / hreadyout
        assert "eth_ss_0_hsel" not in ports
        assert "eth_ss_0_hreadyout" not in ports

    def test_get_instance_port_width(self):
        child = Module(name="prod", gen=False)
        child.params = {}
        child.interfaces = [_wire("BUS", "out", 8)]
        top = Module(name="t", gen=True)
        u = Instance(instance_name="u_p", module_name="prod")
        u.resolved_module = child
        top.instances = [u]
        be = _backend(top)
        assert be._get_instance_port_width(u, "BUS") == 8
        assert be._get_instance_port_width(u, "NOPE") is None

    def test_find_instance_interface_inline_fallback(self):
        """_find_instance_interface falls back to inline_interfaces when the
        port is not on the resolved module."""
        top = Module(name="t", gen=True)
        inst = Instance(instance_name="u_rtl", module_name="rtl_blk",
                        is_rtl_module=True)
        inst.inline_interfaces = [_wire("CUSTOM", "in", 1)]
        inst.resolved_module = None
        be = _backend(top)
        iface = be._find_instance_interface(inst, "CUSTOM")
        assert iface is not None and iface.name == "CUSTOM"
        assert be._find_instance_interface(inst, "MISSING") is None


# ===========================================================================
# _expand_bus_connection (846-905) — direct instance<->interconnect wiring.
# ===========================================================================

class TestExpandBusConnection:
    def test_initiator_with_passthrough_target_collision(self):
        """An initiator whose name collides with a passthrough target uses the
        '_ic_' feedback wires to avoid a multi-driver conflict."""
        top = Module(name="bus_soc", gen=True)
        top.params = {
            "SYS_ADDR_W": Param(name="SYS_ADDR_W", type="int", default=32),
            "SYS_DATA_W": Param(name="SYS_DATA_W", type="int", default=32),
        }
        ic = Interconnect(name="bus_ahb_interconnect", gen=True)
        ic.initiators = [InterconnectInitiator(name="cpu_0", instance="u_cpu")]
        ic.targets = [
            InterconnectTarget(name="sram_0", instance="u_sram",
                               base=0, size=0x1000),
            InterconnectTarget(name="system", base=0x20000000, size=0x10000000,
                               passthrough="cpu_0"),   # collides with cpu_0
        ]
        top.interconnects = [ic]

        cpu = Module(name="cpu", gen=False)
        cpu.interfaces = [_ahb("ahb_master", "initiator")]
        u_cpu = Instance(instance_name="u_cpu", module_name="cpu")
        u_cpu.connections = [
            Connection(port="ahb_master", conn="bus_ahb_interconnect.cpu_0"),
        ]
        u_cpu.resolved_module = cpu

        sram = Module(name="sram", gen=False)
        sram.interfaces = [_ahb("ahb_slave", "target", EXCLUDE=["hburst"])]
        u_sram = Instance(instance_name="u_sram", module_name="sram")
        u_sram.connections = [
            Connection(port="ahb_slave", conn="bus_ahb_interconnect.sram_0"),
        ]
        u_sram.resolved_module = sram

        top.instances = [u_cpu, u_sram]
        be = _backend(top)
        insts = {i["instance_name"]: i for i in be._build_instances()}

        cpu_conns = {c["port"]: c["signal"]
                     for c in insts["u_cpu"]["connections"]}
        # initiator with collision -> cpu_0_ic_<sig> feedback wires
        assert cpu_conns["HADDR"] == "cpu_0_ic_haddr"
        assert cpu_conns["HRDATA"] == "cpu_0_ic_hrdata"

        sram_conns = {c["port"]: c["signal"]
                      for c in insts["u_sram"]["connections"]}
        # target -> sram_0_<sig>; the EXCLUDE'd signal is dropped
        assert sram_conns["HSEL"] == "sram_0_hsel"
        assert sram_conns["HADDR"] == "sram_0_haddr"
        assert "HBURST" not in sram_conns

    def test_target_child_pt_initiator_maps_hready_to_hreadyout(self):
        """A child AHB port that is a passthrough-initiator on its own matrix,
        wired as a parent-interconnect TARGET, takes the cmsdk initiator
        boundary; its bare 'hready' output maps onto the matrix 'hreadyout'."""
        child = Module(name="eth_ss", gen=True)
        cic = Interconnect(name="eth_ss_ahb_interconnect", gen=True)
        cic.initiators = [InterconnectInitiator(name="eth_ss_1",
                                                passthrough="eth_ss_1")]
        child.interconnects = [cic]
        child.interfaces = [_ahb("eth_ss_1", "target")]

        top = Module(name="bus2_soc", gen=True)
        top.params = {
            "SYS_ADDR_W": Param(name="SYS_ADDR_W", type="int", default=32),
            "SYS_DATA_W": Param(name="SYS_DATA_W", type="int", default=32),
        }
        ic = Interconnect(name="bus2_ahb_interconnect", gen=True)
        ic.initiators = [InterconnectInitiator(name="cpu_0")]
        ic.targets = [InterconnectTarget(name="eth_t", instance="u_eth",
                                         base=0x40000000, size=0x1000)]
        top.interconnects = [ic]

        u_eth = Instance(instance_name="u_eth", module_name="eth_ss")
        u_eth.connections = [
            Connection(port="eth_ss_1", conn="bus2_ahb_interconnect.eth_t"),
        ]
        u_eth.resolved_module = child
        top.instances = [u_eth]

        conns = {c["port"]: c["signal"]
                 for c in _backend(top)._build_instances()[0]["connections"]}
        # initiator-boundary names, target-wire signals
        assert conns["eth_ss_1_haddr"] == "eth_t_haddr"
        assert conns["eth_ss_1_hrdata"] == "eth_t_hrdata"
        # the tell-tale: child initiator 'hready' -> matrix target 'hreadyout'
        assert conns["eth_ss_1_hready"] == "eth_t_hreadyout"
        assert "eth_ss_1_hsel" not in conns


# ===========================================================================
# _coalesce_bit_select_ports (1128-1159) + _get_instance_port_width (1165-1171).
# ===========================================================================

class TestCoalesceBitSelect:
    def test_multi_bit_concat_and_singletons(self):
        child = Module(name="gpio_blk", gen=False)
        child.interfaces = [
            _wire("P", "out", 4),     # 4-bit port collected from 4 bit-selects
            _wire("EN", "out", 1),    # 1-bit port from a single bit-select
        ]
        inst = Instance(instance_name="u_g", module_name="gpio_blk")
        inst.resolved_module = child
        conns = [
            {"port": "P[3]", "signal": "a3"},
            {"port": "P[2]", "signal": "a2"},
            {"port": "P[1]", "signal": "a1"},
            {"port": "P[0]", "signal": "a0"},
            {"port": "EN[0]", "signal": "en"},
            {"port": "plain", "signal": "p"},
            {"port": "open_p", "signal": None},
        ]
        out = {c["port"]: c.get("signal")
               for c in _backend(Module(name="t", gen=True))
               ._coalesce_bit_select_ports(conns, inst)}
        # multi-bit -> MSB-first concatenation
        assert out["P"] == "{a3, a2, a1, a0}"
        # 1-bit port, single bit -> bare base name
        assert out["EN"] == "en"
        # non-sliced ports pass through untouched
        assert out["plain"] == "p"
        # a signal-None (unconnected) connection survives unchanged
        assert out["open_p"] is None

    def test_single_partial_bit_best_effort(self):
        """A lone bit-select on a wider port (width unknown / partial) connects
        directly to the base port (best effort)."""
        child = Module(name="blk", gen=False)
        child.interfaces = [_wire("WIDE", "out", 8)]
        inst = Instance(instance_name="u_w", module_name="blk")
        inst.resolved_module = child
        conns = [{"port": "WIDE[2]", "signal": "s2"}]
        out = _backend(Module(name="t", gen=True))._coalesce_bit_select_ports(
            conns, inst)
        assert out == [{"port": "WIDE", "signal": "s2"}]

    def test_single_bit_covers_full_width(self):
        """A single slice covering the full port width collapses to the base."""
        child = Module(name="blk", gen=False)
        child.interfaces = [_wire("Q", "out", 4)]
        inst = Instance(instance_name="u_q", module_name="blk")
        inst.resolved_module = child
        conns = [{"port": "Q[3:0]", "signal": "nibble"}]
        out = _backend(Module(name="t", gen=True))._coalesce_bit_select_ports(
            conns, inst)
        assert out == [{"port": "Q", "signal": "nibble"}]
