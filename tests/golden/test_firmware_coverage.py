# Copyright 2026, SoC Labs (www.soclabs.org)
"""Gap-fill coverage for ``soc_model/backends/firmware.py``.

``test_firmware.py`` (single-CPU) and ``test_firmware_multicore.py`` (one
bridge subsystem, happy alias/remap path) leave a handful of edge branches
uncovered. This sibling file pins those branches with *behavioural* assertions
(not no-assert coverage padding):

  * ``_stitch_module`` bridge-merge skip arms (firmware.py 289,296,303,322,
    324,338,341): a richer, hand-built subsystem whose internal interconnects
    contain a no-exit IC, a dangling bridge instance, a passthrough internal
    initiator, a non-intersecting internal master, a remap window on the exit
    target itself, and a duplicate internal master (de-dupe). Driven inline so
    the precise malformed shapes are explicit.
  * ``_stitch_module`` phys_size resolution (240-243): a ``"2 ** $W"`` target
    resolves to an int; an unresolvable ``"$NO_SUCH_PARAM"`` falls to ``None``.
  * ``_build_cpu_initiator_data`` not-found ValueError (353-354).
  * ``_find_firmware`` via a child instance's resolved_module (155).
  * ``_resolve_address`` default-fallback to the first window (441).
  * ``_build_linker_context`` skip/format tails (465-466,474-476,478,488,492):
    no-region-type skip, no-phys_size skip, string phys_size, and the MB / byte
    LENGTH comment arms.
  * ``_build_memmap_context`` default-resolve ValueError -> continue (517-518)
    and the remap-define ValueError swallow (555-556).
  * ``_build_makefile_context`` hex_adjust ValueError swallow (622-623).
  * ``_build_adp_context`` empty-adp early return (649) and resolve ValueError
    -> addr 0 (659-660).
  * The ``write_if_changed`` "Skipped (unchanged)" else arms (97,106,115,125,
    134,143) via a double ``generate()`` into the same directory.

The jinja2-missing guards (26-28,74-75) and the two defensive ``parent_entry
is None`` / ``child_entry is None`` arms (286,327) are intentionally NOT forced
here: the jinja arms only fire with jinja2 uninstalled, and 286/327 are dead
guards (``_build_initiator_data`` always registers an entry for every
initiator, so the ``.get()`` never returns None through the real build path).
"""

import re

import pytest

from soc_model.model import (
    Module, Param, Instance, Connection, Firmware,
    LinkerProfile, LinkerRegion,
    Interconnect, InterconnectTarget,
    InterconnectInitiator, InterconnectInitiatorTarget,
)
from soc_model.backends.firmware import SoCFirmwareBackend

from conftest import FIXTURES
from helpers import build_module_from_yaml

pytestmark = pytest.mark.golden


# ---------------------------------------------------------------------------
# Small inline-model builders (no YAML fixture needed for these edge shapes).
# ---------------------------------------------------------------------------

def _it(name, vis=None):
    return InterconnectInitiatorTarget(name=name, visibility=vis or [])


def _alias_imem_vis():
    """IMEM with a canonical window + an alias window (low)."""
    return [
        {'base': 0x10000000, 'aperture': 0x1000},
        {'base': 0x00000000, 'aperture': 0x1000, 'remap_behaviour': 'alias'},
    ]


def _remap_brom_vis():
    """BOOTROM with a remap overlay (bit 0)."""
    return [
        {'base': 0x00000000, 'aperture': 0x800,
         'remap': {'remapping': True, 'remap_bit': 0, 'remap_behaviour': 'alias'}},
    ]


