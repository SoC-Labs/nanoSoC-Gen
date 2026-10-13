# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for SoCChipWrapperBackend (doc 07 — chip-functional wrapper).

The chip wrapper is the clean functional seam between the inner SoC top and the
pad ring: it re-exposes only the BONDED inner ports 1:1 (grouping bidir in/out/oe
triplets onto one logical pad), drives TIED inner inputs to constants, and leaves
OPEN outputs unconnected.  It is driven entirely by an editable chip-boundary
spec (lib/chip_boundary/<name>.yaml) — ties/opens are never hard-coded.

Two layers:
  * UNIT — hermetic fixture (tests/fixtures/chip_boundary/multicore_chip.yaml):
    parser round-trip, boundary-port resolution, instance classification,
    coverage guards, render assertions, CLI-no-op-when-flag-absent.
  * REAL  — the bundled nanosoc_multicore spec must classify EVERY real soc.sv
    port exactly once (bonded|tied|open|terminated), with matching dir/width
    (cross-checked against the read-only generated nanosoc_multicore_soc.sv).
"""

import os
import re
from pathlib import Path

import pytest

from soc_model.model import ChipBoundary, ChipPad
from soc_model.parser import SoCParser
from soc_model.builder import SoCBuilder
from soc_model.backends.chip_wrapper import SoCChipWrapperBackend

from tests.conftest import FIXTURES

pytestmark = pytest.mark.unit

_FIX_BOUNDARY = "multicore_chip"


def _real_soc_sv_path():
    """Resolve the authoritative generated nanosoc_multicore_soc.sv.

    Opt-in, mirroring tests/slow/test_real_soc_smoke.py: an explicit
    NANOSOC_MULTICORE_SOC_SV wins; otherwise NANOSOC_REAL_ROOT (the
    multicore superproject root) implies the standard
    build_soc/rtl/nanosoc_multicore_soc.sv layout. When neither is set the
    REAL cross-check tests SKIP — the default suite must be hermetic, and
    the previous hard-coded personal absolute path made these tests fail
    on any machine where a STALE copy of that checkout happened to exist
    (the artifact drifts independently of this repo's chip-boundary spec).
    Point the env var at a CURRENT regeneration to run the cross-check.
    """
    env = os.environ.get("NANOSOC_MULTICORE_SOC_SV")
    if env:
        return Path(env)
    root = os.environ.get("NANOSOC_REAL_ROOT")
    if root:
        return Path(root) / "build_soc" / "rtl" / "nanosoc_multicore_soc.sv"
    return None


# The read-only authoritative generated SoC top (79 ports) — used only by the
# REAL cross-check tests; skipped cleanly unless explicitly pointed at a
# current reference tree (see _real_soc_sv_path).
_REAL_SOC_SV = _real_soc_sv_path()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _fixture_backend():
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    builder = SoCBuilder(parser)
    raw = parser.parse_chip_boundary(_FIX_BOUNDARY)
    boundary = builder._build_chip_boundary(raw)
    return SoCChipWrapperBackend(boundary)


def _real_backend(soc_ports=None):
    """Backend for the bundled nanosoc_multicore spec (lib/chip_boundary/)."""
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    builder = SoCBuilder(parser)
    raw = parser.parse_chip_boundary("nanosoc_multicore")
    assert raw is not None, "bundled nanosoc_multicore spec must resolve"
    return SoCChipWrapperBackend(builder._build_chip_boundary(raw), soc_ports)


# The SoC's own parameter defaults, needed to concretise bus-port widths that
# the .sv declares symbolically (`[SYS_DATA_W-1:0]`). soc_model's CLI resolves
# these from the model; here we read them straight off the port declarations.
_SOC_PARAM_DEFAULTS = {"SYS_ADDR_W": 32, "SYS_DATA_W": 32}


def _soc_port_dicts(soc):
    """Convert {name: (direction, width_str)} into the backend's soc_ports list.

    A wrapper cannot declare `wire [SYS_DATA_W-1:0]` — it does not instantiate
    the SoC's parameters — so widths must reach the backend as literals.
    """
    def _w(width_str):
        if not width_str:
            return 1
        m = re.match(r"\[(\d+):0\]$", width_str)
        if m:
            return int(m.group(1)) + 1
        m = re.match(r"\[([A-Z_][A-Z0-9_]*)-1:0\]$", width_str)
        if m and m.group(1) in _SOC_PARAM_DEFAULTS:
            return _SOC_PARAM_DEFAULTS[m.group(1)]
        raise AssertionError(f"unresolvable port width {width_str!r}")
    return [{'name': n, 'direction': d, 'width': _w(w)}
            for n, (d, w) in soc.items()]


def _parse_soc_sv_ports(sv_text):
    """Parse {name: (dir, width_str)} from a generated soc .sv port section."""
    m = re.search(r"\)\s*\(\n(.*?)\n\);", sv_text, re.S)
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
def test_parse_chip_boundary_fixture():
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    raw = parser.parse_chip_boundary(_FIX_BOUNDARY)
    assert raw is not None and "chip_boundary" in raw
    assert raw["chip_boundary"]["chip_module"] == "multicore_chip"


def test_parse_chip_boundary_bundled_fallback():
    """The bundled nanosoc_multicore spec resolves via the package lib dir."""
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    raw = parser.parse_chip_boundary("nanosoc_multicore")
    assert raw is not None and "chip_boundary" in raw
    assert raw["chip_boundary"]["inner_module"] == "nanosoc_multicore_soc"


def test_parse_chip_boundary_missing_returns_none():
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    assert parser.parse_chip_boundary("nonexistent_boundary") is None


def test_build_chip_boundary_flat_pads():
    be = _fixture_backend()
    b = be.boundary
    assert b.chip_module == "multicore_chip"
    assert b.inner_module == "multicore_soc"
    kinds = sorted({p.kind for p in b.pads})
    assert kinds == ["bidir", "input", "output", "tristate_out"]
    assert len(b.ties) == 2
    assert len(b.opens) == 9          # status_o, irq_o + 7 cpu_m address-phase
    assert len(b.terminators) == 1    # cpu_m response answered by a responder
    assert b.terminators[0].module == "ahb_idle_slave"


def test_build_chip_boundary_grouped_pads_form():
    """The bundled spec uses the grouped `pads: {group: [..]}` mapping form."""
    be = _real_backend()
    # grouped form flattens to a single ChipPad list with group labels.
    assert any(p.group == "swd" for p in be.boundary.pads)
    assert any(p.group == "qspi" for p in be.boundary.pads)


# ---------------------------------------------------------------------------
# boundary-port resolution (the bonded ports the pad ring will consume)
# ---------------------------------------------------------------------------
def test_boundary_ports_per_kind():
    be = _fixture_backend()
    bp = {p["name"]: p for p in be.boundary_ports()}
    # input -> 1 input
    assert bp["clk_i"]["direction"] == "input" and bp["clk_i"]["width"] == 1
    # output -> 1 output
    assert bp["txd_o"]["direction"] == "output"
    # bus widths
    assert bp["rxbus_i"]["width_str"] == "[1:0]"
    assert bp["io_i"]["width_str"] == "[3:0]"
    # tristate_out -> out + oe, both outputs
    assert bp["tdo_o"]["direction"] == "output"
    assert bp["ntdoen_o"]["direction"] == "output"
    # bidir -> in (input) + out (output) + oe (output)
    assert bp["swdi"]["direction"] == "input"
    assert bp["swdo"]["direction"] == "output"
    assert bp["swdoen"]["direction"] == "output"


def test_boundary_ports_no_tie_or_open_leak():
    """Tied inputs + open outputs are NEVER exposed as wrapper ports."""
    be = _fixture_backend()
    names = {p["name"] for p in be.boundary_ports()}
    for tie in be.boundary.ties:
        assert tie["soc_port"] not in names
    for name in be.boundary.opens:
        assert name not in names


def test_unknown_pad_kind_raises():
    b = ChipBoundary(chip_module="t", inner_module="s",
                     pads=[ChipPad(chip_port="x", kind="bogus", in_net="n")])
    with pytest.raises(ValueError, match="bogus"):
        SoCChipWrapperBackend(b).boundary_ports()


# ---------------------------------------------------------------------------
# instance connection classification
# ---------------------------------------------------------------------------
def test_instance_connections_classified():
    be = _fixture_backend()
    conns = {c["port"]: c for c in be.instance_connections()}
    # bonded -> .port(port)
    assert conns["clk_i"]["kind"] == "bonded" and conns["clk_i"]["expr"] == "clk_i"
    # tied -> constant
    assert conns["nmi"]["kind"] == "tied" and conns["nmi"]["expr"] == "1'b0"
    assert conns["slave_haddr"]["expr"] == "{ADDR_W{1'b0}}"
    # open -> empty expr
    assert conns["status_o"]["kind"] == "open" and conns["status_o"]["expr"] == ""


def test_coverage_no_duplicates():
    be = _fixture_backend()
    cov = be.coverage()
    # fixture bonded inner nets: clk_i, txd_o, rxbus_i, txbus_o (4 single/bus)
    # + sys_hclk, sys_hresetn (bus clk/reset) + tdo_o,ntdoen_o (tristate)
    # + swdi,swdo,swdoen (bidir) + io_i,io_o,io_e = 14
    assert len(cov["bonded"]) == 14
    assert len(cov["tied"]) == 2
    # status_o, irq_o + the 7 cpu_m address-phase outputs
    assert len(cov["open"]) == 9
    # cpu_m_htrans/hrdata/hready/hresp answered by the terminator
    assert len(cov["terminated"]) == 4
    assert cov["duplicates"] == []
    # no port classified twice
    assert len(cov["all"]) == len(set(cov["all"]))


def test_fixture_terminator_renders_instance_and_wires():
    """A terminator becomes a sized wire set + a helper instance, and the inner
    SoC's ports bind to those wires rather than to constants or nothing."""
    be = _fixture_backend()
    (term,) = be.terminator_context()
    assert term["module"] == "ahb_idle_slave"
    assert term["instance"] == "u_cpu_m_idle"
    # bonded_refs resolve to the wrapper's own nets for those bonded ports
    conns = {c["port"]: c["expr"] for c in term["conns"]}
    assert conns["hclk"] == "sys_hclk"
    assert conns["hresetn"] == "sys_hresetn"
    assert conns["htrans"] == "cpu_m_htrans"

    kinds = {c["port"]: c["kind"] for c in be.instance_connections()}
    for p in ("cpu_m_htrans", "cpu_m_hrdata", "cpu_m_hready", "cpu_m_hresp"):
        assert kinds[p] == "terminated"


def test_coverage_duplicate_classification_raises():
    """A port both bonded and tied is a spec bug -> hard error."""
    b = ChipBoundary(
        chip_module="t", inner_module="s",
        pads=[ChipPad(chip_port="a", kind="input", in_net="clk")],
        ties=[{"soc_port": "clk", "const": "1'b0"}], opens=[])
    with pytest.raises(ValueError, match="clk"):
        SoCChipWrapperBackend(b).coverage()


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------
def test_generate_emits_v_and_flist(tmp_path):
    be = _fixture_backend()
    out = be.generate(tmp_path / "rtl", tmp_path / "flist")
    assert out is not None and out.name == "multicore_chip.v"
    assert out.exists()
    flist = tmp_path / "flist" / "multicore_chip.flist"
    assert flist.exists()
    assert "multicore_chip.v" in flist.read_text()


def test_generate_instantiates_inner_and_classifies(tmp_path):
    be = _fixture_backend()
    out = be.generate(tmp_path / "rtl")
    v = out.read_text()
    # (a) instantiates the inner module
    assert "multicore_soc u_soc" in v
    # (b) bonded pad exposed + connected
    assert re.search(r"input\s+wire\s+clk_i", v)
    assert ".clk_i" in v and "(clk_i)" in v
    # bidir triplet ports all present
    for sig in ("swdi", "swdo", "swdoen", "io_i", "io_o", "io_e"):
        assert sig in v
    # (c) tied input driven to constant, NOT a port
    assert re.search(r"\.nmi\s+\(1'b0\)", v)
    assert not re.search(r"wire\s+nmi\b", v.split("u_soc")[0])
    # (d) open outputs left unconnected
    assert re.search(r"\.status_o\s+\(\)", v)
    assert re.search(r"\.irq_o\s+\(\)", v)
    # no true inout in the chip top (that lives in the pad ring)
    assert "inout" not in v


def test_generate_comma_discipline(tmp_path):
    """Last port + last instance connection carry no trailing comma."""
    be = _fixture_backend()
    v = be.generate(tmp_path / "rtl").read_text()
    mh = re.search(r"module multicore_chip \(\n(.*?)\n\);", v, re.S)
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


# ---------------------------------------------------------------------------
# REAL cross-check against the authoritative generated soc.sv (79 ports)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(_REAL_SOC_SV is None or not _REAL_SOC_SV.exists(),
                    reason="real nanosoc_multicore_soc.sv not configured "
                           "(set NANOSOC_MULTICORE_SOC_SV or NANOSOC_REAL_ROOT)")
