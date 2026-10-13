"""Cortex-M debug-AHB slave-bus protocol adapter (`dbg_ahb`).

Tables moved verbatim from `protocol_utils.py`. This is the slcorem0
EXTERNAL_DAP=1 DBGAHB_* port group: a CoreSight-style debug slave bus driven by
an AHB-AP-attached bridge. It is a single-boundary (target-only) bus, so its
member names are emitted in table order (not the sorted union AHB uses).
"""

from __future__ import annotations

from typing import List

from .base import Protocol, SignalSpec
from .registry import register_protocol

# Debug-AHB slave bus (from the slave/target perspective).
DBGAHB_TARGET_SIGNALS = [
    ('slvaddr',  'in',  '32'),
    ('slvwdata', 'in',  '32'),
    ('slvtrans', 'in',  '2'),
    ('slvwrite', 'in',  '1'),
    ('slvsize',  'in',  '2'),
    ('slvrdata', 'out', '32'),
    ('slvready', 'out', '1'),
    ('slvresp',  'out', '1'),
]


@register_protocol
class DbgAhbProtocol(Protocol):
    type = "dbg_ahb"
    # Single boundary form (target only). Treated as non-bus so member_names
    # preserves table order (matching the legacy bus_member_names dbg_ahb path).
    is_bus = False

    TARGET = DBGAHB_TARGET_SIGNALS

    def table(self, role: str) -> List[SignalSpec]:
        return [SignalSpec(*t) for t in self.TARGET]
