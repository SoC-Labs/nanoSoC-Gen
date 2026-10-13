"""Unit tests for the performance-probe backend (SoCPerfProbeBackend) — M2.

Covers: the probe: / perf_probes: schema parse, the tap wiring-plan net
names, harness + flist emission, and the STRICT no-op gate (probe-free models
must produce nothing, so they regenerate byte-identically).

Run from the nanosoc_gen root:
    python3 -m pytest tests/test_perf_probe_backend.py -v
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from soc_model.parser import SoCParser                       # noqa: E402
from soc_model.builder import SoCBuilder                     # noqa: E402
from soc_model.model import (                                # noqa: E402
    Interconnect, InterconnectInitiator, InterconnectTarget, Module,
)
from soc_model.backends.perf_probe import (                  # noqa: E402
    SoCPerfProbeBackend, probe_plan, module_prefix, inject_perf_probes,
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _builder():
    return SoCBuilder(SoCParser(".", ["."]))


def _ic(name="eth_ss_ahb_interconnect", initiators=None, targets=None,
        perf_probes=None):
    return Interconnect(
        name=name,
        initiators=initiators or [],
        targets=targets or [],
        perf_probes=perf_probes,
    )


def _top_with(ic):
    """Duck-typed top module carrying one interconnect, no child instances."""
    return types.SimpleNamespace(interconnects=[ic], instances=[])


# --------------------------------------------------------------------------
# schema parse
# --------------------------------------------------------------------------
def test_builder_parses_probe_and_perf_probes():
    b = _builder()
    ic = b._build_interconnect({
        "name": "eth_ss_ahb_interconnect",
        "perf_probes": {"instance_name": "u_pp_0",
                        "base": 0x58000000, "size": 0x08000000},
        "targets": [
            {"name": "ethmac_0", "instance": "u_ethmac_0", "probe": True},
            {"name": "dmem_0", "instance": "u_region_dmem_0"},
        ],
        "initiators": [
            {"name": "cpu_0", "instance": "u_cpu_0", "probe": True},
            {"name": "eth_ss_0", "passthrough": "eth_ss_0"},
        ],
    })
    assert ic.perf_probes["base"] == 0x58000000
    tgt = {t.name: t for t in ic.targets}
    assert tgt["ethmac_0"].probe is True
    assert tgt["dmem_0"].probe is False
    init = {i.name: i for i in ic.initiators}
    assert init["cpu_0"].probe is True
    assert init["eth_ss_0"].probe is False


def test_builder_probe_defaults_false():
    b = _builder()
    ic = b._build_interconnect({"name": "ic",
                                "targets": [{"name": "t"}],
                                "initiators": [{"name": "m"}]})
    assert ic.targets[0].probe is False
    assert ic.initiators[0].probe is False
    assert ic.perf_probes is None


# --------------------------------------------------------------------------
# wiring plan (tap net names)
# --------------------------------------------------------------------------
def test_probe_plan_initiator_taps():
    ic = _ic(initiators=[InterconnectInitiator(name="cpu_0", probe=True)])
    plan = probe_plan(ic)
    assert len(plan) == 1
    p = plan[0]
    assert p["label"] == "cpu_0" and p["is_initiator"] == 1
    assert p["taps"] == {
        "hsel": "1'b1",
        "htrans": "cpu_0_htrans",
        "hready": "cpu_0_hready",
        "hwrite": "cpu_0_hwrite",
    }


def test_probe_plan_target_taps():
    ic = _ic(targets=[InterconnectTarget(name="ethmac_0", probe=True)])
    p = probe_plan(ic)[0]
    assert p["is_initiator"] == 0
    # target sees HREADY via the matrix's HREADYMUX, not its own HREADYOUT
    assert p["taps"] == {
        "hsel": "ethmac_0_hsel",
        "htrans": "ethmac_0_htrans",
        "hready": "ethmac_0_hreadymux",
        "hwrite": "ethmac_0_hwrite",
    }


def test_probe_plan_order_initiators_before_targets():
    ic = _ic(
        initiators=[InterconnectInitiator(name="cpu_0", probe=True),
                    InterconnectInitiator(name="dma_0", probe=False)],
        targets=[InterconnectTarget(name="ethmac_0", probe=True),
                 InterconnectTarget(name="dmem_0", probe=False)],
    )
    labels = [p["label"] for p in probe_plan(ic)]
    assert labels == ["cpu_0", "ethmac_0"]  # only probed, initiators first


def test_probe_plan_empty_when_no_probes():
    ic = _ic(initiators=[InterconnectInitiator(name="cpu_0")],
             targets=[InterconnectTarget(name="dmem_0")])
    assert probe_plan(ic) == []


def test_module_prefix():
    assert module_prefix("eth_ss_ahb_interconnect") == "eth_ss"
    assert module_prefix("nanosoc_interconnect") == "nanosoc"


# --------------------------------------------------------------------------
# no-op gate (byte-identical guarantee for probe-free models)
# --------------------------------------------------------------------------
def test_noop_without_perf_probes_block(tmp_path):
    # probed ports BUT no perf_probes block -> emit nothing
    ic = _ic(initiators=[InterconnectInitiator(name="cpu_0", probe=True)])
    be = SoCPerfProbeBackend(_top_with(ic))
    assert be.generate_all(tmp_path) == []
    assert list(tmp_path.iterdir()) == []


def test_noop_without_probed_ports(tmp_path):
    # perf_probes block BUT no probed ports -> emit nothing
    ic = _ic(initiators=[InterconnectInitiator(name="cpu_0")],
             perf_probes={"instance_name": "u_pp_0", "base": 0, "size": 0})
    be = SoCPerfProbeBackend(_top_with(ic))
    assert be.generate_all(tmp_path) == []
    assert list(tmp_path.iterdir()) == []


def test_noop_recurses_children(tmp_path):
    ic = _ic(initiators=[InterconnectInitiator(name="cpu_0", probe=True)],
             perf_probes={"instance_name": "u_pp_0", "base": 0, "size": 0})
    child = types.SimpleNamespace(interconnects=[ic], instances=[])
    child_inst = types.SimpleNamespace(resolved_module=child)
    top = types.SimpleNamespace(interconnects=[], instances=[child_inst])
    be = SoCPerfProbeBackend(top)
    assert len(be.interconnects_with_probes()) == 1


# --------------------------------------------------------------------------
# emission
# --------------------------------------------------------------------------
def _full_ic():
    return _ic(
        perf_probes={"instance_name": "u_eth_ss_perf_probe_0",
                     "base": 0x58000000, "size": 0x08000000},
        initiators=[
            InterconnectInitiator(name="cpu_0", probe=True),
            InterconnectInitiator(name="ethmac_0_dma", probe=True),
        ],
        targets=[
            InterconnectTarget(name="ethmac_0", probe=True),
            InterconnectTarget(name="dmem_0", probe=False),
        ],
    )


def test_generate_emits_harness_and_flist(tmp_path):
    be = SoCPerfProbeBackend(_full_ic_top())
    out = be.generate_all(tmp_path)
    assert len(out) == 1
    harness = tmp_path / "rtl" / "eth_ss_ahb_interconnect" / "eth_ss_perf_probe_harness.sv"
    flist = tmp_path / "flist" / "eth_ss_perf_probe.flist"
    assert harness.exists() and flist.exists()

    sv = harness.read_text()
    # module + read-out instance + the three probed ports, two initiators first
    assert "module eth_ss_perf_probe_harness" in sv
    assert "localparam N_PROBES = 3;" in sv
    assert "ahb_perf_probe_regs #(.N_PROBES(N_PROBES)" in sv
    assert "u_eth_ss_perf_probe_0" in sv
    assert sv.count("ahb_perf_probe #(") == 3
    assert "u_probe_cpu_0" in sv and "u_probe_ethmac_0_dma" in sv and "u_probe_ethmac_0" in sv
    # initiator probes get IS_INITIATOR(1), the target probe IS_INITIATOR(0)
    assert sv.count(".IS_INITIATOR(1)") == 2
    assert sv.count(".IS_INITIATOR(0)") == 1
    # dmem_0 is NOT probed
    assert "u_probe_dmem_0" not in sv

    fl = flist.read_text()
    assert "ahb_perf_probe.v" in fl and "ahb_perf_probe_regs.v" in fl
    assert "eth_ss_perf_probe_harness.sv" in fl


def _full_ic_top():
    return _top_with(_full_ic())


# --------------------------------------------------------------------------
# inject_perf_probes (model mutation for system wiring) — M2b
# --------------------------------------------------------------------------
def _module_with_probes():
    ic = _full_ic()
    m = Module(name="ethernet_ss_ahb")
    m.interconnects.append(ic)
    return m, ic


def test_inject_noop_without_probes():
    ic = _ic(initiators=[InterconnectInitiator(name="cpu_0")],
             targets=[InterconnectTarget(name="dmem_0")])
    m = Module(name="ethernet_ss_ahb")
    m.interconnects.append(ic)
    assert inject_perf_probes(m) == 0
    assert m.instances == []
    assert [t.name for t in ic.targets] == ["dmem_0"]


def test_inject_adds_target_and_harness_instance():
    m, ic = _module_with_probes()
    assert inject_perf_probes(m) == 1

    # read-out target appended + granted to every initiator
    tgt = {t.name: t for t in ic.targets}
    assert "eth_ss_perf_probe" in tgt
    ro = tgt["eth_ss_perf_probe"]
    assert ro.base == 0x58000000 and ro.instance == "u_eth_ss_perf_probe_0"
    for init in ic.initiators:
        assert "eth_ss_perf_probe" in [t.name for t in init.targets]

    # harness instance added
    inst = {i.instance_name: i for i in m.instances}
    assert "u_eth_ss_perf_probe_0" in inst
    h = inst["u_eth_ss_perf_probe_0"]
    assert h.module_name == "eth_ss_perf_probe_harness" and h.is_rtl_module
    # MUST have a stub resolved_module (mirrors SoCBuilder._resolve_instances):
    # the subsystem internal-wire scan skips resolved_module is None, which
    # would leave the i_<readout>_* AHB nets 1-bit (read-out reads 0 in HW).
    assert h.resolved_module is not None
    assert any(i.name == "ahb_slave" and i.type == "ahb"
               for i in h.resolved_module.interfaces)
    conns = {c.port: c.conn for c in h.connections}
    # AHB slave wired to the new matrix target
    assert conns["ahb_slave"] == "eth_ss_ahb_interconnect.eth_ss_perf_probe"
    # tap connections use i_-prefixed system nets; initiator hsel tied 1'b1
    assert conns["m0_hsel"] == "1'b1"                 # cpu_0 (initiator)
    assert conns["m0_htrans"] == "i_cpu_0_htrans"
    assert conns["m0_hready"] == "i_cpu_0_hready"
    assert conns["m2_hsel"] == "i_ethmac_0_hsel"      # ethmac_0 (target)
    assert conns["m2_hready"] == "i_ethmac_0_hreadymux"


def test_inject_idempotent():
    m, ic = _module_with_probes()
    inject_perf_probes(m)
    n_targets, n_insts = len(ic.targets), len(m.instances)
    inject_perf_probes(m)  # second pass must not double-add
    assert len(ic.targets) == n_targets and len(m.instances) == n_insts


def test_harness_tap_port_count_matches_probes(tmp_path):
    be = SoCPerfProbeBackend(_full_ic_top())
    be.generate_all(tmp_path)
    sv = (tmp_path / "rtl" / "eth_ss_ahb_interconnect"
          / "eth_ss_perf_probe_harness.sv").read_text()
    # one tap group (m<i>_hsel) per probed port = 3
    assert sv.count("_hsel,") + sv.count("_hsel)") >= 3
    for i in range(3):
        assert f"m{i}_htrans" in sv
    assert "m3_htrans" not in sv  # only 3 probes
