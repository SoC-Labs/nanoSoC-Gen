# Copyright 2026, SoC Labs (www.soclabs.org)
"""Golden + structural tests for the subsystem SV backend.

Covers ``soc_model.backends.subsystem.SoCSubsystemBackend`` — the
``SoCTopLevelBackend`` subclass that renders structural SV for *nested*
``gen:True`` subsystem modules. None of the toplevel fixtures contain a nested
``gen:True`` structural subsystem, so before this file the entire module was
uncovered (the ``generate_all`` ``_walk`` recursion found nothing to emit).

The driver here is ``nested_subsystem_soc.yaml``: its top instantiates
``tiny_cpu_ss`` (a ``gen:True`` subsystem that owns its OWN ``gen:True``
interconnect + an internal SRAM region). ``SoCSubsystemBackend.generate_all``
walks into it and renders ``tiny_cpu_ss.sv``.

``tiny_cpu_ss`` deliberately exercises BOTH passthrough branches:
  * a passthrough TARGET ('system') routed to a module-level AHB master port
    ``sub_m`` — the ``_is_passthrough_target`` / target-window path of
    ``_build_interconnect`` and the HTRANS-gate / hreadyout tie-off,
  * a passthrough INITIATOR ('sub_s') whose endpoint iface declares
    ``EXCLUDE: [hready]`` — the ``_init_passthrough_maps`` /
    ``_is_passthrough_initiator`` lookup, the slave/target-boundary branch of
    ``_expand_ahb_port`` (hsel in, hreadyout out, NO bare hready), and the
    target-boundary (``_hreadyout``) branch of ``_build_interconnect``.

Each test asserts real structure; the golden byte-match is backed by explicit
structural assertions so a broken/empty render fails loudly.
"""

import pytest

from soc_model.model import Module
from soc_model.backends.subsystem import SoCSubsystemBackend

from tests.conftest import SNAPSHOTS
from tests.helpers import build_module_from_yaml, assert_matches_snapshot

pytestmark = pytest.mark.golden

# The fixture whose top instantiates the nested gen:True subsystem, and the
# name of the subsystem module the backend actually renders.
TOP_FIXTURE = "nested_subsystem_soc"
SUBSYS_NAME = "tiny_cpu_ss"


def _generate_subsystems(fixtures_dir, tmp_path):
    """Build the nested fixture and run the subsystem backend's classmethod
    entry. Returns (results, build_dir)."""
    top = build_module_from_yaml(fixtures_dir, f"{TOP_FIXTURE}.yaml")
    build_dir = tmp_path / "build"
    results = SoCSubsystemBackend.generate_all(top, build_dir)
    return top, results, build_dir


def _subsystem_sv(fixtures_dir, tmp_path):
    """Generate + return the rendered subsystem SV text."""
    _, results, build_dir = _generate_subsystems(fixtures_dir, tmp_path)
    # generate_all returns [(module_name, output_path)]
    by_name = dict(results)
    assert SUBSYS_NAME in by_name, (
        f"generate_all did not emit {SUBSYS_NAME}; got {results}")
    sv_path = build_dir / "rtl" / f"{SUBSYS_NAME}.sv"
    assert sv_path.is_file(), f"missing rendered SV at {sv_path}"
    return sv_path.read_text()


# ---------------------------------------------------------------------------
# SS-01 — generate_all _walk finds the nested gen:True subsystem and emits it.
# ---------------------------------------------------------------------------
def test_generate_all_emits_nested_subsystem(fixtures_dir, tmp_path):
    top, results, build_dir = _generate_subsystems(fixtures_dir, tmp_path)
    # Exactly one structural subsystem (tiny_cpu_ss) should be emitted — the
    # gen:False top SRAM region is NOT a subsystem and must not appear.
    names = [n for n, _ in results]
    assert names == [SUBSYS_NAME], (
        f"expected only {SUBSYS_NAME!r} emitted, got {names}")
    # The returned path is a real file under build_dir/rtl.
    out_path = dict(results)[SUBSYS_NAME]
    assert str(build_dir / "rtl" / f"{SUBSYS_NAME}.sv") == out_path
    assert (build_dir / "rtl" / f"{SUBSYS_NAME}.sv").is_file()
    # The flist is emitted alongside (generate() writes both).
    assert (build_dir / "flist" / f"{SUBSYS_NAME}_toplevel.flist").is_file()


