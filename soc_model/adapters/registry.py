"""Protocol registry (roadmap doc 02).

`@register_protocol` registers a `Protocol` subclass keyed by its `.type`.
`protocol_for(iface_or_type)` resolves the protocol for an `Interface` (or a
bare type string); `all_protocols()` lists the registered instances.

The registry stores singleton instances (protocols are stateless), so callers
get a shared object per type.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .base import Protocol

# type-string -> singleton Protocol instance
_REGISTRY: Dict[str, Protocol] = {}

# Interface.type aliases that map onto the same protocol (e.g. 'axis_stream'
# is the long spelling of 'axis').
_ALIASES: Dict[str, str] = {
    'axis_stream': 'axis',
}


def register_protocol(cls):
    """Class decorator: instantiate `cls` and register it under `cls.type`.

    Idempotent per type within a process; a duplicate `type` is rejected so two
    protocols cannot silently shadow each other.
    """
    assert getattr(cls, 'type', ''), f"{cls.__name__} must set .type"
    if cls.type in _REGISTRY and type(_REGISTRY[cls.type]) is not cls:
        raise ValueError(f"duplicate protocol type {cls.type!r}")
    _REGISTRY[cls.type] = cls()
    return cls


def _resolve_type(iface_or_type) -> Optional[str]:
    t = iface_or_type if isinstance(iface_or_type, str) else getattr(
        iface_or_type, 'type', None)
    if t is None:
        return None
    return _ALIASES.get(t, t)


def protocol_for(iface_or_type) -> Optional[Protocol]:
    """Return the registered `Protocol` for an `Interface` or type string.

    Returns `None` for plain wire/clock/reset (no protocol expansion) or any
    unregistered type — callers treat that as "not a bus".
    """
    t = _resolve_type(iface_or_type)
    if t is None:
        return None
    return _REGISTRY.get(t)


def all_protocols() -> List[Protocol]:
    """All registered protocol instances (de-duplicated, insertion order)."""
    seen = []
    for p in _REGISTRY.values():
        if p not in seen:
            seen.append(p)
    return seen
