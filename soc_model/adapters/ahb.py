"""AHB protocol adapter (roadmap doc 02 — `adapters/ahb.py`).

Owns the AHB initiator/target signal tables (moved verbatim from the legacy
`protocol_utils.py`) AND the load-bearing passthrough boundary heuristic
(`passthrough_uses_initiator_boundary`) — the one place that decision lives.
"""

from __future__ import annotations

from typing import Any, List

from .base import Protocol, SignalSpec
from .registry import register_protocol

# ---------------------------------------------------------------------------
# Raw tables — VERBATIM from protocol_utils.py. (suffix, direction, width_expr)
# Direction is from the named role's perspective.
# ---------------------------------------------------------------------------

# AHB initiator (master) signals: direction is from the initiator's perspective
AHB_INITIATOR_SIGNALS = [
    ('haddr',     'out', 'SYS_ADDR_W'),
    ('htrans',    'out', '2'),
    ('hwrite',    'out', '1'),
    ('hsize',     'out', '3'),
    ('hburst',    'out', '3'),
    ('hprot',     'out', '4'),
    ('hwdata',    'out', 'SYS_DATA_W'),
    ('hmastlock', 'out', '1'),
    ('hrdata',    'in',  'SYS_DATA_W'),
    ('hready',    'in',  '1'),
    ('hresp',     'in',  '1'),
]

# AHB target (slave) signals: direction is from the target's perspective
AHB_TARGET_SIGNALS = [
    ('hsel',      'in',  '1'),
    ('haddr',     'in',  'SYS_ADDR_W'),
    ('htrans',    'in',  '2'),
    ('hwrite',    'in',  '1'),
    ('hsize',     'in',  '3'),
    ('hburst',    'in',  '3'),
    ('hprot',     'in',  '4'),
    ('hwdata',    'in',  'SYS_DATA_W'),
    ('hmastlock', 'in',  '1'),
    ('hready',    'in',  '1'),
    ('hrdata',    'out', 'SYS_DATA_W'),
    ('hresp',     'out', '1'),
    ('hreadyout', 'out', '1'),
]


@register_protocol
class AhbProtocol(Protocol):
    type = "ahb"
    is_bus = True

    INITIATOR = AHB_INITIATOR_SIGNALS
    TARGET = AHB_TARGET_SIGNALS

    def table(self, role: str) -> List[SignalSpec]:
        rows = self.INITIATOR if role == "initiator" else self.TARGET
        return [SignalSpec(*t) for t in rows]

    def passthrough_uses_initiator_boundary(self, iface_params: Any) -> bool:
        """Decide the boundary form for a passthrough-initiator AHB iface.

        No `hready` in EXCLUDE -> cmsdk *initiator* boundary (True);
        `hready` in EXCLUDE     -> AHB-Lite slave/target boundary minus the bare
        `hready` (False). See the legacy `protocol_utils` docstring for the
        eth_ss-vs-cpu_ss provenance. Accepts the raw `params` dict (as the
        legacy function did) so callers need not build an Interface.
        """
        if not isinstance(iface_params, dict):
            return True
        exclude = iface_params.get('EXCLUDE', []) or []
        return 'hready' not in set(exclude)
