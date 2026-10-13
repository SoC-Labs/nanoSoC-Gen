# Copyright 2026, SoC Labs (www.soclabs.org)
"""Golden + structural tests for the top-level structural SV backend.

Covers ``soc_model.backends.toplevel.SoCTopLevelBackend.generate`` across the
hermetic fixtures that build cleanly. Each fixture's rendered ``<top>.sv`` is
byte-compared (timestamp/copyright stripped) against a committed golden, and
backed by explicit STRUCTURAL assertions so the test has semantic teeth beyond
the byte-match: module declaration, expected instances/interconnect, port
expansion and — for the passthrough fixture — the HTRANS-gate tie-off.

Maps to TEST_PLAN rows TL-01, TL-02, TL-03, TL-04 and TL-68 (golden tier) plus
the structural-instance / port-expansion checks.
"""

import pytest

from soc_model.backends.toplevel import SoCTopLevelBackend

from tests.conftest import SNAPSHOTS
from tests.helpers import build_module_from_yaml, assert_matches_snapshot

pytestmark = pytest.mark.golden


# Fixtures whose toplevel .sv renders cleanly (per the scaffold backend-smoke
# manifest: minimal/two_region/apb_bridge/passthrough/firmware all run clean).
TOPLEVEL_FIXTURES = [
    "minimal_soc",
    "two_region_soc",
    "apb_bridge_soc",
    "passthrough_soc",
    "firmware_soc",
]


def _generate_sv(fixture, fixtures_dir, tmp_path):
    """Build the fixture and run the toplevel backend; return (top, sv_text)."""
    top = build_module_from_yaml(fixtures_dir, f"{fixture}.yaml")
    rtl_dir = tmp_path / "rtl"
    flist_dir = tmp_path / "flist"
    out = SoCTopLevelBackend(top).generate(rtl_dir, flist_dir)
    # generate() returns the Path to the written .sv
    assert out == rtl_dir / f"{top.name}.sv"
    assert out.exists()
    return top, out.read_text()


# ---------------------------------------------------------------------------
# TL-01 — golden byte-match of the full rendered .sv (timestamps stripped)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fixture", TOPLEVEL_FIXTURES)
def test_toplevel_sv_golden(fixture, fixtures_dir, tmp_path, request):
    top, sv = _generate_sv(fixture, fixtures_dir, tmp_path)
    # Structural sanity FIRST so a broken/empty render fails loudly even on
    # --snapshot-update (a worthless golden never gets written from this body).
    assert sv.strip(), f"{fixture}: generated SV is empty"
    assert f"module {top.name} #(" in sv
    assert "endmodule" in sv
    assert_matches_snapshot(
        sv, SNAPSHOTS / fixture / f"{top.name}.sv", request)


# ---------------------------------------------------------------------------
# TL-02 — return value / module declaration / endmodule
# ---------------------------------------------------------------------------
def test_returns_path_and_has_module_decl(fixtures_dir, tmp_path):
    top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
    rtl_dir = tmp_path / "rtl"
    out = SoCTopLevelBackend(top).generate(rtl_dir, tmp_path / "flist")
    assert out == rtl_dir / "minimal_soc.sv"
    assert out.is_file()
    sv = out.read_text()
    assert "module minimal_soc #(" in sv
    assert "endmodule" in sv


# ---------------------------------------------------------------------------
# TL-03 — flist defaults to rtl_dir.parent/'flist'/<name>_toplevel.flist
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_flist_default_dir_when_none(fixtures_dir, tmp_path):
    top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
    rtl_dir = tmp_path / "build" / "rtl"
    SoCTopLevelBackend(top).generate(rtl_dir)  # flist_dir omitted -> default
    default_flist = rtl_dir.parent / "flist" / "minimal_soc_toplevel.flist"
    assert default_flist.is_file()
    # And not somewhere else accidentally.
    assert (rtl_dir / "minimal_soc.sv").is_file()


# ---------------------------------------------------------------------------
# TL-04 — jinja2 absent => returns None, writes nothing
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_no_jinja2_returns_none(fixtures_dir, tmp_path, monkeypatch):
    import soc_model.backends.toplevel as tl
    monkeypatch.setattr(tl, "Environment", None)
    top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
    rtl_dir = tmp_path / "rtl"
    result = SoCTopLevelBackend(top).generate(rtl_dir, tmp_path / "flist")
    assert result is None
    assert not (rtl_dir / "minimal_soc.sv").exists()


# ---------------------------------------------------------------------------
# Structural: expected child instances + interconnect instance present
# ---------------------------------------------------------------------------
def test_minimal_instances_and_interconnect(fixtures_dir, tmp_path):
    _, sv = _generate_sv("minimal_soc", fixtures_dir, tmp_path)
    # The gen:False region instance is rendered with its module + instance name.
    assert "tiny_region #(" in sv
    assert "u_sram_0 (" in sv
    # The single gen:True interconnect renders as <prefix>_interconnect
    # ("minimal_ahb_interconnect" -> "minimal_interconnect"), instance
    # u_interconnect.
    assert "minimal_interconnect #(" in sv
    assert "u_interconnect (" in sv
    # The CPU initiator AHB iface expanded into per-signal ports.
    assert "cpu_m_haddr" in sv
    assert "cpu_m_hrdata" in sv
    # The SRAM target bus bundle wires exist.
    assert "sram_0_hsel" in sv
    # AHB-Lite target boundary: hreadymux fed by the target hready.
    assert ".sram_0_hreadymux                (sram_0_hready)" in sv


