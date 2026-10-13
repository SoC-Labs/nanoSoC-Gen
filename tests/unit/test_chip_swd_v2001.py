# Copyright 2026, SoC Labs (www.soclabs.org)
"""SoCChipBackend (soc_chip.v.j2): swd-type debug wiring + Verilog-2001 DMA ladder.

The chip wrapper is a plain ``.v`` (Vivado's IP packager parses it as
Verilog-2001), and it is the only place the die's debug path is bonded out.
Two invariants are pinned here, each with a golden snapshot:

  * ``swd``  — a system with an ``swd``-type interface (single-core nanoSoC:
    ``cpu_0_swd`` bound to ``CPU_0_SWDI/SWCLK/SWDO/SWDOEN``) wires the core's
    SWD straight to the ``swdio_*`` / ``swdclk_i`` pads, and the ``DMAC_*_TYPE``
    override on ``u_system`` is a ``localparam`` ladder, never ``pkg::``.
  * ``dap``  — a system with only ``dap_*`` wire interfaces (multicore-style
    SWJ-DAP boundary) is UNCHANGED: dap ports pass straight through and the
    legacy SWDIO pads stay inert. ``none`` (neither) is likewise unchanged.

The goldens for ``dap`` / ``none`` were captured from the generator BEFORE the
swd path was added (commit c2ca528), so they are the regression gate for the
multicore lineage; ``swd`` / ``both`` are the new behaviour.
"""

import re

import pytest

from soc_model.backends.chip import SoCChipBackend
from soc_model.model import Connection, Instance, Interface, Module, Param

from tests.conftest import SNAPSHOTS
from tests.helpers import assert_matches_snapshot

pytestmark = pytest.mark.unit

_SNAP = SNAPSHOTS / "chip"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def _gpio(name, w=16):
    return Interface(name=name, type='gpio', direction='inout',
                     params={'WIDTH': w}, desc=f'GPIO {name}')


def _dap_wires():
    return [
        Interface(name='dap_swclktck', type='wire', direction='in',  params={'WIDTH': 1}, desc='SWCLK'),
        Interface(name='dap_swditms',  type='wire', direction='in',  params={'WIDTH': 1}, desc='SWDI'),
        Interface(name='dap_swdo',     type='wire', direction='out', params={'WIDTH': 1}, desc='SWDO'),
        Interface(name='dap_swdoen',   type='wire', direction='out', params={'WIDTH': 1}, desc='SWDOEN'),
    ]


def _qspi_wires():
    return [
        Interface(name='qspi_sclk', type='wire', direction='out', params={'WIDTH': 1}, desc='SCLK'),
        Interface(name='qspi_csn',  type='wire', direction='out', params={'WIDTH': 1}, desc='CSn'),
        Interface(name='qspi_io_o', type='wire', direction='out', params={'WIDTH': 4}, desc='IO out'),
        Interface(name='qspi_io_i', type='wire', direction='in',  params={'WIDTH': 4}, desc='IO in'),
        Interface(name='qspi_io_e', type='wire', direction='out', params={'WIDTH': 4}, desc='IO oe'),
    ]


def _spi_wires():
    # upper-case, as nanosoc_m0_system.yaml declares them
    return [
        Interface(name='SPI_SCLK', type='wire', direction='out', params={'WIDTH': 1}, desc='SCLK'),
        Interface(name='SPI_SS',   type='wire', direction='out', params={'WIDTH': 1}, desc='SS'),
        Interface(name='SPI_MOSI', type='wire', direction='out', params={'WIDTH': 1}, desc='MOSI'),
        Interface(name='SPI_MISO', type='wire', direction='in',  params={'WIDTH': 1}, desc='MISO'),
    ]


