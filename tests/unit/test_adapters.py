# Copyright 2026, SoC Labs (www.soclabs.org)
"""Adapter equivalence tests (roadmap doc 02, milestone M4 — "Adapter
equivalence (M4)").

The M4 refactor moved the bus-signal tables out of `backends/protocol_utils.py`
into per-protocol adapter classes behind a shim. This is a BEHAVIOUR-PRESERVING
refactor: the adapters must reproduce the legacy `bus_member_names` / boundary
heuristic EXACTLY for every bus iface type and for the `EXCLUDE: [hready]`
boundary cases (eth_ss vs cpu_ss).

The "legacy" snapshot below was captured from the original `protocol_utils.py`
functions BEFORE the refactor, so the equivalence assertions are meaningful: the
shim still routes through these functions, but the snapshot is an independent
record of what they returned, pinning the contract.
"""

import pytest

from soc_model.model import Interface
from soc_model.backends import protocol_utils as pu
from soc_model.adapters.ahb import AhbProtocol
from soc_model.adapters.apb import ApbProtocol
from soc_model.adapters.dbgahb import DbgAhbProtocol
from soc_model.adapters.axis import AxisProtocol, AxisByteProtocol
from soc_model.adapters.swd import SwdProtocol
from soc_model.adapters.gpio import GpioProtocol
from soc_model.adapters.registry import protocol_for, all_protocols

pytestmark = pytest.mark.unit


def _iface(name, t, params=None):
    return Interface(name=name, type=t, params=params or {})


# --- Legacy snapshot (captured from the pre-refactor protocol_utils.py) ------
# bus_member_names(iface) for a sample of every type + the two boundary cases.
LEGACY_MEMBER_NAMES = {
    ('cpu_0_ahb', 'ahb', None): [
        'cpu_0_ahb_haddr', 'cpu_0_ahb_hburst', 'cpu_0_ahb_hmastlock',
        'cpu_0_ahb_hprot', 'cpu_0_ahb_hrdata', 'cpu_0_ahb_hready',
        'cpu_0_ahb_hreadyout', 'cpu_0_ahb_hresp', 'cpu_0_ahb_hsel',
        'cpu_0_ahb_hsize', 'cpu_0_ahb_htrans', 'cpu_0_ahb_hwdata',
        'cpu_0_ahb_hwrite',
    ],
    # EXCLUDE:[hready] does NOT change name-recognition (legacy uses full tables)
    ('cpu_0_ahb', 'ahb', 'excl_hready'): [
        'cpu_0_ahb_haddr', 'cpu_0_ahb_hburst', 'cpu_0_ahb_hmastlock',
        'cpu_0_ahb_hprot', 'cpu_0_ahb_hrdata', 'cpu_0_ahb_hready',
        'cpu_0_ahb_hreadyout', 'cpu_0_ahb_hresp', 'cpu_0_ahb_hsel',
        'cpu_0_ahb_hsize', 'cpu_0_ahb_htrans', 'cpu_0_ahb_hwdata',
        'cpu_0_ahb_hwrite',
    ],
    ('cpu_0_dbgahb', 'dbg_ahb', None): [
        'cpu_0_dbgahb_slvaddr', 'cpu_0_dbgahb_slvwdata', 'cpu_0_dbgahb_slvtrans',
        'cpu_0_dbgahb_slvwrite', 'cpu_0_dbgahb_slvsize', 'cpu_0_dbgahb_slvrdata',
        'cpu_0_dbgahb_slvready', 'cpu_0_dbgahb_slvresp',
    ],
    ('cpu_0_swd', 'swd', None): [
        'cpu_0_swdi', 'cpu_0_swclk', 'cpu_0_swdo', 'cpu_0_swdoen',
    ],
    # name without the _swd suffix -> no stripping
    ('cpu_0', 'swd', None): [
        'cpu_0_swdi', 'cpu_0_swclk', 'cpu_0_swdo', 'cpu_0_swdoen',
    ],
    ('strm', 'axis', None): [
        'strm_tvalid', 'strm_tready', 'strm_tdata', 'strm_tstrb', 'strm_tlast',
    ],
    ('strm', 'axis_stream', None): [
        'strm_tvalid', 'strm_tready', 'strm_tdata', 'strm_tstrb', 'strm_tlast',
    ],
    ('strm', 'axis_byte', None): [
        'strm_tvalid', 'strm_tready', 'strm_tdata',
    ],
    ('gpio0', 'gpio', None): [
        'gpio0_in', 'gpio0_out', 'gpio0_outen',
    ],
    # plain wire -> no expansion
    ('clk', 'wire', None): [],
}

