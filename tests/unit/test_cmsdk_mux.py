# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for ``impl: cmsdk_mux`` (soc_model/backends/cmsdk_mux.py).

Covers: the schema default, the derived topology and decoders, every
fail-loud limit, the deadlock rule (with a negative control on a hand-broken
plan), the validator hook, and what the AHB backend writes (and does not
write, and never runs) for a cmsdk_mux interconnect.
"""

import copy
import re
import subprocess

import pytest

from soc_model.backends import cmsdk_mux
from soc_model.backends.ahb import SoCAhbBackend
from soc_model.backends.cmsdk_mux import CmsdkMuxError, plan_cmsdk_mux
from soc_model.model import InterconnectInitiator, InterconnectInitiatorTarget, InterconnectTarget
from soc_model.validator import SoCValidator

from tests.helpers import build_module_from_yaml

pytestmark = pytest.mark.unit

# The vendor name is assembled from pieces, so this file carries no notice text
# of its own (the QuickStart ci/no-arm-banner.sh scans every file of the tree).
_CO = 'A' + 'rm'
ARM_BANNER = re.compile(_CO + r' Limited|' + _CO.upper() + r' Limited|COPYRIGHT[^\n]*\bA[Rr][Mm]\b')

EXISTING_FIXTURES = ["minimal_soc", "two_region_soc", "apb_bridge_soc",
                     "passthrough_soc", "firmware_soc", "firmware_multicore_soc"]


def _ic(top, name=None):
    ics = top.interconnects
    return ics[0] if name is None else next(i for i in ics if i.name == name)


@pytest.fixture
def mux_top(fixtures_dir):
    return build_module_from_yaml(fixtures_dir, "cmsdk_mux_soc.yaml")


def _shared_top(fixtures_dir, n_init=4, casc_differ=False):
    """two_region_soc switched to cmsdk_mux with n_init initiators. dma_1
    sees only sram_1, so the decoder must be keyed by HMASTER; initiators from
    the third on (the cascade) share a map unless casc_differ."""
    top = build_module_from_yaml(fixtures_dir, "two_region_soc.yaml")
    ic = _ic(top)
    ic.impl = 'cmsdk_mux'
    for k in range(1, n_init):
        tgts = ['sram_1'] if k == 1 else ['sram_0', 'sram_1']
        if casc_differ and k == n_init - 1:
            tgts = ['sram_0']
        ic.initiators.append(InterconnectInitiator(
            name=f'dma_{k}',
            targets=[InterconnectInitiatorTarget(name=t) for t in tgts]))
    return top


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fixture", EXISTING_FIXTURES)
def test_default_impl_is_busmatrix(fixture, fixtures_dir):
    top = build_module_from_yaml(fixtures_dir, f"{fixture}.yaml")
    for ic in top.interconnects:
        assert ic.impl == 'busmatrix'
        assert cmsdk_mux.ic_impl(ic) == 'busmatrix'


def test_yaml_impl_is_parsed(mux_top):
    assert _ic(mux_top).impl == 'cmsdk_mux'


# ---------------------------------------------------------------------------
# Topology and decoders
# ---------------------------------------------------------------------------
def test_split_topology(mux_top):
    plan = plan_cmsdk_mux(_ic(mux_top))
    inits = {i['name']: i for i in plan['initiators']}
    assert inits['cpu_m']['front'] and inits['cpu_m']['private'] == ['SYSTEM']
    assert not inits['ext_s']['front']
    assert plan['shared_targets'] == ['ROM_0', 'RAM_0', 'DAT_0']
    assert plan['slave_ports'][-1] == 'DEFAULT'
    assert not plan['cascade'] and not plan['keyed']
    assert plan['dec_lsb'] == 27 and plan['dec_w'] == 5
    assert plan['remap_bits'] == [0]


def test_decoder_comes_from_effective_maps(mux_top):
    plan = plan_cmsdk_mux(_ic(mux_top))
    ex = plan['shared_exprs']
    assert ex['ROM_0'] == "(b_dec_a == 5'h0) & ~REMAP[0] |\n      (b_dec_a == 5'h1)"
    assert ex['RAM_0'] == "(b_dec_a == 5'h0) & REMAP[0] |\n      (b_dec_a == 5'h2)"
    assert ex['DAT_0'] == "(b_dec_a == 5'h3)"
    cpu = next(i for i in plan['initiators'] if i['name'] == 'cpu_m')
    assert cpu['front_exprs'] == {'SYSTEM': "(fe_CPU_M_dec_a >= 5'h4)"}


def test_decoder_follows_a_moved_region(mux_top):
    """Change the map in the YAML model and the decoder changes with it:
    the decode is derived, not written down."""
    ic = _ic(mux_top)
    dat = next(t for t in ic.targets if t.name == 'dat_0')
    dat.base = 0x1C000000
    dat.size = 0x04000000
    plan = plan_cmsdk_mux(ic)
    assert plan['dec_lsb'] == 26
    assert plan['shared_exprs']['DAT_0'] == "(b_dec_a == 6'h7)"


def test_cascade_and_keyed_decoder(fixtures_dir):
    plan = plan_cmsdk_mux(_ic(_shared_top(fixtures_dir, 4)))
    assert plan['cascade'] and plan['keyed'] and plan['mst_w'] == 2
    assert [i['name'] for i in plan['top_ports']] == ['cpu_m', 'dma_1']
    assert [i['name'] for i in plan['casc_ports']] == ['dma_2', 'dma_3']
    # keyed by the TOP-LEVEL port: sram_0 is seen on S0 (cpu_m) and S2 (the
    # cascade), not on S1 (dma_1); sram_1 by every port.
    assert "((b_hmaster == 2'd0) | (b_hmaster == 2'd2))" in plan['shared_exprs']['SRAM_0']
    assert 'b_hmaster' not in plan['shared_exprs']['SRAM_1']


def test_cascaded_initiators_must_share_a_map(fixtures_dir):
    """The cascaded mux's HMASTER does not follow a transfer held in the top
    mux's port-2 buffer, so different maps behind the cascade are refused."""
    with pytest.raises(CmsdkMuxError, match=r"cascaded onto the master mux's port 2.*different"):
        plan_cmsdk_mux(_ic(_shared_top(fixtures_dir, 4, casc_differ=True)))


