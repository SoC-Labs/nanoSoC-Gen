# Copyright 2026, SoC Labs (www.soclabs.org)
"""Golden + structural tests: the PARENT qualifies HTRANS by HSEL on every
link from its bus matrix into a child subsystem's passthrough initiator.

A bus-matrix target port legally shows HTRANS=NONSEQ with HSEL=0 for a cycle
when its last master moves on to another slave. A leaf slave ignores that
cycle because it reads HSEL. A child subsystem's passthrough initiator feeds
the child's own matrix, which ties HSEL high, so an ungated link lets the
child execute a transfer the parent never selected (on the ethernet chiplet's
ETH_SS link: a DMA-250 write re-executed with zero data through the child's
system window, and the DMA's next access lost).

036d381 gated the target-boundary form (cpu_ss: the child has an hsel input)
inside the child. The initiator-boundary form (eth_ss_0: the child has NO hsel
input) can only be gated in the parent. Fixture ``child_link_soc`` drives both
forms of ``tiny_cpu_ss`` (sub_s: target boundary, ext_m: initiator boundary)
from the top matrix, next to a leaf SRAM.
"""

import pytest

from soc_model.backends.subsystem import SoCSubsystemBackend
from soc_model.backends.toplevel import SoCTopLevelBackend

from tests.conftest import SNAPSHOTS
from tests.helpers import build_module_from_yaml, assert_matches_snapshot

pytestmark = pytest.mark.golden

FIXTURE = "child_link_soc"


def _render(fixtures_dir, tmp_path):
    top = build_module_from_yaml(fixtures_dir, f"{FIXTURE}.yaml")
    out = SoCTopLevelBackend(top).generate(tmp_path / "rtl", tmp_path / "flist")
    SoCSubsystemBackend.generate_all(top, tmp_path)
    return top, out.read_text(), (tmp_path / "rtl" / "tiny_cpu_ss.sv").read_text()


def _conn(sv, port):
    """The signal bound to `.port` in the rendered SV (first binding)."""
    for ln in sv.splitlines():
        s = ln.strip()
        if s.startswith(f".{port} ") or s.startswith(f".{port}("):
            return s.split("(", 1)[1].rsplit(")", 1)[0].strip()
    raise AssertionError(f".{port} is not bound in the render")


# ---------------------------------------------------------------------------
# CL-01 — golden byte-match of the parent render (timestamps stripped)
# ---------------------------------------------------------------------------
def test_child_link_soc_golden(fixtures_dir, tmp_path, request):
    top, sv, _ = _render(fixtures_dir, tmp_path)
    assert f"module {top.name} #(" in sv and "endmodule" in sv
    assert_matches_snapshot(sv, SNAPSHOTS / FIXTURE / f"{top.name}.sv", request)


# ---------------------------------------------------------------------------
# CL-02 — initiator-boundary form (the ETH_SS / eth_ss_0 shape). Fails on
# nanosoc_gen main 9d6506a: ext_m_htrans was bound to the raw matrix output
# and cpu_ss_0_ext_hsel was read by nothing.
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_initiator_boundary_link_htrans_gated_by_parent(fixtures_dir, tmp_path):
    _, sv, child = _render(fixtures_dir, tmp_path)
    # The child has no hsel input on this boundary, so the parent must gate.
    assert "ext_m_hsel" not in child
    assert _conn(sv, "ext_m_htrans") == "u_cpu_ss_0_ext_m_htrans"
    assert ("assign u_cpu_ss_0_ext_m_htrans = "
            "cpu_ss_0_ext_hsel ? cpu_ss_0_ext_htrans : 2'b00;") in sv
    assert "wire [1:0]                u_cpu_ss_0_ext_m_htrans;" in sv
    # The matrix-driven hsel is READ outside the matrix (not only declared
    # and bound to u_interconnect).
    reads = [ln for ln in sv.splitlines()
             if "cpu_ss_0_ext_hsel" in ln and ln.lstrip().startswith("assign")]
    assert reads, "cpu_ss_0_ext_hsel has no reader outside the matrix"
    # Only HTRANS is qualified; HREADY/HMASTLOCK and the address phase are not.
    assert _conn(sv, "ext_m_hready") == "cpu_ss_0_ext_hreadyout"
    assert _conn(sv, "ext_m_hmastlock") == "cpu_ss_0_ext_hmastlock"
    assert _conn(sv, "ext_m_haddr") == "cpu_ss_0_ext_haddr"


