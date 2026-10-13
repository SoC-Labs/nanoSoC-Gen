# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for SoCFpgaWrapperBackend (doc 07 M4 — FPGA Vivado wrapper).

The FPGA wrapper is the synthesizable sibling of the chip-functional wrapper.
Both consume the SAME chip-boundary spec (lib/chip_boundary/<name>.yaml) for the
bonded / tied / open topology + oe polarity; the FPGA emitter additionally:

  * emits bidir/tristate pads as the Vivado IOBUF triple _i/_o/_t, where
    _t = ~oe (active_high) | oe (active_low)  (VERIFIED vs the hand reference),
  * instantiates the inner SoC top with NO pad cells,
  * applies the bespoke FPGA/board layer from an FpgaProfile
    (lib/fpga_profile/<name>.yaml): board pin renames, FPGA param overrides,
    debug taps, dropped pads, SYSRESETREQ per-core isolation, rtc_clk<-sys_fclk.

Two layers:
  * UNIT — hermetic fixtures (multicore_chip boundary + fpga_profile):
    tristate mapping, renames, excludes, debug taps, feature overrides,
    instance classification, render assertions, CLI no-op-when-flag-absent.
  * REAL — the bundled nanosoc_multicore profile reproduces the hand wrapper's
    32 ports (after renames/excludes), preserves every IP-XACT physical_name,
    and emits no pad cells (cross-checked vs the read-only hand reference V).
