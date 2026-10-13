# Copyright 2026, SoC Labs (www.soclabs.org)
"""Regression tests: INST_PORT_PREFIXED opt-out of bare AHB port naming.

Downstream provenance: nanosoc_m0_soc PYNQ-Z2 bring-up
(doc/reports/pynq_z2_build.md §8.4). The top-level backend emits BARE
uppercase AHB port names (.HADDR/.HTRANS/...) for gen:False modules with a
single AHB interface (toplevel.py::_ahb_inst_port_name), but the actual
arch_tech RTL keeps PREFIXED names:

  * nanosoc_ss_debug.v      : DEBUG_HADDR / DEBUG_HTRANS / ...
  * nanosoc_ss_systemctrl.v : SOC_PERIPHERAL_HSEL / SOC_PERIPHERAL_HADDR / ...

Elaboration failed on 11 named-port mismatches and the project had to carry
local-override RTL copies with renamed ports.

The subsystem backend already grew the `INST_PORT_PREFIXED: True` iface
param for the same problem on `ahb_master` bundles
(subsystem.py::_expand_bus_connection). This teaches the TOP-LEVEL backend
(and the subsystem `ahb_slave` bare rule) the same knob: a module whose
hand-written RTL keeps prefixed AHB ports declares it on the interface in
its module YAML, and every instantiation follows. Default behaviour
(no flag) is pinned unchanged.
"""

import pytest

from soc_model.backends.subsystem import SoCSubsystemBackend
from soc_model.backends.toplevel import SoCTopLevelBackend
from soc_model.model import (
    Connection, Instance, Interconnect, InterconnectInitiator,
    InterconnectInitiatorTarget, InterconnectTarget, Interface, Module,
)

pytestmark = pytest.mark.unit


def _ahb(name, direction, params=None):
    p = {"ADDR_WIDTH": "$SYS_ADDR_W", "DATA_WIDTH": "$SYS_DATA_W"}
    p.update(params or {})
    return Interface(name=name, type="ahb", direction=direction, params=p)


def _genfalse_module(name, iface):
    m = Module(name=name, gen=False)
    m.interfaces = [iface]
    return m


def _inst(name, module, connections=()):
    u = Instance(instance_name=name, module_name=module.name)
    u.connections = list(connections)
    u.resolved_module = module
    return u


def _be(top):
    be = SoCTopLevelBackend(top)
    be._output_port_to_xref = {}
    return be


class TestAhbInstPortName:
    def test_default_bare_for_genfalse_single_ahb_pinned(self):
        """PIN: without the flag, a gen:False single-AHB module still gets
        BARE uppercase names (the slcorem0 convention) — no default change."""
        mod = _genfalse_module("dbg", _ahb("DEBUG", "initiator"))
        top = Module(name="soc", gen=True)
        u = _inst("u_dbg", mod)
        top.instances = [u]
        assert _be(top)._ahb_inst_port_name(u, "DEBUG", "haddr") == "HADDR"

    def test_prefixed_flag_uppercase_iface(self):
        """REGRESSION: INST_PORT_PREFIXED on the iface yields the arch_tech
        RTL's real names (DEBUG_HADDR), not bare HADDR."""
        mod = _genfalse_module(
            "nanosoc_ss_debug",
            _ahb("DEBUG", "initiator", {"INST_PORT_PREFIXED": True}))
        top = Module(name="soc", gen=True)
        u = _inst("u_dbg", mod)
        top.instances = [u]
        be = _be(top)
        assert be._ahb_inst_port_name(u, "DEBUG", "haddr") == "DEBUG_HADDR"
        assert be._ahb_inst_port_name(u, "DEBUG", "htrans") == "DEBUG_HTRANS"

    def test_prefixed_flag_lowercase_iface(self):
        """A lowercase iface name with the flag prefixes in lowercase
        (matching the inline rtl_module convention)."""
        mod = _genfalse_module(
            "per", _ahb("ahb_s", "target", {"INST_PORT_PREFIXED": True}))
        top = Module(name="soc", gen=True)
        u = _inst("u_per", mod)
        top.instances = [u]
        assert _be(top)._ahb_inst_port_name(u, "ahb_s", "hsel") == "ahb_s_hsel"

    def test_prefixed_flag_overrides_ahb_slave_bare_rule(self):
        mod = _genfalse_module(
            "region", _ahb("ahb_slave", "target",
                           {"INST_PORT_PREFIXED": True}))
        top = Module(name="soc", gen=True)
        u = _inst("u_r", mod)
        top.instances = [u]
        assert _be(top)._ahb_inst_port_name(
            u, "ahb_slave", "hsel") == "ahb_slave_hsel"

    def test_ahb_slave_default_bare_pinned(self):
        mod = _genfalse_module("region", _ahb("ahb_slave", "target"))
        mod.interfaces.append(_ahb("ahb_master", "initiator"))  # 2 AHB ifaces
        top = Module(name="soc", gen=True)
        u = _inst("u_r", mod)
        top.instances = [u]
        assert _be(top)._ahb_inst_port_name(u, "ahb_slave", "hsel") == "HSEL"


