# Copyright 2026, SoC Labs (www.soclabs.org)
"""SoCChipBackend: the chip passes the top YAML's memory widths down to u_system.

The system wrapper (soc_system.sv.j2) declares every memory address width
(BOOTROM_ADDR_W, IMEM_/DMEM_/SRAM_n_RAM_ADDR_W) with the SYSTEM YAML's
default, and passes it on to the core and each memory region. Before this the
chip set only the DMA types on u_system, so the memories the RTL elaborated
were the system YAML's size whatever the top YAML said -- while the firmware
backend (linker MEMORY, nanosoc_memmap.h) and the config package read the top
YAML. A variant that grew IMEM to 128 KB linked for 128 KB and simulated 16 KB.

Pinned here:
  * EVERY width the system declares is set on u_system, as a Verilog-2001
    localparam after the DMA types, even when the two YAMLs agree: a flow may
    compile a hand-written nanosoc_system.v whose defaults no comparison here
    can see, so the chip states the sizes itself;
  * the value: --config-override > top YAML > the system YAML's own default;
  * a width that is not an integer is an error (ChipParamError), never emitted;
  * any OTHER parameter the top and system YAMLs disagree on, which the chip
    does not pass, is an error unless allow_param_mismatch
    (--allow-param-mismatch), in which case it is reported;
  * end to end through ``python -m soc_model --system-yaml``: widths passed,
    rc 1 on a mismatch, rc 0 with --allow-param-mismatch.
"""

import re

import pytest
import yaml

import soc_model.backends.chip as chip_backend

# getattr: so that on a generator without it each test fails, not the module
ChipParamError = getattr(chip_backend, 'ChipParamError', type('ChipParamErrorMissing', (Exception,), {}))
from soc_model.model import Param

from tests.conftest import SNAPSHOTS
from tests.helpers import assert_matches_snapshot
from tests.unit.test_chip_swd_v2001 import _core, _render, _system

pytestmark = pytest.mark.unit

_SNAP = SNAPSHOTS / "chip"

_WIDTHS = ('BOOTROM_ADDR_W', 'IMEM_RAM_ADDR_W', 'DMEM_RAM_ADDR_W',
           'SRAM_0_RAM_ADDR_W', 'SRAM_1_RAM_ADDR_W')
_SYS_DEFAULTS = {'BOOTROM_ADDR_W': 11, 'IMEM_RAM_ADDR_W': 14, 'DMEM_RAM_ADDR_W': 14,
                 'SRAM_0_RAM_ADDR_W': 14, 'SRAM_1_RAM_ADDR_W': 14}


def _p(name, default, type_='int'):
    return Param(name=name, type=type_, default=default)


def _mem_system(dma=True, widths=_WIDTHS, **kw):
    """The swd fixture system plus nanosoc_system's memory widths."""
    m = _system(swd=True, dma=dma, **kw)
    for n in widths:
        m.params[n] = _p(n, _SYS_DEFAULTS[n])
    # a data width and an image name: never passed (not address widths)
    m.params['IMEM_RAM_DATA_W'] = _p('IMEM_RAM_DATA_W', 32)
    m.params['IMEM_MEM_FPGA_IMG'] = _p('IMEM_MEM_FPGA_IMG', 'image.hex', 'str')
    return m


def _mem_core(dma=True, **widths):
    c = _core(dma=dma)
    for n, v in {**_SYS_DEFAULTS, **widths}.items():
        c.params[n] = _p(n, v)
    c.params['IMEM_RAM_DATA_W'] = _p('IMEM_RAM_DATA_W', 32)
    c.params['IMEM_MEM_FPGA_IMG'] = _p('IMEM_MEM_FPGA_IMG', 'image.hex', 'str')
    return c


def _u_system_params(v):
    """[(name, value-expression)] of the #( ... ) list on u_system, in order."""
    m = re.search(r'\n  fx_system(?: #\((.*?)\n  \))? u_system \(', v, re.S)
    assert m, 'no u_system instance'
    return re.findall(r'\.(\w+)\((\w+)\)', m.group(1) or '')