# ---------------------------------------------------------------------------
# SS-02 — gen:False top regions are skipped; non-structural gen:True is skipped.
# A subsystem is only emitted if resolved_module.gen AND (interconnects|instances).
# ---------------------------------------------------------------------------
def test_only_structural_gen_true_emitted(fixtures_dir, tmp_path):
    top, results, _ = _generate_subsystems(fixtures_dir, tmp_path)
    emitted = {n for n, _ in results}
    # tiny_region is gen:False -> not a subsystem.
    assert "tiny_region" not in emitted
    # Confirm the top really does carry a gen:False region + a gen:True subsys,
    # so the skip is meaningful (not a vacuous pass).
    gens = {i.module_name: i.resolved_module.gen for i in top.instances}
    assert gens["tiny_region"] is False
    assert gens["tiny_cpu_ss"] is True


# ---------------------------------------------------------------------------
# SS-03 — golden byte-match of the rendered subsystem .sv (timestamps stripped).
# ---------------------------------------------------------------------------
def test_subsystem_sv_golden(fixtures_dir, tmp_path, request):
    sv = _subsystem_sv(fixtures_dir, tmp_path)
    # Structural sanity FIRST so a worthless golden never gets written on
    # --snapshot-update.
    assert sv.strip(), "subsystem SV is empty"
    assert f"module {SUBSYS_NAME} #(" in sv
    assert "endmodule" in sv
    assert_matches_snapshot(
        sv, SNAPSHOTS / TOP_FIXTURE / f"{SUBSYS_NAME}.sv", request)


# ---------------------------------------------------------------------------
# SS-04 — golden byte-match of the rendered subsystem flist.
# ---------------------------------------------------------------------------
def test_subsystem_flist_golden(fixtures_dir, tmp_path, request):
    _, _, build_dir = _generate_subsystems(fixtures_dir, tmp_path)
    flist = (build_dir / "flist" / f"{SUBSYS_NAME}_toplevel.flist").read_text()
    assert flist.strip(), "subsystem flist is empty"
    # The generated subsystem .sv is referenced in the flist.
    assert f"{SUBSYS_NAME}.sv" in flist
    assert_matches_snapshot(
        flist, SNAPSHOTS / TOP_FIXTURE / f"{SUBSYS_NAME}_toplevel.flist",
        request)


# ---------------------------------------------------------------------------
# SS-05 — module boundary: passthrough-initiator (EXCLUDE:[hready]) takes the
# slave/target boundary form (hsel IN, hreadyout OUT, NO bare hready).
# Exercises _expand_ahb_port case 1->fallthrough-to-case-2 (is_target branch).
# ---------------------------------------------------------------------------
def test_passthrough_initiator_slave_boundary(fixtures_dir, tmp_path):
    sv = _subsystem_sv(fixtures_dir, tmp_path)
    # Slave-boundary: HSEL is a module INPUT (target receives address phase).
    assert "input   wire                      sub_s_hsel," in sv
    # hreadyout is a module OUTPUT (slave drives ready out).
    assert "output  wire                      sub_s_hreadyout" in sv
    # The bare `sub_s_hready` is NOT a module PORT (EXCLUDE:[hready]). Scope the
    # check to the port-declaration block (between `module ... #(` and the
    # first `);` that closes the port list) so the legitimate interconnect-
    # instance tie `.sub_s_hready (sub_s_hreadyout)` below is not a false hit.
    port_block = sv.split("module " + SUBSYS_NAME + " #(", 1)[1].split(");", 1)[0]
    # `sub_s_hready` is a substring of `sub_s_hreadyout`, so match the exact
    # port token: strip the inline `// comment`, then take the declaration's
    # last token (the port name) before its trailing comma — never the prefix.
    port_tokens = set()
    for ln in port_block.splitlines():
        code = ln.split("//", 1)[0].strip().rstrip(",").strip()
        if code:
            port_tokens.add(code.split()[-1])
    assert "sub_s_hready" not in port_tokens, (
        "bare sub_s_hready must not appear as a module port (EXCLUDE:[hready])")
    assert "sub_s_hsel" in port_tokens       # but hsel/hreadyout DO (slave boundary)
    assert "sub_s_hreadyout" in port_tokens
    # Address-phase signals are inputs (target receives), responses outputs.
    assert "input   wire [SYS_ADDR_W-1:0]     sub_s_haddr," in sv
    assert "output  wire [SYS_DATA_W-1:0]     sub_s_hrdata," in sv


