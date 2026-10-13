"""AXI-Stream protocol adapters (`axis` / `axis_stream`, and `axis_byte`).

Tables moved verbatim from `protocol_utils.py`. Direction is from the source's
perspective. Both are single-direction streams, so member names are emitted in
table order. The `axis_stream` long spelling aliases onto `axis` in the
registry.
"""

from __future__ import annotations

from typing import List

from .base import Protocol, SignalSpec
from .registry import register_protocol

# AXIS stream signals: direction is from the source's perspective
AXIS_SIGNALS = [
    ('tvalid', 'out', '1'),
    ('tready', 'in',  '1'),
    ('tdata',  'out', 'SYS_DATA_W'),
    ('tstrb',  'out', '4'),
    ('tlast',  'out', '1'),
]

# AXIS byte signals
AXIS_BYTE_SIGNALS = [
    ('tvalid', 'out', '1'),
    ('tready', 'in',  '1'),
    ('tdata',  'out', '8'),
]


@register_protocol
class AxisProtocol(Protocol):
    type = "axis"
    is_bus = False

    SIGNALS = AXIS_SIGNALS

    def table(self, role: str) -> List[SignalSpec]:
        return [SignalSpec(*t) for t in self.SIGNALS]


@register_protocol
class AxisByteProtocol(Protocol):
    type = "axis_byte"
    is_bus = False

    SIGNALS = AXIS_BYTE_SIGNALS
    # Hand-written byte streams (socdebug_ft1248_control txd/rxd) also carry
    # tlast. Recognised on gen: False modules only; never expanded.
    rtl_extra_suffixes = ('tlast',)

    def table(self, role: str) -> List[SignalSpec]:
        return [SignalSpec(*t) for t in self.SIGNALS]