"""

import os
import re
from pathlib import Path

import pytest

from soc_model.model import ChipBoundary, ChipPad, FpgaProfile
from soc_model.parser import SoCParser
from soc_model.builder import SoCBuilder
from soc_model.backends.fpga_wrapper import SoCFpgaWrapperBackend

from tests.conftest import FIXTURES

pytestmark = pytest.mark.unit

_FIX = "multicore_chip"

# read-only hand references (REAL cross-check tier; skipped if absent)
# ($NANOSOC_REAL_ROOT = a nanosoc-multicore-system checkout; unset -> skipped)
_REAL_ROOT = Path(os.environ.get("NANOSOC_REAL_ROOT", "/nonexistent-NANOSOC_REAL_ROOT-unset"))
_HAND_V = _REAL_ROOT / "pynq/vivado_ip/nanosoc_multicore_vivado_wrapper.v"
_HAND_TCL = _REAL_ROOT / "pynq/vivado_ip/package_nanosoc_multicore_ip.tcl"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _fixture_backend():
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    builder = SoCBuilder(parser)
    boundary = builder._build_chip_boundary(parser.parse_chip_boundary(_FIX))
    raw = parser.parse_fpga_profile(_FIX)
    profile = builder._build_fpga_profile(raw, _FIX) if raw else None
    return SoCFpgaWrapperBackend(boundary, profile)


def _real_backend():
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    builder = SoCBuilder(parser)
    cb = parser.parse_chip_boundary("nanosoc_multicore")
    assert cb is not None, "bundled nanosoc_multicore boundary must resolve"
    boundary = builder._build_chip_boundary(cb)
    fp = parser.parse_fpga_profile("nanosoc_multicore")
    assert fp is not None, "bundled nanosoc_multicore fpga_profile must resolve"
    profile = builder._build_fpga_profile(fp, "nanosoc_multicore")
    return SoCFpgaWrapperBackend(boundary, profile)


def _parse_ports(v):
    """{name: (dir, width_str)} from a generated/hand wrapper port section."""
    m = re.search(r"\)\s*\(\n(.*?)\n\);", v, re.S)
    ports = {}
    for line in m.group(1).splitlines():
        line = line.split("//")[0].strip().rstrip(",")
        mm = re.match(r"(input|output)\s+wire\s*(\[[^\]]+\])?\s*(\w+)", line)
        if mm:
            ports[mm.group(3)] = (mm.group(1), mm.group(2) or "")
    return ports


# ---------------------------------------------------------------------------
# parser / builder round-trip
# ---------------------------------------------------------------------------
def test_parse_fpga_profile_fixture():
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    raw = parser.parse_fpga_profile(_FIX)
    assert raw is not None and "fpga_profile" in raw
    assert raw["fpga_profile"]["module_name"] == "multicore_fpga_wrapper"


def test_parse_fpga_profile_bundled_fallback():
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    raw = parser.parse_fpga_profile("nanosoc_multicore")
    assert raw is not None and "fpga_profile" in raw
    assert (raw["fpga_profile"]["module_name"]
            == "nanosoc_multicore_vivado_wrapper")


def test_parse_fpga_profile_missing_returns_none():
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    assert parser.parse_fpga_profile("nonexistent_profile") is None


def test_build_fpga_profile_fields():
    be = _fixture_backend()
    fp = be.profile
    assert fp.module_name == "multicore_fpga_wrapper"
    assert fp.port_renames["clk_i"] == "sys_clk"
    assert fp.exclude_pads == ["io"]
    assert any(t["name"] == "status_o" for t in fp.debug_taps)
    assert "nmi_from_strap" in fp.features


def test_inert_without_profile():
    """No profile => a plain tristate emission (no renames/excludes/taps)."""
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    builder = SoCBuilder(parser)
    boundary = builder._build_chip_boundary(parser.parse_chip_boundary(_FIX))
    be = SoCFpgaWrapperBackend(boundary, None)
    names = {p["name"] for p in be.wrapper_ports()}
    # inner net names survive verbatim (no rename) -> base = inner in_net
    assert "clk_i" in names and "sys_clk" not in names
    # bidir swdi (no rename) -> base swdi -> swdi_i / swdi_o / swdi_t triple
    assert {"swdi_i", "swdi_o", "swdi_t"} <= names
    # io bidir bus NOT excluded -> its triple is present (base = io_i)
    assert {"io_i_i", "io_i_o", "io_i_t"} <= names
    assert be.residuals() == []


# ---------------------------------------------------------------------------
# tristate / rename / exclude port resolution
# ---------------------------------------------------------------------------
def test_tristate_triple_and_rename():
    be = _fixture_backend()
    bp = {p["name"]: p for p in be.wrapper_ports()}
    # bidir swdi (renamed base swd_dio) -> _i input, _o/_t outputs
    assert bp["swd_dio_i"]["direction"] == "input"
    assert bp["swd_dio_o"]["direction"] == "output"
    assert bp["swd_dio_t"]["direction"] == "output"
    # tristate_out tdo -> _o + _t (no _i), both outputs
    assert bp["tdo_o_o"]["direction"] == "output"
    assert bp["tdo_o_t"]["direction"] == "output"
    assert "tdo_o_i" not in bp
    # board pin rename clk_i -> sys_clk
    assert "sys_clk" in bp and "clk_i" not in bp


def test_excluded_pad_drops_ports():
    be = _fixture_backend()
    names = {p["name"] for p in be.wrapper_ports()}
    # io bidir bus excluded -> no io_i/io_o/io_t ports
    assert not any(n.startswith("io_") for n in names)


def test_debug_taps_appended_as_outputs():
    be = _fixture_backend()
    bp = {p["name"]: p for p in be.wrapper_ports()}
    assert bp["status_o"]["direction"] == "output"   # soc_out re-export
    assert bp["txd_o_dbg"]["direction"] == "output"  # wire re-export


def test_tristate_assigns_polarity():
    be = _fixture_backend()
    assigns = {a["lhs"]: a["rhs"] for a in be.tristate_assigns()}
    # bidir swdio: active_high oe -> _t = ~oe
    assert assigns["swd_dio_t"] == "~swdoen"
    # tristate tdo: active_low oe -> _t = oe (no inversion)
    assert assigns["tdo_o_t"] == "ntdoen_o"
    # data-out leg present for renamed bases (port != inner net)
    assert assigns["swd_dio_o"] == "swdo"


def test_no_self_assign_when_port_equals_inner_net():
    """When the bidir _o port name == inner out_net, bind directly (no assign).

    The real qspi_io case: port qspi_io_o == inner qspi_io_o. With no rename in
    the fixture for io (it is excluded) we assert the general property on the
    real backend instead — here just guard against `assign x = x;`.
    """
    be = _fixture_backend()
    for a in be.tristate_assigns():
        assert a["lhs"] != a["rhs"], f"self-assign {a}"


# ---------------------------------------------------------------------------
# instance connection classification
# ---------------------------------------------------------------------------
def test_instance_connections_classified():
    be = _fixture_backend()
    conns = {c["port"]: c for c in be.instance_connections()}
    # bonded input renamed
    assert conns["clk_i"]["expr"] == "sys_clk" and conns["clk_i"]["kind"] in (
        "bonded", "feature")
    # bidir in always binds to the _i port
    assert conns["swdi"]["expr"] == "swd_dio_i"
    # feature overrides the boundary tie nmi=1'b0 -> strap 1'b1
    assert conns["nmi"]["kind"] == "feature" and conns["nmi"]["expr"] == "1'b1"
    # soc_out tap binds the inner output directly to the tap port
    assert conns["status_o"]["kind"] == "tap"
    assert conns["status_o"]["expr"] == "status_o"
    # remaining open stays open
    assert conns["irq_o"]["kind"] == "open" and conns["irq_o"]["expr"] == ""
    # excluded bidir bus: inner ports tied/open, never a port
    assert conns["io_i"]["kind"] == "tied"
    assert conns["io_o"]["kind"] == "open"
    assert conns["io_e"]["kind"] == "open"


def test_excluded_pad_uses_exclude_tie_constant():
    """An exclude_ties override sets the excluded pad's inner-input constant."""
    boundary = ChipBoundary(
        chip_module="t", inner_module="s",
        pads=[ChipPad(chip_port="jtrst", kind="input", in_net="dap_ntrst")],
        ties=[], opens=[])
    profile = FpgaProfile(exclude_pads=["jtrst"],
                          exclude_ties={"dap_ntrst": "1'b1"})
    be = SoCFpgaWrapperBackend(boundary, profile)
    conns = {c["port"]: c for c in be.instance_connections()}
    assert conns["dap_ntrst"]["expr"] == "1'b1"
    assert "dap_ntrst" not in {p["name"] for p in be.wrapper_ports()}


