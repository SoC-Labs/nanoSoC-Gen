# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for SoCConstraintsBackend (SDC + XDC timing constraints).

The backend emits TIMING constraints (clocks / generated-clocks / clock-groups
/ io-delays / false-paths / multicycle / ASIC pad-cell constraints) for the
ASIC (Synopsys SDC) and FPGA (Xilinx XDC) flows, driven by:

  (a) the model top clocks + SYS_CLK_FREQ_HZ + the chip boundary, and
  (b) per-module constraints: blocks aggregated up the hierarchy (the
      pre-verified-IP story).

Layers:
  * UNIT — the hermetic tests/fixtures/constraints_soc.yaml (system clock + an
    eth RMII IP + a QSPI IP, each carrying a constraints: block) + the matching
    constraints_chip boundary: clock periods, the SWD pair, the pad-cell block,
    per-IP aggregation, scope filtering, io-delays, generated clocks.
  * REAL — the real multicore SoC + its bundled boundary: the ASIC SDC must
    reproduce the hand-written ASIC constraints.sdc intent (EXTCLK/SWDCLK,
    uPAD_SWDIO_IO block, swd false-path). Skipped if the real tree is absent.
"""

import os
from pathlib import Path

import pytest

from soc_model.parser import SoCParser
from soc_model.builder import SoCBuilder
from soc_model.backends.constraints import SoCConstraintsBackend

from tests.conftest import FIXTURES

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# fixture helpers
# ---------------------------------------------------------------------------
def _top(yaml="constraints_soc.yaml"):
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    return SoCBuilder(parser).build_system(yaml)


def _boundary(name="constraints_chip"):
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    builder = SoCBuilder(parser)
    raw = parser.parse_chip_boundary(name)
    return builder._build_chip_boundary(raw)


def _backend(with_boundary=True, **kw):
    return SoCConstraintsBackend(
        _top(), _boundary() if with_boundary else None, **kw)


# ===========================================================================
# SDC — primary + SWD clock derivation from the model
# ===========================================================================
class TestSdcClocks:
    def test_system_clock_period_from_freq(self):
        """SYS_CLK_FREQ_HZ=100 MHz -> 10.00000 ns create_clock on the boundary
        clock pad (sys_fclk -> SYS_FCLK)."""
        sdc = _backend().render_sdc()
        assert ("create_clock -name sys_fclk -period 10.00000 "
                "-waveform \"0 5.00000\" [get_ports SYS_FCLK]") in sdc
        assert "set_clock_uncertainty 0.35 [get_clocks sys_fclk]" in sdc

    def test_swd_clock_is_four_x_system(self):
        """No explicit SWD freq -> SWDCLK = 4 x EXTCLK = 40 ns, on the swd
        input pad (dap_swclktck -> DAP_SWCLKTCK)."""
        sdc = _backend().render_sdc()
        assert ("create_clock -name swdclk -period 40.00000 "
                "-waveform \"0 20.00000\" [get_ports DAP_SWCLKTCK]") in sdc

    def test_swd_ext_async_handoff(self):
        sdc = _backend().render_sdc()
        assert ("set_clock_uncertainty -setup 0.1 -rise_from [get_clocks swdclk]"
                " -rise_to [get_clocks sys_fclk]") in sdc
        assert "set_multicycle_path 2 -setup -end -from swdclk -to sys_fclk" in sdc
        assert "set_multicycle_path 1 -hold -end -from swdclk -to sys_fclk" in sdc
        assert "set_false_path -hold -from sys_fclk -to swdclk" in sdc

    def test_units_and_design_rules(self):
        sdc = _backend().render_sdc()
        assert "set_units -time ns" in sdc
        assert "set_units -capacitance pF" in sdc
        assert "set_max_capacitance 3 [all_outputs]" in sdc
        assert "set_max_fanout 10 [all_inputs]" in sdc

    def test_custom_uncertainty(self):
        sdc = _backend(sys_uncertainty=0.5, inter_uncertainty=0.2).render_sdc()
        assert "set_clock_uncertainty 0.5 [get_clocks sys_fclk]" in sdc
        assert "-setup 0.2 -rise_from" in sdc

    def test_no_freq_reports_residual(self):
        """A top with no resolvable system frequency reports a residual rather
        than emitting a bogus create_clock."""
        from soc_model.model import Module, Clock
        m = Module(name="nofreq")
        m.clocks.append(Clock(name="sys_fclk", source="external"))
        be = SoCConstraintsBackend(m, None)
        sdc = be.render_sdc()
        assert "no external system clock with a resolvable frequency" in sdc
        assert any("no external system clock" in r for r in be.residuals())


# ===========================================================================
# SDC — ASIC pad-cell multicycle / false-path block (uPAD_*)
# ===========================================================================
class TestSdcPadCells:
    def test_swdio_pad_cell_block(self):
        """The swd bidir pad -> uPAD_SWDIO_IO false-path + multicycle-through +
        per-control-pin multicycle (I/IE/DS/OEN -> C). Matches the hand SDC."""
        sdc = _backend().render_sdc()
        assert "set_false_path -through uPAD_SWDIO_IO" in sdc
        assert "set_multicycle_path 2 -through uPAD_SWDIO_IO" in sdc
        for p in ("I", "IE", "DS", "OEN"):
            assert (f"set_multicycle_path 2 -from uPAD_SWDIO_IO/{p} "
                    f"-to uPAD_SWDIO_IO/C") in sdc

    def test_hostio_bus_wildcard_block(self):
        """A wide bidir host bus -> wildcard per-control-pin multicycle
        (the multicore equivalent of the hand uPAD_P0_*/uPAD_P1_* block)."""
        sdc = _backend().render_sdc()
        for p in ("I", "IE", "DS", "OEN"):
            assert (f"set_multicycle_path 2 -from uPAD_HOSTIO4_*/{p} "
                    f"-to uPAD_HOSTIO4_*/C") in sdc

    def test_no_boundary_no_pad_block(self):
        sdc = _backend(with_boundary=False).render_sdc()
        assert "uPAD_SWDIO_IO" not in sdc
        assert any("no chip boundary" in r for r in
                   _backend(with_boundary=False).residuals())


# ===========================================================================
# SDC — boundary-classified input delays
# ===========================================================================
class TestSdcIoDelays:
    def test_input_delays_on_control_pads(self):
        """Non-clock pure-input pads get input_delay @ system clock; the swd
        clock pad references swdclk. Mirrors the hand NRST/TEST @ EXTCLK,
        SWDIO @ SWDCLK."""
        sdc = _backend().render_sdc()
        assert ("set_input_delay -clock [get_clocks sys_fclk] -add_delay 0.1 "
                "[get_ports SYS_SYSRESETN]") in sdc
        assert ("set_input_delay -clock [get_clocks swdclk] -add_delay 0.1 "
                "[get_ports DAP_SWCLKTCK]") in sdc

    def test_clock_pads_not_input_delayed(self):
        sdc = _backend().render_sdc()
        # the clk-group pads (SYS_FCLK / RMII_REF_CLK) must NOT get a
        # set_input_delay (clocks aren't input-delayed to themselves).
        assert "set_input_delay -clock [get_clocks sys_fclk] -add_delay 0.1 " \
               "[get_ports SYS_FCLK]" not in sdc
        assert "set_input_delay -clock [get_clocks sys_fclk] -add_delay 0.1 " \
               "[get_ports RMII_REF_CLK]" not in sdc


# ===========================================================================
# per-IP AGGREGATION (the pre-verified-IP story)
# ===========================================================================
class TestAggregation:
    def test_aggregated_paths(self):
        agg = _backend().aggregated_constraints()
        paths = {p for p, _cs in agg}
        assert "u_eth_ss_0" in paths
        assert "u_qspi_flash_0" in paths

    def test_eth_clock_and_gen_clocks_in_xdc(self):
        xdc = _backend().render_xdc()
        # RMII ref clock @ 50 MHz = 20 ns, aggregated from the eth IP
        assert ("create_clock -name rmii_ref_clk -period 20.00000 "
                "[get_ports rmii_ref_clk]") in xdc
        # the two /2 MII generated clocks
        assert "create_generated_clock -name mii_rx_clk -divide_by 2" in xdc
        assert "create_generated_clock -name mii_tx_clk -divide_by 2" in xdc
        assert "mrx_clk_reg/C" in xdc and "mrx_clk_reg/Q" in xdc

    def test_qspi_gen_clocks_aggregate(self):
        xdc = _backend().render_xdc()
        assert "create_generated_clock -name qspi_sclk_i -divide_by 2" in xdc
        assert "create_generated_clock -name qspi_sclk -divide_by 1" in xdc
        assert "QSPI_SCLK_reg" in xdc

    def test_async_clock_group_aggregates(self):
        xdc = _backend().render_xdc()
        assert ("set_clock_groups -name async_sys_rmii -asynchronous "
                "-group [get_clocks {sys_clk}] "
                "-group [get_clocks {rmii_ref_clk mii_rx_clk mii_tx_clk}]") in xdc

    def test_group_expr_clock_names(self):
        """A group of plain clock names renders one get_clocks list."""
        from soc_model.backends.constraints import SoCConstraintsBackend as B
        assert (B._group_expr(["rmii_ref_clk", "mii_rx_clk"])
                == "[get_clocks {rmii_ref_clk mii_rx_clk}]")

    def test_group_expr_pin_glob(self):
        """A group entry containing '/' is a hierarchical PIN glob, resolved by
        get_clocks -of_objects (BD clk_wiz robustness, hand-XDC style)."""
        from soc_model.backends.constraints import SoCConstraintsBackend as B
        assert (B._group_expr(["*clk_wiz_0*/clk_out1"])
                == '[get_clocks -of_objects [get_pins -hierarchical -filter '
                   '{NAME =~ "*clk_wiz_0*/clk_out1"}]]')

    def test_group_expr_mixed_names_and_pin(self):
        """A group mixing names and a pin glob concatenates both collections."""
        from soc_model.backends.constraints import SoCConstraintsBackend as B
        out = B._group_expr(["sys_clk", "*clk_wiz_0*/clk_out1"])
        assert out.startswith("[concat [get_clocks {sys_clk}] ")
        assert "-of_objects" in out

    def test_rmii_io_delays_aggregate(self):
        xdc = _backend().render_xdc()
        assert ("set_input_delay -clock [get_clocks rmii_ref_clk] -min 2.0 "
                "[get_ports {phy_rmii_rxd[*]}]") in xdc
        assert ("set_output_delay -clock [get_clocks rmii_ref_clk] -max 5.0 "
                "[get_ports {phy_rmii_tx_en}]") in xdc

    def test_qspi_io_delays_aggregate(self):
        xdc = _backend().render_xdc()
        assert ("set_output_delay -clock [get_clocks qspi_sclk] -max 3.0 "
                "[get_ports {qspi_io[*]}]") in xdc
        assert ("set_input_delay -clock [get_clocks qspi_sclk] -max 7.0 "
                "[get_ports {qspi_io[*]}]") in xdc

    def test_per_ip_false_path_aggregates(self):
        xdc = _backend().render_xdc()
        assert "set_false_path -to [get_ports phy_mdc]" in xdc

    def test_boundary_swd_suppression_logic(self):
        """A declared board-name SWD false-path suppresses the boundary-derived
        (chip-port) swd lines; phy_mdc (non-swd) does not."""
        from soc_model.model import FalsePath, ConstraintSet
        be = _backend()
        # the fixture boundary has an swd group, and the fixture declares NO
        # board swd false-path -> boundary swd lines ARE emitted by default
        assert "swdio" in be.render_xdc()
        assert be._agg_declares_swd(
            [('', ConstraintSet(false_paths=[FalsePath(from_='swd_clk')]))]) is True
        assert be._agg_declares_swd(
            [('', ConstraintSet(false_paths=[FalsePath(to='phy_mdc')]))]) is False

    def test_path_provenance_comment(self):
        """Aggregated entries carry their instance path as a provenance tag."""
        xdc = _backend().render_xdc()
        assert "# u_eth_ss_0" in xdc
        assert "# u_qspi_flash_0" in xdc
        # io-delay lines carry an inline ;# <path> provenance tag
        sdc_xdc = _backend().render_xdc()
        assert ";# u_eth_ss_0" in sdc_xdc


# ===========================================================================
# scope filtering (asic / fpga / both)
# ===========================================================================
class TestScopeFiltering:
    def test_fpga_scoped_ip_not_in_sdc(self):
        """The eth/qspi IPs are scope:fpga, so their RMII/QSPI clocks must NOT
        appear in the ASIC SDC."""
        sdc = _backend().render_sdc()
        assert "rmii_ref_clk" not in sdc
        assert "qspi_sclk" not in sdc
        assert "mii_rx_clk" not in sdc

    def test_both_scope_appears_in_both(self):
        """A both-scoped per-IP false-path lands in BOTH renderers."""
        from soc_model.model import (Module, ConstraintSet, FalsePath, Clock,
                                     Instance)
        top = Module(name="t")
        top.params = {}
        top.clocks.append(Clock(name="sys_fclk", source="external"))
        child = Module(name="ip")
        cs = ConstraintSet(scope="both")
        fp = FalsePath(to="dbg_o")
        fp._scope = "both"
        cs.false_paths.append(fp)
        child.constraints = cs
        inst = Instance(instance_name="u_ip")
        inst.resolved_module = child
        top.instances.append(inst)
        be = SoCConstraintsBackend(top, None)
        assert "set_false_path -to dbg_o" in be.render_sdc()
        assert "set_false_path -to [get_ports dbg_o]" in be.render_xdc()


# ===========================================================================
# file emission
# ===========================================================================
class TestGenerate:
    def test_generate_writes_sdc_and_xdc(self, tmp_path):
        out = tmp_path / "constraints"
        paths = _backend().generate(out)
        names = {p.name for p in paths}
        assert "constraints_soc_constraints.sdc" in names
        assert "constraints_soc_constraints.xdc" in names
        for p in paths:
            assert p.exists() and p.read_text()

    def test_generate_content_matches_render(self, tmp_path):
        be = _backend()
        be.generate(tmp_path)
        sdc = (tmp_path / "constraints_soc_constraints.sdc").read_text()
        assert sdc == be.render_sdc()


# ===========================================================================
# residuals (honest non-derivable disclosure)
# ===========================================================================
class TestResiduals:
    def test_pin_placement_out_of_scope(self):
        rs = _backend().residuals()
        assert any("pin placement" in r and "OUT OF SCOPE" in r for r in rs)

    def test_ips_without_constraints_reported(self):
        """two_region_soc's SRAM IPs carry no constraints: block -> reported."""
        be = SoCConstraintsBackend(_top("two_region_soc.yaml"), None)
        missing = be.ips_without_constraints()
        assert any("u_sram_0" in m for m in missing)
        assert any("IP instances with NO constraints" in r
                   for r in be.residuals())

    def test_ips_with_constraints_not_reported(self):
        """constraints_soc's IPs DO carry blocks -> not in the missing list."""
        be = _backend()
        missing = be.ips_without_constraints()
        assert not any("u_eth_ss_0" in m for m in missing)
        assert not any("u_qspi_flash_0" in m for m in missing)


