# Copyright 2026, SoC Labs (www.soclabs.org)
"""One memory size, everywhere it is written down.

A memory's size reaches four outputs: the chip (u_system parameters), the
config package, the firmware files (linker MEMORY, nanosoc_memmap.h/.mk) and
the discovery ROM tables. Two ways they used to disagree, silently:

  * `--config-override IMEM_RAM_ADDR_W=12` reached the chip and the package but
    not the firmware backend: a 4 KB IMEM, a linker still at 16 KB, rc 0.
  * the discovery backend resolved a subsystem region's "2 ** $IMEM_RAM_ADDR_W"
    against the SUBSYSTEM's default, ignoring the value its parent instantiates
    it with: a 128 KB IMEM reported as 16 KB.
"""
import re

import pytest
import yaml

from soc_model.backends.discovery import SoCDiscoveryBackend
from soc_model.model import Instance, Module, Param

from tests.golden.test_discovery_yaml import _ic, _init, _tgt

pytestmark = pytest.mark.unit


def _tgt_size(rm, tname):
    for r in rm.registers:
        if r.desc.startswith('Target ') and f'({tname}) address window size' in r.desc:
            return r.reset_value
    raise AssertionError(f'no size register for {tname}')


# ---------------------------------------------------------------------------
# discovery: a child's region size follows the value it is instantiated with
# ---------------------------------------------------------------------------
def _parent_child(child_default=12, inst_value='$TOP_MEM_W', top_w=14):
    child = Module(name='child_ss')
    child.params = {'MEM_W': Param(name='MEM_W', type='int', default=child_default)}
    child.interconnects = [_ic('child_ic', gen=True,
                               targets=[_tgt('cmem', region_type='memory',
                                             phys_size='2 ** $MEM_W')],
                               initiators=[_init('cm', ['cmem'])])]
    top = Module(name='par')
    top.params = {'TOP_MEM_W': Param(name='TOP_MEM_W', type='int', default=top_w)}
    top.interconnects = [_ic('top_ic', gen=True,
                             targets=[_tgt('tmem', region_type='memory')],
                             initiators=[_init('tm', ['tmem'])])]
    params = {} if inst_value is None else {'MEM_W': inst_value}
    top.instances = [Instance(instance_name='u_child', module_name='child_ss',
                              resolved_module=child, params=params)]
    return top


def _child_size(top, tmp_path, **kw):
    results = SoCDiscoveryBackend(top, **kw).generate(tmp_path)
    rm = {n: r for n, r, _ in results}['child_ic']
    return _tgt_size(rm, 'cmem')


def test_discovery_follows_the_instance_override(tmp_path):
    assert _child_size(_parent_child(), tmp_path) == 2 ** 14


def test_discovery_without_override_keeps_the_child_default(tmp_path):
    assert _child_size(_parent_child(inst_value=None), tmp_path) == 2 ** 12


def test_discovery_follows_a_literal_instance_value(tmp_path):
    assert _child_size(_parent_child(inst_value=16), tmp_path) == 2 ** 16


def test_discovery_follows_config_override(tmp_path):
    assert _child_size(_parent_child(), tmp_path,
                       config_overrides={'TOP_MEM_W': 17}) == 2 ** 17


# ---------------------------------------------------------------------------
# firmware + chip + package + discovery under --config-override, end to end
# ---------------------------------------------------------------------------
def _yaml_edit(src, dst, name=None, **defaults):
    doc = yaml.safe_load(src.read_text())
    if name:
        doc['module']['name'] = name
    for k, v in defaults.items():
        doc['module']['params'][k]['default'] = v
    dst.write_text(yaml.safe_dump(doc, sort_keys=False))
    return dst


@pytest.mark.parametrize('override,imem', [(None, 0x4000), ('IMEM_RAM_ADDR_W=12', 0x1000),
                                           ('IMEM_RAM_ADDR_W=0x10', 0x10000)])
def test_config_override_reaches_the_firmware(fixtures_dir, tmp_path, monkeypatch,
                                              override, imem):
    """firmware_soc: IMEM_RAM_ADDR_W 14 (16 KB). An override must move the
    linker MEMORY, memmap.h and memmap.mk with it."""
    from tests.unit.test_main_cli import _run_main
    out = tmp_path / 'out'
    argv = [fixtures_dir / 'firmware_soc.yaml', '--build-dir', out, '--lib-dir', fixtures_dir]
    if override:
        argv += ['--config-override', override]
    assert _run_main(monkeypatch, argv) == 0
    fw = out / 'firmware'
    h = next(fw.glob('*_memmap.h')).read_text()
    mk = next(fw.glob('*_memmap.mk')).read_text()
    assert re.search(rf'_IMEM_0_SIZE\s+\(0x{imem:08X}UL\)', h), h
    assert re.search(rf'^IMEM_0_SIZE\s*=\s*0x{imem:X}$', mk, re.M), mk
    lds = [p.read_text() for p in fw.glob('*_memory.ld')]
    assert any(re.search(rf'IMEM_0\s.*LENGTH = 0x{imem:X}\b', t) for t in lds), lds


def test_config_override_chip_and_firmware_agree(fixtures_dir, tmp_path, monkeypatch):
    """With --system-yaml: the chip's IMEM width and the firmware's IMEM size
    are one number under an override."""
    from tests.unit.test_main_cli import _run_main
    top = fixtures_dir / 'firmware_soc.yaml'
    system = _yaml_edit(top, tmp_path / 'sys.yaml', name='firmware_soc_system')
    out = tmp_path / 'out'
    rc = _run_main(monkeypatch, [top, '--build-dir', out, '--lib-dir', fixtures_dir,
                                 '--system-yaml', system,
                                 '--config-override', 'IMEM_RAM_ADDR_W=12'])
    assert rc == 0
    chip = (out / 'rtl' / 'firmware_soc_chip.v').read_text()
    assert 'localparam IMEM_RAM_ADDR_W_CFG = 12;  // --config-override' in chip
    h = next((out / 'firmware').glob('*_memmap.h')).read_text()
    assert re.search(r'_IMEM_0_SIZE\s+\(0x00001000UL\)', h)
