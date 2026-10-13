# Copyright 2026, SoC Labs (www.soclabs.org)
"""Regression tests: a link declared from BOTH endpoints must be ONE net.

Downstream provenance: nanosoc_m0_soc PYNQ-Z2 bring-up
(doc/reports/pynq_z2_build.md §8.5c). The debug<->hostio4 ADP byte streams
were declared from both sides:

    u_ss_debug:   { port: ADP_RXD_TVALID_o, conn: u_ss_hostio4.ADP_RXD_TVALID }
    u_ss_hostio4: { port: ADP_RXD_TVALID,   conn: u_ss_debug.ADP_RXD_TVALID_o }

_resolve_conn_to_wire's cycle breaker returned the flat name of whichever
reference it happened to START from, so the two resolutions produced TWO
disjoint wires (u_ss_debug_ADP_RXD_TVALID_o vs u_ss_hostio4_ADP_RXD_TVALID)
carrying the same logical signal — the whole ADP path was severed and USRT2
TX backpressure hung UartPutc.

The fix canonicalises the cycle members to one deterministic flat wire
(lexicographically smallest member), so every entry point into the cycle
resolves to the SAME net.
"""

import pytest

from soc_model.backends.toplevel import SoCTopLevelBackend
from soc_model.model import Connection, Instance, Interface, Module

pytestmark = pytest.mark.unit


def _wire(name, direction="in", width=1):
    return Interface(name=name, type="wire", direction=direction,
                     params={"WIDTH": width})


def _inst(name, module, connections=()):
    u = Instance(instance_name=name, module_name=module.name)
    u.connections = list(connections)
    u.resolved_module = module
    return u


def _mutual_pair_top():
    """debug/hostio-shaped mutual declaration: driver output X on u_a, consumer
    input Y on u_b, EACH side declaring the link at the other's port."""
    a = Module(name="debug_ss", gen=False)
    a.interfaces = [_wire("X", "out", 8)]
    b = Module(name="hostio", gen=False)
    b.interfaces = [_wire("Y", "in", 8)]
    top = Module(name="soc", gen=True)
    top.instances = [
        _inst("u_a", a, [Connection(port="X", conn="u_b.Y")]),
        _inst("u_b", b, [Connection(port="Y", conn="u_a.X")]),
    ]
    return top


def _conns(ctx, inst_name):
    inst = next(i for i in ctx["instances"] if i["instance_name"] == inst_name)
    return {c["port"]: c["signal"] for c in inst["connections"]}


class TestDuplicateLinkCanonicalisation:
    def test_resolution_is_entry_point_independent(self):
        """REGRESSION: both directions of a mutual reference must resolve to
        the SAME canonical flat wire (previously each start point produced
        its own flat name -> two disjoint wires)."""
        be = SoCTopLevelBackend(_mutual_pair_top())
        assert be._resolve_conn_to_wire("u_a.X") == "u_a_X"
        assert be._resolve_conn_to_wire("u_b.Y") == "u_a_X"

    def test_both_instances_bound_to_one_wire(self):
        ctx = SoCTopLevelBackend(_mutual_pair_top())._build_context()
        assert _conns(ctx, "u_a")["X"] == "u_a_X"
        assert _conns(ctx, "u_b")["Y"] == "u_a_X"

    def test_single_xref_wire_declared_with_port_width(self):
        """Exactly ONE wire is declared for the pair (not two), sized from
        the canonical member's port."""
        ctx = SoCTopLevelBackend(_mutual_pair_top())._build_context()
        names = [w["name"] for w in ctx["xref_wires"]]
        assert names.count("u_a_X") == 1
        assert "u_b_Y" not in names
        wire = next(w for w in ctx["xref_wires"] if w["name"] == "u_a_X")
        assert wire["width"] == 8

    def test_lead_in_reference_joins_the_same_net(self):
        """A third instance referencing INTO the cycle (u_c.din -> u_a.X)
        resolves to the same canonical wire, not a third name — and the
        lead-in reference itself must not perturb the canonical choice."""
        top = _mutual_pair_top()
        c = Module(name="spy", gen=False)
        c.interfaces = [_wire("din", "in", 8)]
        top.instances.append(
            _inst("u_c", c, [Connection(port="din", conn="u_a.X")]))
        be = SoCTopLevelBackend(top)
        ctx = be._build_context()
        assert _conns(ctx, "u_c")["din"] == "u_a_X"
        assert _conns(ctx, "u_a")["X"] == "u_a_X"
        assert _conns(ctx, "u_b")["Y"] == "u_a_X"
        assert [w["name"] for w in ctx["xref_wires"]].count("u_a_X") == 1

    def test_bit_sliced_mutual_reference_keeps_slices(self):
        """Slices ride on top of the canonical name."""
        a = Module(name="a", gen=False)
        a.interfaces = [_wire("X", "out", 8)]
        b = Module(name="b", gen=False)
        b.interfaces = [_wire("Y", "in", 4)]
        top = Module(name="soc", gen=True)
        top.instances = [
            _inst("u_a", a, [Connection(port="X", conn="u_b.Y")]),
            _inst("u_b", b, [Connection(port="Y", conn="u_a.X[3:0]")]),
        ]
        be = SoCTopLevelBackend(top)
        assert be._resolve_conn_to_wire("u_a.X[3:0]") == "u_a_X[3:0]"
        assert be._resolve_conn_to_wire("u_a.X[2]") == "u_a_X[2]"

    def test_render_declares_one_wire_used_twice(self, tmp_path):
        out = SoCTopLevelBackend(_mutual_pair_top()).generate(
            tmp_path / "rtl", tmp_path / "flist")
        sv = out.read_text()
        assert "u_b_Y" not in sv                      # no disjoint twin wire
        assert sv.count("(u_a_X)") == 2               # driver + consumer
        assert sv.count("wire [7:0]                u_a_X;") == 1