# ===========================================================================
# REAL multicore cross-check (slow; skipped if the real tree is absent)
# ===========================================================================
# A nanosoc-multicore-system checkout, named by $NANOSOC_REAL_ROOT; unset -> the
# real-model tier skips (there is no default location).
_REAL_ROOT = Path(os.environ.get("NANOSOC_REAL_ROOT", "/nonexistent-NANOSOC_REAL_ROOT-unset"))
_REAL_YAML = _REAL_ROOT / "sys_desc" / "nanosoc_multicore_soc.yaml"
_REAL_LIBS = [
    _REAL_ROOT / "sys_desc",
    _REAL_ROOT / "nanosoc_arch_tech" / "sys_desc",
    _REAL_ROOT / "nanosoc_arch_tech" / "sys_desc" / "regions",
    _REAL_ROOT / "nanosoc_arch_tech" / "sys_desc" / "subsystems",
    _REAL_ROOT / "ethernet-subsystem-ahb" / "sys_desc",
    _REAL_ROOT / "ethernet-subsystem-ahb" / "sys_desc" / "regions",
    _REAL_ROOT / "ahb_qspi" / "sys_desc",
]
_REAL_SKIP = (not _REAL_YAML.is_file()
              or any(not d.is_dir() for d in _REAL_LIBS))


