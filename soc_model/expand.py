# Copyright 2026, SoC Labs (www.soclabs.org)
"""Opt-in YAML desugaring pre-pass for nanosoc_gen (roadmap doc 05).

A single pure function, :func:`expand_module_dict`, rewrites a raw module dict
(the ``{'module': {...}}`` shape :class:`SoCParser` loads) into the canonical
shape :class:`SoCBuilder` already consumes — so the builder and every backend
stay ignorant of sugar.

The pass is **opt-in and transparent**:

* :class:`SoCParser` only applies it when constructed with an ``expander``
  (default ``None`` -> exactly today's behaviour); and
* :func:`expand_module_dict` returns its input **unchanged** unless a
  recognised sugar key is present.

So existing (sugar-free) YAML expands to byte-identical canonical output — the
non-breakage guarantee.  v1 implements **Feature 1 — presets / ``uses:``**
(mixins for repeated blocks).  Later doc-05 features (address auto-assignment,
terse ``bus:`` wiring, derived ``phys_size``) slot into :func:`_expand_module`
behind the same identity guard.
"""

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .yaml_ints import load_yaml_checked

# Module-level sugar keys.  If none are present the pass is a strict identity —
# this set is the single source of truth for "does this module use sugar?".
_MODULE_SUGAR_KEYS = ('uses',)

# Fields used to match list items when a preset list merges with a module list.
_ID_FIELDS = ('name', 'instance_name', 'instance')


class PresetLibrary:
    """A ``name -> partial-module-dict`` map loaded from ``lib/presets/*.yaml``.

    Each preset file is ``{'preset': {'name': <id>, <module keys...>}}``.  The
    preset's own ``name`` is the library key, not a module field, so it is
    stripped from the merge body.
    """

    def __init__(self, presets: Optional[Dict[str, dict]] = None):
        self._presets: Dict[str, dict] = dict(presets or {})

    def get(self, name: str) -> Optional[dict]:
        return self._presets.get(name)

    def names(self) -> List[str]:
        return sorted(self._presets)

    def __len__(self) -> int:
        return len(self._presets)

    @classmethod
    def from_dirs(cls, dirs) -> "PresetLibrary":
        """Build a library by scanning each dir for ``*.yaml`` with a ``preset:`` key."""
        presets: Dict[str, dict] = {}
        for d in dirs:
            d = Path(d)
            if not d.is_dir():
                continue
            for f in sorted(d.glob('*.yaml')):
                if f.name.startswith('.'):
                    continue
                try:
                    # misread integers are refused (YamlIntegerError), not skipped
                    data = load_yaml_checked(f.read_text(), str(f)) or {}
                except (yaml.YAMLError, OSError):
                    continue
                body = data.get('preset') if isinstance(data, dict) else None
                if isinstance(body, dict) and body.get('name'):
                    name = body['name']
                    presets[name] = {k: v for k, v in body.items() if k != 'name'}
        return cls(presets)


def make_expander(presets: PresetLibrary, params_ctx: Optional[dict] = None):
    """Return a 1-arg callable suitable for ``SoCParser(expander=...)``."""
    def _expander(data: Any) -> Any:
        return expand_module_dict(data, presets=presets, params_ctx=params_ctx)
    return _expander


def expand_module_dict(data: Any, *, presets: PresetLibrary,
                       params_ctx: Optional[dict] = None) -> Any:
    """Desugar a raw ``{'module': {...}}`` dict into canonical builder shape.

    Pure (never mutates ``data``), idempotent, and a **strict identity** when
    the module carries no recognised sugar key.
    """
    if not isinstance(data, dict) or 'module' not in data:
        return data
    mod = data['module']
    if not isinstance(mod, dict) or not _has_module_sugar(mod):
        return data  # IDENTITY — sugar-free YAML is returned untouched
    out = copy.deepcopy(data)
    out['module'] = _expand_module(out['module'], presets)
    return out


