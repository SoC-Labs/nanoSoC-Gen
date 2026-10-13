"""Shared protocol signal definitions and width formatting utilities.

This module is now a thin SHIM over the `soc_model.adapters` package (roadmap
doc 02, milestone M4). The bus-signal tables and the AHB boundary heuristic have
a single source of truth in `adapters/`; this module re-exports them and
delegates so existing backend imports keep working unchanged and generator
output stays byte-identical.

Re-exported tables (verbatim from the adapter classes):
    AHB_INITIATOR_SIGNALS, AHB_TARGET_SIGNALS  -> adapters.ahb
    AXIS_SIGNALS, AXIS_BYTE_SIGNALS            -> adapters.axis
    SWD_SIGNALS                                -> adapters.swd
    DBGAHB_TARGET_SIGNALS                      -> adapters.dbgahb
    GPIO_SIGNALS                               -> adapters.gpio

Delegating functions:
    bus_member_names(iface)                            -> adapters registry
    passthrough_initiator_uses_initiator_boundary(p)   -> AhbProtocol

`width_str` / `param_width_str` remain pure-Python utilities here (no protocol
state) — kept as-is.
"""

from typing import Any

from ..adapters.ahb import (
    AHB_INITIATOR_SIGNALS,
    AHB_TARGET_SIGNALS,
    AhbProtocol,
)
from ..adapters.axis import AXIS_SIGNALS, AXIS_BYTE_SIGNALS
from ..adapters.swd import SWD_SIGNALS
from ..adapters.dbgahb import DBGAHB_TARGET_SIGNALS
from ..adapters.gpio import GPIO_SIGNALS
from ..adapters.registry import protocol_for

__all__ = [
    'AHB_INITIATOR_SIGNALS', 'AHB_TARGET_SIGNALS',
    'AXIS_SIGNALS', 'AXIS_BYTE_SIGNALS',
    'SWD_SIGNALS', 'DBGAHB_TARGET_SIGNALS', 'GPIO_SIGNALS',
    'bus_member_names', 'passthrough_initiator_uses_initiator_boundary',
    'width_str', 'param_width_str',
]


def bus_member_names(iface) -> list:
    """Flattened member port names a bus-typed interface expands into at the
    module boundary.

    Mirrors the boundary expansion performed by SoCTopLevelBackend /
    SoCSubsystemBackend (e.g. a `dbg_ahb` iface `cpu_0_dbgahb` becomes the
    ports `cpu_0_dbgahb_slvaddr`, `cpu_0_dbgahb_slvwdata`, ...). The
    validator uses this so a per-member connection (`port: cpu_0_dbgahb_slvaddr`)
    is recognised as valid even though only the un-expanded bus iface name
    appears in `Module.interfaces`. Plain wire/clock/reset interfaces (no
    protocol expansion) return [].

    Names only — direction/width are intentionally not returned; callers that
    need those expand from the per-type signal tables above.

    Delegates to the adapter registry: `protocol_for(iface).member_names(iface)`.
    """
    proto = protocol_for(iface)
    if proto is None:
        return []
    return proto.member_names(iface)


def passthrough_initiator_uses_initiator_boundary(iface_params: Any) -> bool:
    """Decide the boundary form for a passthrough-initiator AHB iface.

    An AHB iface declared `direction: target` that is also the
    `passthrough` of an interconnect *initiator* can take one of two
    module-boundary forms, and the choice is encoded by whether the
    iface explicitly excludes `hready`:

      * No `hready` in EXCLUDE  -> cmsdk *initiator* boundary
        (no hsel / no hreadyout; bare `hready` is a module output).
        This matches the generated bus-matrix wrapper for a plain
        external master driving transactions IN (e.g. eth_ss_0 /
        eth_ss_1 on ethernet_ss_ahb — confirmed by the ASIC golden
        gate netlist). Returns True.

      * `hready` in EXCLUDE     -> AHB-Lite *slave/target* boundary
        minus the bare `hready` (hsel + addr-phase in; hrdata / hresp
        / hreadyout out). The `EXCLUDE: [hready]` is the designer's
        explicit signal that the slave-side boundary with `hreadyout`
        is wanted and the bare `hready` removed (e.g. cpu_ss on
        nanosoc_ss_cpu — confirmed by the hand-written
        nanosoc_ss_cpu.v reference). Returns False.

    Single source of truth shared by SoCTopLevelBackend and
    SoCSubsystemBackend (both the module-own-boundary path and the
    parent instantiation/connection path) so the child boundary and the
    parent instantiation cannot drift — the divergence that produced the
    eth_ss_0_hsel / cpu_ss_hreadyout UPIMI-E port mismatches.

    Delegates to `AhbProtocol.passthrough_uses_initiator_boundary`, the one
    place this heuristic now lives.
    """
    return AhbProtocol().passthrough_uses_initiator_boundary(iface_params)


def width_str(w: Any) -> str:
    """Convert width to SV declaration string. Returns '' for 1-bit, '[N-1:0]' otherwise."""
    if isinstance(w, int):
        return '' if w == 1 else f'[{w-1}:0]'
    if isinstance(w, str):
        w_clean = w.lstrip('$')
        try:
            v = int(w_clean)
            return '' if v == 1 else f'[{v-1}:0]'
        except ValueError:
            return f'[{w_clean}-1:0]'
    return ''


def param_width_str(w: Any) -> str:
    """Width string for parameterised widths like 'SYS_ADDR_W'."""
    if isinstance(w, int):
        return '' if w == 1 else f'[{w-1}:0]'
    if isinstance(w, str):
        w_clean = w.lstrip('$')
        try:
            v = int(w_clean)
            return '' if v == 1 else f'[{v-1}:0]'
        except ValueError:
            return f'[{w_clean}-1:0]'
    return ''
