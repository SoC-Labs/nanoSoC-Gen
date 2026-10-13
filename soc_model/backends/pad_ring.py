# Copyright 2026, SoC Labs (www.soclabs.org)
"""Pad-ring backend — generates a pad-level wrapper from a pad-tech descriptor.

The pad-cell names and port maps come from data (a pad tech + a pin map), never
from this code. The bundled example technology is ``generic``; foundry techs
live in pad packs (soc_model/pad_packs.py: --pad-tech-dir, $NANOSOC_PAD_TECH_DIR).

Two generation MODES, selected by the pin-map:

  GPIO-bus mode (``pin_map.mode == 'gpio'``, the default — ROUND 1):
    Produces ``<chip>_pads_<tech>_<N>pin.v`` by combining:
      - the system module's gpio interfaces (P0/P1) for the bonded GPIO pads,
      - a PadTech descriptor (pad-cell port maps, power-cell catalogue),
      - a PinMap descriptor (per-variant boundary, bonding, tie-off, power pads).
    Two body flavours: ``cfg`` (Arch-A 44pin, nanosoc_chip_cfg + sibling, SE pin)
    and ``direct`` (Arch-B 28/60pin, single bare nanosoc_chip).

  MIXED-I/O mode (``pin_map.mode == 'mixed'`` — ROUND 2):
    Produces ``<module>_<tech>_multicore.v`` from an explicit per-signal pad list
    (``pin_map.pads``: kinds input|output|tristate_out|bidir, single + bus),
    wrapping a bonded chip top (``pin_map.chip_module``, e.g.
    ``nanosoc_multicore_chip``).  One pad cell per bonded signal; bus pads expand
    to per-bit cells; power pads reuse the GPIO-mode catalogue.

Generated files:
  - <module>_<tech>_<N>pin.v / <module>_<tech>_multicore.v  — pad-ring wrapper
  - <…>.flist                                              — inline file list (style (a))
"""

import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..model import Module, PadCell, PadSignal, PadTech, PinMap
from .. import pad_packs
from ..utils import resolve_param_ref, flatten_params, write_if_changed

try:
    from jinja2 import Environment, FileSystemLoader, TemplateNotFound
except ImportError:
    Environment = None
    FileSystemLoader = None
    TemplateNotFound = Exception

_BACKEND_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _BACKEND_DIR / 'templates'