def _passed(v):
    return {n for n, _ in _u_system_params(v)}


def _localparams(v):
    return {n: int(x) for n, x in re.findall(r'^\s*localparam (\w+)_CFG = (\d+);', v, re.M)}


# ---------------------------------------------------------------------------
# which parameters are memory widths
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('name,hit', [
    ('BOOTROM_ADDR_W', True), ('IMEM_RAM_ADDR_W', True), ('DMEM_RAM_ADDR_W', True),
    ('SRAM_0_RAM_ADDR_W', True), ('SRAM_1_RAM_ADDR_W', True), ('SS_IMEM_RAM_ADDR_W', True),
    ('IMEM_RAM_DATA_W', False), ('SYS_ADDR_W', False), ('APB_ADDR_W', False),
    ('RAM_ADDR_W', False), ('IMEM_MEM_FPGA_IMG', False), ('DMAC_0_TYPE', False),
])
def test_mem_width_param_names(name, hit):
    assert bool(chip_backend._MEM_WIDTH_PARAM_RE.match(name)) is hit


# ---------------------------------------------------------------------------
# every width, always
# ---------------------------------------------------------------------------
_ALL = [('DMAC_0_TYPE', 'DMAC_0_TYPE_CFG'), ('DMAC_1_TYPE', 'DMAC_1_TYPE_CFG')] + \
       [(n, f'{n}_CFG') for n in _WIDTHS]


def test_differing_widths_reach_u_system(tmp_path):
    v = _render(tmp_path, _mem_system(), _mem_core(IMEM_RAM_ADDR_W=17, DMEM_RAM_ADDR_W=16))
    assert _localparams(v) == {'DMAC_0_TYPE': 0, 'DMAC_1_TYPE': 0, 'BOOTROM_ADDR_W': 11,
                               'IMEM_RAM_ADDR_W': 17, 'DMEM_RAM_ADDR_W': 16,
                               'SRAM_0_RAM_ADDR_W': 14, 'SRAM_1_RAM_ADDR_W': 14}
    # DMA types first, then the widths in the system's declaration order
    assert _u_system_params(v) == _ALL
    assert 'localparam IMEM_RAM_ADDR_W_CFG = 17;  // fx YAML; fx_system YAML default 14' in v
    assert 'localparam SRAM_0_RAM_ADDR_W_CFG = 14;  // fx YAML\n' in v
    assert '::' not in v          # still a Verilog-2001 .v


def test_agreeing_widths_are_passed_too(tmp_path):
    """The YAMLs agree, but the module a flow compiles may not (a hand-written
    nanosoc_system.v carries its own defaults): the chip states every width."""
    v = _render(tmp_path, _mem_system(), _mem_core())
    assert _u_system_params(v) == _ALL
    assert {n: _localparams(v)[n] for n in _WIDTHS} == _SYS_DEFAULTS
    assert 'Memory Sizes' in v


def test_widths_without_dma_params(tmp_path):
    """A system with no DMAC_*_TYPE (DMA-250 SoCs) still gets its widths."""
    v = _render(tmp_path, _mem_system(dma=False), _mem_core(dma=False, IMEM_RAM_ADDR_W=17))
    assert _u_system_params(v) == [(n, f'{n}_CFG') for n in _WIDTHS]
    assert 'DMA Controller Type Selection' not in v
    # the "sourced from the config package" line would now be untrue
    assert 'DMA type and other design parameters are sourced' not in v


def test_width_as_param_reference_is_resolved(tmp_path):
    core = _mem_core()
    core.params['UPY_IMEM_W'] = _p('UPY_IMEM_W', 17)
    core.params['IMEM_RAM_ADDR_W'] = _p('IMEM_RAM_ADDR_W', '$UPY_IMEM_W')
    v = _render(tmp_path, _mem_system(), core)
    assert _localparams(v)['IMEM_RAM_ADDR_W'] == 17


def test_no_core_module_passes_the_system_defaults(tmp_path):
    v = _render(tmp_path, _mem_system(), None)
    assert _u_system_params(v) == _ALL
    assert 'localparam IMEM_RAM_ADDR_W_CFG = 14;  // fx_system YAML\n' in v