_PARAM_PRESETS = {
    None: {},
    'excl_hready': {'EXCLUDE': ['hready']},
}


@pytest.mark.parametrize('key', list(LEGACY_MEMBER_NAMES))
def test_shim_bus_member_names_matches_legacy_snapshot(key):
    name, t, preset = key
    iface = _iface(name, t, _PARAM_PRESETS[preset])
    assert pu.bus_member_names(iface) == LEGACY_MEMBER_NAMES[key]


def test_ahb_member_names_equals_legacy():
    """AhbProtocol().member_names == legacy bus_member_names for AHB, including
    the EXCLUDE:[hready] boundary case."""
    for preset in (None, 'excl_hready'):
        iface = _iface('cpu_0_ahb', 'ahb', _PARAM_PRESETS[preset])
        assert AhbProtocol().member_names(iface) == pu.bus_member_names(iface)
        # and equals the captured legacy snapshot
        assert AhbProtocol().member_names(iface) == \
            LEGACY_MEMBER_NAMES[('cpu_0_ahb', 'ahb', preset)]


@pytest.mark.parametrize('proto,name,t', [
    (DbgAhbProtocol(), 'cpu_0_dbgahb', 'dbg_ahb'),
    (AxisProtocol(),   'strm',         'axis'),
    (AxisByteProtocol(), 'strm',       'axis_byte'),
    (SwdProtocol(),    'cpu_0_swd',    'swd'),
    (SwdProtocol(),    'cpu_0',        'swd'),
    (GpioProtocol(),   'gpio0',        'gpio'),
])
def test_protocol_member_names_match_legacy(proto, name, t):
    iface = _iface(name, t)
    assert proto.member_names(iface) == pu.bus_member_names(iface)
    assert proto.member_names(iface) == LEGACY_MEMBER_NAMES[(name, t, None)]


# --- Boundary heuristic (the load-bearing eth_ss vs cpu_ss decision) ---------

LEGACY_BOUNDARY = [
    ({}, True),                          # eth_ss: initiator boundary
    ({'EXCLUDE': ['hready']}, False),    # cpu_ss: slave/target boundary
    ({'EXCLUDE': ['hburst']}, True),     # unrelated exclude -> still initiator
    (None, True),                        # non-dict -> default initiator
    ('notdict', True),                   # non-dict -> default initiator
]


@pytest.mark.parametrize('params,expected', LEGACY_BOUNDARY)
def test_passthrough_boundary_shim_matches_legacy(params, expected):
    assert pu.passthrough_initiator_uses_initiator_boundary(params) == expected


@pytest.mark.parametrize('params,expected', LEGACY_BOUNDARY)
def test_ahb_protocol_boundary_matches_shim(params, expected):
    """The heuristic now lives on AhbProtocol; the shim delegates to it."""
    assert AhbProtocol().passthrough_uses_initiator_boundary(params) == expected


# --- Registry sanity ---------------------------------------------------------

def test_protocol_for_resolves_types():
    assert isinstance(protocol_for(_iface('x', 'ahb')), AhbProtocol)
    assert isinstance(protocol_for(_iface('x', 'dbg_ahb')), DbgAhbProtocol)
    assert isinstance(protocol_for(_iface('x', 'axis')), AxisProtocol)
    # axis_stream aliases onto axis
    assert isinstance(protocol_for(_iface('x', 'axis_stream')), AxisProtocol)
    assert isinstance(protocol_for(_iface('x', 'axis_byte')), AxisByteProtocol)
    assert isinstance(protocol_for(_iface('x', 'swd')), SwdProtocol)
    assert isinstance(protocol_for(_iface('x', 'gpio')), GpioProtocol)
    # plain wire / unknown -> None (not a bus)
    assert protocol_for(_iface('clk', 'wire')) is None
    assert protocol_for(_iface('x', 'no_such_type')) is None
    # accepts a bare type string too
    assert isinstance(protocol_for('ahb'), AhbProtocol)


