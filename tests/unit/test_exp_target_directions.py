# Copyright 2026, SoC Labs (www.soclabs.org)
"""Regression tests: external-target-socket AHB port DIRECTIONS.

Downstream provenance: nanosoc_m0_soc PYNQ-Z2 bring-up
(doc/reports/pynq_z2_build.md §8.2). The `exp` expansion region is an
interconnect target with NO instance and NO passthrough whose name matches
a top-level `type: ahb, direction: target` interface — the generated bus
matrix connects its target-side port group straight onto the expanded
top-level ports by name. At that boundary the SoC hosts the INITIATOR side
of the target socket: the matrix DRIVES hsel/haddr/htrans/... out to the
external slave and RECEIVES hrdata/hresp/hreadyout back. The backend
instead emitted the raw target-perspective directions:

    input  wire        exp_hsel;    // driven INTERNALLY by the matrix
    output wire [31:0] exp_hrdata;  // consumed INTERNALLY by the matrix

Simulators collapse the conflicting nets (coerced ports) so sim passes,
but Vivado refuses to back-propagate through coerced ports (verified
empirically on 2024.1): wiring the default slave through this boundary
produced undriven / multiply-driven nets and the FPGA wrapper had to tie
the whole expansion region off. The fix inverts the boundary directions
for exactly this shape; true targets (the SoC as addressable slave) and
passthrough-initiator endpoints are pinned unchanged.
"""

import pytest

from soc_model.backends.toplevel import SoCTopLevelBackend
from soc_model.model import (
    Connection, Instance, Interconnect, InterconnectInitiator,
    InterconnectInitiatorTarget, InterconnectTarget, Interface, Module, Param,
)

pytestmark = pytest.mark.unit


def _wire(name, direction="in", width=1):
    return Interface(name=name, type="wire", direction=direction,
                     params={"WIDTH": width})


def _ahb(name, direction, params=None):
    p = {"ADDR_WIDTH": "$SYS_ADDR_W", "DATA_WIDTH": "$SYS_DATA_W"}
    p.update(params or {})
    return Interface(name=name, type="ahb", direction=direction, params=p)


def _exp_soc():
    """Minimal exp-shaped SoC: one instance-bound SRAM target plus the
    instance-less, passthrough-less `exp` target whose name matches the
    top-level target iface."""
    region = Module(name="tiny_region", gen=False)
    region.interfaces = [
        _wire("HCLK"), _wire("HRESETn"), _ahb("ahb_slave", "target"),
    ]

    top = Module(name="exp_soc", gen=True)
    top.params = {
        "SYS_ADDR_W": Param(name="SYS_ADDR_W", type="int", default=32),
        "SYS_DATA_W": Param(name="SYS_DATA_W", type="int", default=32),
    }
    top.interfaces = [
        _wire("sys_hclk"), _wire("sys_hresetn"),
        _ahb("exp", "target"),
    ]
    u = Instance(instance_name="u_sram_0", module_name="tiny_region")
    u.resolved_module = region
    u.connections = [
        Connection(port="HCLK", conn="sys_hclk"),
        Connection(port="HRESETn", conn="sys_hresetn"),
        Connection(port="ahb_slave", conn="soc_ic.sram_0"),
    ]
    top.instances = [u]

    ic = Interconnect(name="soc_ic")
    ic.connections = [
        Connection(port="sys_hclk", conn="sys_hclk"),
        Connection(port="sys_hresetn", conn="sys_hresetn"),
    ]
    ic.targets = [
        InterconnectTarget(name="sram_0", instance="u_sram_0",
                           base=0x00000000, size=0x1000),
        InterconnectTarget(name="exp", base=0x60000000, size=0x10000000),
    ]
    ic.initiators = [InterconnectInitiator(
        name="cpu_0",
        targets=[InterconnectInitiatorTarget(name="sram_0"),
                 InterconnectInitiatorTarget(name="exp")])]
    top.interconnects = [ic]
    return top


