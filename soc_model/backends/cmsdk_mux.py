"""``impl: cmsdk_mux`` — an AHB-Lite interconnect built from Quickstart CMSDK parts.

Copyright (C) 2026, SoC Labs (www.soclabs.org)

The default interconnect implementation (``impl: busmatrix``) fills in Arm's
``cmsdk_ahb_busmatrix`` templates with ``BuildBusMatrix.pl``. Those templates
ship only with Arm Academic Access; a Cortex-M0 Quickstart licensee does not
hold them. This implementation instead emits a SoC Labs-written
``<ic>_lite`` core that instantiates three components that DO ship in the
Quickstart bundle, referenced by path through ``$(ARM_CORSTONE_101_DIR)`` and
never copied:

  cmsdk_ahb_master_mux     up to 3 initiators onto one bus (port 2 = high
                           priority, ports 0/1 round-robin)
  cmsdk_ahb_slave_mux      up to 10 targets' responses back onto one bus
  cmsdk_ahb_default_slave  ERROR response for unmapped addresses

The ``<ic>_lite`` module name and port list are identical to the
BuildBusMatrix output, so the generated wrapper and everything above it are
unchanged.

Topology (derived, not configured)
----------------------------------
* **Shared bus.** Every initiator enters one master mux (a second master mux
  is cascaded into port 2 for 4-5 initiators). The mux output is decoded by a
  remap-aware decoder built from the SAME effective address maps the bus
  matrix XML and the address-map report are built from
  (``SoCAhbBackend._build_initiator_data`` / ``_generate_remap_configurations``
  / ``_compute_effective_address_map``); when initiators see different maps,
  the decoder is keyed by the master mux's address-phase ``HMASTER``.
* **Private passthrough paths.** A target with ``passthrough:`` leaves this
  module for another interconnect. If it sat on the shared bus, an initiator
  stalled on it would hold the shared bus while a transfer arriving from that
  other interconnect (a ``passthrough:`` initiator) waited for the shared bus
  here: a deadlock across the two interconnects. So a passthrough target is
  reached on a private path from its single initiator, split off by a
  front-end decoder and merged back with a slave mux.
* **Each target sees a port of its own.** ``HSEL`` is a pure address
  decode; ``HREADYMUX`` is the target's own view, low only during its own
  waited data phase. ``HTRANS`` shows the bus transfer when it is addressed
  to that target, a master is on the bus (``HSELM``), and either the bus is
  ready or the target is itself in the data phase (its ``HREADYMUX`` then IS
  the bus ``HREADY``, so a waited NONSEQ/SEQ/BUSY stays visible and a burst
  stays a legal burst at the target). While ANOTHER target stalls the bus
  the target is shown IDLE: its ``HREADYMUX`` is high, and it must not take
  a transfer the bus has not accepted. So a target takes a transfer exactly
  when the bus does, which the subsystem boundary (``system`` -> the main
  interconnect's ``cpu_0``, which uses its own HREADY) depends on.
* **Locked transfers.** A private-path initiator's ``HMASTLOCK`` reaches the
  shared master mux except while it drives a NONSEQ/SEQ to a private target.
  A locked transfer on the private path therefore does not lock the shared
  bus (holding it would recreate the deadlock the private path exists to
  break), while an IDLE or BUSY inside a locked sequence keeps the lock
  whatever its don't-care HADDR decodes to (review R-ICX-2: gating on the
  address alone let another master in during such an IDLE). A locked
  sequence is atomic on each path while it stays on that path.
* **Keying through a cascade.** The decoder is keyed by the top master mux's
  address-phase ``HMASTER``. The cascaded mux's own ``HMASTER`` does not
  follow a transfer the top mux holds in its port-2 buffer, so initiators
  sharing port 2 must see one map (checked).

Anything this topology cannot represent raises :class:`CmsdkMuxError` (and
the validator reports it as an error): more than 5 initiators (3 per master
mux plus one cascade level), cascaded initiators with different maps, more
than 10 slave-mux ports (targets plus the default slave), an address width
other than 32, overlapping effective regions, a passthrough target
reachable by more than one initiator, and a passthrough loop (a passthrough
initiator reaching a passthrough target).

Input the core would otherwise render wrongly while exiting 0 is refused the
same way (none of it is guessed at or silently dropped):

* an effective region outside 0x0000_0000-0xFFFF_FFFF (HADDR is 32 bits),
* a size or visibility aperture <= 0, or a base/size/aperture that is not a
  number,
* more than 9 private passthrough targets on one initiator (its front-end
  slave mux has 10 ports and port 0 is the shared bus),
* a ``remap_bit`` that is not an integer 0..3 (REMAP is 4 bits), a
  ``remap:`` that is not a mapping, and a ``remap_behaviour`` other than
  alias / move / none,
* an interconnect ``type:`` that is not AHB (``ahb_lite`` or ``ahb``),
* initiator / target names that are not plain Verilog identifiers, that
  become equal when upper-cased (the core's port names are
  ``H<SIGNAL>_<NAME upper-cased>``), or that the core uses itself
  (a target called ``default``; ``a`` / ``shared``, whose derived wires
  would differ from the core's own only in case),
* an ``impl:`` key that is present but empty, null or not a string, and an
  unknown interconnect key that looks like a misspelt ``impl`` or whose value
  is ``cmsdk_mux`` (otherwise the interconnect is silently a bus matrix).

When the AHB backend or the CLI refuses an interconnect, a ``<ic>_lite.v``
(and cmsdk_mux flist) left by an earlier good run is deleted, so a stale core
is never picked up by a flow that ignores the exit code.

Performance: it is a shared bus. Transfers that a bus matrix would run in
parallel (CPU to one target, DMA to another) are serialised; port 2 (the
third initiator onwards) has priority over ports 0/1, which round-robin.
"""