def test_width_the_system_does_not_declare_is_not_passed(tmp_path):
    v = _render(tmp_path, _mem_system(widths=('IMEM_RAM_ADDR_W',)),
                _mem_core(IMEM_RAM_ADDR_W=17, SRAM_1_RAM_ADDR_W=16))
    assert _passed(v) == {'DMAC_0_TYPE', 'DMAC_1_TYPE', 'IMEM_RAM_ADDR_W'}


def test_width_the_top_does_not_set_keeps_the_system_default(tmp_path):
    core = _mem_core(IMEM_RAM_ADDR_W=17)
    del core.params['DMEM_RAM_ADDR_W']
    v = _render(tmp_path, _mem_system(), core)
    assert _localparams(v)['DMEM_RAM_ADDR_W'] == 14
    assert 'localparam DMEM_RAM_ADDR_W_CFG = 14;  // fx_system YAML\n' in v


# ---------------------------------------------------------------------------
# precedence and refusals
# ---------------------------------------------------------------------------
def test_config_override_wins_over_the_top_yaml(tmp_path):
    v = _render(tmp_path, _mem_system(), _mem_core(IMEM_RAM_ADDR_W=17),
                config_overrides={'IMEM_RAM_ADDR_W': '15', 'DMEM_RAM_ADDR_W': 16})
    lp = _localparams(v)
    assert lp['IMEM_RAM_ADDR_W'] == 15 and lp['DMEM_RAM_ADDR_W'] == 16
    assert '// --config-override; fx_system YAML default 14' in v


def test_config_override_as_hex_text(tmp_path):
    v = _render(tmp_path, _mem_system(), _mem_core(),
                config_overrides={'IMEM_RAM_ADDR_W': '0x11'})
    assert _localparams(v)['IMEM_RAM_ADDR_W'] == 17


def test_non_integer_width_is_an_error(tmp_path):
    core = _mem_core()
    core.params['IMEM_RAM_ADDR_W'] = _p('IMEM_RAM_ADDR_W', '$NO_SUCH_PARAM')
    with pytest.raises(ChipParamError, match=r"fx\.IMEM_RAM_ADDR_W = '\$NO_SUCH_PARAM' is not an integer"):
        _render(tmp_path, _mem_system(), core)


# ---------------------------------------------------------------------------
# nothing else is dropped silently: a disagreement is an error
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('name,sys_v,top_v,shown', [
    ('BKPT', 4, 2, "BKPT: 2 in the top YAML ? (module fx), 4 in the system YAML ? "
                   "(module fx_system); the chip does not pass BKPT to u_system, so the RTL elaborates 4"),
    ('SYSTABLE_BASE', 0xF0000000, 0xE0000000,
     "SYSTABLE_BASE: 0xE0000000 in the top YAML ? (module fx), 0xF0000000 in the system YAML"),
    ('IMEM_MEM_FPGA_IMG', 'image.hex', 'upy.hex',
     "IMEM_MEM_FPGA_IMG: 'upy.hex' in the top YAML ? (module fx), 'image.hex' in the system YAML"),
])
def test_other_disagreeing_param_is_an_error(tmp_path, name, sys_v, top_v, shown):
    system, core = _mem_system(), _mem_core(IMEM_RAM_ADDR_W=17)
    t = 'str' if isinstance(sys_v, str) else 'int'
    system.params[name] = _p(name, sys_v, t)
    core.params[name] = _p(name, top_v, t)
    with pytest.raises(ChipParamError) as e:
        _render(tmp_path, system, core)
    assert shown in str(e.value) and '--allow-param-mismatch' in str(e.value)
    assert not (tmp_path / 'rtl' / 'fx_chip.v').exists()   # nothing half-written


