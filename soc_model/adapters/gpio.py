"""GPIO protocol adapter (`gpio`).

Table moved verbatim from `protocol_utils.py`. A simple in/out/outen wire group
(width `WIDTH`); single-direction so member names are in table order.
"""

from __future__ import annotations

from typing import List

from .base import Protocol, SignalSpec
from .registry import register_protocol

# GPIO signals
GPIO_SIGNALS = [
    ('in',     'in',  'WIDTH'),
    ('out',    'out', 'WIDTH'),
    ('outen',  'out', 'WIDTH'),
]


@register_protocol
class GpioProtocol(Protocol):
    type = "gpio"
    is_bus = False

    SIGNALS = GPIO_SIGNALS

    def table(self, role: str) -> List[SignalSpec]:
        return [SignalSpec(*t) for t in self.SIGNALS]
