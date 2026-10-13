# Copyright 2026, SoC Labs (www.soclabs.org)
"""Tests for the opt-in YAML desugar pre-pass (roadmap doc 05).

v1 = Feature 1, presets / ``uses:``.  The load-bearing property under test is
NON-BREAKAGE: the pass is a strict identity on any module with no sugar key, so
existing YAML is byte-for-byte unchanged.  The whole-suite golden tests (and
the real-SoC regen-diff in the commit gate) cover that end-to-end; here we pin
the unit-level guarantees + the merge semantics + the parser wiring.
"""
from pathlib import Path

import pytest

from soc_model.expand import (
    PresetLibrary,
    expand_module_dict,
    make_expander,
    _deep_merge,
    _parse_size,
    _align_up,
    _pack_interconnect,
)
from soc_model.parser import SoCParser

pytestmark = pytest.mark.unit

_BUNDLED_PRESETS = Path(__file__).resolve().parents[2] / 'lib' / 'presets'
_EXPAND_FIX = Path(__file__).resolve().parents[1] / 'fixtures' / 'expand'


def _lib(d=None):
    return PresetLibrary(d or {})


# ---------------------------------------------------------------------------
# Non-breakage: identity on sugar-free input
# ---------------------------------------------------------------------------
def test_identity_returns_same_object_when_no_sugar():
    data = {'module': {'name': 'm', 'params': {'A': 1},
                       'interfaces': [{'name': 'x'}]}}
    out = expand_module_dict(data, presets=_lib())
    assert out is data            # strict identity: not even copied


def test_identity_non_module_dict():
    data = {'register_map': {'name': 'r'}}
    assert expand_module_dict(data, presets=_lib()) is data


def test_identity_empty_and_non_dict():
    assert expand_module_dict({}, presets=_lib()) == {}
    assert expand_module_dict(None, presets=_lib()) is None


def test_parser_without_expander_is_unchanged():
    """Default SoCParser (expander=None) is a no-op even on a uses: module."""
    p = SoCParser(str(_EXPAND_FIX), [str(_EXPAND_FIX)])   # no expander
    raw = p.parse_module('tiny_uses_region')
    assert 'uses' in raw['module']            # untouched — sugar passed through
    assert 'interfaces' not in raw['module']  # preset NOT merged


# ---------------------------------------------------------------------------
# Merge semantics
# ---------------------------------------------------------------------------
def test_deep_merge_dict_overlay_wins_and_recurses():
    base = {'params': {'A': 1, 'B': 2}, 'gen': False}
    overlay = {'params': {'B': 9, 'C': 3}, 'gen': True}
    out = _deep_merge(base, overlay)
    assert out == {'params': {'A': 1, 'B': 9, 'C': 3}, 'gen': True}
    assert base == {'params': {'A': 1, 'B': 2}, 'gen': False}  # base not mutated


def test_merge_lists_identity_replace_then_append():
    base = {'ifaces': [{'name': 'x', 'w': 1}, {'name': 'y', 'w': 2}]}
    overlay = {'ifaces': [{'name': 'x', 'w': 9}, {'name': 'z', 'w': 3}]}
    out = _deep_merge(base, overlay)
    assert out['ifaces'] == [
        {'name': 'x', 'w': 9},   # identity match -> replaced
        {'name': 'y', 'w': 2},   # untouched
        {'name': 'z', 'w': 3},   # new -> appended (order preserved)
    ]


def test_merge_lists_without_identity_appends():
    out = _deep_merge({'l': [1, 2]}, {'l': [3]})
    assert out['l'] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Feature 1 — uses:
# ---------------------------------------------------------------------------
def _bundled():
    lib = PresetLibrary.from_dirs([_BUNDLED_PRESETS])
    assert lib.get('ahb_target_region') is not None, "bundled preset missing"
    return lib


def test_bundled_preset_loads():
    lib = _bundled()
    p = lib.get('ahb_target_region')
    assert 'name' not in p                       # preset's own name is stripped
    assert set(p['params']) == {'SYS_ADDR_W', 'SYS_DATA_W', 'RAM_ADDR_W', 'RAM_DATA_W'}
    assert [i['name'] for i in p['interfaces']] == ['HCLK', 'HRESETn', 'ahb_slave']


