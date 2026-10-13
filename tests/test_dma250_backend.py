"""Unit tests for the DMA-250 render backend (SoCDma250Backend).

Run from the nanosoc_gen root:  python3 -m pytest tests/test_dma250_backend.py -v
"""
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from soc_model.backends.dma250 import (          # noqa: E402
    SoCDma250Backend, _flatten_design_vc, _config_name,
    _DMA250_DEFAULT_RELPATH,
)


def _fake_top(module_names):
    """Build a duck-typed top module with the given instance module names."""
    insts = [types.SimpleNamespace(module_name=mn, resolved_module=None)
             for mn in module_names]
    return types.SimpleNamespace(instances=insts)


# --------------------------------------------------------------------------
# pure-function unit tests (no IP library needed)
# --------------------------------------------------------------------------

def test_config_name():
    assert _config_name('cfg_min.yaml') == 'CFG_MIN'
    assert _config_name('cfg_def.yaml') == 'CFG_DEF'


def test_detects_dma250_instance():
    be = SoCDma250Backend(_fake_top(['dma_250_ahb', 'eth_ss_ahb']))
    assert be._find_dma250_instances() == ['dma_250_ahb']


def test_no_dma250_instance():
    be = SoCDma250Backend(_fake_top(['dma_230_ahb', 'cc_periph']))
    assert be._find_dma250_instances() == []


def test_walk_recurses_submodules():
    child = _fake_top(['dma_250_ahb'])
    parent_inst = types.SimpleNamespace(module_name='sub_ss', resolved_module=child)
    top = types.SimpleNamespace(instances=[parent_inst])
    be = SoCDma250Backend(top)
    assert be._find_dma250_instances() == ['dma_250_ahb']


def test_generate_all_noop_without_instance(tmp_path):
    be = SoCDma250Backend(_fake_top([]))
    assert be.generate_all(tmp_path) == []


def test_flatten_design_vc_resolves_relative_and_nested(tmp_path):
    # layout: top/verilog/design.vc -F ../../sub/sub.vc ; files relative to each .vc
    (tmp_path / 'top' / 'verilog').mkdir(parents=True)
    (tmp_path / 'shared').mkdir()
    (tmp_path / 'sub' / 'verilog').mkdir(parents=True)
    (tmp_path / 'shared' / 'pkg.sv').write_text('// pkg')
    (tmp_path / 'sub' / 'verilog' / 'child.sv').write_text('// child')
    (tmp_path / 'top' / 'verilog' / 'top.sv').write_text('// top')
    (tmp_path / 'sub' / 'verilog' / 'sub.vc').write_text('child.sv\n')
    design = tmp_path / 'top' / 'verilog' / 'design.vc'
    design.write_text(
        "// comment\n"
        "+incdir+../../shared\n"
        "../../shared/pkg.sv\n"
        "-F ../../sub/verilog/sub.vc\n"
        "top.sv\n"
    )
    out = []
    _flatten_design_vc(design, out, set())
    # every file entry is absolute and exists; +incdir resolved to abs dir
    files = [l for l in out if not l.startswith(('+', '-'))]
    assert all(os.path.isabs(f) and os.path.exists(f) for f in files)
    assert any(f.endswith('shared/pkg.sv') for f in files)
    assert any(f.endswith('sub/verilog/child.sv') for f in files)   # nested -F pulled in
    assert any(f.endswith('top/verilog/top.sv') for f in files)
    assert any(l.startswith('+incdir+') and os.path.isabs(l[len('+incdir+'):]) for l in out)


# --------------------------------------------------------------------------
# integration test: actually render via Arm's generate (gated on the IP lib)
# --------------------------------------------------------------------------

def _dma250_dir():
    arm = os.environ.get('ARM_IP_LIBRARY_PATH')
    if not arm:
        return None
    d = Path(arm) / _DMA250_DEFAULT_RELPATH
    return d if (d / 'generate').exists() else None


@pytest.mark.tooling   # runs Arm's DMA-250 `generate` (cwd inside the IP tree)
@pytest.mark.skipif(_dma250_dir() is None,
                    reason="DMA-250 IP library not available")
def test_render_produces_flist_and_rtl(tmp_path):
    be = SoCDma250Backend(_fake_top(['dma_250_ahb']),
                          arm_ip_library_path=os.environ.get('ARM_IP_LIBRARY_PATH'))
    res = be.generate_all(tmp_path)
    assert res and res[0][0] == 'dma250_CFG_MIN'
    top_sv = (tmp_path / 'rtl' / 'dma250' / 'rendered_CFG_MIN' / 'logical'
              / 'dma250_top_CFG_MIN' / 'verilog' / 'dma250_top_CFG_MIN.sv')
    flist = tmp_path / 'flist' / 'dma250_rendered.flist'
    assert top_sv.exists()
    assert flist.exists()
    entries = [l for l in flist.read_text().splitlines()
               if l and not l.startswith('//')]
    assert len(entries) > 20
    for e in entries:
        if e.startswith('+incdir+'):
            assert os.path.isdir(e[len('+incdir+'):])
        elif not e.startswith(('+', '-')):
            assert os.path.isfile(e), f"missing rendered file: {e}"