import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

IMPLS = ('busmatrix', 'cmsdk_mux')
DEFAULT_IMPL = 'busmatrix'

MASTER_MUX_PORTS = 3      # cmsdk_ahb_master_mux: S0, S1 (round-robin), S2 (high priority)
SLAVE_MUX_PORTS = 10      # cmsdk_ahb_slave_mux: PORT0..PORT9
MAX_INITIATORS = 2 + MASTER_MUX_PORTS   # one cascade level into port 2
# An initiator's front-end slave mux: PORT0 is its way onto the shared bus,
# PORT1..PORT9 its private passthrough targets.
MAX_PRIVATE_TARGETS = SLAVE_MUX_PORTS - 1
REMAP_BITS = 4            # the core's REMAP input is [3:0]
ADDR_SPACE = 1 << 32      # the core decodes HADDR[31:0]

# Interconnect ``type:`` values the core implements (it is AHB-Lite only).
AHB_TYPES = ('ahb_lite', 'ahb')
# ``remap_behaviour`` values the effective-map code gives a meaning to; any
# other string would silently act as 'alias'/'none'.
REMAP_BEHAVIOURS = ('alias', 'move', 'none')

_VERILOG_ID = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')

# Target names (upper-cased, as the core uses them) that collide with the
# core's own identifiers.
_RESERVED_TARGET_NAMES = {
    'DEFAULT': "the core's default slave already uses b_dec_DEFAULT and "
               "b_hsel_DEFAULT",
    'A': "its decode wire b_dec_A would differ only in case from the core's "
         "b_dec_a, which a case-insensitive tool merges",
    'SHARED': "as a private target its select fe_<INITIATOR>_sel_SHARED would "
              "differ only in case from fe_<INITIATOR>_sel_shared",
}

# The three Quickstart components, as they sit under $(ARM_CORSTONE_101_DIR).
CMSDK_COMPONENTS = (
    'cmsdk_ahb_master_mux/verilog/cmsdk_ahb_master_mux.v',
    'cmsdk_ahb_slave_mux/verilog/cmsdk_ahb_slave_mux.v',
    'cmsdk_ahb_default_slave/verilog/cmsdk_ahb_default_slave.v',
)


class CmsdkMuxError(ValueError):
    """The interconnect cannot be built as ``impl: cmsdk_mux``."""


def ic_impl(ic) -> Any:
    """The interconnect's ``impl`` as written (the model defaults it to the
    bus matrix when the YAML has no ``impl:`` key).

    An ``impl:`` that is present but empty or null is returned as it is, NOT
    turned into the default: callers must refuse anything not in IMPLS (see
    :func:`impl_error`), or ``impl: ""`` would silently build a bus matrix.
    """
    return getattr(ic, 'impl', DEFAULT_IMPL)


def impl_error(ic) -> Optional[str]:
    """Why ``ic``'s ``impl`` is unusable, or None when it is one of IMPLS."""
    impl = ic_impl(ic)
    if not isinstance(impl, str) or not impl.strip():
        got = 'empty' if isinstance(impl, str) else (
            'null' if impl is None else f'not a string ({impl!r})')
        return (f"{ic.name}: 'impl:' is present but {got}; write impl: busmatrix "
                f"or impl: cmsdk_mux, or delete the key to get the default "
                f"({DEFAULT_IMPL})")
    if impl not in IMPLS:
        return (f"{ic.name}: unknown interconnect impl '{impl}' "
                f"(expected one of {list(IMPLS)})")
    return None


