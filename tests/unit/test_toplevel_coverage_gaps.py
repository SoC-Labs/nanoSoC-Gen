# Copyright 2026, SoC Labs (www.soclabs.org)
"""Gap-fill coverage for ``soc_model.backends.toplevel.SoCTopLevelBackend``.

The companion modules ``tests/golden/test_toplevel_sv.py`` (golden tier) and
``tests/unit/test_toplevel_connections.py`` (unit tier) already drive the bulk
of the backend through hermetic fixtures and hand-built ``Module`` graphs. This
module targets the *residual* uncovered arms that those two do not reach, each
pinned with a behavioural assertion (no coverage padding):

  * ``_resolve_conn_to_wire`` — the interconnect-ref short-circuit (957), the
    expanded-AHB-output-port fall-through (982-990), the resolve-direct
    bit-slice returns (994-996), the flat-fallback multi-bit slice (1004), and
    the cycle-break-with-bit-slice arm (947-949).
  * ``_build_internal_wires`` — an internal wire whose name collides with a
    top-level port is dropped (301).
  * ``_infer_width`` — the gpio empty-candidate ``continue`` (620) and the
    hierarchical-instance ``$PARAM`` width resolution (636).
  * ``_expand_protocol_connection`` — the SWD non-``_swd`` port arm (1098) and
    the ``dbg_ahb`` member expansion (1103-1109).
  * ``_build_interconnect`` — the initiator/passthrough-target name-collision
    ``_ic_`` feedback-wire arm (1225).
  * ``_build_passthrough_tieoffs`` — the ``hreadyout``-from-top-level-port arm
    (1303), exercised alongside a second passthrough target.

All tests are in-memory (no YAML / parser), in the unit tier.
"""

import pytest