def test_uses_merges_preset_under_module():
    data = {'module': {'name': 'r', 'gen': False, 'uses': ['ahb_target_region'],
                       'srams': [{'name': 'u_sram', 'addr_width': '$RAM_ADDR_W'}]}}
    out = expand_module_dict(data, presets=_bundled())
    mod = out['module']
    assert data['module'].get('uses') == ['ahb_target_region']  # input not mutated
    assert 'uses' not in mod                                    # sugar consumed
    assert set(mod['params']) == {'SYS_ADDR_W', 'SYS_DATA_W', 'RAM_ADDR_W', 'RAM_DATA_W'}
    assert [i['name'] for i in mod['interfaces']] == ['HCLK', 'HRESETn', 'ahb_slave']
    assert mod['srams'][0]['name'] == 'u_sram'   # module's own content preserved
    assert mod['name'] == 'r' and mod['gen'] is False


def test_uses_module_keys_win_on_conflict():
    data = {'module': {'name': 'r', 'uses': ['ahb_target_region'],
                       # override one inherited param value + replace one interface
                       'params': {'RAM_ADDR_W': {'type': 'int', 'default': 99}},
                       'interfaces': [{'name': 'ahb_slave', 'type': 'ahb',
                                       'direction': 'target', 'desc': 'OVERRIDDEN'}]}}
    mod = expand_module_dict(data, presets=_bundled())['module']
    assert mod['params']['RAM_ADDR_W']['default'] == 99           # module wins (dict recurse)
    assert mod['params']['SYS_ADDR_W']['default'] == 32           # preset value kept
    ahb = [i for i in mod['interfaces'] if i['name'] == 'ahb_slave'][0]
    assert ahb['desc'] == 'OVERRIDDEN'                            # module wins (list replace)
    assert [i['name'] for i in mod['interfaces']] == ['HCLK', 'HRESETn', 'ahb_slave']


def test_uses_idempotent():
    data = {'module': {'name': 'r', 'uses': ['ahb_target_region'], 'srams': []}}
    once = expand_module_dict(data, presets=_bundled())
    twice = expand_module_dict(once, presets=_bundled())   # no 'uses' now -> identity
    assert twice is once
    assert expand_module_dict(once, presets=_bundled()) == once


def test_uses_string_form():
    data = {'module': {'name': 'r', 'uses': 'ahb_target_region'}}
    mod = expand_module_dict(data, presets=_bundled())['module']
    assert [i['name'] for i in mod['interfaces']] == ['HCLK', 'HRESETn', 'ahb_slave']


def test_unknown_preset_raises():
    data = {'module': {'name': 'r', 'uses': ['does_not_exist']}}
    with pytest.raises(ValueError, match="unknown preset 'does_not_exist'"):
        expand_module_dict(data, presets=_bundled())


def test_recursive_preset_rejected():
    lib = PresetLibrary({'a': {'uses': ['b'], 'params': {}}})
    data = {'module': {'name': 'r', 'uses': ['a']}}
    with pytest.raises(ValueError, match="recursive presets"):
        expand_module_dict(data, presets=lib)


# ---------------------------------------------------------------------------
# Parser wiring (end-to-end through SoCParser)
# ---------------------------------------------------------------------------
def test_parser_with_expander_applies_uses():
    p = SoCParser(str(_EXPAND_FIX), [str(_EXPAND_FIX)],
                  expander=make_expander(_bundled()))
    mod = p.parse_module('tiny_uses_region')['module']
    assert 'uses' not in mod
    assert [i['name'] for i in mod['interfaces']] == ['HCLK', 'HRESETn', 'ahb_slave']
    assert mod['srams'][0]['name'] == 'u_sram'
    assert set(mod['params']) == {'SYS_ADDR_W', 'SYS_DATA_W', 'RAM_ADDR_W', 'RAM_DATA_W'}


# ---------------------------------------------------------------------------
# Feature 2 — address auto-assignment (auto_base + size shorthand)
# ---------------------------------------------------------------------------
def test_parse_size_forms():
    assert _parse_size(4096) == 4096
    assert _parse_size("0x20000000") == 0x20000000
    assert _parse_size("16M") == 16 * 1024 * 1024
    assert _parse_size("64K") == 64 * 1024
    assert _parse_size("512M") == 512 * 1024 * 1024
    assert _parse_size("4G") == 4 * 1024 ** 3
    assert _parse_size("256") == 256