class SoCPadRingBackend:
    """Generates a pad-ring wrapper from a system module + pad-tech + pin-map."""

    def __init__(self, system_module: Module, core_module: Optional[Module],
                 pad_tech: PadTech, pin_map: PinMap,
                 pad_tech_dirs: Optional[List[str]] = None):
        self.system = system_module
        # pad packs (--pad-tech-dir; $NANOSOC_PAD_TECH_DIR is added at render
        # time): their templates/ are searched after the bundled ones.
        self.pad_tech_dirs = list(pad_tech_dirs or [])
        self.core = core_module
        self.pad_tech = pad_tech
        self.pin_map = pin_map
        self.flat_params = flatten_params(
            {n: p.default for n, p in system_module.params.items()}
        )

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def _is_mixed(self) -> bool:
        """Mixed-I/O mode is selected by the pin-map (mode or an explicit pad list)."""
        return self.pin_map.mode == 'mixed' or bool(self.pin_map.pads)

    def _env(self) -> 'Environment':
        search = [str(_TEMPLATE_DIR)] + [
            str(d) for d in pad_packs.template_dirs(self.pad_tech_dirs)]
        env = Environment(
            loader=FileSystemLoader(search),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        env.filters['expand_cell'] = self._expand_cell_filter
        # left-justify a string to a fixed width (jinja has no ljust builtin);
        # used by the cfg body to column-align the nanosoc_chip instance ports
        # for techs whose hand file is aligned (align_ports).
        env.filters['lj'] = lambda s, w: str(s).ljust(int(w))
        return env

    def generate(self, rtl_dir: Path, flist_dir: Path = None) -> List[Path]:
        """Render the pad-ring .v + an inline flist into rtl_dir/flist_dir.

        Dispatches on the pin-map mode: the GPIO-bus body (round 1) is byte
        unchanged; mixed-I/O (round 2) renders the per-signal ring.  Returns the
        list of generated paths ([] when jinja2 is absent).
        """
        if Environment is None:
            print("  WARNING: jinja2 not available — skipping pad-ring generation")
            return []
        if self._is_mixed():
            outs = self._generate_mixed(rtl_dir, flist_dir)
        else:
            outs = self._generate_gpio(rtl_dir, flist_dir)
        # M6: emit the Innovus .io floorplan template alongside the pad ring
        # whenever the pin-map carries ring/side placement data.  Purely
        # additive — no .io is written (and behaviour is unchanged) when absent.
        io_path = self._render_io(Path(rtl_dir).parent / 'pnr')
        if io_path is not None:
            outs.append(io_path)
        return outs

    def _generate_gpio(self, rtl_dir: Path, flist_dir: Path = None) -> List[Path]:
        rtl_dir = Path(rtl_dir)
        rtl_dir.mkdir(parents=True, exist_ok=True)

        env = self._env()
        ctx = self._build_context()
        try:
            template = env.get_template('nanosoc_chip_pads_asic.v.j2')
        except TemplateNotFound:
            raise pad_packs.PadPackError(pad_packs.missing(
                f"fixed-pin pad-ring template (pin map mode 'gpio', pad tech "
                f"'{self.pad_tech.name}')", 'nanosoc_chip_pads_asic.v.j2',
                self.pad_tech_dirs)) from None
        content = template.render(**ctx)

        tech = self.pad_tech.name
        # filename variant token: pin_map.variant overrides the '<N>pin' token
        # (so a non-numeric variant like 'no_pads' names cleanly).
        token = self.pin_map.variant or f'{self.pin_map.pin_count}pin'
        v_name = f'{ctx["module_name"]}_{tech}_{token}.v'
        v_path = rtl_dir / v_name
        write_if_changed(v_path, content)

        if flist_dir is None:
            flist_dir = rtl_dir.parent / 'flist'
        flist_dir = Path(flist_dir)
        flist_dir.mkdir(parents=True, exist_ok=True)

        flist_content = (
            f"// Auto-generated file list for pad ring {tech} {token}\n"
            f"$SOCLABS_NANOSOC_SOC_DIR/build_soc/rtl/{v_name}\n"
        )
        flist_path = flist_dir / f'{ctx["module_name"]}_{tech}_{token}.flist'
        write_if_changed(flist_path, flist_content)

        return [v_path, flist_path]

    def _generate_mixed(self, rtl_dir: Path, flist_dir: Path = None) -> List[Path]:
        """Render the mixed-I/O pad ring (per-signal pads) wrapping a chip top."""
        rtl_dir = Path(rtl_dir)
        rtl_dir.mkdir(parents=True, exist_ok=True)

        env = self._env()
        ctx = self._build_mixed_context()
        template = env.get_template('nanosoc_chip_pads_mixed.v.j2')
        content = template.render(**ctx)

        tech = self.pad_tech.name
        v_name = f'{ctx["module_name"]}.v'
        v_path = rtl_dir / v_name
        write_if_changed(v_path, content)

        if flist_dir is None:
            flist_dir = rtl_dir.parent / 'flist'
        flist_dir = Path(flist_dir)
        flist_dir.mkdir(parents=True, exist_ok=True)

        flist_content = (
            f"// Auto-generated file list for mixed pad ring {tech}\n"
            f"$SOCLABS_NANOSOC_SOC_DIR/build_soc/rtl/{v_name}\n"
        )
        flist_path = flist_dir / f'{ctx["module_name"]}.flist'
        write_if_changed(flist_path, flist_content)

        return [v_path, flist_path]

    # ------------------------------------------------------------------
    # GPIO extraction (reuse chip.py:93-101 approach)
    # ------------------------------------------------------------------
    def _gpio_ports(self) -> List[Dict[str, Any]]:
        """Extract gpio ports + per-port bonded/tie-off/net stem from the model.

        Width comes from the iface (core bus, e.g. 16); the *bonded* subset
        and the *tie-off* set come from the pin-map (independent of width).
        """
        ports: List[Dict[str, Any]] = []
        for iface in self.system.interfaces:
            if iface.type != 'gpio':
                continue
            width = int(resolve_param_ref(iface.params.get('WIDTH', 16), self.flat_params))
            name = iface.name
            idx = len(ports)
            bonded = self.pin_map.bonded_gpio.get(name, list(range(width)))
            tied = self.pin_map.tie_off.get(name, [])
            ports.append({
                'name': name,
                'width': width,
                'idx': idx,
                'bonded': bonded,
                'tie_off': tied,
                'net': self._net_stem(idx),
            })
        return ports

    def _net_stem(self, idx: int) -> str:
        """Resolve the gpio_net_template to a concrete net base.

        e.g. 'pad_gpio_port{idx}' -> 'pad_gpio_port0'; 'p{idx}' -> 'p0'.
        """
        return self.pin_map.gpio_net_template.format(idx=idx)

    # ------------------------------------------------------------------
    # cell expansion
    # ------------------------------------------------------------------
    def _expand_cell(self, cell: PadCell, *, net: str = None, pad: str = None,
                     c: str = None, bit: int = None, ren: str = None) -> List[str]:
        """Return ['.KEY(expr)', ...] in port_map order with placeholders resolved.

        Substitutes:
          {i}/{o}/{e}/{z} -> '<net>_<x>[<bit:02d>]'
          {pad}           -> pad (caller supplies bit-indexed form for GPIO)
          {c}             -> c
          {ren}           -> ren (per-control-pin pull-enable, for cells that have one)
        tielo/tiehi are left literal (they are real nets in the body).
        """
        out: List[str] = []
        bitstr = f'{bit:02d}' if bit is not None else ''
        # When the tech requests aligned ports, pad each key to the cell's
        # longest key + 1 space before '(' (the generic GLIB '.PAD (X)' style).
        align = getattr(self.pad_tech, 'align_ports', False)
        keyw = max((len(k) for k in cell.port_map), default=0) if align else 0
        for key, expr in cell.port_map.items():
            resolved = expr
            if net is not None and bit is not None:
                resolved = resolved.replace('{i}', f'{net}_i[{bitstr}]')
                resolved = resolved.replace('{o}', f'{net}_o[{bitstr}]')
                resolved = resolved.replace('{e}', f'{net}_e[{bitstr}]')
                resolved = resolved.replace('{z}', f'{net}_z[{bitstr}]')
            elif net is not None:
                # scalar net base (e.g. SWDIO uses swdio / pad_swdio + _i/_o/_z)
                resolved = resolved.replace('{i}', f'{net}_i')
                resolved = resolved.replace('{o}', f'{net}_o')
                resolved = resolved.replace('{e}', f'{net}_e')
                resolved = resolved.replace('{z}', f'{net}_z')
            if pad is not None:
                resolved = resolved.replace('{pad}', pad)
            if c is not None:
                resolved = resolved.replace('{c}', c)
            if ren is not None:
                resolved = resolved.replace('{ren}', ren)
            if align:
                out.append(f'.{key.ljust(keyw)} ({resolved})')
            else:
                out.append(f'.{key}({resolved})')
        return out

    def _expand_cell_filter(self, cell: PadCell, **kwargs) -> List[str]:
        """Jinja filter wrapper for _expand_cell (cell | expand_cell(...))."""
        return self._expand_cell(cell, **kwargs)

    # ------------------------------------------------------------------
    # MIXED-I/O cell expansion + pad resolution
    # ------------------------------------------------------------------
    @staticmethod
    def _slice(net: str, bit: Optional[int], width: int) -> str:
        """Return the bit slice of a chip-top net (scalar nets are left bare)."""
        if bit is None or width <= 1:
            return net
        return f'{net}[{bit}]'

    def _expand_mixed_cell(self, cell: PadCell, *, pad: str,
                           i: str = None, o: str = None, z: str = None,
                           c: str = None) -> List[str]:
        """Resolve a pad-cell port map for one MIXED-I/O pad instance.

        Unlike the GPIO ``_expand_cell`` (which derives ``<net>_i/_o/_z`` from a
        single net stem), the mixed path passes each chip-top net explicitly:
          {i}->i  {o}->o  {z}->z (active-low OE net)  {c}->c (sink)  {pad}->pad
        Anything left None is required by the cell's port map only for the kinds
        that bind it; tielo/tiehi stay literal.  Emission order = port_map order
        (load-bearing).
        """
        out: List[str] = []
        subs = {'{i}': i, '{o}': o, '{z}': z, '{c}': c, '{pad}': pad}
        for key, expr in cell.port_map.items():
            resolved = expr
            for ph, val in subs.items():
                if val is not None:
                    resolved = resolved.replace(ph, val)
            out.append(f'.{key}({resolved})')
        return out

    def _cell_for_kind(self, kind: str) -> PadCell:
        """Select the PadTech cell for a mixed-I/O pad kind."""
        if kind == 'input':
            return self.pad_tech.input_cell
        if kind == 'output':
            return self.pad_tech.output_cell
        if kind == 'bidir':
            return self.pad_tech.bidir_cell
        if kind == 'tristate_out':
            # tristate output = bidir-tristate (no-pull) cell with the input leg
            # parked on a sink; falls back to bidir_cell if no _io variant.
            return self.pad_tech.bidir_io_cell or self.pad_tech.bidir_cell
        raise ValueError(f"mixed pad: unknown kind '{kind}'")

    def _signal_pads(self) -> Dict[str, Any]:
        """Resolve pin_map.pads into per-instance cell data + boundary ports.

        Netlist topology (the pad cell sits between a physical PAD pin and the
        chip-top net it bonds):
          input        : PAD=<PIN>          C=<in_net>            (pad -> chip)
          output       : PAD=<PIN> I=<out_net> C=<sink>          (chip -> pad)
          tristate_out : PAD=<PIN> I=<out_net> OEN/IE=<_z> C=<sink>
          bidir        : PAD=<PIN> C=<in_net> I=<out_net> OEN/IE=<_z>
        Every chip-top net (in/out/oe) is declared as an internal wire so the
        wrapped-chip instance can bind .port(net); active-high OEs are inverted
        into a per-pad active-low ``_z`` net.

        Returns {ports, instances, oe_wires, sink_wires, chip_wires}.
        """
        ports: List[Dict[str, Any]] = []
        instances: List[Dict[str, Any]] = []
        oe_wires: List[Dict[str, Any]] = []
        sink_wires: List[Dict[str, Any]] = []
        chip_wires: List[Dict[str, Any]] = []

        def _wstr(w: int) -> str:
            return '' if int(w) <= 1 else f'[{int(w) - 1}:0]'

        for ps in self.pin_map.pads:
            width = int(ps.width)
            pad_port = ps.pad or ps.name.upper()
            cell = self._cell_for_kind(ps.kind)
            # boundary PAD port (the physical pin) — always present, sized
            ports.append({
                'name': pad_port,
                'direction': 'inout' if ps.kind in ('bidir', 'tristate_out') else (
                    'input' if ps.kind == 'input' else 'output'),
                'width': width,
                'width_str': _wstr(width),
                'comment': f'{ps.kind} <- {self._pad_comment(ps)}',
            })

            # chip-top nets become internal wires (one per in/out/oe net used)
            for net in (ps.in_net, ps.out_net, ps.oe_net):
                if net:
                    chip_wires.append({'name': net, 'width': width,
                                       'width_str': _wstr(width)})

            # internal active-low OE net (bidir/tristate): _z = ~oe (active-high)
            # or = oe (already active-low). One per pad (bus -> vectored wire).
            z_base = None
            if ps.kind in ('bidir', 'tristate_out') and ps.oe_net:
                z_base = f'{ps.name}_z'
                expr = ps.oe_net if ps.oe_polarity == 'active_low' else f'~{ps.oe_net}'
                oe_wires.append({'name': z_base, 'width': width,
                                 'width_str': _wstr(width), 'expr': expr})
            # dangling receive-leg sink for output/tristate (cell C / {i} port)
            sink_base = None
            if ps.kind in ('output', 'tristate_out'):
                sink_base = f'{ps.name}_nc'
                sink_wires.append({'name': sink_base, 'width': width,
                                   'width_str': _wstr(width)})

            bits = range(width) if width > 1 else [None]
            for bit in bits:
                suffix = f'_{bit}' if bit is not None else ''
                inst = f'uPAD_{ps.name.upper()}{suffix}'
                pad_expr = f'{pad_port}[{bit}]' if bit is not None else pad_port
                in_s = self._slice(ps.in_net, bit, width) if ps.in_net else None
                out_s = self._slice(ps.out_net, bit, width) if ps.out_net else None
                z_s = self._slice(z_base, bit, width) if z_base else None
                sink_s = self._slice(sink_base, bit, width) if sink_base else None

                # map nets onto the cell's placeholders per kind:
                #   input        : {c}=in_net (receiver -> chip)
                #   output       : {o}=out_net (chip -> driver), {c}=sink
                #   bidir        : {i}=in_net, {o}=out_net, {z}=oe-low
                #   tristate_out : {i}=sink,  {o}=out_net, {z}=oe-low
                if ps.kind == 'input':
                    kw = dict(c=in_s)
                elif ps.kind == 'output':
                    kw = dict(o=out_s, c=sink_s)
                elif ps.kind == 'bidir':
                    kw = dict(i=in_s, o=out_s, z=z_s)
                else:  # tristate_out
                    kw = dict(i=sink_s, o=out_s, z=z_s)

                lines = self._expand_mixed_cell(cell, pad=pad_expr, **kw)
                instances.append({'inst': inst, 'module': cell.module,
                                  'lines': lines, 'group': ps.name})

        # de-duplicate chip wires (same net never declared twice)
        seen = set()
        uniq_chip_wires = []
        for w in chip_wires:
            if w['name'] not in seen:
                seen.add(w['name'])
                uniq_chip_wires.append(w)

        return {'ports': ports, 'instances': instances, 'oe_wires': oe_wires,
                'sink_wires': sink_wires, 'chip_wires': uniq_chip_wires}

    @staticmethod
    def _pad_comment(ps: PadSignal) -> str:
        parts = []
        if ps.in_net:
            parts.append(f'in={ps.in_net}')
        if ps.out_net:
            parts.append(f'out={ps.out_net}')
        if ps.oe_net:
            parts.append(f'oe={ps.oe_net}')
        return ', '.join(parts)

    def _chip_connections(self) -> List[Dict[str, Any]]:
        """Wrapped-chip instance connections: one .port(net) per bonded chip port.

        The mixed chip top (nanosoc_multicore_chip) exposes the SAME in/out/oe
        net names as the pad list references, so each connection is .net(net).
        """
        conns: List[Dict[str, Any]] = []
        for ps in self.pin_map.pads:
            for net in (ps.in_net, ps.out_net, ps.oe_net):
                if net:
                    conns.append({'port': net, 'expr': net})
        return conns

    def _build_mixed_context(self) -> Dict[str, Any]:
        now = datetime.datetime.now()
        sys_name = getattr(self.system, 'name', '') if self.system else ''
        chip_module = (self.pin_map.chip_module
                       or sys_name.replace('_soc', '_chip')
                       or 'nanosoc_multicore_chip')
        tech = self.pad_tech.name
        module_name = f'nanosoc_chip_pads_{tech}_multicore'
        resolved = self._signal_pads()
        return {
            'module_name': module_name,
            'chip_module': chip_module,
            'boundary_ports': resolved['ports'],
            'pad_instances': resolved['instances'],
            'oe_wires': resolved['oe_wires'],
            'sink_wires': resolved['sink_wires'],
            'chip_wires': resolved['chip_wires'],
            'chip_connections': self._chip_connections(),
            'power_pads': self._resolved_power_pads(),
            'pad_tech': self.pad_tech,
            'pin_map': self.pin_map,
            'generated_date': now.strftime('%Y-%m-%d %H:%M:%S'),
            'generated_year': now.strftime('%Y'),
        }

    # ------------------------------------------------------------------
    # context
    # ------------------------------------------------------------------
    def _resolved_power_pads(self) -> List[Dict[str, Any]]:
        """Flatten pin_map.power_pads into per-instance dicts for the template.

        Resolves each cell's supply_port from the pad-tech catalogue and
        expands count into <prefix>_<index_base+k> instance names.
        """
        ring_connect = self.pin_map.supply_connect
        if ring_connect is None:
            ring_connect = self.pad_tech.supply_connect

        instances: List[Dict[str, Any]] = []
        for pp in self.pin_map.power_pads:
            cell_info = self.pad_tech.power_cells.get(pp.cell, {})
            supply_port = pp.supply_port or cell_info.get('supply_port')
            # precedence: per-row supply_connect -> ring (pin_map|pad_tech).
            # e.g. a ring whose VDDIO rows set supply_connect:false (emit EMPTY "( );")
            # while VDD/VSS/VDDACC inherit the ring's true (emit .VDD(net)).
            row_connect = (ring_connect if pp.supply_connect is None
                           else pp.supply_connect)
            for k in range(pp.count):
                instances.append({
                    'cell': pp.cell,
                    'inst': f'{pp.prefix}_{pp.index_base + k}',
                    'supply_port': supply_port,
                    'net': pp.net,
                    'commented': pp.commented,
                    'supply_connect': row_connect,
                })
        return instances

    def _ctrl_nets(self) -> Dict[str, str]:
        """Control/SWDIO net names, flavour-dependent.

        cfg    -> pad_clk_i/pad_test_i/pad_nrst_i/pad_swdclk_i/pad_se_i and the
                  pad_swdio base (for _i/_o/_z).
        direct -> clk_i/test_i/nrst_i/swdclk_i and the swdio base.
        """
        if self.pin_map.body_flavour == 'cfg':
            return {
                'clk': 'pad_clk_i',
                'test': 'pad_test_i',
                'nrst': 'pad_nrst_i',
                'swdclk': 'pad_swdclk_i',
                'se': 'pad_se_i',
                'swdio': 'pad_swdio',
            }
        return {
            'clk': 'clk_i',
            'test': 'test_i',
            'nrst': 'nrst_i',
            'swdclk': 'swdclk_i',
            'se': 'se_i',
            'swdio': 'swdio',
        }

    def _ctrl_ren(self) -> Dict[str, str]:
        """Per-control-pin REN expr for the {ren} placeholder (cells with a pull-enable).

        Reads pin_map.ctrl_ren; any pin not listed falls back to ``tielo`` so a
        cell with a {ren} placeholder always resolves.  Techs whose input_cell
        has no {ren} placeholder (e.g. generic) ignore this entirely.
        """
        cr = dict(self.pin_map.ctrl_ren)
        out = {}
        for pin in ('se', 'clk', 'test', 'nrst', 'swdck'):
            out[pin] = cr.get(pin, 'tielo')
        return out

    # ------------------------------------------------------------------
    # M6 — Innovus .io floorplan template emitter
    # ------------------------------------------------------------------
    _IO_SIDES = ('top', 'left', 'bottom', 'right')
    _IO_PITCH = 108.57          # even-spacing placeholder offset increment

    def _signal_io_insts(self, ps: PadSignal) -> List[str]:
        """Pad-cell instance name(s) for one signal pad — EXACTLY as the pad
        ring .v declares them (must match pad_ring _signal_pads: ``uPAD_<NAME>``
        scalar, ``uPAD_<NAME>_<bit>`` per bus bit)."""
        width = int(ps.width)
        base = f'uPAD_{ps.name.upper()}'
        if width <= 1:
            return [base]
        return [f'{base}_{bit}' for bit in range(width)]

    def _power_io_insts(self) -> List[Dict[str, str]]:
        """Power-pad .io placements: [{'name': inst, 'side': side}, ...].

        Reuses the .v instance-naming (``<prefix>_<index_base+k>``) and reads the
        per-row ``pin_side`` placeholder.  ``pin_side`` may be a single side
        string (applied to every instance of the row) or a per-instance list
        (length == count).  Rows without ``pin_side`` contribute nothing.
        """
        out: List[Dict[str, str]] = []
        for pp in self.pin_map.power_pads:
            if pp.commented or pp.pin_side is None:
                continue
            sides = pp.pin_side
            if isinstance(sides, str):
                sides = [sides] * pp.count
            for k in range(pp.count):
                side = sides[k] if k < len(sides) else sides[-1]
                out.append({'name': f'{pp.prefix}_{pp.index_base + k}',
                            'side': side})
        return out

    def _ring_sides(self) -> List[Dict[str, Any]]:
        """Build the per-side instance lists for the .io, or [] if no side data.

        Two sources, in priority order:
          1. ``pin_map.ring`` — an explicit ``{side: [inst, ...]}`` mapping; the
             instance order per side is taken verbatim (the faithful round-trip
             of an Innovus .io export, and the ONLY source for GPIO-bus rings,
             which have no per-pad side hints).
          2. mixed-mode per-pad ``pin_side`` (signal pads) + power-pad
             ``pin_side`` — grouped into the four sides in pad-list order.  Used
             when no explicit ``ring`` is given.  Returns [] (no .io) if the
             mixed pads carry no side hints, so the emitter stays opt-in.

        Offsets are even-spacing placeholders; only side membership + order are
        load-bearing (doc 07 M6 acceptance).
        """
        ring = self.pin_map.ring
        if ring:
            return [self._side_block(s, list(ring.get(s, [])))
                    for s in self._IO_SIDES]

        # derive from pin_side (mixed mode only)
        if not self.pin_map.pads:
            return []
        if not any(ps.pin_side for ps in self.pin_map.pads):
            return []
        by_side: Dict[str, List[str]] = {s: [] for s in self._IO_SIDES}
        for ps in self.pin_map.pads:
            if not ps.pin_side or ps.pin_side not in by_side:
                continue
            by_side[ps.pin_side].extend(self._signal_io_insts(ps))
        for pio in self._power_io_insts():
            if pio['side'] in by_side:
                by_side[pio['side']].append(pio['name'])
        if not any(by_side.values()):
            return []
        return [self._side_block(s, by_side[s]) for s in self._IO_SIDES]

    def _side_block(self, name: str, insts: List[str]) -> Dict[str, Any]:
        return {
            'name': name,
            'insts': [{'name': n, 'offset': (i + 1) * self._IO_PITCH}
                      for i, n in enumerate(insts)],
        }

    def _io_meta(self) -> Dict[str, str]:
        """module_name / tech / variant tokens used to name + head the .io."""
        tech = self.pad_tech.name
        if self._is_mixed():
            module_name = f'nanosoc_chip_pads_{tech}_multicore'
            variant = 'multicore'
        else:
            chip_name = self.system.name.replace('_system', '_chip')
            module_name = f'{chip_name}_pads'
            variant = self.pin_map.variant or f'{self.pin_map.pin_count}pin'
        return {'module_name': module_name, 'tech': tech, 'variant': variant}

    def _render_io(self, pnr_dir: Path) -> Optional[Path]:
        """Render the Innovus .io floorplan template from the pin-map ring/side
        data into ``pnr_dir`` and return its path, or None when no side data
        exists (the emitter is purely additive)."""
        sides = self._ring_sides()
        if not sides:
            return None
        pnr_dir = Path(pnr_dir)
        pnr_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.datetime.now()
        meta = self._io_meta()
        env = self._env()
        template = env.get_template('nanosoc_io_plan.io.j2')
        content = template.render(
            sides=sides,
            module_name=meta['module_name'],
            tech=meta['tech'],
            variant=meta['variant'],
            generated_date=now.strftime('%Y-%m-%d %H:%M:%S'),
            generated_year=now.strftime('%Y'),
        )
        io_path = pnr_dir / f"nanosoc_io_plan_{meta['tech']}_{meta['variant']}.io"
        write_if_changed(io_path, content)
        return io_path

    def _build_context(self) -> Dict[str, Any]:
        now = datetime.datetime.now()
        chip_name = self.system.name.replace('_system', '_chip')
        return {
            'module_name': f'{chip_name}_pads',
            'chip_module_name': 'nanosoc_chip',
            'cfg_module_name': 'nanosoc_chip_cfg',
            'gpio_ports': self._gpio_ports(),
            'power_pads': self._resolved_power_pads(),
            'ctrl_nets': self._ctrl_nets(),
            'ctrl_ren': self._ctrl_ren(),
            'pad_tech': self.pad_tech,
            'pin_map': self.pin_map,
            'generated_date': now.strftime('%Y-%m-%d %H:%M:%S'),
            'generated_year': now.strftime('%Y'),
        }