def _build_edge_subsystem():
    """Subsystem with TWO internal interconnects exercising the skip arms.

      * ``ss_ic_noexit``: an IC with no ``passthrough == 'br'`` target, so its
        ``exit_targets`` set is empty -> the bridge-merge skips it (303).
      * ``edge_ss_ic``: the real exit IC. Its initiators include
          - ``ss_cpu_m``    : carries IMEM alias + BOOTROM remap, AND a remap
                              window on the *exit* target itself (folded-out at
                              338 because its interface is an exit_target),
          - ``ss_dma_m``    : a duplicate internal master carrying the SAME
                              IMEM/BOOTROM windows -> de-dupe (341 + seen_addr),
          - ``ss_local_m``  : reaches only IMEM, not the exit -> no-intersect
                              skip (324),
          - ``br_in``       : a passthrough internal initiator with a name that
                              differs from the bridge initiator -> passthrough
                              skip (322).
    """
    ss = Module(name='edge_subsystem')
    ss.params = {'W': Param(name='W', default=12)}
    ss.instances = []

    ic_noexit = Interconnect(name='ss_ic_noexit', gen=True)
    ic_noexit.targets = [
        InterconnectTarget(name='ss_aux', base=0x30000000, size=0x1000,
                           phys_size=0x1000, region_type='memory'),
    ]
    ic_noexit.initiators = [
        InterconnectInitiator(name='ss_auxcpu_m', targets=[_it('ss_aux')]),
    ]

    ic = Interconnect(name='edge_ss_ic', gen=True)
    ic.targets = [
        InterconnectTarget(name='ss_imem', base=0x10000000, size=0x1000,
                           phys_size=0x1000, region_type='memory'),
        InterconnectTarget(name='ss_brom', base=0x00000000, size=0x800,
                           phys_size=0x800, region_type='memory', role='bootrom'),
        InterconnectTarget(name='ss_exit', base=0x20000000, size=0x1000,
                           region_type=None, passthrough='br'),
    ]
    ic.initiators = [
        InterconnectInitiator(name='ss_cpu_m', targets=[
            _it('ss_imem', _alias_imem_vis()),
            _it('ss_brom', _remap_brom_vis()),
            # remap window on the EXIT target itself -> folded out at 338.
            _it('ss_exit', [{'base': 0x20000000, 'aperture': 0x1000,
                             'remap': {'remapping': True, 'remap_bit': 1,
                                       'remap_behaviour': 'alias'}}]),
        ]),
        InterconnectInitiator(name='ss_dma_m', targets=[
            _it('ss_imem', _alias_imem_vis()),
            _it('ss_brom', _remap_brom_vis()),
            _it('ss_exit'),
        ]),
        InterconnectInitiator(name='ss_local_m', targets=[_it('ss_imem')]),
        InterconnectInitiator(name='br_in', passthrough='br', targets=[
            _it('ss_imem'), _it('ss_brom'), _it('ss_exit'),
        ]),
    ]
    ss.interconnects = [ic_noexit, ic]
    return ss


def _build_edge_top():
    """Parent matrix whose bridge initiator folds in the edge subsystem.

    Adds two further bridge initiators that exercise the early-skip arms:
      * ``dead_m`` -> ``u_dead`` whose ``resolved_module is None`` (289),
      * ``nobridge_m`` -> ``u_ss`` but with NO connection matching the
        ``parent_ic.nobridge_m`` needle -> ``bridge_port is None`` (296).
    """
    ss = _build_edge_subsystem()
    top = Module(name='edge_top')
    top.params = {'W': Param(name='W', default=12)}

    u_ss = Instance(instance_name='u_ss', module_name='edge_subsystem')
    u_ss.resolved_module = ss
    u_ss.connections = [Connection(port='br', conn='parent_ic.eth_ss_m')]

    u_dead = Instance(instance_name='u_dead', module_name='nope')
    u_dead.resolved_module = None
    u_dead.connections = []

    top.instances = [u_ss, u_dead]

    pic = Interconnect(name='parent_ic', gen=True)
    pic.targets = [
        InterconnectTarget(name='eth_ss_0', instance='u_ss', base=0x40000000,
                           size=0x10000000, phys_size=0x10000,
                           region_type='memory'),
        InterconnectTarget(name='dead_0', instance='u_dead', base=0x60000000,
                           size=0x1000, phys_size=0x1000, region_type='memory'),
    ]
    pic.initiators = [
        InterconnectInitiator(name='eth_ss_m', instance='u_ss',
                              targets=[_it('eth_ss_0')]),
        InterconnectInitiator(name='dead_m', instance='u_dead',
                              targets=[_it('dead_0')]),
        InterconnectInitiator(name='nobridge_m', instance='u_ss',
                              targets=[_it('eth_ss_0')]),
    ]
    top.interconnects = [pic]
    return top