def test_real_spec_accounts_for_every_soc_port_once():
    """Every SoC port classified exactly once, and nothing classified that the
    SoC does not have.

    The counts are NOT frozen here on purpose. They were (80 = 39+17+24), and a
    frozen count tells you a number changed, not which port went missing. The
    invariant that matters is the set equality, and the partition summing to the
    whole; `validate_soc_ports` reports the offending names.
    """
    soc = _parse_soc_sv_ports(_REAL_SOC_SV.read_text())
    be = _real_backend(_soc_port_dicts(soc))
    cov = be.coverage()
    classified = set(cov["all"])
    assert set(soc) - classified == set(), "soc ports unclassified"
    assert classified - set(soc) == set(), "classified ports not on the soc"
    # The four buckets partition the port set (coverage() already rejects dups).
    assert (len(cov["bonded"]) + len(cov["tied"])
            + len(cov["open"]) + len(cov["terminated"])) == len(soc)
    # And the generator's own gate agrees.
    be.validate_soc_ports()


@pytest.mark.skipif(_REAL_SOC_SV is None or not _REAL_SOC_SV.exists(),
                    reason="real nanosoc_multicore_soc.sv not configured "
                           "(set NANOSOC_MULTICORE_SOC_SV or NANOSOC_REAL_ROOT)")