# ---------------------------------------------------------------------------
# SS-06 — module boundary: passthrough-TARGET endpoint (sub_m) takes the
# INITIATOR boundary form (haddr OUT, hrdata IN, NO hsel / NO hreadyout port).
# Exercises _expand_ahb_port case 3 (initiator) for the master-out port.
# ---------------------------------------------------------------------------
def test_passthrough_target_master_boundary(fixtures_dir, tmp_path):
    sv = _subsystem_sv(fixtures_dir, tmp_path)
    # Master boundary: HADDR is a module OUTPUT (subsystem drives the master).
    assert "output  wire [SYS_ADDR_W-1:0]     sub_m_haddr," in sv
    # hrdata / hready / hresp are inputs (initiator receives the response).
    assert "input   wire [SYS_DATA_W-1:0]     sub_m_hrdata," in sv
    assert "input   wire                      sub_m_hready," in sv
    # No slave-boundary ports on a master endpoint.
    assert "sub_m_hsel" not in sv
    assert "sub_m_hreadyout" not in sv


# ---------------------------------------------------------------------------
# SS-06b — module boundary: passthrough-INITIATOR with NO hready EXCLUDE
# (ext_m) takes the cmsdk INITIATOR boundary form: address-phase signals are
# INPUTS (external master drives in), hrdata/hready/hresp are OUTPUTS, and
# there is NO hsel / NO hreadyout port. Exercises _expand_ahb_port case 1
# (is_passthrough_initiator True, lines 112-122).
# ---------------------------------------------------------------------------
def test_passthrough_initiator_master_boundary(fixtures_dir, tmp_path):
    sv = _subsystem_sv(fixtures_dir, tmp_path)
    port_block = sv.split("module " + SUBSYS_NAME + " #(", 1)[1].split(");", 1)[0]
    port_tokens = set()
    for ln in port_block.splitlines():
        code = ln.split("//", 1)[0].strip().rstrip(",").strip()
        if code:
            port_tokens.add(code.split()[-1])
    # Initiator-boundary form: haddr is an INPUT (external master drives in).
    assert "input   wire [SYS_ADDR_W-1:0]     ext_m_haddr," in sv
    # hready is a subsystem OUTPUT (the receiving slave end drives ready back);
    # hrdata/hresp are outputs too.
    assert "output  wire                      ext_m_hready" in sv
    assert "output  wire [SYS_DATA_W-1:0]     ext_m_hrdata," in sv
    # NO slave-boundary spurious ports on the initiator boundary.
    assert "ext_m_hsel" not in port_tokens
    assert "ext_m_hreadyout" not in port_tokens
    # The bare ext_m_hready IS present (unlike the EXCLUDE:[hready] sub_s).
    assert "ext_m_hready" in port_tokens


# ---------------------------------------------------------------------------
# SS-06c — interconnect side of the initiator-boundary passthrough (ext_m):
# the cmsdk initiator's `hready` input ties to the bare module `ext_m_hready`
# (NOT _hreadyout — that is the EXCLUDE/cpu_ss form). Exercises the
# uses_init_boundary=True branch of _build_interconnect (lines 371-375).
# ---------------------------------------------------------------------------
def test_interconnect_initiator_boundary_passthrough(fixtures_dir, tmp_path):
    sv = _subsystem_sv(fixtures_dir, tmp_path)
    # Initiator-boundary: hready ties to the BARE module port (no _hreadyout).
    assert ".ext_m_hready                    (ext_m_hready)," in sv
    # Address-phase and response ports route straight to the module ports.
    assert ".ext_m_haddr                     (ext_m_haddr)," in sv
    assert ".ext_m_hrdata                    (ext_m_hrdata)," in sv
    # Contrast: the EXCLUDE:[hready] sub_s ties hready to _hreadyout.
    assert ".sub_s_hready                    (sub_s_hreadyout)," in sv