def _edit_distance(a: str, b: str) -> int:
    """Edit distance counting a swap of two adjacent characters as one edit
    (optimal string alignment), so 'ipml' is one edit from 'impl'."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1,
                          d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def _looks_like_impl_key(key: str) -> bool:
    """One edit (or a case change) from 'impl': imp, Impl, ipml, impel, mpl.
    Not 'ip' or 'implementation_notes' (review R-ICX-2: those are harmless
    keys and were refused)."""
    return _edit_distance(key.strip().lower(), 'impl') <= 1


def _misspelling_of(key: Any, known) -> Optional[str]:
    """The known key that ``key`` is most likely a misspelling of, or None:
    at most two edits away, or at least 80% similar (passthru/passthrough)."""
    import difflib
    k = str(key).strip().lower()
    if not k:
        return None
    best = min(known, key=lambda c: _edit_distance(k, c))
    if _edit_distance(k, best) <= 2 and len(k) > 2:
        return best
    close = difflib.get_close_matches(k, list(known), n=1, cutoff=0.8)
    return close[0] if close else None


def _is_cmsdk_mux_value(value: Any) -> bool:
    return (isinstance(value, str)
            and value.strip().lower().replace('-', '_') == 'cmsdk_mux')


def unknown_interconnect_keys(ic) -> Dict[str, Any]:
    """The interconnect's YAML keys that the builder does not read, with
    their values (recorded by ``SoCBuilder._build_interconnect``; empty for an
    Interconnect built in code)."""
    return dict(getattr(ic, '_unknown_yaml_keys', None) or {})


def check_interconnect_keys(ic) -> Tuple[List[str], List[str]]:
    """(errors, warnings) for the interconnect's unknown YAML keys.

    An unknown key is ignored by the builder. It is an error when its value
    is ``cmsdk_mux`` (the author meant impl: cmsdk_mux and would silently get
    a bus matrix), or when the interconnect IS cmsdk_mux and the key is one
    edit from ``impl`` (``imp``, ``Impl``, ``ipml``: a second, contradicting
    impl). Anything else, on any interconnect, is only a warning: a
    bus-matrix interconnect with a stray key builds exactly as before.
    On a cmsdk_mux interconnect the warnings also list unknown keys on its
    targets, initiators and their target entries that are not misspellings
    of a known key (misspellings are refused by :func:`plan_cmsdk_mux`).
    """
    errors: List[str] = []
    warnings: List[str] = []
    is_mux = ic_impl(ic) == 'cmsdk_mux'
    for key, value in unknown_interconnect_keys(ic).items():
        if _is_cmsdk_mux_value(value) or (is_mux and _looks_like_impl_key(str(key))):
            errors.append(
                f"{ic.name}: unknown interconnect key '{key}' (value {value!r}) is "
                f"ignored, so this interconnect would be built as impl: "
                f"{ic_impl(ic)}. Did you mean 'impl'? Write impl: cmsdk_mux or "
                f"impl: busmatrix, and remove '{key}'.")
        else:
            warnings.append(
                f"{ic.name}: unknown interconnect key '{key}' is ignored")
    if is_mux:
        for where, key, _value, known in _nested_unknown_keys(ic):
            if _misspelling_of(key, known) is None:
                warnings.append(f"{ic.name}: {where} has unknown key '{key}'; it is ignored")
    return errors, warnings


# YAML keys the effective-map code reads from a visibility window and from
# its remap: spec (soc_model/backends/ahb.py _build_initiator_data).
VISIBILITY_YAML_KEYS = frozenset({'base', 'aperture', 'remap', 'remap_behaviour'})
REMAP_YAML_KEYS = frozenset({'remapping', 'remap_bit', 'remap_behaviour'})


def _nested_unknown_keys(ic):
    """(where, key, value, known keys) for every key the builder or the map
    code ignores on a target, an initiator, an initiator's target entry, a
    visibility window or a remap spec."""
    from ..builder import (INTERCONNECT_TARGET_YAML_KEYS, INTERCONNECT_INITIATOR_YAML_KEYS,
                           INITIATOR_TARGET_YAML_KEYS)
    out = []
    for t in ic.targets:
        for k, v in (getattr(t, '_unknown_yaml_keys', None) or {}).items():
            out.append((f"target '{t.name}'", k, v, INTERCONNECT_TARGET_YAML_KEYS))
    for init in ic.initiators:
        for k, v in (getattr(init, '_unknown_yaml_keys', None) or {}).items():
            out.append((f"initiator '{init.name}'", k, v, INTERCONNECT_INITIATOR_YAML_KEYS))
        for it in init.targets:
            where = f"initiator '{init.name}', target '{it.name}'"
            for k, v in (getattr(it, '_unknown_yaml_keys', None) or {}).items():
                out.append((where, k, v, INITIATOR_TARGET_YAML_KEYS))
            if not isinstance(it.visibility, list):
                continue
            for n, vis in enumerate(it.visibility):
                if not isinstance(vis, dict):
                    continue
                for k, v in vis.items():
                    if k not in VISIBILITY_YAML_KEYS:
                        out.append((f"{where}, visibility[{n}]", k, v, VISIBILITY_YAML_KEYS))
                remap = vis.get('remap')
                if isinstance(remap, dict):
                    for k, v in remap.items():
                        if k not in REMAP_YAML_KEYS:
                            out.append((f"{where}, visibility[{n}].remap", k, v, REMAP_YAML_KEYS))
    return out


def _check_nested_keys(ic) -> None:
    """Refuse a misspelt key on a target, initiator, target entry, window or
    remap spec (review R-ICX-2): the builder and the map code ignore it, so
    `passthru` put a passthrough target on the shared bus (the deadlock
    topology), `remaping` made a remap window always visible, `remap_behavior`
    turned a move into an alias and `aperature` gave a window the target's
    size, each with exit 0."""
    for where, key, value, known in _nested_unknown_keys(ic):
        guess = _misspelling_of(key, known)
        if guess is not None:
            raise CmsdkMuxError(
                f"{ic.name}: {where} has unknown key '{key}' (value {value!r}); "
                f"did you mean '{guess}'? impl: cmsdk_mux ignores unknown keys, so "
                f"the core would be built as if '{guess}' were absent. Fix the key.")


# ---------------------------------------------------------------------------
# Effective maps (reused from the bus-matrix backend, never re-derived)
# ---------------------------------------------------------------------------

def _effective_maps(ic):
    """Per-initiator effective maps for every remap configuration.

    Returns (configs, maps) where configs is the list of remap configurations
    (``{bit: bool}``) and maps[init_name][k] is the list of
    ``(lo, hi, target)`` for configs[k], sorted by ``lo``.
    """
    from .ahb import SoCAhbBackend
    be = SoCAhbBackend(None)
    init_data = be._build_initiator_data(ic)
    configs = be._generate_remap_configurations(init_data)
    maps: Dict[str, List[List[Tuple[int, int, str]]]] = {}
    for name, data in init_data.items():
        per_cfg = []
        for cfg in configs:
            eff = be._compute_effective_address_map(data, cfg)
            per_cfg.append([(int(r['mem_lo'], 16), int(r['mem_hi'], 16),
                             r['interface']) for r in eff])
        maps[name] = per_cfg
    return configs, maps


def _cfg_str(cfg: Dict[int, bool]) -> str:
    if not cfg:
        return 'no remap bits'
    return ', '.join(f'remap[{b}]={int(v)}' for b, v in sorted(cfg.items()))


def _check_no_overlap(ic_name, init_name, cfg, regions):
    for (lo1, hi1, t1), (lo2, hi2, t2) in zip(regions, regions[1:]):
        if lo2 <= hi1:
            raise CmsdkMuxError(
                f"{ic_name}: initiator '{init_name}' ({_cfg_str(cfg)}) has "
                f"overlapping effective regions '{t1}' 0x{lo1:08x}-0x{hi1:08x} "
                f"and '{t2}' 0x{lo2:08x}-0x{hi2:08x}; impl: cmsdk_mux needs "
                f"an unambiguous decode")


def _trailing_zeros(v: int) -> int:
    return (v & -v).bit_length() - 1


# ---------------------------------------------------------------------------
# Decoder expression building
# ---------------------------------------------------------------------------

def _hex(v: int) -> str:
    return f"-0x{-v:x}" if v < 0 else f"0x{v:x}"


def _range_expr(sig: str, lo: int, hi: int, lsb: int, width: int) -> str:
    """Verilog test of ``sig`` (= HADDR[31:lsb]) against [lo, hi].

    Refuses (rather than truncates) a range that is outside the 32-bit space,
    empty, or not on the ``lsb`` grid: shifting such a range would silently
    decode a different one.
    """
    grain = (1 << lsb) - 1 if 0 <= lsb < 32 else -1
    if (grain < 0 or width != 32 - lsb or not 0 <= lo <= hi < ADDR_SPACE
            or lo & grain or (hi + 1) & grain):
        raise CmsdkMuxError(
            f"internal: decoder range {_hex(lo)}-{_hex(hi)} cannot be decoded "
            f"exactly on HADDR[31:{lsb}] ({width} bits); refusing to truncate it")
    lo_g, hi_g = lo >> lsb, hi >> lsb
    maxv = (1 << width) - 1
    lo_s, hi_s = f"{width}'h{lo_g:x}", f"{width}'h{hi_g:x}"
    if lo_g == 0 and hi_g == maxv:
        return "1'b1"
    if lo_g == hi_g:
        return f"({sig} == {lo_s})"
    if lo_g == 0:
        return f"({sig} <= {hi_s})"
    if hi_g == maxv:
        return f"({sig} >= {lo_s})"
    return f"(({sig} >= {lo_s}) & ({sig} <= {hi_s}))"


def _remap_cond(cfg_idx: Sequence[int], configs, remap_sig: str) -> Optional[str]:
    """Condition on the REMAP input selecting exactly the configs in cfg_idx."""
    sel = set(cfg_idx)
    if sel == set(range(len(configs))):
        return None
    bits = sorted(configs[0].keys())
    for b in bits:
        for v in (True, False):
            if sel == {k for k, c in enumerate(configs) if c[b] == v}:
                return f"{'' if v else '~'}{remap_sig}[{b}]"
    terms = []
    for k in sorted(sel):
        lits = [f"{'' if configs[k][b] else '~'}{remap_sig}[{b}]" for b in bits]
        terms.append('(' + ' & '.join(lits) + ')')
    return '(' + ' | '.join(terms) + ')'


def _decode_exprs(entries, targets, configs, n_masters, keyed, addr_sig,
                  mst_sig, mst_w, remap_sig, lsb, width):
    """Build one select expression per target.

    ``entries`` is a list of ``(master_idx, cfg_idx, lo, hi, target)``.
    Returns {target: expr}; a target with no entry maps to ``1'b0``.
    """
    exprs = {}
    for t in targets:
        # (lo, hi) -> master -> set(cfg)
        by_range: Dict[Tuple[int, int], Dict[int, set]] = {}
        for m, k, lo, hi, tgt in entries:
            if tgt == t:
                by_range.setdefault((lo, hi), {}).setdefault(m, set()).add(k)
        terms = []
        for (lo, hi) in sorted(by_range):
            rng = _range_expr(addr_sig, lo, hi, lsb, width)
            per_m = by_range[(lo, hi)]
            # group masters that see this range under the same configs
            groups: Dict[frozenset, List[int]] = {}
            for m, cs in sorted(per_m.items()):
                groups.setdefault(frozenset(cs), []).append(m)
            for cs, ms in sorted(groups.items(), key=lambda kv: kv[1]):
                conds = []
                if keyed and len(ms) != n_masters:
                    mc = [f"({mst_sig} == {mst_w}'d{m})" for m in ms]
                    conds.append(mc[0] if len(mc) == 1 else '(' + ' | '.join(mc) + ')')
                rc = _remap_cond(sorted(cs), configs, remap_sig)
                if rc:
                    conds.append(rc)
                parts = ([] if rng == "1'b1" else [rng]) + conds
                terms.append(' & '.join(parts) if parts else "1'b1")
        exprs[t] = ' |\n      '.join(terms) if terms else "1'b0"
    return exprs


# ---------------------------------------------------------------------------
# Input checks: refuse what the core would render wrongly
# ---------------------------------------------------------------------------

def _check_type(ic) -> None:
    ic_type = getattr(ic, 'type', 'ahb_lite')
    if ic_type not in AHB_TYPES:
        raise CmsdkMuxError(
            f"{ic.name}: type: {ic_type!r}, but impl: cmsdk_mux builds an AHB-Lite "
            f"interconnect only (type: one of {list(AHB_TYPES)}); it would "
            f"generate an AHB core for a {ic_type!r} interconnect. Use type: "
            f"ahb_lite, or remove impl: cmsdk_mux.")


def _check_names(ic) -> None:
    """Names become Verilog identifiers in the core: ``H<SIGNAL>_<NAME>``
    ports for initiators AND targets, ``b_dec_<NAME>`` / ``b_dp_<NAME>`` /
    ``fe_<INIT>_sel_<NAME>`` wires and ``u_fe_<init>_slave_mux`` instances,
    with <NAME> upper-cased. Every other identifier in the core starts with a
    fixed lower-case prefix, so unique upper-cased names plus the reserved
    list keep them all distinct."""
    name = ic.name
    if not isinstance(name, str) or not _VERILOG_ID.fullmatch(name):
        raise CmsdkMuxError(
            f"{name!r}: impl: cmsdk_mux names the core module <interconnect>_lite, "
            f"so the interconnect name must be a plain Verilog identifier "
            f"([A-Za-z_][A-Za-z0-9_]*). Rename the interconnect.")
    seen: Dict[str, Tuple[str, str]] = {}
    for kind, items in (('initiator', ic.initiators), ('target', ic.targets)):
        for item in items:
            n = item.name
            if not isinstance(n, str) or not _VERILOG_ID.fullmatch(n):
                raise CmsdkMuxError(
                    f"{name}: {kind} name {n!r} is not a plain Verilog identifier "
                    f"([A-Za-z_][A-Za-z0-9_]*); impl: cmsdk_mux builds port and wire "
                    f"names from it (HADDR_<NAME>, ...). Rename the {kind}.")
            up = n.upper()
            if kind == 'target' and up in _RESERVED_TARGET_NAMES:
                raise CmsdkMuxError(
                    f"{name}: target name '{n}' is reserved by impl: cmsdk_mux: "
                    f"{_RESERVED_TARGET_NAMES[up]}. Rename the target.")
            if up in seen:
                k0, n0 = seen[up]
                raise CmsdkMuxError(
                    f"{name}: {k0} '{n0}' and {kind} '{n}' both become '{up}' in "
                    f"the generated core (HADDR_{up}, HRDATA_{up}, ...), so their "
                    f"ports collide. Give them names that differ other than by case.")
            seen[up] = (kind, n)


def _as_number(ic_name: str, what: str, value: Any) -> int:
    """An address/size as the AHB backend reads it (``_to_hex``: an int, or a
    hex string), refusing anything it would crash on or misread."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip(), 16)
        except ValueError:
            pass
    raise CmsdkMuxError(
        f"{ic_name}: {what} is {value!r}; impl: cmsdk_mux needs a number here "
        f"(an integer, or a hex string). Fix the value.")


