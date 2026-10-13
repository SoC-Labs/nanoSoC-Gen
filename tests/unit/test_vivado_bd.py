# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for SoCVivadoBdBackend (roadmap doc 08).

The backend emits, from the SoC model:
  * an IP-XACT packaging TCL (``ipx::``) that types the FPGA wrapper's
    clocks / reset / UART (+ promoted AHB/APB/AXI) as Vivado bus interfaces,
  * an optional bare (no-PS) block-design TCL (``create_bd_*``) that
    instantiates the packaged IP and maps the model's interconnect targets.

These are pure-Python, no-Vivado tests: they assert the rendered TCL strings
carry the expected ipx:: / create_bd_* / typed-port commands, that each model
interconnect target maps to an address segment, and that emission is
deterministic (re-run == identical).
"""

from pathlib import Path

import pytest

from soc_model.model import (
    Module, Clock, Reset, Interface, Param,
    Interconnect, InterconnectTarget,
)
from soc_model.backends import vivado_bd
from soc_model.backends.vivado_bd import (
    SoCVivadoBdBackend, WRAPPER_BOUNDARY, IFACE_TYPE_MAP,
    interface_mode, CLOCK, RESET, UART, GPIO, AHB_LITE, APB,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# fixture builders (hermetic — no YAML, no real Vivado)
# ---------------------------------------------------------------------------
def _multicore_top():
    """A small model standing in for the live multicore SoC top.

    Two clocks (sys_fclk 25 MHz, rmii_ref_clk 50 MHz), an active-low reset, a
    UART iface, a promoted AHB initiator iface, and one interconnect with two
    addressable targets (for the address-map test).
    """
    m = Module(name='nanosoc_multicore_soc')
    m.params = {'SYS_CLK_FREQ_HZ': Param(name='SYS_CLK_FREQ_HZ',
                                         default=25000000)}
    m.clocks.append(Clock(name='sys_fclk', source='external', freq_hz=25000000))
    m.clocks.append(Clock(name='rmii_ref_clk', source='external',
                          freq_hz=50000000))
    m.resets.append(Reset(name='nrst', active='low'))
    m.interfaces.append(Interface(name='uart', type='uart', direction='out'))
    # A promoted AHB initiator at the boundary (M5 path).
    m.interfaces.append(Interface(
        name='acc_ahb', type='ahb', direction='initiator',
        params={'fpga_expose': True}))
    ic = Interconnect(name='soc_bus_matrix')
    ic.targets.append(InterconnectTarget(name='sram_0', base=0x00000000,
                                         size=0x00010000))
    ic.targets.append(InterconnectTarget(name='gpio_0', base=0x41200000,
                                         size=0x00010000))
    m.interconnects.append(ic)
    return m


def _legacy_top():
    """A model for the legacy single-core wrapper path (clk/reset/gpio/uart)."""
    m = Module(name='nanosoc_chip')
    m.clocks.append(Clock(name='clk', source='external', freq_hz=25000000))
    m.resets.append(Reset(name='nrst', active='low'))
    return m


def _mc_backend():
    return SoCVivadoBdBackend(_multicore_top())


def _legacy_backend():
    return SoCVivadoBdBackend(
        _legacy_top(), core_rev=2, ip_name='nanosoc_chip',
        wrapper_top='nanosoc_chip_vivado_wrapper')


# ===========================================================================
# inference table — VLNV per type + interface_mode
# ===========================================================================
class TestInferenceTable:
    def test_iface_type_map_vlnvs(self):
        assert IFACE_TYPE_MAP['gpio'] is GPIO
        assert IFACE_TYPE_MAP['ahb'] is AHB_LITE
        assert IFACE_TYPE_MAP['apb'] is APB

    def test_stock_clock_reset_uart_vlnvs(self):
        assert CLOCK.bus_type_vlnv == 'xilinx.com:signal:clock:1.0'
        assert CLOCK.abstraction_vlnv == 'xilinx.com:signal:clock_rtl:1.0'
        assert RESET.abstraction_vlnv == 'xilinx.com:signal:reset_rtl:1.0'
        assert UART.abstraction_vlnv == 'xilinx.com:interface:uart_rtl:1.0'
        assert GPIO.bus_type_vlnv == 'xilinx.com:interface:gpio:1.0'

    def test_custom_buses_flagged(self):
        assert AHB_LITE.custom_busdef is True
        assert APB.custom_busdef is True
        assert AHB_LITE.bus_type_vlnv == 'soclabs.org:interface:ahblite:1.0'
        assert APB.abstraction_vlnv == 'soclabs.org:interface:apb_rtl:1.0'

    def test_interface_mode_ahb_initiator_master(self):
        iface = Interface(name='m', type='ahb', direction='initiator')
        assert interface_mode(iface, AHB_LITE) == 'master'

    def test_interface_mode_ahb_target_slave(self):
        iface = Interface(name='s', type='ahb', direction='target')
        assert interface_mode(iface, AHB_LITE) == 'slave'

    def test_interface_mode_apb_always_slave(self):
        iface = Interface(name='p', type='apb', direction='initiator')
        assert interface_mode(iface, APB) == 'slave'

    def test_stock_mode_from_default(self):
        iface = Interface(name='c', type='wire', direction='in')
        assert interface_mode(iface, CLOCK) == 'slave'
        assert interface_mode(iface, UART) == 'master'


# ===========================================================================
# §5.3.1 drift guard — WRAPPER_BOUNDARY physical names self-consistency
# ===========================================================================
class TestWrapperBoundary:
    def test_multicore_boundary_shape(self):
        b = WRAPPER_BOUNDARY['nanosoc_multicore_vivado_wrapper']
        assert set(b) == {'sys_fclk', 'rmii_ref_clk', 'nrst', 'uart'}
        assert b['sys_fclk'][0] is CLOCK
        assert b['rmii_ref_clk'][0] is CLOCK
        assert b['nrst'][0] is RESET
        assert b['uart'][0] is UART
        # uart physical names match the live script (RxD/TxD -> uart_rxd/txd).
        assert b['uart'][1] == {'RxD': 'uart_rxd', 'TxD': 'uart_txd'}

    def test_legacy_boundary_has_two_gpio(self):
        b = WRAPPER_BOUNDARY['nanosoc_chip_vivado_wrapper']
        assert b['gpio0'][0] is GPIO
        assert b['gpio1'][0] is GPIO
        assert b['gpio0'][1]['TRI_T'] == 'gpio0_tri_t'

    @pytest.mark.parametrize('wrapper', list(WRAPPER_BOUNDARY))
    def test_every_logical_has_a_physical(self, wrapper):
        """Every logical port pinned in WRAPPER_BOUNDARY covers a busdef
        logical port and names a non-empty physical port (the drift guard:
        a hand-edit that drops a pin name is caught here)."""
        for name, (busdef, phys) in WRAPPER_BOUNDARY[wrapper].items():
            busdef_logicals = {pm.logical for pm in busdef.port_maps}
            for logical, physical in phys.items():
                assert logical in busdef_logicals, (wrapper, name, logical)
                assert physical and isinstance(physical, str)


# ===========================================================================
# typed-interface context construction
# ===========================================================================
class TestTypedInterfaces:
    def test_multicore_typed_set(self):
        ctx = _mc_backend()._typed_interfaces()
        names = {c['name'] for c in ctx}
        # curated clk x2 + reset + uart, plus the promoted AHB iface.
        assert {'sys_fclk', 'rmii_ref_clk', 'nrst', 'uart', 'acc_ahb'} <= names

    def test_promoted_ahb_uses_custom_vlnv_and_master_mode(self):
        ctx = {c['name']: c for c in _mc_backend()._typed_interfaces()}
        ahb = ctx['acc_ahb']
        assert ahb['bus_type_vlnv'] == 'soclabs.org:interface:ahblite:1.0'
        assert ahb['mode'] == 'master'           # initiator -> master
        # physical names are the model expansion (iface_name + suffix).
        physicals = {pm['physical'] for pm in ahb['port_maps']}
        assert 'acc_ahb_haddr' in physicals
        assert 'acc_ahb_hrdata' in physicals

    def test_rmii_clock_carries_freq_hz(self):
        ctx = {c['name']: c for c in _mc_backend()._typed_interfaces()}
        assert ctx['rmii_ref_clk']['freq_hz'] == 50000000
        # the system clock has no FREQ_HZ override on the wrapper.
        assert ctx['sys_fclk']['freq_hz'] is None

    def test_clock_carries_associated_reset_and_busif(self):
        ctx = {c['name']: c for c in _mc_backend()._typed_interfaces()}
        # ASSOCIATED_RESET = the reset iface; ASSOCIATED_BUSIF lists typed buses.
        assert ctx['sys_fclk']['associated_reset'] == 'nrst'
        assert 'uart' in ctx['sys_fclk']['associated_busif']

    def test_reset_polarity_from_model(self):
        ctx = {c['name']: c for c in _mc_backend()._typed_interfaces()}
        assert ctx['nrst']['polarity'] == 'ACTIVE_LOW'

    def test_reset_polarity_active_high(self):
        top = _multicore_top()
        top.resets[0] = Reset(name='nrst', active='high')
        ctx = {c['name']: c for c in SoCVivadoBdBackend(top)._typed_interfaces()}
        assert ctx['nrst']['polarity'] == 'ACTIVE_HIGH'

    def test_legacy_gpio_typed(self):
        ctx = {c['name']: c for c in _legacy_backend()._typed_interfaces()}
        assert ctx['gpio0']['bus_type_vlnv'] == 'xilinx.com:interface:gpio:1.0'
        physicals = {pm['physical'] for pm in ctx['gpio0']['port_maps']}
        assert physicals == {'gpio0_tri_i', 'gpio0_tri_o', 'gpio0_tri_t'}


# ===========================================================================
# address segments from the model interconnect targets
# ===========================================================================
class TestAddressSegments:
    def test_segments_match_interconnect_targets(self):
        segs = {s['name']: s for s in _mc_backend()._address_segments()}
        assert segs['sram_0']['base'] == 0x00000000
        assert segs['sram_0']['size'] == 0x00010000
        assert segs['gpio_0']['base'] == 0x41200000
        assert segs['gpio_0']['size'] == 0x00010000

    def test_zero_size_targets_skipped(self):
        top = _multicore_top()
        top.interconnects[0].targets.append(
            InterconnectTarget(name='empty', base=0x50000000, size=0))
        names = {s['name'] for s in SoCVivadoBdBackend(top)._address_segments()}
        assert 'empty' not in names


# ===========================================================================
# rendered packaging TCL (ipx::)
# ===========================================================================
class TestPackageTcl:
    def test_renders_and_has_no_jinja_artifacts(self):
        tcl = _mc_backend().render_package_tcl()
        assert tcl is not None, "jinja2 not available"
        assert '{{' not in tcl and '{%' not in tcl

    def test_ipx_add_bus_interface_per_typed_iface(self):
        tcl = _mc_backend().render_package_tcl()
        for name in ('sys_fclk', 'rmii_ref_clk', 'nrst', 'uart', 'acc_ahb'):
            assert f'ipx::add_bus_interface {name} $core' in tcl

    def test_typed_vlnvs_present(self):
        tcl = _mc_backend().render_package_tcl()
        assert 'xilinx.com:signal:clock_rtl:1.0' in tcl
        assert 'xilinx.com:signal:reset_rtl:1.0' in tcl
        assert 'xilinx.com:interface:uart_rtl:1.0' in tcl
        assert 'soclabs.org:interface:ahblite_rtl:1.0' in tcl

    def test_port_map_and_physical_name(self):
        tcl = _mc_backend().render_package_tcl()
        assert ('ipx::add_port_map RxD [ipx::get_bus_interfaces uart '
                '-of_objects $core]') in tcl
        assert 'set_property physical_name uart_rxd' in tcl
        assert 'set_property physical_name uart_txd' in tcl

    def test_rmii_freq_hz_parameter(self):
        tcl = _mc_backend().render_package_tcl()
        assert 'ipx::add_bus_parameter FREQ_HZ' in tcl
        assert 'set_property value 50000000' in tcl

    def test_associated_reset_and_polarity(self):
        tcl = _mc_backend().render_package_tcl()
        assert 'ipx::add_bus_parameter ASSOCIATED_RESET' in tcl
        assert 'ipx::add_bus_parameter POLARITY' in tcl
        assert 'set_property value ACTIVE_LOW' in tcl

    def test_strip_step_present(self):
        tcl = _mc_backend().render_package_tcl()
        assert 'ipx::remove_bus_interface' in tcl

    def test_vlnv_identity(self):
        be = _mc_backend()
        tcl = be.render_package_tcl()
        assert be.ip_vlnv == 'soclabs.org:user:nanosoc_multicore_vivado_wrapper:1.0'
        assert 'set_property name        nanosoc_multicore_vivado_wrapper $core' \
            in tcl

    def test_legacy_identity_and_gpio(self):
        tcl = _legacy_backend().render_package_tcl()
        assert 'ipx::add_bus_interface gpio0 $core' in tcl
        assert 'xilinx.com:interface:gpio_rtl:1.0' in tcl
        assert _legacy_backend().ip_vlnv == 'soclabs.org:user:nanosoc_chip:2.0'


# ===========================================================================
# rendered block-design TCL (create_bd_*)
# ===========================================================================
class TestBdTcl:
    def test_renders_and_creates_design(self):
        tcl = _mc_backend().render_bd_tcl()
        assert tcl is not None
        assert '{{' not in tcl and '{%' not in tcl
        assert 'create_bd_design nanosoc_multicore_vivado_wrapper_bd' in tcl

    def test_instantiates_packaged_ip_by_vlnv(self):
        tcl = _mc_backend().render_bd_tcl()
        assert ('create_bd_cell -type ip -vlnv '
                'soclabs.org:user:nanosoc_multicore_vivado_wrapper:1.0 '
                'nanosoc_0') in tcl

    def test_typed_bus_as_intf_port(self):
        tcl = _mc_backend().render_bd_tcl()
        assert ('create_bd_intf_port -mode Master -vlnv '
                'xilinx.com:interface:uart_rtl:1.0 uart') in tcl

    def test_raw_pins_as_scalar_ports(self):
        tcl = _mc_backend().render_bd_tcl()
        assert 'create_bd_port -dir I sys_fclk' in tcl
        assert 'create_bd_port -dir O -from 1 -to 0 phy_rmii_txd' in tcl
        assert 'create_bd_port -dir I swd_clk' in tcl

    def test_assign_bd_address_from_model(self):
        tcl = _mc_backend().render_bd_tcl()
        assert 'assign_bd_address -offset 0x00000000 -range 0x00010000' in tcl
        assert 'assign_bd_address -offset 0x41200000 -range 0x00010000' in tcl

    def test_validate_and_save(self):
        tcl = _mc_backend().render_bd_tcl()
        assert 'validate_bd_design' in tcl
        assert 'save_bd_design' in tcl


# ===========================================================================
# file emission + determinism
# ===========================================================================
class TestGenerate:
    def test_generate_writes_both(self, tmp_path):
        paths = _mc_backend().generate(tmp_path)
        names = {p.name for p in paths}
        assert 'nanosoc_multicore_vivado_wrapper_package_ip.tcl' in names
        assert 'nanosoc_multicore_vivado_wrapper_bd.tcl' in names
        for p in paths:
            assert p.exists() and p.read_text()

    def test_generate_content_matches_render(self, tmp_path):
        be = _mc_backend()
        be.generate(tmp_path)
        pkg = (tmp_path /
               'nanosoc_multicore_vivado_wrapper_package_ip.tcl').read_text()
        assert pkg == be.render_package_tcl()
        bd = (tmp_path /
              'nanosoc_multicore_vivado_wrapper_bd.tcl').read_text()
        assert bd == be.render_bd_tcl()

    def test_deterministic_rerun(self):
        a = _mc_backend()
        b = _mc_backend()
        assert a.render_package_tcl() == b.render_package_tcl()
        assert a.render_bd_tcl() == b.render_bd_tcl()

    def test_generate_package_only(self, tmp_path):
        paths = _mc_backend().generate(tmp_path, emit_bd=False)
        assert len(paths) == 1
        assert paths[0].name.endswith('_package_ip.tcl')