def test_three_initiators_no_cascade(fixtures_dir):
    plan = plan_cmsdk_mux(_ic(_shared_top(fixtures_dir, 3)))
    assert not plan['cascade'] and plan['mst_w'] == 2
    assert [i['port'] for i in plan['top_ports']] == [0, 1, 2]


# ---------------------------------------------------------------------------
# Fail-loud limits
# ---------------------------------------------------------------------------
def test_too_many_initiators(fixtures_dir):
    with pytest.raises(CmsdkMuxError, match=r"6 initiators.*at most 5"):
        plan_cmsdk_mux(_ic(_shared_top(fixtures_dir, 6)))


def test_five_initiators_is_the_limit(fixtures_dir):
    plan = plan_cmsdk_mux(_ic(_shared_top(fixtures_dir, 5)))
    assert len(plan['casc_ports']) == 3


def test_too_many_targets(fixtures_dir):
    top = build_module_from_yaml(fixtures_dir, "two_region_soc.yaml")
    ic = _ic(top)
    ic.impl = 'cmsdk_mux'
    for k in range(2, 10):          # 10 targets + default = 11 slave-mux ports
        ic.targets.append(InterconnectTarget(name=f'sram_{k}', base=0x20000000 * 0 + 0x40000000 + k * 0x1000, size=0x1000))
        ic.initiators[0].targets.append(InterconnectInitiatorTarget(name=f'sram_{k}'))
    with pytest.raises(CmsdkMuxError, match=r"11 slave-mux ports"):
        plan_cmsdk_mux(ic)
    ic.targets.pop()
    ic.initiators[0].targets.pop()
    assert plan_cmsdk_mux(ic)['default_port'] == 9      # 9 targets + default fit


def test_passthrough_loop(fixtures_dir):
    """passthrough_soc lets its passthrough initiator reach the passthrough
    target: a loop out of and back into the interconnect."""
    top = build_module_from_yaml(fixtures_dir, "passthrough_soc.yaml")
    ic = _ic(top)
    ic.impl = 'cmsdk_mux'
    with pytest.raises(CmsdkMuxError, match=r"passthrough loop.*ext_s.*system"):
        plan_cmsdk_mux(ic)


def test_passthrough_target_shared_by_two(mux_top):
    ic = _ic(mux_top)
    ic.initiators[1].passthrough = None
    ic.initiators[1].targets.append(InterconnectInitiatorTarget(name='system'))
    with pytest.raises(CmsdkMuxError, match=r"'system' is reachable by \['cpu_m', 'ext_s'\]"):
        plan_cmsdk_mux(ic)


def test_address_width(mux_top):
    with pytest.raises(CmsdkMuxError, match=r"32-bit address"):
        plan_cmsdk_mux(_ic(mux_top), addr_width=24)


def test_overlapping_effective_regions(mux_top):
    ic = _ic(mux_top)
    ic.initiators[0].targets[2].visibility = [{'base': 0x10000000}]   # dat_0 over ram_0
    with pytest.raises(CmsdkMuxError, match=r"overlapping effective regions"):
        plan_cmsdk_mux(ic)


def test_undefined_target(mux_top):
    ic = _ic(mux_top)
    ic.initiators[0].targets.append(InterconnectInitiatorTarget(name='nope'))
    with pytest.raises(CmsdkMuxError, match=r"undefined target 'nope'"):
        plan_cmsdk_mux(ic)