def _check_windows(ic) -> None:
    """Sizes, apertures and remap specs, before any map is derived."""
    name = ic.name
    for t in ic.targets:
        _as_number(name, f"target '{t.name}' base", t.base)
        size = _as_number(name, f"target '{t.name}' size", t.size)
        if size <= 0:
            raise CmsdkMuxError(
                f"{name}: target '{t.name}' has size {t.size!r}; a region needs a "
                f"size > 0 (impl: cmsdk_mux would decode an empty or inverted "
                f"range). Set size: to the window the target decodes.")
    for init in ic.initiators:
        for it in init.targets:
            vis_list = it.visibility
            if vis_list is None:
                continue
            if not isinstance(vis_list, list):
                raise CmsdkMuxError(
                    f"{name}: initiator '{init.name}', target '{it.name}': "
                    f"visibility: must be a list of windows, got {vis_list!r}")
            for k, vis in enumerate(vis_list):
                where = (f"initiator '{init.name}', target '{it.name}', "
                         f"visibility[{k}]")
                if not isinstance(vis, dict) or 'base' not in vis:
                    raise CmsdkMuxError(
                        f"{name}: {where} must be a mapping with a base:, got {vis!r}")
                _as_number(name, f"{where} base", vis['base'])
                if 'aperture' in vis:
                    ap = _as_number(name, f"{where} aperture", vis['aperture'])
                    if ap <= 0:
                        raise CmsdkMuxError(
                            f"{name}: {where} has aperture {vis['aperture']!r}; a "
                            f"window needs an aperture > 0. Fix or remove aperture:.")
                beh = vis.get('remap_behaviour')
                if beh is not None and beh not in REMAP_BEHAVIOURS:
                    raise CmsdkMuxError(
                        f"{name}: {where} has remap_behaviour {beh!r}; expected one "
                        f"of {list(REMAP_BEHAVIOURS)} (any other value would "
                        f"silently act as 'alias'/'none')")
                if 'remap' not in vis or vis['remap'] is None:
                    continue
                remap = vis['remap']
                if not isinstance(remap, dict):
                    raise CmsdkMuxError(
                        f"{name}: {where} has remap: {remap!r}; it must be a mapping "
                        f"{{remapping: true, remap_bit: 0..{REMAP_BITS - 1}, "
                        f"remap_behaviour: alias|move}} (otherwise the window is "
                        f"silently always visible)")
                if 'remapping' in remap and not isinstance(remap['remapping'], bool):
                    raise CmsdkMuxError(
                        f"{name}: {where} has remapping: {remap['remapping']!r} "
                        f"({type(remap['remapping']).__name__}); it must be true or "
                        f"false, unquoted (a quoted \"false\" is a non-empty string, "
                        f"which the map code reads as true)")
                if not remap.get('remapping', False):
                    continue
                if 'remap_bit' not in remap:
                    raise CmsdkMuxError(
                        f"{name}: {where} is a remap window with no remap_bit; set "
                        f"remap_bit: 0..{REMAP_BITS - 1}")
                bit = remap['remap_bit']
                if type(bit) is not int or not 0 <= bit < REMAP_BITS:
                    raise CmsdkMuxError(
                        f"{name}: {where} has remap_bit {bit!r}; the core's REMAP "
                        f"input is {REMAP_BITS} bits, so remap_bit must be an "
                        f"integer 0..{REMAP_BITS - 1}. Fix remap_bit.")
                beh = remap.get('remap_behaviour', 'alias')
                if beh not in REMAP_BEHAVIOURS:
                    raise CmsdkMuxError(
                        f"{name}: {where} has remap_behaviour {beh!r}; expected one "
                        f"of {list(REMAP_BEHAVIOURS)} (any other value would "
                        f"silently act as 'alias'/'none')")