from soc_model.backends.toplevel import SoCTopLevelBackend
from soc_model.model import (
    Connection, Instance, Interconnect,
    InterconnectInitiator, InterconnectTarget, Interface, Module, Param,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Local construction helpers (kept independent of the sibling test files so
# this module can be read / run in isolation).
# ---------------------------------------------------------------------------

def _wire(name, direction="in", width=1):
    return Interface(name=name, type="wire", direction=direction,
                     params={"WIDTH": width})


def _ahb(name, direction, **extra):
    params = {"ADDR_WIDTH": "$SYS_ADDR_W", "DATA_WIDTH": "$SYS_DATA_W"}
    params.update(extra)
    return Interface(name=name, type="ahb", direction=direction, params=params)


def _backend(top):
    be = SoCTopLevelBackend(top)
    be._output_port_to_xref = {}
    return be


def _sys_params():
    return {
        "SYS_ADDR_W": Param(name="SYS_ADDR_W", type="int", default=32),
        "SYS_DATA_W": Param(name="SYS_DATA_W", type="int", default=32),
    }


# ===========================================================================
# _resolve_conn_to_wire — residual arms (947-949, 957, 982-996, 1004).
# ===========================================================================

class TestResolveConnToWireGaps:
    def test_interconnect_ref_returned_verbatim(self):
        """A cross-ref whose instance segment names an interconnect is NOT a
        cross-instance wire — it is handled elsewhere — so it returns verbatim
        (line 957)."""
        top = Module(name="t", gen=True)
        top.interconnects = [Interconnect(name="myic", gen=True)]
        be = _backend(top)
        assert be._resolve_conn_to_wire("myic.some_port") == "myic.some_port"

    def test_resolves_to_expanded_ahb_output_port_falls_to_flat(self):
        """When a source port resolves to an EXPANDED AHB output port name
        (e.g. an initiator iface's ``m_haddr``), the resolver must NOT wire
        other instances onto that output directly — it falls through to the
        flat wire name (lines 982-990)."""
        top = Module(name="t", gen=True)
        top.interfaces = [Interface(name="m", type="ahb", direction="initiator",
                                    params={})]
        prod = Module(name="prod", gen=False)
        prod.interfaces = [_wire("P", "out", 1)]
        u = Instance(instance_name="u_p", module_name="prod")
        u.connections = [Connection(port="P", conn="m_haddr")]
        u.resolved_module = prod
        top.instances = [u]
        be = _backend(top)
        # m_haddr is an AHB-initiator (output) expanded port -> must not be
        # driven directly; resolver returns the intermediate flat wire instead.
        assert be._resolve_conn_to_wire("u_p.P") == "u_p_P"

    def test_resolves_direct_to_internal_wire_with_bit_slice(self):
        """A cross-ref that resolves cleanly to an internal wire carries its
        bit slice through (lines 993-996): multi-bit and single-bit forms."""
        top = Module(name="t", gen=True)
        top.internal_wires = [
            Interface(name="net", type="wire", direction="internal",
                      params={"WIDTH": 8}),
        ]
        prod = Module(name="prod", gen=False)
        prod.interfaces = [_wire("P", "out", 8)]
        u = Instance(instance_name="u_p", module_name="prod")
        u.connections = [Connection(port="P", conn="net")]
        u.resolved_module = prod
        top.instances = [u]
        be = _backend(top)
        assert be._resolve_conn_to_wire("u_p.P[3:0]") == "net[3:0]"
        assert be._resolve_conn_to_wire("u_p.P[5]") == "net[5]"

    def test_flat_fallback_carries_multibit_slice(self):
        """A cross-ref with no resolvable source connection falls through to
        the flat-wire fallback, preserving a multi-bit slice (line 1004)."""
        top = Module(name="t", gen=True)
        prod = Module(name="prod", gen=False)
        prod.interfaces = [_wire("P", "out", 8)]
        u = Instance(instance_name="u_p", module_name="prod")
        u.connections = []   # P has no driving connection -> flat fallback
        u.resolved_module = prod
        top.instances = [u]
        be = _backend(top)
        assert be._resolve_conn_to_wire("u_p.P[3:0]") == "u_p_P[3:0]"

    def test_cycle_break_with_bit_slice_executes(self):
        """The cycle-detection arm that carries a bit slice (lines 946-949) is
        reached when the *cyclic* reference itself is sliced. The mutual
        u_a.X <-> u_b.Y reference, where u_b.Y is wired to ``u_a.X[3:0]``,
        recurses until the sliced ``u_a.X`` re-enters the visited set."""
        a = Module(name="a", gen=False)
        a.interfaces = [_wire("X", "out", 4)]
        b = Module(name="b", gen=False)
        b.interfaces = [_wire("Y", "out", 4)]
        top = Module(name="cyc", gen=True)
        u_a = Instance(instance_name="u_a", module_name="a")
        u_a.connections = [Connection(port="X", conn="u_b.Y")]
        u_a.resolved_module = a
        u_b = Instance(instance_name="u_b", module_name="b")
        u_b.connections = [Connection(port="Y", conn="u_a.X[3:0]")]
        u_b.resolved_module = b
        top.instances = [u_a, u_b]
        be = _backend(top)
        # The whole chain resolves to the flat wire (cycle broken); the assert
        # pins that a sliced cyclic ref does not crash and collapses to a flat
        # name rather than recursing forever.
        assert be._resolve_conn_to_wire("u_a.X") == "u_a_X"

    def test_cycle_break_with_single_bit_slice_executes(self):
        """Same cycle break, but the cyclic ref carries a single-bit slice
        (``u_a.X[2]``) — exercises the high==low arm at line 948."""
        a = Module(name="a", gen=False)
        a.interfaces = [_wire("X", "out", 4)]
        b = Module(name="b", gen=False)
        b.interfaces = [_wire("Y", "out", 4)]
        top = Module(name="cyc", gen=True)
        u_a = Instance(instance_name="u_a", module_name="a")
        u_a.connections = [Connection(port="X", conn="u_b.Y")]
        u_a.resolved_module = a
        u_b = Instance(instance_name="u_b", module_name="b")
        u_b.connections = [Connection(port="Y", conn="u_a.X[2]")]
        u_b.resolved_module = b
        top.instances = [u_a, u_b]
        be = _backend(top)
        assert be._resolve_conn_to_wire("u_a.X") == "u_a_X"


# ===========================================================================
# _build_internal_wires — port-name collision drop (line 301).
# ===========================================================================

class TestInternalWirePortCollision:
    def test_internal_wire_sharing_top_port_name_is_dropped(self):
        top = Module(name="t", gen=True)
        top.interfaces = [_wire("shared", "in", 1)]
        top.internal_wires = [
            # collides with the top-level port 'shared' -> dropped (line 301)
            Interface(name="shared", type="wire", direction="internal",
                      params={"WIDTH": 1}),
            Interface(name="only_internal", type="wire", direction="internal",
                      params={"WIDTH": 2}),
        ]
        names = [w["name"] for w in _backend(top)._build_internal_wires()]
        assert names == ["only_internal"]
        assert "shared" not in names


# ===========================================================================
# _infer_width — gpio empty-candidate skip (620) + hierarchical $PARAM (636).
# ===========================================================================

class TestInferWidthGaps:
    def test_gpio_loop_skips_empty_candidate(self):
        """With a gpio iface present and an EMPTY signal name, the gpio
        candidate loop hits the ``if not check_name: continue`` guard (line
        620) and the inference falls back to the default width of 1."""
        top = Module(name="g", gen=True)
        top.interfaces = [
            Interface(name="p0", type="gpio", direction="inout",
                      params={"WIDTH": 16}),
        ]
        assert _backend(top)._infer_width("", None) == 1

    def test_hierarchical_instance_port_param_width_resolved(self):
        """A ``inst.port`` reference whose child iface width is a ``$PARAM``
        string resolves against flat_params (line 636)."""
        child = Module(name="prod", gen=False)
        child.interfaces = [
            Interface(name="BUS", type="wire", direction="out",
                      params={"WIDTH": "$SYS_DATA_W"}),
        ]
        top = Module(name="t", gen=True)
        top.params = {"SYS_DATA_W": Param(name="SYS_DATA_W", type="int",
                                          default=64)}
        u = Instance(instance_name="u_p", module_name="prod")
        u.resolved_module = child
        top.instances = [u]
        assert _backend(top)._infer_width("u_p.BUS", None) == 64


# ===========================================================================
# _expand_protocol_connection — SWD non-_swd arm (1098) + dbg_ahb (1103-1109).
# ===========================================================================

class TestProtocolConnectionGaps:
    def test_swd_port_without_swd_suffix(self):
        """A SWD interface whose name does NOT end in ``_swd`` keeps its name
        verbatim and appends the signal suffix (line 1098)."""
        top = Module(name="t", gen=True)
        top.interfaces = [Interface(name="dbgport", type="swd",
                                    direction="in", params={})]
        child = Module(name="cpu", gen=False)
        child.interfaces = [Interface(name="dbgport", type="swd",
                                      direction="in", params={})]
        inst = Instance(instance_name="u_cpu", module_name="cpu")
        inst.connections = [Connection(port="dbgport", conn="dbgport")]
        inst.resolved_module = child
        top.instances = [inst]
        conns = {c["port"]: c["signal"]
                 for c in _backend(top)._build_instances()[0]["connections"]}
        # no _swd suffix to strip -> base name kept on both port and signal
        assert conns == {
            "dbgport_swdi": "dbgport_swdi",
            "dbgport_swclk": "dbgport_swclk",
            "dbgport_swdo": "dbgport_swdo",
            "dbgport_swdoen": "dbgport_swdoen",
        }

    def test_dbg_ahb_member_expansion(self):
        """The ``dbg_ahb`` branch of _expand_protocol_connection expands the
        Cortex-M debug-slave bus into its member signals (lines 1103-1109).

        It is invoked directly because _build_instances only routes
        axis/axis_byte/swd ifaces through _expand_protocol_connection; dbg_ahb
        member expansion is its own boundary path."""
        top = Module(name="t", gen=True)
        child = Module(name="cpu", gen=False)
        inst = Instance(instance_name="u_cpu", module_name="cpu")
        inst.resolved_module = child
        top.instances = [inst]
        be = _backend(top)
        dbg = Interface(name="cpu_0_dbgahb", type="dbg_ahb",
                        direction="in", params={})
        inst_data = {"connections": []}
        be._expand_protocol_connection(inst, inst_data, "cpu_0_dbgahb", dbg)
        conns = {c["port"]: c["signal"] for c in inst_data["connections"]}
        # all 8 DBGAHB_TARGET_SIGNALS members, port==signal==iface_sig
        assert conns == {
            "cpu_0_dbgahb_slvaddr": "cpu_0_dbgahb_slvaddr",
            "cpu_0_dbgahb_slvwdata": "cpu_0_dbgahb_slvwdata",
            "cpu_0_dbgahb_slvtrans": "cpu_0_dbgahb_slvtrans",
            "cpu_0_dbgahb_slvwrite": "cpu_0_dbgahb_slvwrite",
            "cpu_0_dbgahb_slvsize": "cpu_0_dbgahb_slvsize",
            "cpu_0_dbgahb_slvrdata": "cpu_0_dbgahb_slvrdata",
            "cpu_0_dbgahb_slvready": "cpu_0_dbgahb_slvready",
            "cpu_0_dbgahb_slvresp": "cpu_0_dbgahb_slvresp",
        }


# ===========================================================================
# _build_interconnect — initiator/passthrough-target collision _ic_ arm (1225)
# and _build_passthrough_tieoffs — hreadyout-from-port arm (1303), with a
# SECOND passthrough target to exercise the multi-target loop.
# ===========================================================================

def _two_passthrough_top():
    """A single interconnect with TWO passthrough targets, one of which
    collides with an initiator name (cpu_0), plus a second passthrough target
    (sys_m) whose top-level AHB port exposes hreadyout."""
    top = Module(name="pt2_soc", gen=True)
    top.params = _sys_params()
    # sys_m declared as an AHB *target* iface so hreadyout IS a top-level port
    # (AHB_TARGET_SIGNALS includes hreadyout) -> drives the 1303 'from port' arm.
    top.interfaces = [_ahb("sys_m", "target")]

    ic = Interconnect(name="pt2_ahb_interconnect", gen=True)
    ic.initiators = [InterconnectInitiator(name="cpu_0")]
    ic.targets = [
        # passthrough target whose name collides with the cpu_0 initiator
        InterconnectTarget(name="local", base=0x00000000, size=0x10000000,
                           passthrough="cpu_0"),
        # second passthrough target with a real top-level AHB port
        InterconnectTarget(name="system", base=0x20000000, size=0x10000000,
                           passthrough="sys_m"),
    ]
    top.interconnects = [ic]
    return top


class TestInterconnectAndTieoffGaps:
    def test_initiator_collision_uses_ic_feedback_wires(self):
        """When an initiator name collides with a passthrough target, the
        interconnect port list routes ALL initiator signals onto unique
        ``<name>_ic_<sig>`` feedback wires (line 1225)."""
        be = _backend(_two_passthrough_top())
        icd = be._build_interconnect()
        init_ports = {p["port"]: p["signal"]
                      for p in icd["ports"]
                      if p["type"] == "port" and p["port"].startswith("cpu_0_")}
        # cpu_0 collides with the 'local' passthrough target -> _ic_ wires.
        assert init_ports["cpu_0_haddr"] == "cpu_0_ic_haddr"
        assert init_ports["cpu_0_hrdata"] == "cpu_0_ic_hrdata"
        # sanity: every cpu_0 initiator signal took the _ic_ form.
        assert all("_ic_" in sig for sig in init_ports.values())

    def test_non_colliding_initiator_keeps_plain_wires(self):
        """Control: an initiator with NO passthrough-target collision keeps the
        plain ``<name>_<sig>`` form (the else arm at 1227)."""
        top = Module(name="plain_soc", gen=True)
        top.params = _sys_params()
        ic = Interconnect(name="plain_ahb_interconnect", gen=True)
        ic.initiators = [InterconnectInitiator(name="cpu_0")]
        ic.targets = [InterconnectTarget(name="sram_0", instance="u_sram",
                                         base=0, size=0x1000)]
        top.interconnects = [ic]
        icd = _backend(top)._build_interconnect()
        init_ports = {p["port"]: p["signal"]
                      for p in icd["ports"]
                      if p["type"] == "port" and p["port"].startswith("cpu_0_")}
        assert init_ports["cpu_0_haddr"] == "cpu_0_haddr"
        assert all("_ic_" not in sig for sig in init_ports.values())

    def test_passthrough_hreadyout_sourced_from_top_port(self):
        """For the ``sys_m`` passthrough target whose top-level AHB port
        carries hreadyout, the tie-off sources the internal wire FROM the
        top-level port rather than tying to 1'b1 (line 1303)."""
        tieoffs = _backend(_two_passthrough_top())._build_passthrough_tieoffs()
        by_wire = {(t["wire"], t["value"]) for t in tieoffs}
        # 'system' target -> system_hreadyout sourced from the sys_m port.
        assert ("system_hreadyout", "sys_m_hreadyout") in by_wire
        # And NOT tied to a constant for this target.
        assert ("system_hreadyout", "1'b1") not in by_wire

    def test_passthrough_hreadyout_tied_high_when_no_top_port(self):
        """Control: a passthrough target with NO matching top-level hreadyout
        port ties hreadyout to 1'b1 (the else arm at 1308-1313)."""
        top = Module(name="tie_soc", gen=True)
        top.params = _sys_params()
        # No top-level AHB port for this passthrough -> hreadyout has no port.
        ic = Interconnect(name="tie_ahb_interconnect", gen=True)
        ic.initiators = [InterconnectInitiator(name="cpu_0")]
        ic.targets = [InterconnectTarget(name="system", base=0x20000000,
                                         size=0x10000000, passthrough="ghost")]
        top.interconnects = [ic]
        tieoffs = _backend(top)._build_passthrough_tieoffs()
        by_wire = {(t["wire"], t["value"]) for t in tieoffs}
        assert ("system_hreadyout", "1'b1") in by_wire