def test_unknown_pad_kind_raises():
    b = ChipBoundary(chip_module="t", inner_module="s",
                     pads=[ChipPad(chip_port="x", kind="bogus", in_net="n")])
    with pytest.raises(ValueError, match="bogus"):
        SoCFpgaWrapperBackend(b).wrapper_ports()


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------
def test_generate_emits_v_and_flist(tmp_path):
    be = _fixture_backend()
    out = be.generate(tmp_path / "rtl", tmp_path / "flist")
    assert out is not None and out.name == "multicore_fpga_wrapper.v"
    assert out.exists()
    flist = tmp_path / "flist" / "multicore_fpga_wrapper.flist"
    assert flist.exists() and "multicore_fpga_wrapper.v" in flist.read_text()


def test_generate_instantiates_inner_no_pad_cells(tmp_path):
    be = _fixture_backend()
    v = be.generate(tmp_path / "rtl").read_text()
    # instantiates the inner module as u_soc
    assert "multicore_soc" in v and "u_soc (" in v
    # NO pad cells, NO true inout (FPGA fabric provides the IOBUFs)
    assert "uPAD" not in v and "inout" not in v
    # param override forwarded
    assert "MEM_ADDR_W" in v
    # tristate triple emitted
    for sig in ("swd_dio_i", "swd_dio_o", "swd_dio_t"):
        assert sig in v
    # feature strap drives nmi
    assert re.search(r"\.nmi\s+\(1'b1\)", v)


def test_generate_comma_discipline(tmp_path):
    """Last port + last instance connection carry no trailing comma; balanced."""
    be = _fixture_backend()
    v = be.generate(tmp_path / "rtl").read_text()
    mh = re.search(r"module multicore_fpga_wrapper.*?\)\s*\(\n(.*?)\n\);",
                   v, re.S)
    plines = [l for l in mh.group(1).splitlines() if l.strip()]
    for i, l in enumerate(plines):
        code = l.split("//")[0].rstrip()
        assert code.endswith(",") != (i == len(plines) - 1)
    mi = re.search(r"u_soc \(\n(.*?)\n    \);", v, re.S)
    ilines = [l for l in mi.group(1).splitlines() if l.strip()]
    for i, l in enumerate(ilines):
        code = l.split("//")[0].rstrip()
        assert code.endswith(",") != (i == len(ilines) - 1)
    assert v.count("(") == v.count(")")


def test_residuals_reported():
    be = _fixture_backend()
    r = " | ".join(be.residuals())
    assert "param_overrides" in r
    assert "port_renames" in r
    assert "exclude_pads" in r
    assert "debug_taps" in r
    assert "feature" in r