# ---------------------------------------------------------------------------
# Deadlock rule, with a negative control
# ---------------------------------------------------------------------------
def test_derived_plan_is_deadlock_free(mux_top):
    cmsdk_mux.assert_deadlock_free(plan_cmsdk_mux(_ic(mux_top)))


def test_deadlock_check_fires_on_a_shared_passthrough(mux_top):
    """Negative control: the feasibility study's plain shared bus (the
    passthrough target moved onto the bus the passthrough initiator uses)."""
    plan = copy.deepcopy(plan_cmsdk_mux(_ic(mux_top)))
    for t in plan['targets']:
        if t['name'] == 'system':
            t['private'] = False
    with pytest.raises(CmsdkMuxError, match=r"deadlock.*'system'.*\['ext_s'\]"):
        cmsdk_mux.assert_deadlock_free(plan)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------
def test_validator_clean_for_good_cmsdk_mux(mux_top):
    msgs = [m for m in SoCValidator(mux_top).validate_all() if m.category == 'interconnect']
    assert msgs == []


def test_validator_reports_loop(fixtures_dir):
    top = build_module_from_yaml(fixtures_dir, "passthrough_soc.yaml")
    _ic(top).impl = 'cmsdk_mux'
    errs = [m for m in SoCValidator(top).validate_all()
            if m.level == 'error' and m.category == 'interconnect']
    assert len(errs) == 1 and 'passthrough loop' in errs[0].message


def test_validator_reports_unknown_impl(fixtures_dir):
    top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
    _ic(top).impl = 'bus_matrix'
    errs = [m for m in SoCValidator(top).validate_all() if m.category == 'interconnect']
    assert len(errs) == 1 and "unknown interconnect impl 'bus_matrix'" in errs[0].message


def test_validator_silent_for_busmatrix(fixtures_dir):
    top = build_module_from_yaml(fixtures_dir, "passthrough_soc.yaml")
    assert [m for m in SoCValidator(top).validate_all() if m.category == 'interconnect'] == []


# ---------------------------------------------------------------------------
# Backend output
# ---------------------------------------------------------------------------
def test_backend_unknown_impl_raises(fixtures_dir, tmp_path):
    top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
    _ic(top).impl = 'bus_matrix'
    with pytest.raises(CmsdkMuxError, match="unknown interconnect impl"):
        SoCAhbBackend(top).generate_all(tmp_path)


def test_backend_loop_raises(fixtures_dir, tmp_path):
    top = build_module_from_yaml(fixtures_dir, "passthrough_soc.yaml")
    _ic(top).impl = 'cmsdk_mux'
    with pytest.raises(CmsdkMuxError):
        SoCAhbBackend(top).generate_all(tmp_path)
    assert not (tmp_path / 'rtl').exists()      # nothing half-written


