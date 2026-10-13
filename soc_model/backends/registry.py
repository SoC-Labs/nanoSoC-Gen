"""Backend registry: @register_backend, all_backends, ordered, run_pipeline.

Roadmap doc 02, section "The registry". Backends register themselves via the
decorator; ``backends/__init__.py`` imports each module so the decorators
fire. ``ordered`` is a stable topological sort by (phase, after-deps);
``run_pipeline`` instantiates and runs a selection, honouring ``enabled`` and
``requires_jinja``.
"""

from __future__ import annotations

from typing import List, Optional, Set, Tuple, Type

from ._jinja import have_jinja
from .base import Backend, BackendResult, GenContext

_REGISTRY: List[Type[Backend]] = []


def register_backend(cls=None):
    """Class decorator. Registers a Backend subclass; rejects duplicate names."""
    def _add(c):
        if not c.name:
            raise ValueError(f"{c.__name__} must set .name")
        if any(x.name == c.name for x in _REGISTRY):
            raise ValueError(f"duplicate backend name {c.name!r}")
        _REGISTRY.append(c)
        return c
    return _add(cls) if cls is not None else _add


def all_backends() -> List[Type[Backend]]:
    """All registered backend classes, in registration order."""
    return list(_REGISTRY)


def ordered(selection: Optional[Set[str]] = None) -> List[Type[Backend]]:
    """Topological sort by (phase, `after` edges), filtered by `selection`.

    Stable: backends are first arranged by (phase, registration-index), then a
    dependency-respecting pass pulls any `after` dep ahead of its dependent.
    Only edges *within the chosen set* are honoured (an `after` dep that is not
    selected is ignored for ordering).
    """
    chosen = [c for c in _REGISTRY
              if selection is None or c.name in selection]
    return _toposort(chosen)


def _toposort(chosen: List[Type[Backend]]) -> List[Type[Backend]]:
    # Stable base order: phase, then original registration index.
    index = {c.name: i for i, c in enumerate(_REGISTRY)}
    base = sorted(chosen, key=lambda c: (int(c.phase), index.get(c.name, 0)))

    chosen_names = {c.name for c in base}
    by_name = {c.name: c for c in base}

    # Kahn's algorithm over the `after` edges restricted to the chosen set,
    # using the stable base order as the tie-break ready-queue ordering.
    deps = {c.name: {d for d in c.after if d in chosen_names} for c in base}
    result: List[Type[Backend]] = []
    placed: Set[str] = set()

    remaining = list(base)
    progress = True
    while remaining and progress:
        progress = False
        still: List[Type[Backend]] = []
        for c in remaining:
            if deps[c.name] <= placed:
                result.append(c)
                placed.add(c.name)
                progress = True
            else:
                still.append(c)
        remaining = still
    # Any cycle leftovers: append in stable base order (defensive).
    result.extend(remaining)
    return result


def run_pipeline(ctx: GenContext,
                 selection: Optional[Set[str]] = None
                 ) -> List[Tuple[str, BackendResult]]:
    """Instantiate and run each ordered backend, returning (name, result)."""
    out: List[Tuple[str, BackendResult]] = []
    for cls in ordered(selection):
        be = cls()
        if not be.enabled(ctx):
            out.append((cls.name, BackendResult(skipped=True, reason="disabled")))
            continue
        if be.requires_jinja and not have_jinja():
            out.append((cls.name, BackendResult(skipped=True, reason="no jinja2")))
            continue
        out.append((cls.name, be.run(ctx)))
    return out