def _system(dap=False, swd=False, dma=False, bind=True, swd_name='cpu_0_swd', qspi=False, spi=False):
    m = Module(name='fx_system', gen=True, desc='fixture')
    m.params = {'SYS_ADDR_W': Param(name='SYS_ADDR_W', type='int', default=32)}
    if dma:
        m.params['DMAC_0_TYPE'] = Param(name='DMAC_0_TYPE', type='int', default=0)
        m.params['DMAC_1_TYPE'] = Param(name='DMAC_1_TYPE', type='int', default=0)
    m.interfaces = [Interface(name='sys_clk', type='wire', direction='in', params={'WIDTH': 1})]
    if dap:
        m.interfaces += _dap_wires()
    if qspi:
        m.interfaces += _qspi_wires()
    if spi:
        m.interfaces += _spi_wires()
    if swd:
        m.interfaces.append(Interface(name=swd_name, type='swd', direction='receiver',
                                      params={}, desc='CPU SWD'))
    m.interfaces += [_gpio('P0'), _gpio('P1')]
    inst = Instance(instance_name='u_ss_cpu', module_name='ss_cpu')
    if swd and bind:
        inst.connections = [Connection(port='cpu_0_swdi',   conn='CPU_0_SWDI'),
                            Connection(port='cpu_0_swclk',  conn='CPU_0_SWCLK'),
                            Connection(port='cpu_0_swdo',   conn='CPU_0_SWDO'),
                            Connection(port='cpu_0_swdoen', conn='CPU_0_SWDOEN')]
    m.instances = [inst]
    return m


def _core(dma=False, dma0=0, dma1=0):
    c = Module(name='fx', gen=True)
    if dma:
        c.params = {'DMAC_0_TYPE': Param(name='DMAC_0_TYPE', type='int', default=dma0),
                    'DMAC_1_TYPE': Param(name='DMAC_1_TYPE', type='int', default=dma1)}
    return c


def _render(tmp_path, system, core=None, **kw):
    chip, _pads = SoCChipBackend(system, core, **kw).generate(tmp_path / 'rtl', tmp_path / 'flist')
    return chip.read_text()


# ---------------------------------------------------------------------------
# swd path: the die's debug pads are driven by the core
# ---------------------------------------------------------------------------
def test_swd_iface_wired_to_pads(tmp_path):
    v = _render(tmp_path, _system(swd=True, dma=True), _core(dma=True))
    # boundary nets declared with the core's conn: casing
    for net in ('CPU_0_SWDI', 'CPU_0_SWCLK', 'CPU_0_SWDO', 'CPU_0_SWDOEN'):
        assert re.search(rf'^\s*wire\s+{net};', v, re.M), net
        assert f'.{net}({net}),' in v
    # pads driven from / into the core, not tied
    assert 'assign CPU_0_SWCLK = swdclk_i;' in v
    assert 'assign CPU_0_SWDI  = swdio_i;' in v
    assert 'assign swdio_o     = CPU_0_SWDO;' in v
    assert 'assign swdio_e     = CPU_0_SWDOEN;' in v
    assert 'assign swdio_z     = !CPU_0_SWDOEN;' in v
    assert "assign swdio_o     = 1'b0;" not in v
    assert 'Legacy SWDIO/SWDCK pads' not in v


def test_swd_no_scope_operator_anywhere(tmp_path):
    """The chip is a .v: no SystemVerilog package-scope references."""
    v = _render(tmp_path, _system(swd=True, dma=True), _core(dma=True))
    assert '::' not in v


def test_swd_unbound_iface_falls_back_to_member_names(tmp_path):
    """An swd iface nobody binds still wires (member names, like system.py)."""
    v = _render(tmp_path, _system(swd=True, bind=False))
    assert 'assign swdio_o     = cpu_0_swdo;' in v
    assert '.cpu_0_swdi(cpu_0_swdi),' in v


def test_two_swd_ifaces_only_first_bonded(tmp_path, capsys):
    sysm = _system(swd=True, dma=False)
    sysm.interfaces.insert(2, Interface(name='cpu_1_swd', type='swd', direction='receiver', params={}))
    v = _render(tmp_path, sysm)
    assert v.count('= swdclk_i;') == 1
    assert 'WARNING: 2 swd interfaces' in capsys.readouterr().out
    # the second is declared, connected and held idle, never tied to the pads
    assert 'assign cpu_1_swclk = 1\'b0;' in v
    assert '.cpu_1_swdo(cpu_1_swdo),' in v


# ---------------------------------------------------------------------------
# DMA type: Verilog-2001 localparam ladder tracking the config package
# ---------------------------------------------------------------------------
def test_dma_ladder_default_from_yaml(tmp_path):
    v = _render(tmp_path, _system(swd=True, dma=True), _core(dma=True))
    assert '`ifdef DMAC_DMA350\n  localparam DMAC_0_TYPE_CFG = 2;  // DMA350 dual-port' in v
    assert '`elsif DMAC_0_PL230\n  localparam DMAC_0_TYPE_CFG = 1;  // PL230 single-port' in v
    assert '`else\n  localparam DMAC_0_TYPE_CFG = 0;  // None' in v
    assert '`ifdef DMAC_1_PL230\n  localparam DMAC_1_TYPE_CFG = 1;  // PL230 single-port' in v
    assert '.DMAC_0_TYPE(DMAC_0_TYPE_CFG),\n      .DMAC_1_TYPE(DMAC_1_TYPE_CFG)' in v
    assert 'Defaults are 0 (no DMA)' in v