# ---------------------------------------------------------------------------
# SS-06d — internal master instance (u_cpu_0) drives the cpu_0 interconnect
# initiator via i_-prefixed wires. Exercises the is_init branch of
# _expand_bus_connection (lines 237-247): bare uppercase ahb_master port names.
# ---------------------------------------------------------------------------
def test_internal_master_bus_connection(fixtures_dir, tmp_path):
    sv = _subsystem_sv(fixtures_dir, tmp_path)
    # The CPU core instance is rendered.
    assert "tiny_cpu_core u_cpu_0 (" in sv
    # ahb_master -> bare uppercase port names mapped to i_cpu_0_* wires.
    assert ".HADDR                   (i_cpu_0_haddr)," in sv
    assert ".HRDATA                  (i_cpu_0_hrdata)," in sv
    assert ".HREADY                  (i_cpu_0_hready)," in sv
    # And those i_cpu_0_* wires drive the interconnect cpu_0 initiator ports.
    assert ".cpu_0_haddr                     (i_cpu_0_haddr)," in sv


# ---------------------------------------------------------------------------
# SS-07 — passthrough TARGET routing: 'system' window routes straight to the
# module master port sub_m, with the HTRANS gate + hreadyout tie-off.
# Exercises _build_interconnect target-passthrough path + tie-offs.
# ---------------------------------------------------------------------------
def test_passthrough_target_routing_and_htrans_gate(fixtures_dir, tmp_path):
    sv = _subsystem_sv(fixtures_dir, tmp_path)
    # HSEL of the SYSTEM target is connected so it can gate the outward HTRANS.
    assert ".system_hsel                     (system_hsel)," in sv
    # Passthrough drives explicit intermediate system_* wires (so the gate is
    # applied at source), then assigns them to the module master port sub_m_*
    # (parallel fix 8454bb7: "drive system_* wires at source").
    assert ".system_haddr                    (system_haddr)," in sv
    assert "assign sub_m_haddr = system_haddr;" in sv
    # The HTRANS gate: a deselected passthrough target presents IDLE.
    assert "assign sub_m_htrans = system_hsel ? system_htrans : 2'b00;" in sv
    # Response signals routed back from the module master port.
    assert "assign system_hrdata = sub_m_hrdata;" in sv
    assert "assign system_hresp = sub_m_hresp;" in sv
    # The system passthrough target's hreadyout mirrors the module master
    # port's `hready` INPUT (initiator-boundary endpoint). This used to assert
    # `= 1'b1;` — see the TL-66 note in tests/golden/test_toplevel_sv.py for
    # why that was a bug.
    #
    # In the SUBSYSTEM case the `assign` drives a wire the interconnect
    # instantiation never reads (it takes `sub_m_hready` directly — asserted
    # below), so the old 1'b1 was harmless here. The same tie was load-bearing,
    # and wrong, at TOP level, where the interconnect is fed from this wire.
    assert "assign system_hreadyout = sub_m_hready;" in sv
    assert "assign system_hreadyout = 1'b1;" not in sv
    # The interconnect takes the module port's hready straight through, so an
    # external slave really can wait-state this passthrough target.
    assert ".system_hreadyout                (sub_m_hready)" in sv
    # Its hreadymux input is left unconnected (system interconnect drives HREADY).
    assert ".system_hreadymux                ( )," in sv