def _has_module_sugar(mod: dict) -> bool:
    if any(k in mod for k in _MODULE_SUGAR_KEYS):
        return True
    # Feature 2 sugar lives inside interconnects (auto_base).
    for ic in mod.get('interconnects') or []:
        if isinstance(ic, dict) and 'auto_base' in ic:
            return True
    return False


def _expand_module(mod: dict, presets: PresetLibrary) -> dict:
    """Apply each desugar feature in turn (F1: uses; F2: auto_base)."""
    if 'uses' in mod:
        mod = _apply_uses(mod, presets)
    if any(isinstance(ic, dict) and 'auto_base' in ic
           for ic in mod.get('interconnects') or []):
        mod = _apply_auto_base(mod)
    return mod


# ---------------------------------------------------------------------------
# Feature 1 — presets / uses: (deep-merge mixins, module keys win)
# ---------------------------------------------------------------------------
def _apply_uses(mod: dict, presets: PresetLibrary) -> dict:
    uses = mod.get('uses')
    if isinstance(uses, str):
        uses = [uses]
    if not isinstance(uses, list):
        raise ValueError(
            f"'uses:' must be a string or list of preset names, got "
            f"{type(uses).__name__}")

    merged: dict = {}
    for name in uses:
        preset = presets.get(name)
        if preset is None:
            avail = ', '.join(presets.names()) or '(none)'
            raise ValueError(
                f"unknown preset '{name}' in uses: (available: {avail})")
        if 'uses' in preset:
            raise ValueError(
                f"preset '{name}' itself declares 'uses:'; recursive presets "
                f"are not supported in v1")
        merged = _deep_merge(merged, preset)   # later preset wins over earlier
    own = {k: v for k, v in mod.items() if k != 'uses'}
    return _deep_merge(merged, own)            # module's own keys win over presets


def _deep_merge(base: Any, overlay: Any) -> Any:
    """Merge ``overlay`` onto ``base`` (overlay wins).

    * dict + dict  -> recursive merge
    * list + list  -> identity-keyed merge (an overlay item whose name/
      instance_name/instance matches a base item **replaces** it; others append)
    * anything else -> overlay wins
    Always returns deep copies, never aliasing ``base`` or ``overlay``.
    """
    if isinstance(base, dict) and isinstance(overlay, dict):
        out = copy.deepcopy(base)
        for k, v in overlay.items():
            out[k] = _deep_merge(out[k], v) if k in out else copy.deepcopy(v)
        return out
    if isinstance(base, list) and isinstance(overlay, list):
        return _merge_lists(base, overlay)
    return copy.deepcopy(overlay)


def _merge_lists(base: list, overlay: list) -> list:
    out = [copy.deepcopy(x) for x in base]
    pos: Dict[Any, int] = {}
    for i, item in enumerate(out):
        iid = _item_id(item)
        if iid is not None:
            pos[iid] = i
    for item in overlay:
        iid = _item_id(item)
        if iid is not None and iid in pos:
            out[pos[iid]] = copy.deepcopy(item)   # identity match -> replace
        else:
            out.append(copy.deepcopy(item))
            if iid is not None:
                pos[iid] = len(out) - 1
    return out


def _item_id(item: Any):
    if isinstance(item, dict):
        for f in _ID_FIELDS:
            if f in item:
                val = item[f]
                if isinstance(val, (str, int, bool)):
                    return (f, val)
    return None


# ---------------------------------------------------------------------------
# Feature 2 — address auto-assignment (auto_base + size shorthand)
# ---------------------------------------------------------------------------
# An interconnect may carry ``auto_base: {from: <addr>, align: <stride>}``. The
# expander packs every target that has NO explicit ``base:`` into an alignment-
# respecting map starting at ``from``, in YAML order, writing back concrete
# integer ``base:`` values; targets that DO specify ``base:`` stay pinned
# (mixed mode). ``size:`` / ``base:`` / ``from`` / ``align`` accept the
# shorthand ``16M``/``64K``/``4G`` (and ints / 0x-hex) and are rewritten to
# integers. A larger-than-stride target (e.g. a 64M XiP aperture) naturally
# consumes several stride slots, so the next auto target lands past the hole.
# An auto-packed window overlapping a pinned window is a hard error. The
# ``auto_base`` key is consumed (removed) from the canonical output.