@pytest.fixture(scope="module")
def edge_top():
    return _build_edge_top()


@pytest.fixture(scope="module")
def edge_backend(edge_top):
    return SoCFirmwareBackend(edge_top)


@pytest.fixture(scope="module")
def edge_stitched(edge_backend):
    return edge_backend._build_stitched_map()


# ---------------------------------------------------------------------------
# _stitch_module bridge-merge skip arms (289,296,303,322,324,338,341).
# ---------------------------------------------------------------------------

def test_bridge_merge_folds_dedup_and_excludes_exit(edge_stitched):
    """The bridge initiator folds the subsystem IMEM/BOOTROM exactly once.

    Two internal masters (ss_cpu_m + ss_dma_m) carry the SAME IMEM alias and
    BOOTROM remap windows; the de-dupe (seen_addr / seen_remap, 341) means each
    region is folded only once. The remap window on the *exit* target itself is
    NOT folded (338 — its interface is an exit_target that already routes back
    out to the parent map).
    """
    eth = edge_stitched['initiator_data']['eth_ss_m']

    # IMEM alias window (rebased to the parent base 0x40000000) folded once.
    imem_alias = [r for r in eth['address_regions']
                  if r['interface'] == 'ss_imem' and r['remapping'] == 'alias']
    assert len(imem_alias) == 1, "IMEM alias folded more than once (de-dupe failed)"
    assert imem_alias[0]['mem_lo'] == '40000000'

    # IMEM canonical window (rebased to 0x50000000) folded once.
    imem_canon = [r for r in eth['address_regions']
                  if r['interface'] == 'ss_imem' and r['remapping'] != 'alias']
    assert len(imem_canon) == 1
    assert imem_canon[0]['mem_lo'] == '50000000'

    # BOOTROM remap region folded exactly once (de-dupe across both masters).
    brom_remap = [r for r in eth['remap_regions'] if r['interface'] == 'ss_brom']
    assert len(brom_remap) == 1, "BOOTROM remap folded more than once"
    assert brom_remap[0]['mem_lo'] == '40000000'
    assert brom_remap[0]['bit'] == 0

    # The exit target's own remap window was NOT folded into the parent (338).
    assert not any(r['interface'] == 'ss_exit' for r in eth['remap_regions'])


def test_bridge_merge_ignores_non_intersecting_and_passthrough_masters(edge_stitched):
    """ss_local_m (no exit reach) and br_in (passthrough) contribute nothing.

    ss_local_m reaches only ss_imem (324 no-intersection skip) — but crucially
    it carries *no* extra region the cpu/dma masters did not already fold, so
    the folded set is exactly {ss_imem, ss_brom}. The passthrough initiator
    br_in (322) and the no-exit IC (303) likewise add nothing new.
    """
    eth = edge_stitched['initiator_data']['eth_ss_m']
    addr_ifaces = {r['interface'] for r in eth['address_regions']}
    remap_ifaces = {r['interface'] for r in eth['remap_regions']}
    # address_regions: the parent target + the folded subsystem IMEM windows.
    # BOOTROM is remap-only (no plain address_region), so it lives in remap_regions.
    assert addr_ifaces == {'eth_ss_0', 'ss_imem'}
    assert remap_ifaces == {'ss_brom'}
    # ss_aux lives behind the no-exit IC -> never folded (303).
    assert 'ss_aux' not in addr_ifaces and 'ss_aux' not in remap_ifaces


def test_dangling_and_unconnected_bridge_initiators_are_harmless(edge_stitched):
    """dead_m (no resolved_module, 289) + nobridge_m (no needle conn, 296).

    Neither bridge initiator folds anything; they must still appear in the
    stitched map with only their own canonical target window.
    """
    init_data = edge_stitched['initiator_data']
    assert 'dead_m' in init_data
    assert {r['interface'] for r in init_data['dead_m']['address_regions']} == {'dead_0'}
    assert 'nobridge_m' in init_data
    assert {r['interface'] for r in init_data['nobridge_m']['address_regions']} == {'eth_ss_0'}