class TestBusConnectionExpansion:
    def _top_with_ic(self, flag):
        """systemctrl-shaped gen:False target on a top-level interconnect."""
        params = {"INST_PORT_PREFIXED": True} if flag else {}
        mod = _genfalse_module(
            "nanosoc_ss_systemctrl", _ahb("SOC_PERIPHERAL", "target", params))
        top = Module(name="soc", gen=True)
        top.instances = [
            _inst("u_sysctrl", mod, [
                Connection(port="SOC_PERIPHERAL", conn="soc_ic.soc_peripheral"),
            ]),
        ]
        ic = Interconnect(name="soc_ic")
        ic.targets = [InterconnectTarget(name="soc_peripheral",
                                         instance="u_sysctrl",
                                         base=0x40000000, size=0x1000)]
        ic.initiators = [InterconnectInitiator(
            name="cpu_0",
            targets=[InterconnectInitiatorTarget(name="soc_peripheral")])]
        top.interconnects = [ic]
        return top

    def test_target_expansion_prefixed(self):
        """End-to-end through _expand_bus_connection: the instantiation lists
        the RTL's real SOC_PERIPHERAL_* ports when the flag is set."""
        ctx = _be(self._top_with_ic(True))._build_context()
        inst = next(i for i in ctx["instances"]
                    if i["instance_name"] == "u_sysctrl")
        ports = {c["port"]: c["signal"] for c in inst["connections"]}
        assert ports.get("SOC_PERIPHERAL_HSEL") == "soc_peripheral_hsel"
        assert ports.get("SOC_PERIPHERAL_HADDR") == "soc_peripheral_haddr"
        assert "HSEL" not in ports
        # the hready -> matrix wire mapping is unchanged by the flag
        assert ports.get("SOC_PERIPHERAL_HREADY") == "soc_peripheral_hready"

    def test_target_expansion_default_bare_pinned(self):
        ctx = _be(self._top_with_ic(False))._build_context()
        inst = next(i for i in ctx["instances"]
                    if i["instance_name"] == "u_sysctrl")
        ports = {c["port"]: c["signal"] for c in inst["connections"]}
        assert ports.get("HSEL") == "soc_peripheral_hsel"
        assert "SOC_PERIPHERAL_HSEL" not in ports


class TestSubsystemAhbSlaveFlag:
    def _subsystem(self, flag):
        params = {"INST_PORT_PREFIXED": True} if flag else {}
        region = Module(name="tiny_region", gen=False)
        region.interfaces = [_ahb("ahb_slave", "target", params)]
        ss = Module(name="tiny_ss", gen=True)
        ss.instances = [
            _inst("u_r", region,
                  [Connection(port="ahb_slave", conn="ss_ic.sram_0")]),
        ]
        ic = Interconnect(name="ss_ic")
        ic.targets = [InterconnectTarget(name="sram_0", instance="u_r",
                                         base=0, size=0x1000)]
        ic.initiators = [InterconnectInitiator(
            name="cpu_0", targets=[InterconnectInitiatorTarget(name="sram_0")])]
        ss.interconnects = [ic]
        return ss

    def test_subsystem_ahb_slave_prefixed_with_flag(self):
        """The subsystem backend's bare `ahb_slave` rule honours the same
        knob (it already honoured it for ahb_master bundles)."""
        ctx = SoCSubsystemBackend(self._subsystem(True))._build_context()
        inst = next(i for i in ctx["instances"] if i["instance_name"] == "u_r")
        ports = {c["port"]: c["signal"] for c in inst["connections"]}
        assert "ahb_slave_hsel" in ports
        assert "HSEL" not in ports

    def test_subsystem_ahb_slave_default_bare_pinned(self):
        ctx = SoCSubsystemBackend(self._subsystem(False))._build_context()
        inst = next(i for i in ctx["instances"] if i["instance_name"] == "u_r")
        ports = {c["port"]: c["signal"] for c in inst["connections"]}
        assert "HSEL" in ports
        assert "ahb_slave_hsel" not in ports