# ---------------------------------------------------------------------------
# SS-08 — interconnect instantiation: passthrough INITIATOR (sub_s) ties its
# cmsdk initiator hready to the module SLAVE hreadyout (EXCLUDE-hready form),
# while internal initiator/target ports use i_-prefixed wires.
# Exercises _build_interconnect initiator-passthrough hreadyout branch +
# _build_bus_wires i_-prefix path.
# ---------------------------------------------------------------------------
def test_interconnect_passthrough_initiator_and_internal_wires(fixtures_dir, tmp_path):
    sv = _subsystem_sv(fixtures_dir, tmp_path)
    # The renamed interconnect module: strip _ahb_interconnect, append _interconnect.
    assert "tiny_cpu_ss_interconnect #(" in sv
    assert "u_interconnect (" in sv
    # Passthrough initiator: the interconnect's full cmsdk initiator port set is
    # exposed; its `hready` input is tied to the module slave `sub_s_hreadyout`
    # (target-boundary form, NOT a bare sub_s_hready).
    assert ".sub_s_hready                    (sub_s_hreadyout)," in sv
    # Its address-phase ports connect straight to the module slave ports.
    assert ".sub_s_haddr                     (sub_s_haddr)," in sv
    # Internal initiator (cpu_0) uses i_-prefixed wires (NOT a module port).
    assert ".cpu_0_haddr                     (i_cpu_0_haddr)," in sv
    # Internal target (imem_0) uses i_-prefixed wires incl. hready->hreadymux.
    assert ".imem_0_hsel                     (i_imem_0_hsel)," in sv
    assert ".imem_0_hreadymux                (i_imem_0_hreadymux)," in sv
    # The internal target bus-wire bundle was declared with i_ prefix.
    assert "wire                      i_imem_0_hsel;" in sv
    assert "wire                      i_imem_0_hreadymux;" in sv


# ---------------------------------------------------------------------------
# SS-09 — bus wires: passthrough target/initiator get NO internal i_ wires
# (they connect directly to module ports). Exercises the early-`continue`
# branches in _build_bus_wires (is_passthrough_initiator / _target).
# ---------------------------------------------------------------------------
def test_passthrough_ports_have_no_internal_wires(fixtures_dir, tmp_path):
    sv = _subsystem_sv(fixtures_dir, tmp_path)
    # No i_system_* / i_sub_s_* / i_ext_m_ internal bus-wire bundles — the
    # passthrough target + both passthrough initiators connect directly.
    assert "i_system_" not in sv
    assert "i_sub_s_" not in sv
    assert "i_ext_m_" not in sv
    # But the INTERNAL cpu_0 initiator + imem_0 target DO get i_ wires.
    assert "i_cpu_0_haddr" in sv
    assert "i_imem_0_hsel" in sv


# ---------------------------------------------------------------------------
# SS-10 — internal SRAM region instance is rendered structurally inside the
# subsystem (proves the subsystem body, not just its ports, is emitted).
# ---------------------------------------------------------------------------
def test_internal_region_instance_rendered(fixtures_dir, tmp_path):
    sv = _subsystem_sv(fixtures_dir, tmp_path)
    assert "tiny_region #(" in sv
    assert "u_imem_0 (" in sv
    # The region's AHB slave is wired to the i_imem_0_* internal bundle.
    assert ".HSEL                    (i_imem_0_hsel)," in sv
    # Clock/reset reach the region from the subsystem ports.
    assert ".HCLK                    (sys_hclk)," in sv


# ---------------------------------------------------------------------------
# SS-11 — direct unit checks of the passthrough-map helpers + __init__, built
# straight from the resolved subsystem Module (no file I/O).
# Exercises __init__ / _init_passthrough_maps / _is_passthrough_* directly.
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_passthrough_maps_built_from_metadata(fixtures_dir):
    top = build_module_from_yaml(fixtures_dir, f"{TOP_FIXTURE}.yaml")
    subsys = next(i.resolved_module for i in top.instances
                  if i.module_name == SUBSYS_NAME)
    backend = SoCSubsystemBackend(subsys)
    # Target passthrough: 'system' window -> module master iface 'sub_m'.
    assert backend._passthrough_targets == {"system": "sub_m"}
    # Initiator passthroughs: 'sub_s' (slave-boundary) + 'ext_m'
    # (initiator-boundary), each -> its like-named module iface.
    assert backend._passthrough_initiators == {"sub_s": "sub_s", "ext_m": "ext_m"}
    assert backend._is_passthrough_target("system") is True
    assert backend._is_passthrough_target("imem_0") is False
    assert backend._is_passthrough_initiator("sub_s") is True
    assert backend._is_passthrough_initiator("ext_m") is True
    # cpu_0 is an INTERNAL initiator (driven by u_cpu_0), not a passthrough.
    assert backend._is_passthrough_initiator("cpu_0") is False