def test_subsystem_targets_rebased_into_parent_window(edge_stitched):
    """Recursing into u_ss rebases its child targets by the parent base (261-264)."""
    bases = {t['name']: t['base'] for t in edge_stitched['targets']}
    assert bases['eth_ss_0'] == 0x40000000
    assert bases['ss_imem'] == 0x50000000     # 0x40000000 + 0x10000000
    assert bases['ss_brom'] == 0x40000000     # 0x40000000 + 0x00000000
    assert bases['ss_aux'] == 0x70000000      # 0x40000000 + 0x30000000


# ---------------------------------------------------------------------------
# _stitch_module phys_size resolution (240-243).
# ---------------------------------------------------------------------------

def test_phys_size_string_resolves_to_int_or_none():
    """A ``2 ** $W`` phys_size resolves to an int; an unresolvable ref -> None."""
    top = Module(name='ps_top')
    top.params = {'W': Param(name='W', default=12)}
    ic = Interconnect(name='ps_ic', gen=True)
    ic.targets = [
        InterconnectTarget(name='good', base=0x0, size=0x1000,
                           phys_size='2 ** $W', region_type='memory'),
        InterconnectTarget(name='unresolv', base=0x1000, size=0x1000,
                           phys_size='$NO_SUCH_PARAM', region_type='memory'),
    ]
    ic.initiators = [InterconnectInitiator(name='cpu_m', targets=[
        _it('good'), _it('unresolv')])]
    top.interconnects = [ic]

    stitched = SoCFirmwareBackend(top)._build_stitched_map()
    by = {t['name']: t for t in stitched['targets']}
    assert by['good']['phys_size'] == 4096      # 2 ** 12, resolved to int (240-242)
    assert by['unresolv']['phys_size'] is None  # unresolvable -> None (243)


# ---------------------------------------------------------------------------
# _build_cpu_initiator_data not-found (353-354) + _find_firmware child (155).
# ---------------------------------------------------------------------------

def test_cpu_initiator_not_found_raises(edge_backend, edge_stitched):
    """An unknown CPU initiator name raises with the available list (353-354)."""
    with pytest.raises(ValueError, match="not found in stitched address map"):
        edge_backend._build_cpu_initiator_data(edge_stitched, "ghost_cpu_m")


def test_find_firmware_via_child_instance():
    """A top with no firmware: section finds it on a child's resolved_module (155)."""
    child = build_module_from_yaml(FIXTURES, "firmware_soc.yaml")
    assert child.firmware is not None

    top = Module(name="wrapper")
    inst = Instance(instance_name="u_child", module_name="firmware_soc")
    inst.resolved_module = child
    top.instances = [inst]
    assert top.firmware is None

    found = SoCFirmwareBackend(top)._find_firmware()
    assert found is child.firmware


def test_find_firmware_none_when_absent_everywhere():
    """No firmware: on top OR any resolved child -> None (the loop falls through)."""
    top = Module(name="bare")
    top.instances = [Instance(instance_name="u_x", module_name="nope")]  # no resolved_module
    assert SoCFirmwareBackend(top)._find_firmware() is None


# ---------------------------------------------------------------------------
# _resolve_address default-fallback to first window (441).
# ---------------------------------------------------------------------------

def test_resolve_default_alias_only_falls_back_to_first_window(edge_backend):
    """default with no target_bases and ONLY an alias-marked window returns it (441).

    The canonical scan (436-439) finds no non-alias address_region, so it falls
    through to ``target_regions[0]`` — the single alias window.
    """
    alias_only = {
        'name': 'x', 'connections': [],
        'address_regions': [
            {'interface': 'aonly', 'mem_lo': '00009000',
             'mem_hi': '00009fff', 'remapping': 'alias'},
        ],
        'remap_regions': [],
    }
    assert edge_backend._resolve_address('aonly', 'default', alias_only) == 0x9000


# ---------------------------------------------------------------------------
# _build_linker_context skip/format tails (465-466,474-476,478,488,492).
# ---------------------------------------------------------------------------

