# Copyright 2026, SoC Labs (www.soclabs.org)
"""Chip-functional-wrapper backend — generates the bonded chip wrapper.

Produces ``<chip_module>.v`` (e.g. ``nanosoc_multicore_chip.v``): a thin
structural wrapper that instantiates an inner SoC top (``inner_module``,
e.g. ``nanosoc_multicore_soc``) and:

  - re-exposes only the BONDED inner ports 1:1 (same names/widths/directions),
    grouping bidir in/out/oe triplets onto one logical pad,
  - drives TIED inner inputs to constants inside the wrapper (NOT bonded),
  - leaves OPEN inner outputs unconnected.

The functional boundary (which inner port is bonded / tied / open, plus the
tie constants) is read from an editable chip-boundary spec
(``lib/chip_boundary/<name>.yaml`` -> ``ChipBoundary``).  The template never
hard-codes ties/opens.  Inner parameters are NOT overridden — the inner SoC
top's own defaults apply.

This is a DIFFERENT generator from SoCChipBackend: the multicore SoC exposes
named functional ports (not generic ``type: gpio`` interfaces), so the GPIO
chip backend would emit a near-empty wrapper.  The seam to the pad ring is the
bonded inner-named port set (the pad ring consumes the in/out/oe nets and
synthesises the true ``inout`` pad).

Generated files:
  - <chip_module>.v          — bonded chip wrapper
  - <chip_module>.flist      — inline file list
"""

import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..model import ChipBoundary, ChipPad
from ..utils import write_if_changed

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    Environment = None
    FileSystemLoader = None

_BACKEND_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _BACKEND_DIR / 'templates'


def _wstr(width: int) -> str:
    """SV declaration width string: '' for 1-bit, '[N-1:0]' otherwise."""
    return '' if int(width) <= 1 else f'[{int(width) - 1}:0]'