# ---------------------------------------------------------------------------
# SS-12 — degenerate subsystem with NO interconnect: _init_passthrough_maps
# yields empty maps, _build_interconnect returns None (the line-284 guard),
# and _build_bus_wires returns [] (no instances). Pure in-memory Module.
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_empty_subsystem_no_interconnect():
    backend = SoCSubsystemBackend(Module(name="empty_sub", gen=True))
    assert backend._passthrough_targets == {}
    assert backend._passthrough_initiators == {}
    # No interconnects -> _build_interconnect short-circuits to None.
    assert backend._build_interconnect() is None
    # No instances -> no internal bus wires.
    assert backend._build_bus_wires() == []


# ---------------------------------------------------------------------------
# SS-13 — the target-boundary passthrough INITIATOR (sub_s, EXCLUDE:[hready];
# the cpu_ss form) must qualify HTRANS by its hsel input before it reaches the
# subsystem interconnect's initiator port.
#
# The port is driven by a PARENT bus-matrix target port. An Arm bus-matrix
# output stage muxes HTRANS from the master its registered arbiter still points
# at, and HSEL from that master's decode, so it shows HTRANS=NONSEQ with HSEL=0
# for a cycle whenever that master moves to another slave. The subsystem
# matrix's initiator port ties HSEL high, so an ungated link takes that cycle
# as a real transfer (to its default slave); the ERROR wait state it inserts is
# invisible to the parent, and the next genuine transfer into the subsystem is
# dropped (lost DMA/debug write into IMEM, truncated ERROR). Before this test,
# the declared `sub_s_hsel` / `cpu_ss_hsel` input was never read.
# ---------------------------------------------------------------------------
def test_passthrough_initiator_htrans_gated_by_hsel(fixtures_dir, tmp_path):
    sv = _subsystem_sv(fixtures_dir, tmp_path)
    # The gate: IDLE unless the parent matrix selected this subsystem.
    assert "assign sub_s_htrans_gated = sub_s_hsel ? sub_s_htrans : 2'b00;" in sv
    assert "wire [1:0]                sub_s_htrans_gated;" in sv
    # The interconnect's initiator htrans takes the gated copy, never the raw port.
    assert ".sub_s_htrans                    (sub_s_htrans_gated)," in sv
    assert ".sub_s_htrans                    (sub_s_htrans)," not in sv
    # hsel is actually READ (not just declared): every occurrence other than the
    # port declaration is a use.
    uses = [ln for ln in sv.splitlines()
            if "sub_s_hsel" in ln and "input " not in ln]
    assert uses, "sub_s_hsel is declared but never used"
    # The initiator-boundary form (ext_m: no hsel port) is left as it was.
    assert "ext_m_htrans_gated" not in sv
    assert ".ext_m_htrans                    (ext_m_htrans)," in sv


@pytest.mark.unit
def test_hsel_gated_passthrough_initiators_map(fixtures_dir):
    top = build_module_from_yaml(fixtures_dir, f"{TOP_FIXTURE}.yaml")
    subsys = next(i.resolved_module for i in top.instances
                  if i.module_name == SUBSYS_NAME)
    backend = SoCSubsystemBackend(subsys)
    # Only the slave/target-boundary passthrough initiator is gated.
    assert backend._hsel_gated_passthrough_initiators() == {"sub_s": "sub_s"}
    gates = [t for t in backend._build_passthrough_tieoffs()
             if t["wire"] == "sub_s_htrans_gated"]
    assert gates == [{
        "wire": "sub_s_htrans_gated",
        "value": "sub_s_hsel ? sub_s_htrans : 2'b00",
        "desc": "sub_s passthrough initiator: htrans qualified by sub_s_hsel",
    }]