def test_all_protocols_registered():
    types = {p.type for p in all_protocols()}
    assert {'ahb', 'apb', 'dbg_ahb', 'axis', 'axis_byte', 'swd', 'gpio'} <= types


# --- APB protocol (new — previously `apb` had no registered protocol) --------

def test_apb_protocol_registered_and_member_names():
    """`apb` is a declarable Interface.type (see soc_model.model.Interface)
    but had NO registered protocol, so bus_member_names() returned [] and APB
    bundle sub-signals (a completer's PRDATA/PREADY/PSLVERR) could never be
    recognised or auto-bound — the undriven-USRT-PREADY bug class."""
    proto = protocol_for(_iface('dbg', 'apb'))
    assert isinstance(proto, ApbProtocol)
    names = proto.member_names(_iface('dbg', 'apb'))
    assert names == sorted(names)
    assert set(names) == {
        'dbg_psel', 'dbg_penable', 'dbg_paddr', 'dbg_pwrite', 'dbg_pwdata',
        'dbg_prdata', 'dbg_pready', 'dbg_pslverr',
    }


def test_apb_role_tables_are_mirrored():
    """Completer and requester tables carry the same suffixes with opposite
    directions (an APB has no target-only signals, unlike AHB's hsel)."""
    proto = ApbProtocol()
    init = {s.suffix: s.direction for s in proto.table('initiator')}
    tgt = {s.suffix: s.direction for s in proto.table('target')}
    assert set(init) == set(tgt)
    for suffix in init:
        assert init[suffix] != tgt[suffix], suffix
    # spot-check the load-bearing response signals from the completer side
    assert tgt['prdata'] == 'out' and tgt['pready'] == 'out'
    assert tgt['psel'] == 'in' and tgt['pwdata'] == 'in'


# --- resolve_member / resolve_boundary_port (member resolution API) ----------

def test_resolve_member_apb_target():
    """An APB completer iface resolves its member names case-insensitively,
    with module-boundary directions already folded in."""
    iface = _iface('DEBUG_APB', 'apb', {'ADDR_WIDTH': 10, 'DATA_WIDTH': 32})
    iface.direction = 'target'
    proto = protocol_for(iface)
    ready = proto.resolve_member(iface, 'DEBUG_APB_PREADY')
    assert ready is not None
    assert ready.suffix == 'pready' and ready.is_output is True
    sel = proto.resolve_member(iface, 'debug_apb_psel')
    assert sel is not None and sel.is_output is False
    assert proto.resolve_member(iface, 'DEBUG_APB_NOPE') is None


def test_resolve_member_axis_byte_directions_and_width():
    """axis_byte member directions fold in the iface direction (source
    perspective tables): a sender's tvalid/tdata are outputs and tready an
    input; a receiver inverts. tdata carries the 8-bit width expression."""
    tx = _iface('ADP_RXD', 'axis_byte')
    tx.direction = 'out'
    proto = protocol_for(tx)
    tdata = proto.resolve_member(tx, 'ADP_RXD_TDATA')
    assert tdata.is_output is True and tdata.width == '8'
    assert proto.resolve_member(tx, 'ADP_RXD_TREADY').is_output is False

    rx = _iface('ADP_TXD', 'axis_byte')
    rx.direction = 'in'
    assert proto.resolve_member(rx, 'ADP_TXD_TVALID').is_output is False
    assert proto.resolve_member(rx, 'ADP_TXD_TREADY').is_output is True


def test_resolve_member_tolerates_direction_decorated_rtl_names():
    """Pre-existing RTL decorates member ports with `_o`/`_i`
    (socdebug's ADP_RXD_TVALID_o / ADP_TXD_TDATA_i); resolution strips one
    trailing decoration before matching."""
    iface = _iface('ADP_RXD', 'axis_byte')
    iface.direction = 'out'
    proto = protocol_for(iface)
    m = proto.resolve_member(iface, 'ADP_RXD_TVALID_o')
    assert m is not None and m.suffix == 'tvalid' and m.is_output is True
    m = proto.resolve_member(iface, 'ADP_RXD_TREADY_i')
    assert m is not None and m.suffix == 'tready' and m.is_output is False