def test_allow_param_mismatch_reports_and_renders(tmp_path, capsys):
    system, core = _mem_system(), _mem_core(IMEM_RAM_ADDR_W=17)
    system.params['BKPT'] = _p('BKPT', 4)
    core.params['BKPT'] = _p('BKPT', 2)
    v = _render(tmp_path, system, core, allow_param_mismatch=True)
    out = capsys.readouterr().out
    assert ("BKPT: 2 in the top YAML ? (module fx), 4 in the system YAML ? (module fx_system); "
            "the chip does not pass BKPT to u_system, so the RTL elaborates 4 "
            "(allowed by --allow-param-mismatch)") in out
    assert out.count('BKPT: 2 in the top YAML') == 1           # reported once
    assert _localparams(v)['IMEM_RAM_ADDR_W'] == 17


def test_config_override_creates_a_mismatch(tmp_path):
    """The two YAMLs agree on BKPT=4, but --config-override BKPT=2 puts 2 in the
    config package while the chip, which does not pass BKPT, leaves the RTL at
    4: the check compares the EFFECTIVE value."""
    system, core = _mem_system(), _mem_core()
    system.params['BKPT'] = _p('BKPT', 4)
    core.params['BKPT'] = _p('BKPT', 4)
    with pytest.raises(ChipParamError, match=r"BKPT: 2 \(--config-override\) in the top YAML"):
        _render(tmp_path, system, core, config_overrides={'BKPT': 2})


def test_config_override_can_resolve_a_mismatch(tmp_path):
    """...and an override that brings the top back to the system's value is fine."""
    system, core = _mem_system(), _mem_core()
    system.params['BKPT'] = _p('BKPT', 4)
    core.params['BKPT'] = _p('BKPT', 2)
    _render(tmp_path, system, core, config_overrides={'BKPT': '4'})


def test_check_params_writes_nothing(tmp_path):
    system, core = _mem_system(), _mem_core()
    system.params['BKPT'] = _p('BKPT', 4)
    core.params['BKPT'] = _p('BKPT', 2)
    b = chip_backend.SoCChipBackend(system, core,
                                    yaml_paths={'core': '/p/top.yaml', 'system': '/p/sys.yaml'})
    with pytest.raises(ChipParamError, match=r"top YAML /p/top\.yaml \(module fx\).*system YAML /p/sys\.yaml"):
        b.check_params()
    assert not any(tmp_path.iterdir())


def test_agreeing_yamls_are_silent(tmp_path, capsys):
    _render(tmp_path, _mem_system(), _mem_core())
    assert 'does not pass' not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# golden: the variant chip
# ---------------------------------------------------------------------------
@pytest.mark.golden
def test_chip_mem_widths_golden(tmp_path, request):
    v = _render(tmp_path, _mem_system(), _mem_core(IMEM_RAM_ADDR_W=17, DMEM_RAM_ADDR_W=16))
    assert_matches_snapshot(v, _SNAP / 'fx_chip_swd_memwidths.v', request)


@pytest.mark.golden
def test_chip_mem_widths_default_golden(tmp_path, request):
    """The YAMLs agree: the chip still states every width."""
    v = _render(tmp_path, _mem_system(), _mem_core())
    assert_matches_snapshot(v, _SNAP / 'fx_chip_swd_memwidths_default.v', request)


# ---------------------------------------------------------------------------
# end to end: python -m soc_model <top> --system-yaml <system>
# ---------------------------------------------------------------------------
def _yaml_variant(src, dst, name=None, **defaults):
    doc = yaml.safe_load(src.read_text())
    if name:
        doc['module']['name'] = name
    for k, v in defaults.items():
        doc['module']['params'][k]['default'] = v
    dst.write_text(yaml.safe_dump(doc, sort_keys=False))
    return dst