def _port_dirs(top):
    ports = SoCTopLevelBackend(top)._build_ports()
    return {p["name"]: p["direction"] for p in ports}


OUTBOUND = ("hsel", "haddr", "htrans", "hwrite", "hsize", "hburst",
            "hprot", "hwdata", "hmastlock", "hready")
INBOUND = ("hrdata", "hresp", "hreadyout")


class TestExternalTargetSocketDirections:
    def test_exp_address_phase_is_output(self):
        """REGRESSION: the SoC drives the socket's address phase (and the
        matrix hreadymux) OUT to the external slave."""
        dirs = _port_dirs(_exp_soc())
        for sig in OUTBOUND:
            assert dirs[f"exp_{sig}"] == "output", sig

    def test_exp_response_phase_is_input(self):
        """REGRESSION: the external slave drives the response back IN."""
        dirs = _port_dirs(_exp_soc())
        for sig in INBOUND:
            assert dirs[f"exp_{sig}"] == "input", sig

    def test_true_target_iface_directions_pinned(self):
        """PIN: a top-level target iface that is NOT an interconnect target
        socket (the SoC itself is the addressable slave) keeps the raw
        target-perspective directions."""
        top = Module(name="slave_soc", gen=True)
        top.interfaces = [_ahb("dap_s", "target")]
        dirs = _port_dirs(top)
        assert dirs["dap_s_hsel"] == "input"
        assert dirs["dap_s_haddr"] == "input"
        assert dirs["dap_s_hrdata"] == "output"
        assert dirs["dap_s_hreadyout"] == "output"

    def test_instance_bound_same_name_target_not_inverted(self):
        """PIN: a target that IS instance-bound never flips a same-named
        top iface — only the instance-less, passthrough-less socket does."""
        top = _exp_soc()
        # bind the exp target to the sram instance: no longer an external socket
        top.interconnects[0].targets[1].instance = "u_sram_0"
        dirs = _port_dirs(top)
        assert dirs["exp_hsel"] == "input"
        assert dirs["exp_hrdata"] == "output"

    def test_conn_bound_same_name_target_not_inverted(self):
        """PIN: a target bound through an instance CONNECTION (rather than
        the `instance:` field) is not an external socket either."""
        top = _exp_soc()
        u = top.instances[0]
        u.resolved_module.interfaces.append(_ahb("exp_s", "target"))
        u.connections.append(Connection(port="exp_s", conn="soc_ic.exp"))
        dirs = _port_dirs(top)
        assert dirs["exp_hsel"] == "input"
        assert dirs["exp_hrdata"] == "output"

    def test_passthrough_target_iface_unaffected(self):
        """PIN: the passthrough-target convention (boundary iface declared
        `direction: initiator`, cpu_0-style) is untouched."""
        top = _exp_soc()
        top.interfaces.append(_ahb("sys_m", "initiator"))
        top.interconnects[0].targets.append(
            InterconnectTarget(name="system", base=0x20000000,
                               size=0x10000000, passthrough="sys_m"))
        dirs = _port_dirs(top)
        assert dirs["sys_m_haddr"] == "output"
        assert dirs["sys_m_hrdata"] == "input"
        # and exp stays fixed (inverted target boundary)
        assert dirs["exp_haddr"] == "output"

    def test_render_directions_and_matrix_hookup(self, tmp_path):
        """End-to-end: the rendered SV declares the corrected directions and
        the matrix target port group still binds the same nets by name."""
        out = SoCTopLevelBackend(_exp_soc()).generate(
            tmp_path / "rtl", tmp_path / "flist")
        sv = out.read_text()
        assert "output  wire [SYS_ADDR_W-1:0]     exp_haddr" in sv
        assert "output  wire                      exp_hsel" in sv
        assert "output  wire                      exp_hready" in sv
        assert "input   wire [SYS_DATA_W-1:0]     exp_hrdata" in sv
        assert "input   wire                      exp_hreadyout" in sv
        assert ".exp_hreadymux                   (exp_hready)" in sv
        assert ".exp_hrdata                      (exp_hrdata)" in sv