def _check_in_address_space(ic_name, init_name, cfg, regions) -> None:
    for lo, hi, t in regions:
        if not 0 <= lo <= hi < ADDR_SPACE:
            raise CmsdkMuxError(
                f"{ic_name}: initiator '{init_name}' ({_cfg_str(cfg)}) sees '{t}' at "
                f"{_hex(lo)}-{_hex(hi)}, outside the 32-bit address space "
                f"0x0-0xffffffff; impl: cmsdk_mux decodes HADDR[31:0] and would drop "
                f"or truncate the region. Keep every window inside it "
                f"(base + size <= 0x1_0000_0000).")


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------

def plan_cmsdk_mux(ic, addr_width: int = 32, data_width: int = 32) -> Dict[str, Any]:
    """Derive the cmsdk_mux topology and decoders for one interconnect.

    Raises :class:`CmsdkMuxError` for anything the topology cannot represent.
    The returned dict is the Jinja2 context of ``ahb_interconnect_cmsdk_mux_lite.v.j2``.
    """
    name = ic.name
    if int(addr_width) != 32:
        raise CmsdkMuxError(
            f"{name}: impl: cmsdk_mux needs a 32-bit address (cmsdk_ahb_master_mux "
            f"has a fixed 32-bit HADDR); SYS_ADDR_W is {addr_width}")
    if int(data_width) not in (32, 64):
        raise CmsdkMuxError(
            f"{name}: impl: cmsdk_mux supports 32- or 64-bit data; SYS_DATA_W is {data_width}")

    _check_type(ic)
    _check_nested_keys(ic)

    targets = list(ic.targets)
    initiators = list(ic.initiators)
    if not initiators:
        raise CmsdkMuxError(f"{name}: impl: cmsdk_mux needs at least one initiator")
    _check_names(ic)
    tnames = [t.name for t in targets]
    tdefs = {t.name: t for t in targets}
    for init in initiators:
        for tn in init.target_names:
            if tn not in tdefs:
                raise CmsdkMuxError(
                    f"{name}: initiator '{init.name}' references undefined target '{tn}'")
    if len(initiators) > MAX_INITIATORS:
        raise CmsdkMuxError(
            f"{name}: {len(initiators)} initiators; impl: cmsdk_mux supports at most "
            f"{MAX_INITIATORS} (a cmsdk_ahb_master_mux has {MASTER_MUX_PORTS} ports and "
            f"one cascade level is generated into port 2). Use impl: busmatrix.")
    _check_windows(ic)

    configs, maps = _effective_maps(ic)
    for init in initiators:
        for k, cfg in enumerate(configs):
            _check_in_address_space(name, init.name, cfg, maps[init.name][k])
            _check_no_overlap(name, init.name, cfg, maps[init.name][k])

    # --- Private passthrough targets -------------------------------------
    reach = {t: [i.name for i in initiators if t in i.target_names] for t in tnames}
    inbound = [i.name for i in initiators if i.passthrough]
    private_owner: Dict[str, str] = {}
    for t in targets:
        if not t.passthrough or not reach[t.name]:
            continue
        loops = [i for i in reach[t.name] if i in inbound]
        if loops:
            raise CmsdkMuxError(
                f"{name}: passthrough loop: passthrough initiator(s) {loops} can reach "
                f"passthrough target '{t.name}', so a transfer can leave and re-enter "
                f"this interconnect and deadlock. Remove '{t.name}' from their targets.")
        if len(reach[t.name]) > 1:
            raise CmsdkMuxError(
                f"{name}: passthrough target '{t.name}' is reachable by "
                f"{reach[t.name]}; impl: cmsdk_mux gives a passthrough target a private "
                f"path from exactly one initiator (a shared path could deadlock with "
                f"a passthrough initiator). Use impl: busmatrix.")
        private_owner[t.name] = reach[t.name][0]
    for init in initiators:
        privs = [t for t in tnames if private_owner.get(t) == init.name]
        if len(privs) > MAX_PRIVATE_TARGETS:
            raise CmsdkMuxError(
                f"{name}: initiator '{init.name}' reaches {len(privs)} passthrough "
                f"targets on its private path ({privs}); its front-end "
                f"cmsdk_ahb_slave_mux has {SLAVE_MUX_PORTS} ports and port 0 is the "
                f"shared bus, so impl: cmsdk_mux supports at most "
                f"{MAX_PRIVATE_TARGETS}. Merge passthrough windows or use "
                f"impl: busmatrix.")

    shared_targets = [t for t in tnames if t not in private_owner]
    n_slave_ports = len(shared_targets) + 1   # + default slave
    if n_slave_ports > SLAVE_MUX_PORTS:
        raise CmsdkMuxError(
            f"{name}: {len(shared_targets)} shared targets + the default slave need "
            f"{n_slave_ports} slave-mux ports; cmsdk_ahb_slave_mux has {SLAVE_MUX_PORTS} "
            f"and impl: cmsdk_mux does not cascade slave muxes. Use impl: busmatrix.")

    # --- Address granularity ----------------------------------------------
    bounds = set()
    for per_cfg in maps.values():
        for regions in per_cfg:
            for lo, hi, _ in regions:
                bounds.add(lo)
                bounds.add(hi + 1)
    tz = [_trailing_zeros(b) for b in bounds if 0 < b < (1 << 32)]
    lsb = min(tz + [31])
    width = 32 - lsb

    remap_bits = sorted(configs[0].keys())

    # --- Initiators and their front ends ----------------------------------
    n = len(initiators)
    cascade = n > MASTER_MUX_PORTS
    init_ctx = []
    for idx, init in enumerate(initiators):
        up = init.name.upper()
        privs = [t for t in tnames if private_owner.get(t) == init.name]
        if cascade and idx >= 2:
            mux, port = 'casc', idx - 2
        else:
            mux, port = 'top', idx
        init_ctx.append({
            'name': init.name, 'up': up, 'idx': idx,
            'passthrough': bool(init.passthrough),
            'private': [t.upper() for t in privs],
            'front': bool(privs), 'mux': mux, 'port': port,
        })

    # Front-end decoders (private targets only; everything else is shared).
    for ictx in init_ctx:
        if not ictx['front']:
            continue
        entries = []
        for k in range(len(configs)):
            for lo, hi, t in maps[ictx['name']][k]:
                if t.upper() in ictx['private']:
                    entries.append((0, k, lo, hi, t.upper()))
        ictx['front_exprs'] = _decode_exprs(
            entries, ictx['private'], configs, 1, False,
            f"fe_{ictx['up']}_dec_a", None, 0, 'REMAP', lsb, width)

    # Shared decoder, keyed by address-phase master only if the maps differ.
    shared_up = [t.upper() for t in shared_targets]
    shared_maps = {}
    for ictx in init_ctx:
        shared_maps[ictx['idx']] = [
            [(lo, hi, t.upper()) for lo, hi, t in maps[ictx['name']][k]
             if t.upper() in shared_up]
            for k in range(len(configs))]
    keyed = len({repr(v) for v in shared_maps.values()}) > 1
    # The key is u_master_mux's address-phase HMASTER: 0/1 for S0/S1, 2 for
    # S2. Behind a cascade, S2 carries several initiators, and the cascaded
    # mux's own HMASTER does NOT follow a transfer that u_master_mux is
    # holding in its S2 buffer (measured: a DMA write decoded as another
    # initiator's and got an ERROR). So initiators sharing S2 must see one
    # map, and the key is the top-level port.
    port_of = {i['idx']: (i['port'] if i['mux'] == 'top' else 2) for i in init_ctx}
    if keyed and cascade:
        casc_maps = {repr(shared_maps[i['idx']]) for i in init_ctx if i['mux'] == 'casc'}
        if len(casc_maps) > 1:
            raise CmsdkMuxError(
                f"{name}: the initiators cascaded onto the master mux's port 2 "
                f"({[i['name'] for i in init_ctx if i['mux'] == 'casc']}) see different "
                f"address maps; impl: cmsdk_mux can key its decoder only by the top-level "
                f"port. Order the initiators so that the ones from the third onwards "
                f"share a map, or use impl: busmatrix.")
    entries = []
    for m, per_cfg in shared_maps.items():
        if not keyed and m != 0:
            continue
        for k, regions in enumerate(per_cfg):
            for lo, hi, t in regions:
                entries.append((port_of[m] if keyed else 0, k, lo, hi, t))
    entries = sorted(set(entries))
    n_keys = len(set(port_of.values()))
    mst_w = 2
    shared_exprs = _decode_exprs(
        entries, shared_up, configs, n_keys if keyed else 1, keyed,
        'b_dec_a', 'b_hmaster', mst_w, 'REMAP', lsb, width)

    top_ports = [i for i in init_ctx if i['mux'] == 'top']
    casc_ports = [i for i in init_ctx if i['mux'] == 'casc']

    plan = {
        'module_name': f'{name}_lite',
        'interconnect_name': name,
        'addr_width': 32,
        'data_width': int(data_width),
        'initiators': init_ctx,
        'targets': [{'name': t, 'up': t.upper(),
                     'private': t in private_owner,
                     'owner': private_owner.get(t, '').upper(),
                     'passthrough': bool(tdefs[t].passthrough),
                     'reachable': bool(reach[t])} for t in tnames],
        'shared_targets': shared_up,
        'default_port': len(shared_up),
        'slave_ports': shared_up + ['DEFAULT'],
        'cascade': cascade,
        'top_ports': top_ports,
        'casc_ports': casc_ports,
        'keyed': keyed,
        'mst_w': mst_w,
        'dec_lsb': lsb,
        'dec_w': width,
        'shared_exprs': shared_exprs,
        'remap_bits': remap_bits,
        'remap_unused': [b for b in range(4) if b not in remap_bits],
        'configs': [_cfg_str(c) for c in configs],
        'inbound': inbound,
    }
    assert_deadlock_free(plan)
    return plan