# ---------------------------------------------------------------------------
# CL-03 — target-boundary form (cpu_ss): gated in the parent AND, by 036d381,
# in the child. The double gate is idempotent.
# ---------------------------------------------------------------------------
def test_target_boundary_link_gated_in_parent_and_child(fixtures_dir, tmp_path):
    _, sv, child = _render(fixtures_dir, tmp_path)
    assert _conn(sv, "sub_s_htrans") == "u_cpu_ss_0_sub_s_htrans"
    assert ("assign u_cpu_ss_0_sub_s_htrans = "
            "cpu_ss_0_slave_hsel ? cpu_ss_0_slave_htrans : 2'b00;") in sv
    # hsel still reaches the child, which keeps its own gate (036d381).
    assert _conn(sv, "sub_s_hsel") == "cpu_ss_0_slave_hsel"
    assert "assign sub_s_htrans_gated = sub_s_hsel ? sub_s_htrans : 2'b00;" in child


# ---------------------------------------------------------------------------
# CL-04 — leaf targets are untouched: a leaf reads its own HSEL.
# ---------------------------------------------------------------------------
def test_leaf_target_link_not_gated(fixtures_dir, tmp_path):
    _, sv, _ = _render(fixtures_dir, tmp_path)
    assert _conn(sv, "HTRANS") == "sram_0_htrans"
    assert _conn(sv, "HSEL") == "sram_0_hsel"
    assert "u_sram_0_ahb_slave_htrans" not in sv


# ---------------------------------------------------------------------------
# CL-05 — the link map: exactly the two child links, nothing else.
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_child_link_htrans_gates_map(fixtures_dir):
    top = build_module_from_yaml(fixtures_dir, f"{FIXTURE}.yaml")
    gates = SoCTopLevelBackend(top)._child_link_htrans_gates()
    assert [(g["inst"], g["port"], g["tgt"], g["wire"], g["value"]) for g in gates] == [
        ("u_cpu_ss_0", "sub_s", "cpu_ss_0_slave", "u_cpu_ss_0_sub_s_htrans",
         "cpu_ss_0_slave_hsel ? cpu_ss_0_slave_htrans : 2'b00"),
        ("u_cpu_ss_0", "ext_m", "cpu_ss_0_ext", "u_cpu_ss_0_ext_m_htrans",
         "cpu_ss_0_ext_hsel ? cpu_ss_0_ext_htrans : 2'b00"),
    ]


# ---------------------------------------------------------------------------
# CL-06 — a render with no child links is byte-identical to before: the new
# template section is emitted only when there is a link to gate.
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_no_child_links_no_section(fixtures_dir, tmp_path):
    top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
    sv = SoCTopLevelBackend(top).generate(tmp_path / "rtl", tmp_path / "flist").read_text()
    assert "Child Subsystem Links" not in sv


# ---------------------------------------------------------------------------
# CL-07 — the same gate when the parent is itself a subsystem (its matrix
# target nets carry the i_ prefix).
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_nested_parent_subsystem_gates_with_i_prefix(fixtures_dir, tmp_path):
    top = build_module_from_yaml(fixtures_dir, f"{FIXTURE}.yaml")
    sv = SoCSubsystemBackend(top).generate(tmp_path / "rtl", tmp_path / "flist").read_text()
    assert ("assign u_cpu_ss_0_ext_m_htrans = "
            "i_cpu_ss_0_ext_hsel ? i_cpu_ss_0_ext_htrans : 2'b00;") in sv
    assert "wire [1:0]                u_cpu_ss_0_ext_m_htrans;" in sv
    assert _conn(sv, "ext_m_htrans") == "u_cpu_ss_0_ext_m_htrans"