def test_resolve_member_ahb_uses_single_role_table():
    """An AHB iface resolves members against ITS role's table only: an
    initiator iface has no hsel/hreadyout members."""
    master = _iface('DEBUG', 'ahb')
    master.direction = 'initiator'
    proto = protocol_for(master)
    haddr = proto.resolve_member(master, 'DEBUG_HADDR')
    assert haddr.is_output is True and haddr.width == 'SYS_ADDR_W'
    assert proto.resolve_member(master, 'DEBUG_HRDATA').is_output is False
    assert proto.resolve_member(master, 'DEBUG_HSEL') is None


def test_resolve_boundary_port_prefers_exact_iface_then_member():
    from soc_model.adapters.base import resolve_boundary_port
    bundle = _iface('USRT0_RXD', 'axis_byte')
    bundle.direction = 'out'
    wire = _iface('USRT0_RXD_TVALID', 'wire')   # exact name shadows member
    # exact iface match wins (member=None)
    got = resolve_boundary_port([bundle, wire], 'USRT0_RXD_TVALID')
    assert got == (wire, None)
    # bundle itself matches exactly
    assert resolve_boundary_port([bundle], 'USRT0_RXD') == (bundle, None)
    # member fallback
    iface, member = resolve_boundary_port([bundle], 'USRT0_RXD_TDATA')
    assert iface is bundle and member.suffix == 'tdata'
    # unknown -> None
    assert resolve_boundary_port([bundle], 'GHOST') is None


def test_tables_reexported_byte_identical():
    """The re-exported tables are the SAME objects the adapters carry, as plain
    lists of (suffix, dir, width) 3-tuples — the form the backends iterate."""
    from soc_model.adapters import ahb as a_ahb
    from soc_model.adapters import axis as a_axis
    from soc_model.adapters import swd as a_swd
    from soc_model.adapters import dbgahb as a_dbg
    from soc_model.adapters import gpio as a_gpio
    assert pu.AHB_INITIATOR_SIGNALS is a_ahb.AHB_INITIATOR_SIGNALS
    assert pu.AHB_TARGET_SIGNALS is a_ahb.AHB_TARGET_SIGNALS
    assert pu.AXIS_SIGNALS is a_axis.AXIS_SIGNALS
    assert pu.AXIS_BYTE_SIGNALS is a_axis.AXIS_BYTE_SIGNALS
    assert pu.SWD_SIGNALS is a_swd.SWD_SIGNALS
    assert pu.DBGAHB_TARGET_SIGNALS is a_dbg.DBGAHB_TARGET_SIGNALS
    assert pu.GPIO_SIGNALS is a_gpio.GPIO_SIGNALS
    # 3-tuple shape preserved
    for tbl in (pu.AHB_INITIATOR_SIGNALS, pu.AHB_TARGET_SIGNALS, pu.AXIS_SIGNALS,
                pu.AXIS_BYTE_SIGNALS, pu.SWD_SIGNALS, pu.DBGAHB_TARGET_SIGNALS,
                pu.GPIO_SIGNALS):
        for row in tbl:
            assert isinstance(row, tuple) and len(row) == 3


def test_expand_carries_width_and_direction():
    """expand() returns fully-qualified SignalPorts; EXCLUDE is applied for
    expansion (unlike member_names)."""
    iface = _iface('cpu_0_ahb', 'ahb', {'EXCLUDE': ['hready']})
    ports = AhbProtocol().expand(iface, 'initiator')
    names = [p.name for p in ports]
    assert 'cpu_0_ahb_hready' not in names      # excluded
    assert 'cpu_0_ahb_haddr' in names
    haddr = next(p for p in ports if p.name == 'cpu_0_ahb_haddr')
    assert haddr.direction == 'out' and haddr.width == 'SYS_ADDR_W'
