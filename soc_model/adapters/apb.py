"""APB protocol adapter (`apb`).

APB3 completer/requester signal tables in the same shape as the AHB adapter
(suffix, direction, width-expr), with direction from the named role's
perspective. `apb` has always been a declarable `Interface.type` (see
`soc_model.model.Interface`), but until now it had no registered protocol, so
`bus_member_names()` returned [] and the binder could not resolve APB bundle
sub-signals — cross-instance references to e.g. `<iface>_PREADY` were silently
left dangling (the undriven-USRT-PREADY class of bug: an APB bridge whose
PREADY input floats hangs the bus on the first transaction).

Width expressions reuse the AHB tokens: 'SYS_ADDR_W' resolves from the iface's
ADDR_WIDTH/ADDR_W param, 'SYS_DATA_W' from DATA_WIDTH/DATA_W (the resolution
itself is the backends' job, as for every other protocol).
"""

from __future__ import annotations

from typing import List

from .base import Protocol, SignalSpec
from .registry import register_protocol

# APB requester (initiator) signals: direction is from the requester's perspective
APB_INITIATOR_SIGNALS = [
    ('psel',    'out', '1'),
    ('penable', 'out', '1'),
    ('paddr',   'out', 'SYS_ADDR_W'),
    ('pwrite',  'out', '1'),
    ('pwdata',  'out', 'SYS_DATA_W'),
    ('prdata',  'in',  'SYS_DATA_W'),
    ('pready',  'in',  '1'),
    ('pslverr', 'in',  '1'),
]

# APB completer (target) signals: direction is from the completer's perspective
APB_TARGET_SIGNALS = [
    ('psel',    'in',  '1'),
    ('penable', 'in',  '1'),
    ('paddr',   'in',  'SYS_ADDR_W'),
    ('pwrite',  'in',  '1'),
    ('pwdata',  'in',  'SYS_DATA_W'),
    ('prdata',  'out', 'SYS_DATA_W'),
    ('pready',  'out', '1'),
    ('pslverr', 'out', '1'),
]


@register_protocol
class ApbProtocol(Protocol):
    type = "apb"
    is_bus = True

    INITIATOR = APB_INITIATOR_SIGNALS
    TARGET = APB_TARGET_SIGNALS

    def table(self, role: str) -> List[SignalSpec]:
        rows = self.INITIATOR if role == "initiator" else self.TARGET
        return [SignalSpec(*t) for t in rows]

    def rtl_bases(self, iface) -> List[str]:
        """Hand-written RTL names an APB port `<prefix>_P<SIG>`, so an iface
        called `DEBUG_APB` has members `DEBUG_PSEL`, `DEBUG_PADDR`, ... (the
        APB suffixes already start with `p`). Recognition only."""
        base = self.base_name(iface)
        bases = [base]
        if base.lower().endswith('_apb') and len(base) > 4:
            bases.append(base[:-4])
        return bases