_SIZE_SUFFIX = {'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3, 'T': 1024 ** 4}


def _parse_size(val: Any, *, what: str = "size") -> int:
    """Parse an int / 0x-hex string / NN[K|M|G|T] shorthand into an integer."""
    if isinstance(val, bool):
        raise ValueError(f"auto_base {what}: expected a size, got bool {val!r}")
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        s = val.strip()
        try:
            if s[:2].lower() == '0x':
                return int(s, 16)
            if s and s[-1].upper() in _SIZE_SUFFIX:
                mult = _SIZE_SUFFIX[s[-1].upper()]
                f = float(s[:-1].strip())
                iv = int(f * mult)
                if iv != f * mult:
                    raise ValueError  # non-integer byte count
                return iv
            return int(s, 10)
        except ValueError:
            raise ValueError(
                f"auto_base {what}: cannot parse {val!r} as a literal size "
                f"(use an int, 0x-hex, or NN[K|M|G|T]); $PARAM / expressions "
                f"are not supported inside an auto_base interconnect")
    raise ValueError(f"auto_base {what}: expected a size, got {type(val).__name__}")


def _align_up(x: int, align: int) -> int:
    return ((x + align - 1) // align) * align if align > 0 else x


def _overlaps(b1: int, s1: int, b2: int, s2: int) -> bool:
    return b1 < b2 + s2 and b2 < b1 + s1


def _apply_auto_base(mod: dict) -> dict:
    ics = mod.get('interconnects')
    if isinstance(ics, list):
        mod['interconnects'] = [
            _pack_interconnect(ic)
            if isinstance(ic, dict) and 'auto_base' in ic else ic
            for ic in ics
        ]
    return mod


def _pack_interconnect(ic: dict) -> dict:
    ic = copy.deepcopy(ic)
    spec = ic.pop('auto_base')
    if not isinstance(spec, dict):
        raise ValueError(
            "auto_base: must be a mapping, e.g. {from: 0x20000000, align: 16M}")
    start = _parse_size(spec.get('from', 0), what="from")
    align = _parse_size(spec.get('align', 0), what="align")
    targets = ic.get('targets')
    if not isinstance(targets, list):
        return ic

    def nm(t):
        return (t.get('name') if isinstance(t, dict) else None) or '<unnamed>'

    # 1) normalise size/base shorthand on every target in this interconnect
    pinned = []  # (name, base, size)
    for t in targets:
        if not isinstance(t, dict):
            continue
        if t.get('size') is not None:
            t['size'] = _parse_size(t['size'], what=f"target '{nm(t)}' size")
        if t.get('base') is not None:
            t['base'] = _parse_size(t['base'], what=f"target '{nm(t)}' base")
            pinned.append((nm(t), t['base'], t.get('size') or 0))

    # 2) pack the unpinned (no base:) targets in YAML order
    cursor = start
    for t in targets:
        if not isinstance(t, dict) or t.get('base') is not None:
            continue
        size = t.get('size')
        if size is None:
            raise ValueError(
                f"auto_base: target '{nm(t)}' has neither base: nor size:")
        base = _align_up(cursor, align)
        for (pn, pb, ps) in pinned:
            if _overlaps(base, size, pb, ps):
                raise ValueError(
                    f"auto_base: target '{nm(t)}' @ {hex(base)} (size {hex(size)}) "
                    f"overlaps pinned window '{pn}' @ {hex(pb)} (size {hex(ps)})")
        t['base'] = base
        cursor = base + _align_up(size, align)
    return ic