def test_two_region_has_both_targets(fixtures_dir, tmp_path):
    _, sv = _generate_sv("two_region_soc", fixtures_dir, tmp_path)
    assert "u_sram_0 (" in sv
    assert "u_sram_1 (" in sv
    assert "sram_0_hsel" in sv
    assert "sram_1_hsel" in sv
    assert "two_region_interconnect #(" in sv


def test_apb_bridge_has_both_targets(fixtures_dir, tmp_path):
    _, sv = _generate_sv("apb_bridge_soc", fixtures_dir, tmp_path)
    # SRAM region + APB-bridged peripheral region both instantiated.
    assert "tiny_region #(" in sv
    assert "tiny_apb_periph #(" in sv
    assert "u_sram_0 (" in sv
    assert "u_apb_periph_0 (" in sv
    assert "apb_bridge_interconnect #(" in sv


# ---------------------------------------------------------------------------
# TL-68 — passthrough fixture: HTRANS gate + passthrough boundary structure
# ---------------------------------------------------------------------------
def test_passthrough_htrans_gate_and_boundary(fixtures_dir, tmp_path):
    _, sv = _generate_sv("passthrough_soc", fixtures_dir, tmp_path)

    # TL-65 / TL-68: the system passthrough target's HTRANS is gated by its
    # own HSEL so a deselected target presents IDLE (2'b00).
    assert "hsel ?" in sv
    assert ": 2'b00" in sv
    assert "assign sys_m_htrans = system_hsel ? system_htrans : 2'b00;" in sv

    # TL-66: hreadyout for a passthrough target whose endpoint is an INITIATOR
    # boundary comes from that endpoint's bare `hready` INPUT.
    #
    # This assertion used to read `assign system_hreadyout = 1'b1;`. That
    # pinned a real bug: AHB_INITIATOR_SIGNALS has no `hreadyout`, so the
    # `{iface}_hreadyout in top_port_names` lookup could never hit and the tie
    # always fell through to always-ready. The matrix then believed an
    # off-module target was permanently ready — `sys_m_hready` was left a
    # completely dangling input port, so the target could never insert a wait
    # state and a multi-cycle slave (a die-to-die link, an off-chip bridge)
    # would have its read data sampled one cycle after address phase.
    #
    # Note the old golden was self-inconsistent: it routed hrdata and hresp
    # back from the endpoint port but not hready.
    assert "assign system_hreadyout = sys_m_hready;" in sv
    assert "assign system_hreadyout = 1'b1;" not in sv

    # Regression lock: `sys_m_hready` must be CONSUMED, not merely declared.
    # A count of 1 means it appears only in the port list — i.e. dangling.
    assert sv.count("sys_m_hready") >= 2, "sys_m_hready is dangling again"

    # Passthrough target response signals routed back from the module port.
    assert "assign system_hrdata = sys_m_hrdata;" in sv
    assert "assign system_hresp = sys_m_hresp;" in sv

    # TL-13: the EXCLUDE:[hready] passthrough-initiator (ext_s) takes the
    # slave/target boundary form: it HAS hsel (target boundary) but its
    # hready signal is dropped from the port list.
    assert "input   wire                      ext_s_hsel," in sv
    assert "ext_s_hready," not in sv

    # ... and the TOP module wires it as a target boundary, like the
    # subsystem backend wires cpu_ss (036d381). Before this, ext_s_hsel was
    # never read (ext_s_htrans went ungated into the matrix, which ties the
    # initiator port's HSEL high), ext_s_hreadyout was never driven, and the
    # matrix HREADY output drove `ext_s_hready`, a net declared nowhere.
    assert ("assign ext_s_htrans_gated = ext_s_hsel ? ext_s_htrans : 2'b00;"
            in sv)
    assert "wire [1:0]                ext_s_htrans_gated;" in sv
    assert ".ext_s_htrans                    (ext_s_htrans_gated)," in sv
    assert ".ext_s_hready                    (ext_s_hreadyout)," in sv
    assert "(ext_s_hready)" not in sv
    # hmastlock and the address phase are not qualified
    assert ".ext_s_hmastlock                 (ext_s_hmastlock)," in sv

    # The passthrough-target master endpoint (sys_m) is declared as a module
    # initiator boundary (haddr OUTPUT), with no spurious hsel.
    assert "output  wire [SYS_ADDR_W-1:0]     sys_m_haddr," in sv
    assert "sys_m_hsel" not in sv


# ---------------------------------------------------------------------------
# Cross-fixture: every rendered SV is non-trivial and well-formed.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fixture", TOPLEVEL_FIXTURES)
def test_sv_is_balanced(fixture, fixtures_dir, tmp_path):
    top, sv = _generate_sv(fixture, fixtures_dir, tmp_path)
    # Exactly one module/endmodule pair for the top.
    assert sv.count(f"module {top.name} #(") == 1
    assert sv.count("endmodule") == 1
    # A real SoC always has the AHB clock/reset top ports.
    assert "sys_hclk" in sv
    assert "sys_hresetn" in sv