@pytest.mark.slow
@pytest.mark.skipif(_REAL_SKIP, reason="real multicore SoC tree not present")
class TestRealMulticoreConstraints:
    def _real_backend(self):
        parser = SoCParser(str(_REAL_YAML.parent),
                           [str(d) for d in _REAL_LIBS])
        builder = SoCBuilder(parser)
        top = builder.build_system(_REAL_YAML.name)
        boundary = builder._build_chip_boundary(
            parser.parse_chip_boundary("nanosoc_multicore"))
        return SoCConstraintsBackend(top, boundary)

    def test_real_sdc_reproduces_hand_intent(self):
        """The ASIC SDC must reproduce the hand-written ASIC constraints.sdc intent:
        EXTCLK @ 100 MHz (10 ns), SWDCLK = 4x (40 ns), the SWD<->EXT async
        multicycle + false-path, the uPAD_SWDIO_IO pad-cell block, and the
        boundary input delays."""
        sdc = self._real_backend().render_sdc()
        # clock pair (period from SYS_CLK_FREQ_HZ=100 MHz -> 10 ns; swd = 40)
        assert "create_clock -name sys_fclk -period 10.00000" in sdc
        assert "create_clock -name swdclk -period 40.00000" in sdc
        assert "set_clock_uncertainty 0.35 [get_clocks sys_fclk]" in sdc
        # async SWD<->EXT handoff
        assert "set_multicycle_path 2 -setup -end -from swdclk -to sys_fclk" in sdc
        assert "set_false_path -hold -from sys_fclk -to swdclk" in sdc
        # pad-cell block on the real SWDIO bidir pad
        assert "set_false_path -through uPAD_SWDIO_IO" in sdc
        assert ("set_multicycle_path 2 -from uPAD_SWDIO_IO/OEN "
                "-to uPAD_SWDIO_IO/C") in sdc
        # boundary input delays
        assert ("set_input_delay -clock [get_clocks sys_fclk] -add_delay 0.1 "
                "[get_ports SYS_SYSRESETN]") in sdc
        assert "set_max_capacitance 3 [all_outputs]" in sdc

    def test_real_sdc_pad_cells_use_real_boundary(self):
        """The hostio4 7-bit bidir bus -> uPAD_HOSTIO4_* wildcard block."""
        sdc = self._real_backend().render_sdc()
        assert ("set_multicycle_path 2 -from uPAD_HOSTIO4_*/I "
                "-to uPAD_HOSTIO4_*/C") in sdc

    def test_real_xdc_has_swd_false_paths(self):
        """The XDC carries SWD false-paths — now the board-name set declared by
        the top constraints: block (the boundary chip-port set is suppressed)."""
        be = self._real_backend()
        xdc = be.render_xdc()
        assert "set_false_path -from [get_ports swd_clk]" in xdc
        assert "set_false_path -from [get_ports swd_dio]" in xdc

    def test_real_xdc_reproduces_rmii_intent(self):
        """The eth-ss IP now carries a constraints: block, so the real XDC
        reproduces the hand nanosoc_multicore_timing.xdc RMII intent: the two
        /2 MII generated clocks, the sys<->rmii async group (sys side resolved
        BY PIN — the BD clk_wiz isn't a named clock), the RMII io-delays, and
        the phy_mdc false-path. No create_clock rmii_ref_clk (the board PIN xdc
        owns it on phy_rmii_ref_clk)."""
        xdc = self._real_backend().render_xdc()
        assert ("create_generated_clock -name mii_rx_clk -divide_by 2 -source "
                '[get_pins -hierarchical -filter {NAME =~ "*u_rmii_to_mii/'
                'mrx_clk_reg/C"}] [get_pins -hierarchical -filter {NAME =~ '
                '"*u_rmii_to_mii/mrx_clk_reg/Q"}]') in xdc
        assert "create_generated_clock -name mii_tx_clk -divide_by 2" in xdc
        # async group: sys clock by pin (-of_objects), rmii + /2 children by name
        assert ("set_clock_groups -name async_sys_rmii -asynchronous -group "
                "[get_clocks -of_objects [get_pins -hierarchical -filter "
                '{NAME =~ "*clk_wiz_0*/clk_out1"}]] -group [get_clocks '
                "{rmii_ref_clk mii_rx_clk mii_tx_clk}]") in xdc
        assert ("set_input_delay -clock [get_clocks rmii_ref_clk] -max 14.0 "
                "[get_ports {phy_rmii_rxd[*]}]") in xdc
        assert ("set_output_delay -clock [get_clocks rmii_ref_clk] -min -1.0 "
                "[get_ports {phy_rmii_txd[*]}]") in xdc
        assert "set_false_path -to [get_ports phy_mdc]" in xdc
        # rmii_ref_clk is NOT create_clock'd here (the pin xdc owns it)
        assert "create_clock -name rmii_ref_clk" not in xdc

    def test_real_xdc_reproduces_qspi_intent(self):
        """The qspi IP constraints: block -> the QSPI SCK generated clocks
        (internal HCLK/2 + the /1 forward to the output port) and the Micron
        N25Q256A io-delays."""
        xdc = self._real_backend().render_xdc()
        assert ("create_generated_clock -name qspi_sclk_i -divide_by 2 -source "
                '[get_pins -hierarchical -filter {NAME =~ "*clk_wiz_0/inst/'
                'clk_out1"}] [get_pins -hierarchical -filter {NAME =~ '
                '"*qspi_clock_div*QSPI_SCLK_reg*/Q"}]') in xdc
        # the /1 forward targets the output PORT, not a pin
        assert ("create_generated_clock -name qspi_sclk -divide_by 1 -source "
                '[get_pins -hierarchical -filter {NAME =~ "*qspi_clock_div*'
                'QSPI_SCLK_reg*/Q"}] [get_ports qspi_sclk]') in xdc
        assert ("set_output_delay -clock [get_clocks qspi_sclk] -max 3.0 "
                "[get_ports {qspi_io[*]}]") in xdc
        assert ("set_input_delay -clock [get_clocks qspi_sclk] -max 7.0 "
                "[get_ports {qspi_io[*]}]") in xdc

    def test_real_xdc_top_false_paths_board_coherent(self):
        """The multicore top constraints: block completes the hand
        timing.xdc false-path set with FPGA-BOARD names, and SUPPRESSES the
        boundary-derived chip-port swd lines (single-naming-level coherence)."""
        xdc = self._real_backend().render_xdc()
        for p in ("sys_hresetn_o", "cpu_lockup_led", "ptp_lock_led", "pps_led",
                  "phc_pps_out", "uart_txd", "uart_rxd"):
            assert f"[get_ports {p}]" in xdc, p
        assert "set_false_path -from [get_ports swd_clk]" in xdc
        assert "set_false_path -from [get_ports swd_dio]" in xdc
        assert "set_false_path -to [get_ports swd_dio]" in xdc
        # boundary-derived chip-port swd lines are gone (board names own it now)
        assert "swdio" not in xdc
        assert "dap_swclktck" not in xdc

    def test_real_eth_qspi_no_longer_need_constraints(self):
        """eth-ss + qspi now carry constraints: blocks, so they drop off the
        ips_without_constraints() list (only un-timed bridges/admin IPs remain)."""
        be = self._real_backend()
        be.render_xdc()
        names = " ".join(be.ips_without_constraints())
        assert "u_eth_ss_0" not in names
        assert "u_qspi_flash_0" not in names