def test_linker_context_skips_and_length_comments(edge_backend):
    """no-region skip (465), no-phys skip (474), str phys (478), MB/byte comments."""
    init = {
        'name': 'x', 'connections': [],
        'address_regions': [
            {'interface': 'mb_mem', 'mem_lo': '00000000',
             'mem_hi': '001fffff', 'remapping': 'none'},
            {'interface': 'small_mem', 'mem_lo': '00003000',
             'mem_hi': '000301ff', 'remapping': 'none'},
            {'interface': 'nophys', 'mem_lo': '00004000',
             'mem_hi': '00004fff', 'remapping': 'none'},
        ],
        'remap_regions': [],
    }
    mrs = [
        {'target': 'mb_mem', 'region_type': 'memory', 'linker_name': 'MBMEM',
         'software_access': 'rwx', 'phys_size': 0x100000, 'role': None},
        {'target': 'small_mem', 'region_type': 'memory', 'linker_name': 'SMALL',
         'software_access': 'rw', 'phys_size': 512, 'role': None},
        {'target': 'nophys', 'region_type': 'memory', 'linker_name': 'NOPHYS',
         'software_access': 'rwx', 'phys_size': None, 'role': None},
    ]
    tb = {'mb_mem': 0x0, 'small_mem': 0x3000, 'nophys': 0x4000}

    prof = LinkerProfile(name='p', regions=[
        LinkerRegion(target='mb_mem', address_select='default'),
        LinkerRegion(target='small_mem', address_select='default'),
        LinkerRegion(target='nophys', address_select='default'),      # no phys -> skip (474-476)
        LinkerRegion(target='ghost_region', address_select='default'),  # no region_type -> skip (465-466)
    ])
    fw = Firmware(cpu='cortex-m0', linker_profiles=[prof])
    regions = edge_backend._build_linker_context(fw, init, mrs, tb)['p']

    by = {r['name']: r for r in regions}
    # ghost_region (no region_type) and nophys (no phys_size) were both skipped.
    assert set(by) == {'MBMEM', 'SMALL'}
    # 1 MB region -> 'MB' comment arm (488).
    assert by['MBMEM']['length'] == '0x100000'
    assert by['MBMEM']['comment'] == '1MB'
    # 512-byte region -> the sub-1K 'bytes' comment arm (492).
    assert by['SMALL']['length'] == '0x200'
    assert by['SMALL']['comment'] == '512 bytes'


def test_linker_context_string_phys_size_on_region(edge_backend):
    """A LinkerRegion phys_size given as a hex string is int-parsed (477-478)."""
    init = {
        'name': 'x', 'connections': [],
        'address_regions': [
            {'interface': 'm', 'mem_lo': '00003000',
             'mem_hi': '00003fff', 'remapping': 'none'},
        ],
        'remap_regions': [],
    }
    mrs = [{'target': 'm', 'region_type': 'memory', 'linker_name': 'M',
            'software_access': 'rwx', 'phys_size': 0x1000, 'role': None}]
    tb = {'m': 0x3000}
    prof = LinkerProfile(name='q', regions=[
        LinkerRegion(target='m', address_select='default', phys_size='0x800')])
    fw = Firmware(cpu='m0', linker_profiles=[prof])
    regions = edge_backend._build_linker_context(fw, init, mrs, tb)['q']
    assert regions[0]['length'] == '0x800'   # string '0x800' -> int 0x800 (478)


# ---------------------------------------------------------------------------
# _build_memmap_context default-resolve continue (517-518) + remap swallow (555-556).
# ---------------------------------------------------------------------------