def test_backend_outputs(mux_top, tmp_path, monkeypatch):
    def no_subprocess(*a, **k):
        raise AssertionError("cmsdk_mux must not run BuildBusMatrix.pl")
    monkeypatch.setattr(subprocess, 'run', no_subprocess)
    monkeypatch.setenv('ARM_IP_LIBRARY_PATH', str(tmp_path / 'would_be_arm_lib'))
    SoCAhbBackend(mux_top, arm_ip_library_path=str(tmp_path / 'arm')).generate_all(tmp_path)

    ic = 'cmsdk_mux_ahb_interconnect'
    files = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob('*') if p.is_file())
    assert files == sorted([
        'flist/cmsdk_mux_components.flist',
        'flist/cmsdk_mux_interconnect.flist',
        f'interconnect/{ic}/address_maps/{ic}_address_maps.txt',
        f'rtl/{ic}/cmsdk_mux_config_pkg.sv',
        f'rtl/{ic}/cmsdk_mux_interconnect.sv',
        f'rtl/{ic}/{ic}_lite.v',
    ])
    assert not list(tmp_path.rglob('*.xml'))
    for f in files:
        assert not ARM_BANNER.search((tmp_path / f).read_text()), f

    flist = (tmp_path / 'flist/cmsdk_mux_interconnect.flist').read_text()
    comps = (tmp_path / 'flist/cmsdk_mux_components.flist').read_text()
    live = lambda text: [l for l in text.splitlines() if l.strip() and not l.startswith('//')]
    for comp in cmsdk_mux.CMSDK_COMPONENTS:
        assert f'$(ARM_CORSTONE_101_DIR)/{comp}' in live(comps)
        # named in the per-interconnect flist, but only as a comment
        assert f'//   $(ARM_CORSTONE_101_DIR)/{comp}' in flist
    assert not [l for l in live(flist) if 'ARM_CORSTONE_101_DIR' in l]
    assert f'build_soc/rtl/{ic}/{ic}_lite.v' in flist
    assert not [l for l in live(flist) if 'busmatrix' in l.lower()]

    lite = (tmp_path / f'rtl/{ic}/{ic}_lite.v').read_text()
    assert f'module {ic}_lite (' in lite
    # every target: HSEL a pure decode; HTRANS shown when addressed to it and
    # either the bus is ready or the target is in its own data phase (so a
    # waited SEQ/BUSY stays visible); HREADYMUX its own data-phase view
    assert "wire [1:0] b_htrans_q = b_hsel ? b_htrans : TRN_IDLE;" in lite
    for t in ('ROM_0', 'RAM_0', 'DAT_0'):
        assert f'assign HSEL_{t} = b_dec_{t};' in lite
        assert (f'assign HTRANS_{t}    = (HSEL_{t} & (b_hready | b_dp_{t})) '
                f'? b_htrans_q : TRN_IDLE;') in lite
        assert f'assign HREADYMUX_{t} = b_dp_{t} ? HREADYOUT_{t} : 1\'b1;' in lite
    assert ('assign HTRANS_SYSTEM    = (fe_CPU_M_sel_SYSTEM & (fe_CPU_M_hready | '
            'fe_CPU_M_dp_SYSTEM)) ? HTRANS_CPU_M : TRN_IDLE;') in lite
    # a locked transfer on the private path must not lock the shared bus
    # (review finding: it deadlocked the two interconnects)
    # ... except that an IDLE/BUSY inside a locked sequence keeps the lock
    # whatever its don't-care HADDR decodes to (review R-ICX-2)
    assert '.HMASTLOCKS0(HMASTLOCK_CPU_M & (fe_CPU_M_sel_shared | ~HTRANS_CPU_M[1]))' in lite
    # the external passthrough initiator has no private path: lock ungated
    assert '.HMASTLOCKS1(HMASTLOCK_EXT_S)' in lite
    # the scan ports carry SoC Labs' own comment (the bus matrix's port-list
    # comment is not reproduced)
    assert '// Scan ports: this core has no flops on a scan chain' in lite
    assert 'dummy' not in lite.lower()
    assert "assign HREADYMUX_SYSTEM = fe_CPU_M_dp_SYSTEM ? HREADYOUT_SYSTEM : 1'b1;" in lite
    assert 'cmsdk_ahb_default_slave u_default_slave' in lite


def test_components_flist_written_once_for_two_interconnects(mux_top, tmp_path):
    """Two cmsdk_mux interconnects: one components flist naming each Arm
    file once (the flows do not de-duplicate; VCS stops on a repeat)."""
    import copy as _copy
    ic2 = _copy.deepcopy(_ic(mux_top))
    ic2.name = 'second_ahb_interconnect'
    mux_top.interconnects.append(ic2)
    SoCAhbBackend(mux_top).generate_all(tmp_path)
    comps = (tmp_path / 'flist/cmsdk_mux_components.flist').read_text()
    for comp in cmsdk_mux.CMSDK_COMPONENTS:
        assert comps.count(f'$(ARM_CORSTONE_101_DIR)/{comp}') == 1
    assert 'cmsdk_mux_ahb_interconnect_lite, second_ahb_interconnect_lite' in comps
    lines = []
    for f in (tmp_path / 'flist').glob('*.flist'):
        lines += [l for l in f.read_text().splitlines() if l.strip() and not l.startswith('//')]
    arm = [l for l in lines if 'ARM_CORSTONE_101_DIR' in l]
    assert len(arm) == len(set(arm)) == 3


def test_busmatrix_writes_no_components_flist(fixtures_dir, tmp_path):
    top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
    SoCAhbBackend(top).generate_all(tmp_path)
    assert not (tmp_path / 'flist/cmsdk_mux_components.flist').exists()



def test_lite_port_list_matches_busmatrix_convention(mux_top, tmp_path):
    """The _lite ports are exactly those the wrapper binds."""
    SoCAhbBackend(mux_top).generate_all(tmp_path)
    ic = 'cmsdk_mux_ahb_interconnect'
    lite = (tmp_path / f'rtl/{ic}/{ic}_lite.v').read_text()
    wrapper = (tmp_path / f'rtl/{ic}/cmsdk_mux_interconnect.sv').read_text()
    inst = wrapper[wrapper.index(f'{ic}_lite u_busmatrix'):]
    bound = set(re.findall(r'^\s*\.(\w+)\s*\(', inst, re.M))
    header = lite[lite.index(f'module {ic}_lite ('):lite.index(');')]
    declared = set(re.findall(r'(?:input|output)\s+wire\s+(?:\[[^\]]+\]\s*)?(\w+)', header))
    assert bound == declared
