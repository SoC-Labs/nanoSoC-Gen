"""Protocol abstraction base classes (roadmap doc 02 — "The Protocol / adapter
abstraction").

A `Protocol` knows how to expand an `Interface` into per-signal ports for either
boundary form, and exposes `member_names()` (the flattened boundary port names)
and `is_bus`. The concrete protocols carry their signal tables verbatim from the
legacy `protocol_utils.py` so generator output stays byte-identical.

`SignalSpec` is one row of a protocol's table (suffix / direction / width-expr).
`SignalPort` is a fully-qualified expanded port (`<iface>_<suffix>`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class SignalSpec:
    """One row of a protocol's signal table.

    `direction` is from the role's perspective ('in'/'out'); `width` is the raw
    width expression as it appears in the legacy tables ('SYS_ADDR_W', '2',
    'WIDTH', ...) — width-string formatting is the backends' job (`width_str`).
    """
    suffix: str
    direction: str
    width: str


@dataclass(frozen=True)
class SignalPort:
    """A fully-qualified expanded boundary port."""
    name: str          # e.g. 'cpu_0_dbgahb_slvaddr'
    direction: str
    width: str


@dataclass(frozen=True)
class ResolvedMember:
    """The result of resolving a per-signal member name against a bus/stream
    interface (see :meth:`Protocol.resolve_member`).

    ``is_output`` is the member's direction *as seen at the owning module's
    boundary* (the iface's role/direction is already folded in), so callers do
    not need to re-derive role semantics. ``width`` is the raw width expression
    from the protocol table ('1', '8', 'SYS_DATA_W', 'WIDTH', ...); resolving
    it against iface params is the caller's job, as everywhere else.
    """
    suffix: str        # table suffix, e.g. 'tdata'
    is_output: bool    # True if the member is an OUTPUT of the owning module
    width: str         # raw width expression from the table


class Protocol(ABC):
    """Base class for a bus/stream/wire-group protocol.

    Subclasses set `type` (matching `Interface.type`), declare `is_bus`, and
    implement `table(role)` returning the raw `SignalSpec` rows for that role
    (no EXCLUDE applied). `signals()` layers the iface's `EXCLUDE` on top;
    `member_names` / `expand` build on those. Subclasses override where the
    legacy behaviour diverges (e.g. SWD's `_swd` suffix stripping).
    """

    type: str = ""
    is_bus: bool = True

    @abstractmethod
    def table(self, role: str) -> List[SignalSpec]:
        """Return the raw SignalSpec rows for the given role/boundary form.

        For a bus, `role` is 'initiator' or 'target'. For a non-bus protocol the
        role is the empty string (single table).
        """
        ...

    @staticmethod
    def _exclude(iface) -> set:
        params = getattr(iface, 'params', None) or {}
        if not isinstance(params, dict):
            return set()
        return set(params.get('EXCLUDE', []) or [])

    def signals(self, role: str, iface) -> List[SignalSpec]:
        """SignalSpec rows for `iface` at `role`, with its EXCLUDE applied."""
        excl = self._exclude(iface)
        return [s for s in self.table(role) if s.suffix not in excl]

    # The "base name" boundary ports are prefixed with. Default = the iface
    # name; SWD overrides this to strip a trailing '_swd'.
    def base_name(self, iface) -> str:
        return iface.name

    def _roles(self):
        return ("initiator", "target") if self.is_bus else ("",)

    def member_names(self, iface) -> List[str]:
        """Flattened boundary port names this iface expands into.

        For a bus this is the sorted union over both boundary forms — matching
        the legacy `bus_member_names`, which uses the full tables (it does NOT
        apply EXCLUDE for name-recognition). Non-bus protocols return their
        single table's names in table order.
        """
        base = self.base_name(iface)
        if self.is_bus:
            suffixes = set()
            for r in self._roles():
                suffixes |= {s.suffix for s in self.table(r)}
            return [f"{base}_{s}" for s in sorted(suffixes)]
        return [f"{base}_{s.suffix}" for s in self.table("")]

    # ------------------------------------------------------------------
    # Hand-written (gen: False) member names (GEN-BACKLOG 20)
    # ------------------------------------------------------------------
    # Member suffixes hand-written RTL may carry on this protocol beyond the
    # table. Recognition ONLY: the generator never expands or emits them, so
    # the tables (and every rendered boundary) are unchanged.
    rtl_extra_suffixes: tuple = ()

    def rtl_bases(self, iface) -> List[str]:
        """Base names hand-written RTL prefixes this iface's members with.
        Default: the generator's base name only."""
        return [self.base_name(iface)]

    def hand_written_member(self, iface, port_name: str):
        """The table suffix `port_name` names when hand-written RTL spells a
        member of `iface`, or None.

        For a `gen: False` module only: its real port names follow the RTL's
        conventions, not the generator's `<base>_<lowercase suffix>`. Accepted:
          * the suffix in any case            (`USRT0_TXD_TVALID`, `P0_IN`);
          * one trailing `_o` / `_i`          (`ADP_RXD_TVALID_o`);
          * a protocol's extra RTL bases      (APB `DEBUG_APB` -> `DEBUG_PSEL`);
          * a protocol's extra RTL suffixes   (axis_byte `txd_tlast`).
        Every bus role's table counts (as in `member_names`). A name that is
        none of these (a typo such as `USRT0_TXD_TVALIX`) is NOT a member.
        """
        name = port_name.lower()
        candidates = {name}
        if name.endswith('_o') or name.endswith('_i'):
            candidates.add(name[:-2])
        suffixes = set(self.rtl_extra_suffixes)
        for r in self._roles():
            suffixes |= {s.suffix for s in self.table(r)}
        for base in self.rtl_bases(iface):
            b = base.lower()
            for s in sorted(suffixes):
                if f"{b}_{s.lower()}" in candidates:
                    return s
        return None

    def expand(self, iface, role: str) -> List[SignalPort]:
        """Expand to fully-qualified `SignalPort`s for the given role."""
        base = self.base_name(iface)
        return [SignalPort(f"{base}_{s.suffix}", s.direction, s.width)
                for s in self.signals(role, iface)]

    # ------------------------------------------------------------------
    # Member resolution (single source of truth for the binder + validator)
    # ------------------------------------------------------------------
    def role_of(self, iface) -> str:
        """The single role an iface takes at its owning module's boundary.

        For a bus, `direction: target|in|receiver` selects the target table,
        anything else the initiator table. Non-bus protocols have one table
        (role '').
        """
        if not self.is_bus:
            return ""
        direction = getattr(iface, 'direction', '') or ''
        return 'target' if direction in ('target', 'in', 'receiver') else 'initiator'

    def _member_is_output(self, iface, spec: SignalSpec) -> bool:
        """Whether a table row is an OUTPUT at the owning module's boundary.

        Bus tables carry directions from the owning role's own perspective
        ('out' = the module drives it), matching how the SV backends emit
        boundary ports. Non-bus (stream/wire-group) tables are written from
        the SOURCE perspective, so a receiver-side iface inverts them.
        """
        if self.is_bus:
            return spec.direction == 'out'
        direction = getattr(iface, 'direction', '') or ''
        is_source = direction in ('out', 'initiator', 'sender')
        return spec.direction == ('out' if is_source else 'in')

    def resolve_member(self, iface, port_name: str) -> 'ResolvedMember | None':
        """Resolve a per-signal member port name against this iface.

        Matches `<base>_<suffix>` case-insensitively (hand-written RTL commonly
        uppercases the suffix, e.g. `ADP_RXD_TVALID` for an `axis_byte` iface
        named `ADP_RXD`), and tolerates a single trailing `_o`/`_i` direction
        decoration (`ADP_RXD_TVALID_o`) as used by some pre-existing RTL.

        Returns a :class:`ResolvedMember` with the module-boundary direction
        already computed, or None when `port_name` is not a member of `iface`.
        """
        base = self.base_name(iface).lower()
        name = port_name.lower()
        candidates = {name}
        if name.endswith('_o') or name.endswith('_i'):
            candidates.add(name[:-2])
        for spec in self.table(self.role_of(iface)):
            if f"{base}_{spec.suffix}".lower() in candidates:
                return ResolvedMember(
                    suffix=spec.suffix,
                    is_output=self._member_is_output(iface, spec),
                    width=spec.width,
                )
        return None


def resolve_boundary_port(ifaces, port_name: str):
    """Resolve `port_name` against a module's interface list.

    Shared by the top-level binder and the validator so the two cannot drift.
    Returns:
      * ``(iface, None)``   — exact interface-name match (wire OR bus bundle),
      * ``(iface, member)`` — expanded per-signal member of a protocol iface
                              (``member`` is a :class:`ResolvedMember`),
      * ``None``            — neither a declared interface nor a recognisable
                              member (the reference cannot be bound).
    """
    from .registry import protocol_for
    for iface in ifaces:
        if iface.name == port_name:
            return iface, None
    for iface in ifaces:
        proto = protocol_for(iface)
        if proto is None:
            continue
        member = proto.resolve_member(iface, port_name)
        if member is not None:
            return iface, member
    return None
