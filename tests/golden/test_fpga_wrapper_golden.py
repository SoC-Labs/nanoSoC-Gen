# Copyright 2026, SoC Labs (www.soclabs.org)
"""Golden snapshot for the multicore FPGA Vivado wrapper (doc 07 M4).

Built from the bundled lib/chip_boundary/nanosoc_multicore.yaml (bonded / tied /
open topology + oe polarity) + the bundled lib/fpga_profile/nanosoc_multicore.yaml
(bespoke board layer: renames, FPGA param overrides, debug taps, dropped pads,
the SYSRESETREQ per-core isolation).  This golden pins the FULL rendered wrapper so future
template / descriptor / boundary drift is caught byte-for-byte; the
timestamp / copyright lines are stripped on both sides by assert_matches_snapshot.

The FPGA wrapper does NOT byte-match the hand reference
(pynq/vivado_ip/nanosoc_multicore_vivado_wrapper.v) — the hand file carries
extra inline FPGA glue (Zynq IMEM $readmemh preload param shape; the old
self-loop 2-FF body is removed in BOTH now — see HW bug #1 isolation).  The structural / port /
tristate / IP-XACT-physical-name equivalence vs the hand file is asserted in the
REAL tier of tests/unit/test_fpga_wrapper.py; this golden only locks the
GENERATED text against itself so regressions are visible.

Regenerate with:  pytest tests/golden/test_fpga_wrapper_golden.py --snapshot-update
"""

import pytest

from soc_model.parser import SoCParser
from soc_model.builder import SoCBuilder
from soc_model.backends.fpga_wrapper import SoCFpgaWrapperBackend

from tests.conftest import FIXTURES, SNAPSHOTS
from tests.helpers import assert_matches_snapshot

pytestmark = pytest.mark.golden

_NAME = "nanosoc_multicore"


def _render(tmp_path):
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    builder = SoCBuilder(parser)
    cb = parser.parse_chip_boundary(_NAME)
    assert cb is not None, "bundled nanosoc_multicore boundary must resolve"
    boundary = builder._build_chip_boundary(cb)
    fp = parser.parse_fpga_profile(_NAME)
    assert fp is not None, "bundled nanosoc_multicore fpga_profile must resolve"
    profile = builder._build_fpga_profile(fp, _NAME)
    be = SoCFpgaWrapperBackend(boundary, profile)
    out = be.generate(tmp_path / "rtl")
    return out.read_text()


def test_multicore_fpga_wrapper_golden(tmp_path, request):
    text = _render(tmp_path)
    # structural teeth so a vacuous golden cannot pass
    assert "module nanosoc_multicore_vivado_wrapper #(" in text
    assert "nanosoc_multicore_soc #(" in text
    assert ") u_soc (" in text
    # Vivado tristate triple + inverted-OE assigns (active-high oe)
    assert "assign mdio_t = ~md_padoe_o;" in text
    assert "assign swd_dio_t = ~dap_swdoen;" in text
    assert "assign qspi_io_t = ~qspi_io_e;" in text
    # NO pad cells / true inout — the FPGA fabric provides the IOBUFs
    assert "uPAD" not in text and "inout" not in text
    # SYSRESETREQ per-core isolation (Round-8 HW bug #1): the wrapper must NOT
    # loop a core's SYSRESETREQ back into the global sys_sysresetreq. The old
    # self-loop (cpu_sysresetreq_any OR + sysresetreq_filt) is GONE; the inner
    # sys_sysresetreq is tied inactive and the two cpu_*_sysresetreq outputs are
    # observed via an unused sink so they do not dangle.
    assert "sysresetreq_filt" not in text
    assert "cpu_sysresetreq_any" not in text
    assert ".sys_sysresetreq      (1'b0)" in text
    # Inner port names follow 548cbed (cpu_0_* -> network_core_*, cpu_1_* ->
    # chip_core_*). The BOARD-facing wrapper port names are unchanged.
    assert ("_unused_sysresetreq = &{1'b0, network_core_sysresetreq, "
            "chip_core_sysresetreq};") in text
    # The die-to-die manager port is answered by a responder, never tied or
    # dropped: Vivado ties an unconnected d2d_ahb_m_hready LOW, which wedges the
    # bus matrix on the first access into the 0x2E000000 window.
    assert "nanosoc_d2d_idle_slave" in text
    assert ".d2d_ahb_m_hready     (d2d_ahb_m_hready)" in text
    # bonded_refs are resolved through port_renames (sys_sysresetn -> nrst)
    assert ".hresetn              (nrst)" in text
    # CPU1 UART keeps its historical board-facing port names (XDC / IP-XACT
    # contract) even though the inner SoC ports were renamed: the wrapper
    # declares `cpu1_uart_rxd` and binds it to inner `chip_core_uart_rxd`.
    assert "input   wire          cpu1_uart_rxd," in text
    assert ".chip_core_uart_rxd   (cpu1_uart_rxd)," in text
    assert_matches_snapshot(
        text,
        SNAPSHOTS / "fpga" / "nanosoc_multicore_vivado_wrapper.v",
        request,
    )