def assert_deadlock_free(plan: Dict[str, Any]) -> None:
    """Check a derived topology for the cross-interconnect deadlock.

    Independent of how the plan was built: if the interconnect has a
    passthrough (inbound) initiator, no reachable passthrough (outbound)
    target may sit on the shared bus that the inbound initiator also uses.
    """
    if not plan['inbound']:
        return
    for t in plan['targets']:
        if t['passthrough'] and t['reachable'] and not t['private']:
            raise CmsdkMuxError(
                f"{plan['interconnect_name']}: deadlock: passthrough target "
                f"'{t['name']}' is on the shared bus with passthrough initiator(s) "
                f"{plan['inbound']}; an initiator stalled on '{t['name']}' would hold "
                f"the bus that an inbound transfer needs")


def check_cmsdk_mux(ic, params: Optional[Dict[str, Any]] = None) -> List[str]:
    """Return the reasons ``ic`` cannot be built as written (empty = OK): an
    unusable ``impl``, an unknown key that reads as a misspelt ``impl``, and,
    for ``impl: cmsdk_mux``, anything :func:`plan_cmsdk_mux` refuses."""
    from ..utils import resolve_param_ref
    params = params or {}
    errors = check_interconnect_keys(ic)[0]
    err = impl_error(ic)
    if err:
        return errors + [err]
    if ic_impl(ic) != 'cmsdk_mux':
        return errors
    aw = resolve_param_ref(ic.params.get('SYS_ADDR_W', 32), params)
    dw = resolve_param_ref(ic.params.get('SYS_DATA_W', 32), params)
    try:
        plan_cmsdk_mux(ic, aw if isinstance(aw, int) else 32,
                       dw if isinstance(dw, int) else 32)
    except CmsdkMuxError as e:
        errors.append(str(e))
    return errors


