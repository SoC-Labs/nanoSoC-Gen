"""SWD protocol adapter (`swd`).

Table moved verbatim from `protocol_utils.py` (signals from the receiver
perspective). The legacy `bus_member_names` strips a trailing `_swd` from the
iface name before prefixing (e.g. `cpu_0_swd` -> `cpu_0_swdi`), so this protocol
overrides `base_name` to preserve that.
"""

from __future__ import annotations

from typing import List

from .base import Protocol, SignalSpec
from .registry import register_protocol

# SWD signals (from receiver perspective)
SWD_SIGNALS = [
    ('swdi',   'in',  '1'),
    ('swclk',  'in',  '1'),
    ('swdo',   'out', '1'),
    ('swdoen', 'out', '1'),
]


@register_protocol
class SwdProtocol(Protocol):
    type = "swd"
    is_bus = False

    SIGNALS = SWD_SIGNALS

    def table(self, role: str) -> List[SignalSpec]:
        return [SignalSpec(*t) for t in self.SIGNALS]

    def base_name(self, iface) -> str:
        name = iface.name
        return name[:-4] if name.endswith('_swd') else name