def test_memmap_context_skips_unresolvable_and_swallows_alias_remap(edge_backend):
    """A region absent from init_data is skipped (517-518); a window-less region
    in target_bases emits only BASE/SIZE (alias 544-545 + remap 555-556 swallowed).
    """
    fw = Firmware(cpu='m0')
    # 'solo' resolves via the target_bases default fast-path, but has NO window
    # in init_data, so alias + remapped both raise and are swallowed.
    # 'ghost' is a memory region absent from BOTH -> default raises -> continue.
    init = {'name': 'x', 'connections': [], 'address_regions': [], 'remap_regions': []}
    mrs = [
        {'target': 'solo', 'region_type': 'memory', 'linker_name': 'SOLO',
         'software_access': 'rwx', 'phys_size': 0x100, 'role': None},
        {'target': 'ghost', 'region_type': 'memory', 'linker_name': 'GHOST',
         'software_access': 'rwx', 'phys_size': 0x100, 'role': None},
    ]
    tb = {'solo': 0x20000000}
    ctx = edge_backend._build_memmap_context(fw, init, mrs, tb)
    names = {d['define_name'] for d in ctx['memory_regions']}

    # ghost omitted entirely (517-518 continue).
    assert not any('GHOST' in n for n in names)
    # solo has BASE + SIZE but no ALIAS (544-545) and no REMAP (555-556).
    assert any(n.endswith('SOLO_BASE') for n in names)
    assert any(n.endswith('SOLO_SIZE') for n in names)
    assert not any(n.endswith('SOLO_ALIAS') for n in names)
    assert not any(n.endswith('SOLO_REMAP') for n in names)


# ---------------------------------------------------------------------------
# _build_makefile_context hex_adjust swallow (622-623).
# ---------------------------------------------------------------------------

def test_makefile_hex_adjust_swallows_unresolvable(edge_backend):
    """A hex_adjust entry whose target can't resolve is dropped, not crashed (622-623)."""
    empty_init = {'name': 'x', 'connections': [], 'address_regions': [], 'remap_regions': []}
    fw = Firmware(cpu='m0', hex_adjust={'bad': {'target': 'ghost', 'address_select': 'default'}})
    ctx = edge_backend._build_makefile_context(fw, empty_init, [], {}, target_bases={})
    assert ctx['hex_adjust'] == {}      # unresolvable entry swallowed
    assert ctx['cpu'] == 'cortex-m0' or ctx['cpu'] == 'm0'


# ---------------------------------------------------------------------------
# _build_adp_context empty (649) + resolve ValueError -> addr 0 (659-660).
# ---------------------------------------------------------------------------

def test_adp_context_empty_returns_zero(edge_backend):
    """No adp: block -> the zero-address default context (649)."""
    fw = Firmware(cpu='m0', adp=None)
    ctx = edge_backend._build_adp_context(fw, {})
    assert ctx['adp_address_hex'] == '00000000'
    assert ctx['adp_nibbles'] == []
    assert ctx['adp_upload_address_python'] == '0x00000000'


def test_adp_context_unresolvable_target_falls_back_to_zero(edge_backend):
    """An adp upload_target that can't resolve falls back to address 0 (659-660)."""
    empty_init = {'name': 'x', 'connections': [], 'address_regions': [], 'remap_regions': []}
    fw = Firmware(cpu='m0', adp={'upload_target': 'ghost', 'address_select': 'default'})
    ctx = edge_backend._build_adp_context(fw, empty_init, target_bases={})
    assert ctx['adp_address_hex'] == '00000000'
    assert ctx['adp_upload_address_python'] == '0x00000000'
    # Still produces the 8 nibble entries for the zero address.
    assert len(ctx['adp_nibbles']) == 8


# ---------------------------------------------------------------------------
# write_if_changed "Skipped (unchanged)" else arms (97,106,115,125,134,143).
# ---------------------------------------------------------------------------

def test_second_generate_skips_unchanged_files(tmp_path, capsys):
    """Re-running generate() into the same dir hits the unchanged-skip arms.

    The first generate() writes every artifact ("Generated:"); the second sees
    identical content and takes the ``else`` (Skipped (unchanged)) arm for each
    file (firmware.py 97,106,115,125,134,143).
    """
    top = build_module_from_yaml(FIXTURES, "firmware_soc.yaml")
    out_dir = tmp_path / "fw_out"

    SoCFirmwareBackend(top).generate(out_dir)
    capsys.readouterr()  # discard first-pass "Generated:" output

    SoCFirmwareBackend(top).generate(out_dir)
    second = capsys.readouterr().out

    # 7 artifacts: 2 ld + memmap.h/.mk/.cmake + adp.vh/.py -> 7 "Skipped" lines.
    skipped = re.findall(r"Skipped \(unchanged\):", second)
    assert len(skipped) == 7, f"expected 7 unchanged skips, got {len(skipped)}"
    assert "Generated:" not in second, "second pass should rewrite nothing"