def test_dma_ladder_default_tracks_top_yaml_and_override(tmp_path):
    # top YAML says PL230 on DMAC_0 -> the `else` arm carries 1
    v = _render(tmp_path, _system(swd=True, dma=True), _core(dma=True, dma0=1))
    assert '`else\n  localparam DMAC_0_TYPE_CFG = 1;  // PL230 single-port' in v
    assert 'Defaults are DMAC_0_TYPE=1, DMAC_1_TYPE=0' in v
    # --config-override wins over the YAML, exactly like the config package
    v = _render(tmp_path / 'ov', _system(swd=True, dma=True), _core(dma=True),
                config_overrides={'DMAC_0_TYPE': 2})
    assert '`else\n  localparam DMAC_0_TYPE_CFG = 2;  // DMA350 dual-port' in v


def test_dma_ladder_absent_when_system_has_no_dma_params(tmp_path):
    v = _render(tmp_path, _system(swd=True, dma=False))
    assert 'localparam' not in v
    assert 'fx_system u_system (' in v


# ---------------------------------------------------------------------------
# dap path (multicore lineage) untouched
# ---------------------------------------------------------------------------
def test_dap_path_passes_through_and_keeps_legacy_pads_inert(tmp_path):
    v = _render(tmp_path, _system(dap=True))
    for p in ('dap_swclktck', 'dap_swditms', 'dap_swdo', 'dap_swdoen'):
        assert f'.{p}({p}),' in v
    assert "assign swdio_o     = 1'b0;" in v
    assert "assign swdio_z     = 1'b1;" in v
    assert 'CPU_0_SWD' not in v and 'Serial-Wire Debug' not in v


def test_both_dap_and_swd_coexist(tmp_path):
    v = _render(tmp_path, _system(dap=True, swd=True, dma=True), _core(dma=True))
    assert '.dap_swclktck(dap_swclktck),' in v
    assert 'assign swdio_o     = CPU_0_SWDO;' in v


# ---------------------------------------------------------------------------
# qspi_ group: the flash controller's pins reach the die boundary
# ---------------------------------------------------------------------------
_QSPI_DECL = {'qspi_sclk': ('output', ''), 'qspi_csn': ('output', ''),
              'qspi_io_o': ('output', r'\[3:0\]'), 'qspi_io_i': ('input', r'\[3:0\]'),
              'qspi_io_e': ('output', r'\[3:0\]')}


def _render_both(tmp_path, system, core=None):
    chip, pads = SoCChipBackend(system, core).generate(tmp_path / 'rtl', tmp_path / 'flist')
    return chip.read_text(), pads.read_text()


def test_qspi_group_reaches_chip_and_pads_at_its_widths(tmp_path):
    """Every qspi_* wire is a chip port, a pads port and a pass-through, at
    its declared width. Before the group existed the chip dropped them
    silently, so the u_system connections are asserted, not just the ports."""
    chip, pads = _render_both(tmp_path, _system(swd=True, qspi=True))
    for name, (direction, rng) in _QSPI_DECL.items():
        decl = rf'^\s*{direction}\s+wire\s+{rng}\s*{name},'
        assert re.search(decl, chip, re.M), f'chip port {name}'
        assert re.search(decl, pads, re.M), f'pads port {name}'
        assert f'.{name}({name}),' in chip, f'u_system connection {name}'
        assert re.search(rf'\.{name}\({name}\),\s*// QSPI flash passthrough', pads), f'pads -> chip {name}'
    assert '::' not in chip


def test_qspi_group_does_not_capture_other_wires(tmp_path):
    """Only the qspi_ prefix joins the group: sys_clk and the dap_ wires keep
    their own handling, and a system with no qspi_ wires emits no QSPI text."""
    chip, _ = _render_both(tmp_path, _system(dap=True, qspi=True))
    # one comment per qspi_ port line, plus the u_system connection header -
    # a wire outside the group would add a line and change the count
    assert chip.count('QSPI flash boundary') == len(_QSPI_DECL) + 1
    assert re.search(r'^\s*input\s+wire\s+dap_swclktck,\s*// CoreSight', chip, re.M)
    chip, pads = _render_both(tmp_path / 'none', _system(swd=True))
    assert 'qspi' not in chip.lower() and 'qspi' not in pads.lower()