def test_real_spec_bonded_dir_width_match_soc():
    soc = _parse_soc_sv_ports(_REAL_SOC_SV.read_text())
    be = _real_backend(_soc_port_dicts(soc))
    bp = {p["name"]: p for p in be.boundary_ports()}
    for name, port in bp.items():
        socdir, socw = soc[name]
        assert port["direction"] == socdir, f"{name} dir mismatch"
        assert port["width_str"] == socw or (socw == "" and port["width"] == 1), \
            f"{name} width mismatch: {port['width_str']!r} vs {socw!r}"
    # tied inputs are soc inputs; open outputs are soc outputs
    for tie in be.boundary.ties:
        assert soc[tie["soc_port"]][0] == "input"
    for name in be.boundary.opens:
        assert soc[name][0] == "output"


@pytest.mark.skipif(_REAL_SOC_SV is None or not _REAL_SOC_SV.exists(),
                    reason="real nanosoc_multicore_soc.sv not present")
def test_real_spec_terminates_the_d2d_manager_port():
    """The die-to-die manager port must be answered, never tied or dropped.

    `d2d_ahb_m` carries the 0x2E000000..0x2FFFFFFF window off the SoC. On this
    monolithic chip nothing is attached. Tying hready/hresp would make the
    window read as zero and swallow writes (the region used to raise a clean
    AHB ERROR, which firmware/apps/default_slave_error_walk asserts); leaving it
    open makes synthesis tie hready low and wedge the bus matrix.
    """
    soc = _parse_soc_sv_ports(_REAL_SOC_SV.read_text())
    be = _real_backend(_soc_port_dicts(soc))
    terminated = set(be.coverage()["terminated"])
    assert terminated == {
        "d2d_ahb_m_htrans", "d2d_ahb_m_hrdata",
        "d2d_ahb_m_hready", "d2d_ahb_m_hresp",
    }
    tied = {t["soc_port"] for t in be.boundary.ties}
    opens = set(be.boundary.opens)
    for p in ("d2d_ahb_m_hready", "d2d_ahb_m_hresp", "d2d_ahb_m_hrdata"):
        assert p not in tied, f"{p} must be answered by a responder, not tied"
        assert p not in opens, f"{p} must be answered by a responder, not open"

    # The responder is instantiated, sized, and clocked from bonded nets.
    (term,) = be.terminator_context()
    assert term["module"] == "nanosoc_d2d_idle_slave"
    wires = {w["name"]: w["width_str"] for w in term["wires"]}
    assert wires["d2d_ahb_m_hrdata"] == "[31:0]"   # $SYS_DATA_W resolved
    assert wires["d2d_ahb_m_htrans"] == "[1:0]"
    conns = {c["port"]: c["expr"] for c in term["conns"]}
    assert conns["hclk"] == "sys_fclk" and conns["hresetn"] == "sys_sysresetn"


@pytest.mark.skipif(_REAL_SOC_SV is None or not _REAL_SOC_SV.exists(),
                    reason="real nanosoc_multicore_soc.sv not present")
def test_validate_soc_ports_rejects_unclassified_and_stale():
    """The gate that would have caught both historical failures.

    Silently dropping a port from `u_soc` is the bug class here: its inputs then
    float (Z in sim, 0 in synth). Generation must refuse rather than emit.
    """
    soc = _parse_soc_sv_ports(_REAL_SOC_SV.read_text())
    ports = _soc_port_dicts(soc)

    # A new SoC port nobody classified -> must raise, and name it.
    be = _real_backend(ports + [{"name": "brand_new_irq",
                                 "direction": "input", "width": 1}])
    with pytest.raises(ValueError, match="brand_new_irq"):
        be.validate_soc_ports()

    # A spec entry for a port the SoC no longer has (the 548cbed rename) -> raise.
    be = _real_backend(ports)
    be.boundary.opens.append("cpu_0_txev")
    with pytest.raises(ValueError, match="cpu_0_txev"):
        be.validate_soc_ports()

    # No soc_ports supplied -> the check is inert (unit tests drive spec alone).
    _real_backend().validate_soc_ports()