class SoCChipWrapperBackend:
    """Generates a bonded chip wrapper from a chip-boundary spec."""

    def __init__(self, boundary: ChipBoundary,
                 soc_ports: Optional[List[Dict[str, Any]]] = None):
        """`soc_ports` is the inner SoC's expanded boundary port list, as
        produced by ``SoCTopLevelBackend._build_ports()``: dicts with at least
        ``name``, ``direction`` and ``width``.

        Supplying it enables :meth:`validate_soc_ports`, which is what stops a
        boundary spec silently drifting away from the SoC it claims to wrap.
        It is optional so unit tests can drive the backend from a spec alone.
        """
        self.boundary = boundary
        self.soc_ports: Dict[str, Dict[str, Any]] = {
            p['name']: p for p in (soc_ports or [])
        }

    # ------------------------------------------------------------------
    # spec <-> SoC cross-check
    # ------------------------------------------------------------------
    def validate_soc_ports(self) -> None:
        """Raise unless the spec classifies exactly the SoC's ports.

        Two failure modes, both of which have bitten this design and both of
        which used to be SILENT — the wrapper simply omitted the port:

        * **unclassified** — a port exists on the SoC but the spec never
          mentions it, so the template drops it from the `u_soc` instantiation.
          Its inputs then float: Z in simulation, tied 0 by synthesis. This is
          how 22 die-to-die ports vanished the moment they were added.

        * **stale** — the spec names a port the SoC does not have, so the
          wrapper cannot elaborate at all. This is how the `cpu_0_*`/`cpu_1_*`
          -> `network_core_*`/`chip_core_*` rename silently broke the chip
          wrapper: nothing compiles it, so nobody noticed.

        No-op when the backend was constructed without `soc_ports` (unit tests
        drive the backend from a spec alone), or when the caller determined the
        spec does not describe this SoC — see the `inner_module` guard in
        `soc_model.__main__`.
        """
        if not self.soc_ports:
            return
        classified = set(self.coverage()['all'])
        actual = set(self.soc_ports)

        unclassified = sorted(actual - classified)
        stale = sorted(classified - actual)
        if not unclassified and not stale:
            return

        lines = [f"chip_boundary '{self.boundary.chip_module}' does not match "
                 f"inner module '{self.boundary.inner_module}':"]
        if unclassified:
            lines.append(
                f"  {len(unclassified)} SoC port(s) unclassified (would be "
                f"SILENTLY DROPPED from the wrapper, leaving inputs floating):")
            lines += [f"      {self.soc_ports[n]['direction']:6} {n}"
                      for n in unclassified]
            lines.append("  -> add each to pads / ties / opens / terminators.")
        if stale:
            lines.append(
                f"  {len(stale)} classified name(s) are not SoC ports (the "
                f"wrapper would not elaborate):")
            lines += [f"      {n}" for n in stale]
            lines.append("  -> the SoC was renamed; update the spec.")
        raise ValueError("\n".join(lines))

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def generate(self, rtl_dir: Path, flist_dir: Path = None) -> Optional[Path]:
        """Render <chip_module>.v + an inline flist into rtl_dir/flist_dir.

        Returns the path to the generated .v (None when jinja2 is absent).
        """
        if Environment is None:
            print("  WARNING: jinja2 not available — skipping chip-wrapper generation")
            return None

        # Fail loudly on spec/SoC drift rather than emitting a wrapper with
        # silently-dropped ports. No-op unless soc_ports was supplied.
        self.validate_soc_ports()

        rtl_dir = Path(rtl_dir)
        rtl_dir.mkdir(parents=True, exist_ok=True)

        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        template = env.get_template('chip_wrapper.v.j2')
        content = template.render(**self._build_context())

        v_name = f'{self.boundary.chip_module}.v'
        v_path = rtl_dir / v_name
        write_if_changed(v_path, content)

        if flist_dir is None:
            flist_dir = rtl_dir.parent / 'flist'
        flist_dir = Path(flist_dir)
        flist_dir.mkdir(parents=True, exist_ok=True)
        flist_content = (
            f"// Auto-generated file list for chip wrapper {self.boundary.chip_module}\n"
            f"$SOCLABS_NANOSOC_SOC_DIR/build_soc/rtl/{v_name}\n"
        )
        flist_path = flist_dir / f'{self.boundary.chip_module}.flist'
        write_if_changed(flist_path, flist_content)

        return v_path

    # ------------------------------------------------------------------
    # boundary resolution
    # ------------------------------------------------------------------
    def boundary_ports(self) -> List[Dict[str, Any]]:
        """Wrapper boundary ports, in pad order.

        Each bonded pad re-exposes its constituent inner net(s) 1:1:
          input        -> 1 input  (in_net)
          output       -> 1 output (out_net)
          tristate_out -> 1 output (out_net) + 1 output (oe_net)
          bidir        -> 1 input (in_net) + 1 output (out_net) + 1 output (oe_net)
        Returns dicts {name, direction, width, width_str, group, pad, role}.
        """
        ports: List[Dict[str, Any]] = []

        def _emit(name, direction, width, pad, role):
            ports.append({
                'name': name,
                'direction': direction,
                'width': int(width),
                'width_str': _wstr(width),
                'group': pad.group,
                'pad': pad.chip_port,
                'role': role,
            })

        for pad in self.boundary.pads:
            if pad.kind == 'input':
                _emit(pad.in_net, 'input', pad.width, pad, 'in')
            elif pad.kind == 'output':
                _emit(pad.out_net, 'output', pad.width, pad, 'out')
            elif pad.kind == 'tristate_out':
                _emit(pad.out_net, 'output', pad.width, pad, 'out')
                _emit(pad.oe_net, 'output', pad.width, pad, 'oe')
            elif pad.kind == 'bidir':
                _emit(pad.in_net, 'input', pad.width, pad, 'in')
                _emit(pad.out_net, 'output', pad.width, pad, 'out')
                _emit(pad.oe_net, 'output', pad.width, pad, 'oe')
            else:
                raise ValueError(
                    f"chip_boundary pad '{pad.chip_port}': unknown kind '{pad.kind}'")
        return ports

    def _bonded_inner_ports(self) -> List[str]:
        """All inner SoC port names consumed by bonded pads."""
        names: List[str] = []
        for pad in self.boundary.pads:
            for n in (pad.in_net, pad.out_net, pad.oe_net):
                if n:
                    names.append(n)
        return names

    def _terminated_inner_ports(self) -> List[str]:
        """Inner SoC port names driven/read by a terminator helper instance."""
        names: List[str] = []
        for t in self.boundary.terminators:
            names.extend(t.soc_ports.values())
        return names

    def terminator_context(self) -> List[Dict[str, Any]]:
        """Terminator instances, with wire declarations, for the template.

        Each entry: {module, instance, desc, params:[{name,value}],
                     wires:[{name,width_str}], conns:[{port,expr}]}.

        `soc_ports` become wires named after the inner SoC port (widths taken
        from the SoC model); `bonded_refs` resolve to the wrapper's own net for
        that inner port, which for the chip wrapper is the port name itself
        (bonded pads are re-exposed 1:1).
        """
        out: List[Dict[str, Any]] = []
        for t in self.boundary.terminators:
            wires = [
                {'name': soc_port,
                 'width_str': _wstr(self._terminator_width(t, soc_port))}
                for soc_port in t.soc_ports.values()
            ]
            conns = [{'port': p, 'expr': n} for p, n in t.soc_ports.items()]
            conns += [{'port': p, 'expr': n} for p, n in t.bonded_refs.items()]
            out.append({
                'module': t.module,
                'instance': t.instance,
                'desc': t.desc,
                'params': [{'name': k, 'value': v} for k, v in t.params.items()],
                'wires': wires,
                'conns': conns,
            })
        return out

    def _soc_width(self, port: str) -> Optional[int]:
        """Width of an inner SoC port from the SoC model, or None if unknown.

        Widths are expected already concretised by the caller — a wrapper cannot
        declare `wire [SYS_DATA_W-1:0]` because it does not instantiate the SoC's
        parameters. An unresolved param reference yields None rather than
        crashing generation.
        """
        if not self.soc_ports:
            return None
        try:
            return int(self.soc_ports[port]['width'])
        except (KeyError, TypeError, ValueError):
            return None

    def _terminator_width(self, term, soc_port: str) -> int:
        """Wire width for a terminated port: the spec's, cross-checked.

        The spec is authoritative because it must work with no SoC model at all
        (unit/golden tests render from the spec alone). When the model IS
        available and disagrees, that is a spec bug worth failing on — a
        mis-sized wire silently truncates a bus.
        """
        declared = term.widths.get(soc_port)
        actual = self._soc_width(soc_port)
        if declared is not None and actual is not None and declared != actual:
            raise ValueError(
                f"terminator '{term.instance}': width of '{soc_port}' declared "
                f"as {declared} but the SoC port is {actual} bits wide")
        if declared is not None:
            return declared
        return actual if actual is not None else 1

    def instance_connections(self) -> List[Dict[str, Any]]:
        """Inner-SoC instance connection list, classified per inner port.

        Each entry: {port, expr, kind} where kind is bonded|tied|open|terminated.
          bonded     -> .port(port)            (same-named boundary net)
          tied       -> .port(<const>)         (constant inside wrapper)
          open       -> .port()                (left unconnected)
          terminated -> .port(port)            (wire to a terminator instance)
        Bonded connections are emitted in pad order; ties, opens, then
        terminated follow.
        """
        conns: List[Dict[str, Any]] = []
        for name in self._bonded_inner_ports():
            conns.append({'port': name, 'expr': name, 'kind': 'bonded'})
        for tie in self.boundary.ties:
            conns.append({
                'port': tie['soc_port'],
                'expr': tie['const'],
                'kind': 'tied',
            })
        for name in self.boundary.opens:
            conns.append({'port': name, 'expr': '', 'kind': 'open'})
        for name in self._terminated_inner_ports():
            conns.append({'port': name, 'expr': name, 'kind': 'terminated'})
        return conns

    def coverage(self) -> Dict[str, Any]:
        """Coverage report over all classified inner ports.

        Returns {bonded, tied, open, terminated, all, duplicates}.
        Asserts no inner port is classified more than once (a pad/tie/open/
        terminator collision is a spec bug).
        """
        bonded = self._bonded_inner_ports()
        tied = [t['soc_port'] for t in self.boundary.ties]
        opens = list(self.boundary.opens)
        terminated = self._terminated_inner_ports()
        all_ports = bonded + tied + opens + terminated
        seen: Dict[str, int] = {}
        for p in all_ports:
            seen[p] = seen.get(p, 0) + 1
        duplicates = sorted(p for p, c in seen.items() if c > 1)
        if duplicates:
            raise ValueError(
                f"chip_boundary '{self.boundary.chip_module}': inner port(s) "
                f"classified more than once: {duplicates}")
        return {
            'bonded': bonded,
            'tied': tied,
            'open': opens,
            'terminated': terminated,
            'all': all_ports,
            'duplicates': duplicates,
        }

    # ------------------------------------------------------------------
    # context
    # ------------------------------------------------------------------
    def _build_context(self) -> Dict[str, Any]:
        now = datetime.datetime.now()
        cov = self.coverage()
        return {
            'chip_module': self.boundary.chip_module,
            'inner_module': self.boundary.inner_module,
            'pads': self.boundary.pads,
            'boundary_ports': self.boundary_ports(),
            'connections': self.instance_connections(),
            'terminators': self.terminator_context(),
            'tie_count': len(self.boundary.ties),
            'open_count': len(self.boundary.opens),
            'terminated_count': len(cov['terminated']),
            'port_count': len(cov['all']),
            'generated_date': now.strftime('%Y-%m-%d %H:%M:%S'),
            'generated_year': now.strftime('%Y'),
        }