# ---------------------------------------------------------------------------
# spi_ group, and nothing dropped silently
# ---------------------------------------------------------------------------
_SPI_DECL = {'SPI_SCLK': 'output', 'SPI_SS': 'output', 'SPI_MOSI': 'output', 'SPI_MISO': 'input'}


def test_spi_group_reaches_chip_and_pads(tmp_path):
    """The PL022 master pins (upper-case in the system YAML) are chip ports,
    pads ports, u_system connections and pads->chip pass-throughs - and the
    QSPI group beside them is untouched ('qspi_' never matches 'spi_')."""
    chip, pads = _render_both(tmp_path, _system(swd=True, qspi=True, spi=True))
    for name, direction in _SPI_DECL.items():
        decl = rf'^\s*{direction}\s+wire\s+{name},\s*// SPI master'
        assert re.search(decl, chip, re.M), f'chip port {name}'
        assert re.search(decl, pads, re.M), f'pads port {name}'
        assert f'.{name}({name}),' in chip, f'u_system connection {name}'
        assert re.search(rf'\.{name}\({name}\),\s*// SPI master passthrough', pads), f'pads -> chip {name}'
    assert chip.count('// SPI master boundary (passed straight through)') == 1
    assert chip.count('QSPI flash boundary') == len(_QSPI_DECL) + 1


def test_unbonded_interface_is_reported(tmp_path, capsys):
    """An interface no chip rule handles is NAMED in a warning - the QSPI and
    SPI pins were lost at the chip boundary for months with no message."""
    sysm = _system(swd=True, qspi=True, spi=True)
    sysm.interfaces.append(Interface(name='TRACE_DATA', type='wire', direction='out',
                                     params={'WIDTH': 4}, desc='nothing bonds this'))
    _render_both(tmp_path, sysm)
    out = capsys.readouterr().out
    assert "interface 'TRACE_DATA' (type wire) is not bonded" in out
    # exactly the unhandled ones are named: TRACE_DATA, and the fixture's
    # lower-case sys_clk (not a template SYS_ port) - never a swd, gpio,
    # qspi_ or spi_ interface
    named = set(re.findall(r"interface '([^']+)' \(type \w+\) is not bonded", out))
    assert named == {'TRACE_DATA', 'sys_clk'}


def test_fully_bonded_system_is_silent(tmp_path, capsys):
    """sys_clk is the fixture's only wire outside a group and not a template
    SYS_ port, so it is reported; a system of only handled kinds is silent."""
    _render_both(tmp_path, _system(dap=True, swd=True, qspi=True, spi=True))
    assert "interface 'sys_clk'" in capsys.readouterr().out
    sysm = _system(dap=True, swd=True, qspi=True, spi=True)
    sysm.interfaces = [i for i in sysm.interfaces if i.name != 'sys_clk'] + [
        Interface(name='SYS_CLK', type='wire', direction='in', params={'WIDTH': 1})]
    _render_both(tmp_path / 'b', sysm)
    assert 'is not bonded to the chip boundary' not in capsys.readouterr().out


def test_template_sys_ports_constant_matches_template():
    """_TEMPLATE_SYS_PORTS is what the unbonded check trusts; it must be
    exactly the SYS_ ports soc_chip.v.j2 connects on u_system by name."""
    from soc_model.backends import chip as chipmod
    tpl = (chipmod._TEMPLATE_DIR / 'soc_chip.v.j2').read_text()
    in_template = set(re.findall(r'^\s*\.(SYS_\w+)\(', tpl, re.M))
    assert in_template == set(chipmod._TEMPLATE_SYS_PORTS)


# ---------------------------------------------------------------------------
# goldens
# ---------------------------------------------------------------------------
@pytest.mark.golden
@pytest.mark.parametrize('shape', ['dap', 'none', 'swd', 'both'])
def test_chip_golden(tmp_path, request, shape):
    kw = {'dap':  dict(dap=True,  swd=False, dma=False),
          'none': dict(dap=False, swd=False, dma=False),
          'swd':  dict(dap=False, swd=True,  dma=True),
          'both': dict(dap=True,  swd=True,  dma=True)}[shape]
    v = _render(tmp_path, _system(**kw), _core(dma=kw['dma']))
    assert_matches_snapshot(v, _SNAP / f'fx_chip_{shape}.v', request)
