"""Bus-protocol adapter layer (roadmap doc 02, milestone M4).

A `Protocol` is the single data-driven source of truth for what signals a bus
interface expands into at a module boundary. Each concrete protocol owns its
signal table (moved verbatim from the legacy `backends/protocol_utils.py`) and
registers itself with the protocol registry via `@register_protocol`.

`backends/protocol_utils.py` is now a thin shim that re-exports the tables from
here and delegates `bus_member_names` / the AHB boundary heuristic to the
registry, so existing backend imports keep working unchanged.

Importing this package registers every protocol (the module-level
`@register_protocol` decorators fire on import).
"""

from .base import Protocol, SignalSpec, SignalPort
from .registry import register_protocol, protocol_for, all_protocols

# Import each protocol module so its @register_protocol decorator fires.
from . import ahb        # noqa: F401  (AHB initiator/target + dbg-ahb heuristic owner)
from . import apb        # noqa: F401
from . import dbgahb     # noqa: F401
from . import axis       # noqa: F401
from . import swd        # noqa: F401
from . import gpio       # noqa: F401

# AhbProtocol is the boundary-heuristic owner; export it for direct use/tests.
from .ahb import AhbProtocol
from .apb import ApbProtocol
from .dbgahb import DbgAhbProtocol
from .axis import AxisProtocol, AxisByteProtocol
from .swd import SwdProtocol
from .gpio import GpioProtocol

__all__ = [
    'Protocol', 'SignalSpec', 'SignalPort',
    'register_protocol', 'protocol_for', 'all_protocols',
    'AhbProtocol', 'ApbProtocol', 'DbgAhbProtocol', 'AxisProtocol',
    'AxisByteProtocol', 'SwdProtocol', 'GpioProtocol',
]