@pytest.mark.parametrize('top_w', [16, 12])
def test_cli_top_yaml_width_reaches_the_chip(fixtures_dir, tmp_path, monkeypatch, top_w):
    """minimal_soc's SRAM_RAM_ADDR_W is 12. The top YAML sets `top_w`; the
    system YAML keeps 12. The generated chip sets the top's value, whether or
    not the two agree."""
    from tests.unit.test_main_cli import _run_main
    top = _yaml_variant(fixtures_dir / 'minimal_soc.yaml', tmp_path / 'top.yaml',
                        SRAM_RAM_ADDR_W=top_w)
    # minimal_soc sizes sram_0 as a constant 0x1000 (2**12). At another width
    # that table is wrong, and the discovery size guard fails the run on it;
    # size it from the width, as a real SoC does.
    doc = yaml.safe_load(top.read_text())
    doc['module']['interconnects'][0]['targets'][0]['phys_size'] = '2 ** $SRAM_RAM_ADDR_W'
    top.write_text(yaml.safe_dump(doc, sort_keys=False))
    system = _yaml_variant(fixtures_dir / 'minimal_soc.yaml', tmp_path / 'sys.yaml',
                           name='minimal_soc_system')
    out = tmp_path / 'out'
    rc = _run_main(monkeypatch, [top, '--build-dir', out, '--lib-dir', fixtures_dir,
                                 '--system-yaml', system])
    assert rc == 0
    chip = (out / 'rtl' / 'minimal_soc_chip.v').read_text()
    assert '.SRAM_RAM_ADDR_W(SRAM_RAM_ADDR_W_CFG)' in chip
    assert f'localparam SRAM_RAM_ADDR_W_CFG = {top_w};' in chip


@pytest.mark.parametrize('flag,rc_want', [(None, 1), ('--allow-param-mismatch', 0)])
def test_cli_param_mismatch_fails_the_run(fixtures_dir, tmp_path, monkeypatch, capsys,
                                         flag, rc_want):
    """The top YAML's SYS_CLK_FREQ_HZ disagrees with the system YAML's, and the
    chip does not pass it: rc 1, unless --allow-param-mismatch."""
    from tests.unit.test_main_cli import _run_main
    top = _yaml_variant(fixtures_dir / 'minimal_soc.yaml', tmp_path / 'top.yaml',
                        SYS_CLK_FREQ_HZ=25000000)
    system = _yaml_variant(fixtures_dir / 'minimal_soc.yaml', tmp_path / 'sys.yaml',
                           name='minimal_soc_system')
    out = tmp_path / 'out'
    argv = [top, '--build-dir', out, '--lib-dir', fixtures_dir, '--system-yaml', system]
    rc = _run_main(monkeypatch, argv + ([flag] if flag else []))
    assert rc == rc_want
    msg = capsys.readouterr()
    assert (f"SYS_CLK_FREQ_HZ: 0x017D7840 in the top YAML {top} (module minimal_soc), "
            f"0x02FAF080 in the system YAML {system} (module minimal_soc_system)") \
        in (msg.err if rc_want else msg.out)


def _tree(d):
    return {p.relative_to(d): p.read_bytes() for p in sorted(d.rglob('*')) if p.is_file()}


@pytest.mark.parametrize('how', ['yaml', 'override'])
def test_cli_refused_run_leaves_the_build_dir_untouched(fixtures_dir, tmp_path, monkeypatch,
                                                        capsys, how):
    """A good run fills out/. A second run whose top YAML (or a
    --config-override) now disagrees with the system YAML is refused BEFORE
    any backend writes: every file in out/ is byte-for-byte what it was, and
    no file is added. Before, the reports, the toplevel RTL and the firmware
    were rewritten and only then the chip refused."""
    from tests.unit.test_main_cli import _run_main
    system = _yaml_variant(fixtures_dir / 'minimal_soc.yaml', tmp_path / 'sys.yaml',
                           name='minimal_soc_system')
    out = tmp_path / 'out'
    base = [fixtures_dir / 'minimal_soc.yaml', '--build-dir', out, '--lib-dir', fixtures_dir,
            '--system-yaml', system]
    assert _run_main(monkeypatch, base) == 0
    before = _tree(out)
    assert before
    if how == 'yaml':
        top = _yaml_variant(fixtures_dir / 'minimal_soc.yaml', tmp_path / 'top.yaml',
                            SYS_CLK_FREQ_HZ=25000000)
        argv = [top] + base[1:]
    else:
        argv = base + ['--config-override', 'SYS_CLK_FREQ_HZ=25000000']
    capsys.readouterr()
    assert _run_main(monkeypatch, argv) == 1
    assert 'Nothing was generated.' in capsys.readouterr().err
    assert _tree(out) == before