# ---------------------------------------------------------------------------
# Refusal support for the AHB backend and the CLI
# ---------------------------------------------------------------------------

def iter_generated_interconnects(module, _seen=None) -> Iterator[Tuple[Any, Any]]:
    """(module, interconnect) for every ``gen: True`` interconnect in the
    hierarchy, each module visited once (the walk the validator and the AHB
    backend make)."""
    seen = _seen if _seen is not None else set()
    if id(module) in seen:
        return
    seen.add(id(module))
    for ic in module.interconnects:
        if ic.gen:
            yield module, ic
    for inst in module.instances:
        if inst.resolved_module:
            yield from iter_generated_interconnects(inst.resolved_module, seen)


def _module_prefix(ic_name: str) -> str:
    # as SoCAhbBackend derives it
    return ic_name.replace('_ahb_interconnect', '').replace('_interconnect', '')


def stale_outputs(ic_name: str, build_dir) -> List[Path]:
    """The cmsdk_mux outputs of ``ic_name`` present in ``build_dir``: its
    ``rtl/<ic>/<ic>_lite.v`` (the bus-matrix core lives one level deeper, in
    ``rtl/<ic>/<ic>/``, and is never listed) and its per-interconnect flist
    when that flist was written by the cmsdk_mux template."""
    build_dir = Path(build_dir)
    found = []
    if not isinstance(ic_name, str) or not _VERILOG_ID.fullmatch(ic_name):
        return found      # never build a delete path from an odd name
    lite = build_dir / 'rtl' / ic_name / f'{ic_name}_lite.v'
    if lite.is_file():
        found.append(lite)
    flist = build_dir / 'flist' / f'{_module_prefix(ic_name)}_interconnect.flist'
    if flist.is_file():
        try:
            text = flist.read_text(errors='replace')
        except OSError:
            text = ''
        if 'ahb_interconnect_cmsdk_mux.flist.j2' in text:
            found.append(flist)
    return found


def remove_stale_outputs(ic_name: str, build_dir) -> List[Path]:
    """Delete :func:`stale_outputs` and return what was deleted.

    Called when an interconnect is refused, so a core from an earlier good run
    cannot be compiled by a flow that ignores the generator's exit code."""
    removed = []
    for p in stale_outputs(ic_name, build_dir):
        p.unlink()
        removed.append(p)
    return removed


def blocking_errors(top, errors) -> List[Any]:
    """The validator errors on which generation must stop.

    Every ``interconnect``-category error (an unusable ``impl``, a misspelt
    ``impl`` key, a cmsdk_mux refusal), plus any error reported against an
    ``impl: cmsdk_mux`` interconnect. Errors that concern only bus-matrix
    interconnects are not blocking (unchanged behaviour)."""
    mux_names = {ic.name for _, ic in iter_generated_interconnects(top)
                 if ic_impl(ic) == 'cmsdk_mux'}
    return [m for m in errors
            if m.level == 'error'
            and (m.category == 'interconnect' or m.instance in mux_names)]