# ---------------------------------------------------------------------------
# SYSRESETREQ per-core isolation (bundled feature, Round-8 HW bug #1)
# ---------------------------------------------------------------------------
def test_sysresetreq_isolated_not_self_looped(tmp_path):
    """The bundled feature is `isolate: true` — a core's SYSRESETREQ must NOT
    loop back into the inner SoC's global sys_sysresetreq (that rebooted both
    cores + the shared fabric). Per-core self-reset is handled inside the SoC by
    nanosoc_reset_ctrl; sys_sysresetreq stays tied inactive and the two
    cpu_*_sysresetreq outputs stay observed via a harmless unused sink."""
    be = _real_backend()
    v = be.generate(tmp_path / "rtl").read_text()
    # the old self-loop (OR + 2-FF + startup-guard + feedback) is GONE
    assert "network_core_sysresetreq | chip_core_sysresetreq" not in v
    assert "cpu_sysresetreq_any" not in v
    assert "sysresetreq_filt" not in v
    # inner global sys_sysresetreq tied inactive (boundary tie wins)
    assert re.search(r"\.sys_sysresetreq\s+\(1'b0\)", v)
    # The two tapped outputs are still bound to internal wires (not open) and
    # collected into an unused sink so they do not dangle. Inner port names
    # follow 548cbed: cpu_0_* -> network_core_*, cpu_1_* -> chip_core_*.
    assert re.search(r"\.network_core_sysresetreq\s+\(network_core_sysresetreq\)", v)
    assert re.search(r"\.chip_core_sysresetreq\s+\(chip_core_sysresetreq\)", v)
    assert ("_unused_sysresetreq = &{1'b0, network_core_sysresetreq, "
            "chip_core_sysresetreq};") in v


# ---------------------------------------------------------------------------
# REAL cross-check vs the hand reference wrapper + IP-XACT tcl
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _HAND_V.exists(),
                    reason="hand FPGA wrapper reference not present")
def test_real_port_set_matches_hand_wrapper(tmp_path):
    be = _real_backend()
    gen = _parse_ports(be.generate(tmp_path / "rtl").read_text())
    hand = _parse_ports(_HAND_V.read_text())
    assert set(gen) == set(hand), (
        f"port-set drift: in_hand_not_gen={sorted(set(hand)-set(gen))} "
        f"in_gen_not_hand={sorted(set(gen)-set(hand))}")
    # direction + width agree on every port
    for nm in hand:
        assert gen[nm] == hand[nm], f"{nm}: gen={gen[nm]} hand={hand[nm]}"


@pytest.mark.skipif(not _HAND_TCL.exists(),
                    reason="hand IP-XACT package tcl not present")
def test_real_ip_xact_physical_names_present(tmp_path):
    """Every curated IP-XACT physical_name MUST exist as a generated port."""
    be = _real_backend()
    gen = set(_parse_ports(be.generate(tmp_path / "rtl").read_text()))
    tcl = _HAND_TCL.read_text()
    physnames = set(re.findall(r"set_property physical_name (\w+) ", tcl))
    assert physnames, "tcl curates at least one physical_name"
    assert physnames <= gen, f"missing IP-XACT ports: {sorted(physnames - gen)}"
    # the profile declares the SAME contract set
    assert set(be.ip_xact_physical_names()) <= gen


@pytest.mark.skipif(not _HAND_V.exists(),
                    reason="hand FPGA wrapper reference not present")
def test_real_tristate_mapping_matches_hand(tmp_path):
    """mdio/qspi/swd tristate triples + inverted-OE assigns match the hand V;
    hostio4 is excluded on both."""
    be = _real_backend()
    v = be.generate(tmp_path / "rtl").read_text()
    gen = set(_parse_ports(v))
    for base in ("mdio", "qspi_io", "swd_dio"):
        assert {f"{base}_i", f"{base}_o", f"{base}_t"} <= gen
    assert "assign mdio_t = ~md_padoe_o;" in v
    assert "assign swd_dio_t = ~dap_swdoen;" in v
    assert "assign qspi_io_t = ~qspi_io_e;" in v
    # hostio4 entirely dropped
    assert not any("hostio4" in p for p in gen)


@pytest.mark.skipif(not _HAND_V.exists(),
                    reason="hand FPGA wrapper reference not present")
def test_real_instantiates_soc_no_pad_cells(tmp_path):
    be = _real_backend()
    v = be.generate(tmp_path / "rtl").read_text()
    assert "nanosoc_multicore_soc" in v and "u_soc (" in v
    assert "uPAD" not in v and "PAD_INOUT" not in v and "inout" not in v
    assert not re.search(r"assign (\w+) = \1;", v)  # no self-assign