def test_parse_size_rejects_params_and_junk():
    for bad in ("$QSPI_FLASH_ADDR_W", "2 ** $N", "16MB", "", True):
        with pytest.raises(ValueError):
            _parse_size(bad)


def test_align_up():
    M = 1024 * 1024
    assert _align_up(0x20000000, 16 * M) == 0x20000000
    assert _align_up(0x20000001, 16 * M) == 0x21000000
    assert _align_up(0, 16 * M) == 0


def _band_ic():
    """The real multicore peripheral band as auto_base sugar (doc 05 §5.3)."""
    return {
        'name': 'multicore_interconnect',
        'gen': True,
        'auto_base': {'from': '0x20000000', 'align': '16M'},
        'targets': [
            {'name': 'eth_ss_slave', 'base': '0x00000000', 'size': '512M'},   # pinned
            {'name': 'dma_230_0',      'size': '16M'},
            {'name': 'qspi_flash_0',   'size': '16M'},
            {'name': 'phc_0',          'size': '16M'},
            {'name': 'ipc_mailbox_0',  'size': '16M'},
            {'name': 'qspi_flash_xip', 'size': '64M'},   # spans 0x24..0x27, eats the gap
            {'name': 'cc_periph_0',    'size': '16M'},    # resumes at 0x28M
            {'name': 'cpu1_remap_0',   'size': '16M'},
            {'name': 'reset_ctrl_0',   'size': '16M'},
            {'name': 'evt_route_0',    'size': '16M'},
            {'name': 'cpu_ss_1_slave', 'base': '0x80000000', 'size': '512M'},  # pinned
        ],
    }


def test_auto_base_reproduces_real_address_map():
    """auto_base packs the band to the exact hand-written bases incl. the 64M
    XiP aperture consuming the 0x25..0x27 gap so the next auto lands at 0x28M."""
    out = _pack_interconnect(_band_ic())
    base = {t['name']: t['base'] for t in out['targets']}
    assert base == {
        'eth_ss_slave':  0x00000000,   # pinned, untouched
        'dma_230_0':     0x20000000,
        'qspi_flash_0':  0x21000000,
        'phc_0':         0x22000000,
        'ipc_mailbox_0': 0x23000000,
        'qspi_flash_xip': 0x24000000,  # 64M
        'cc_periph_0':   0x28000000,   # <- past the 64M hole
        'cpu1_remap_0':  0x29000000,
        'reset_ctrl_0':  0x2A000000,
        'evt_route_0':   0x2B000000,
        'cpu_ss_1_slave': 0x80000000,  # pinned, untouched
    }
    # sizes normalised to ints; auto_base sugar consumed
    assert all(isinstance(t['size'], int) for t in out['targets'])
    assert 'auto_base' not in out


def test_auto_base_overlap_with_pinned_is_an_error():
    ic = {
        'name': 'ic', 'auto_base': {'from': '0x20000000', 'align': '16M'},
        'targets': [
            {'name': 'pinned_here', 'base': '0x20000000', 'size': '16M'},
            {'name': 'auto_collides', 'size': '16M'},   # would land on 0x20000000
        ],
    }
    with pytest.raises(ValueError, match="overlaps pinned window"):
        _pack_interconnect(ic)


def test_auto_base_via_expand_module_dict_and_input_not_mutated():
    data = {'module': {'name': 'soc', 'interconnects': [_band_ic()]}}
    out = expand_module_dict(data, presets=_lib())
    assert out is not data                               # transformed (sugar present)
    assert 'auto_base' in data['module']['interconnects'][0]   # input untouched
    ic = out['module']['interconnects'][0]
    assert 'auto_base' not in ic
    assert {t['name']: t['base'] for t in ic['targets']}['cc_periph_0'] == 0x28000000


def test_no_auto_base_is_identity():
    """An interconnect module with NO auto_base (and no uses) is untouched."""
    data = {'module': {'name': 'soc', 'interconnects': [
        {'name': 'ic', 'targets': [{'name': 't', 'base': 0x1000, 'size': 0x1000}]}]}}
    assert expand_module_dict(data, presets=_lib()) is data
