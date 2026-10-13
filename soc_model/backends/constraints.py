# Copyright 2026, SoC Labs (www.soclabs.org)
"""Timing-constraints backend — emits SDC (Synopsys/ASIC) + XDC (Xilinx/FPGA).

Scope = TIMING / clock constraints only:
    create_clock, create_generated_clock, set_clock_groups -asynchronous,
    set_input_delay / set_output_delay, set_false_path, set_multicycle_path,
    clock uncertainty, and ASIC pad-cell multicycle/false-path constraints on
    the uPAD_* boundary cells.

NOT in scope: board pin placement (PACKAGE_PIN / IOSTANDARD) — that is a
project-level, board-specific concern (a future pin-map-driven emitter); see
``residuals()``.

The generator is driven by TWO sources, AGGREGATED:

  (a) the model's top-level clocks + frequencies + the chip boundary —
      the top ``clocks:`` list (with optional per-clock ``freq_hz`` / period),
      ``SYS_CLK_FREQ_HZ`` for the primary system clock, and the ChipBoundary
      pad groups (clk / swd / qspi / eth_rmii / ...) which classify every
      boundary port so the right io-delay / false-path / pad-cell constraint
      lands on the right pad;

  (b) a per-module ``constraints:`` block — a PRE-VERIFIED IP declares the
      clocks / generated-clocks / false-paths / multicycle / io-delays /
      clock-groups it needs, and the backend walks every resolved instance,
      collecting those declarations and emitting them at the top.  This is the
      pre-verified-IP story: drop an IP in, its timing intent comes with it.

Two renderers:
  * render_sdc() — Synopsys SDC for the ASIC flow.  Reproduces the hand-written
    ASIC constraints.sdc intent: EXTCLK/SWDCLK create_clock, the
    SWD<->EXT clock-uncertainty + multicycle, the SWD false_path, and the
    uPAD_SWDIO_IO / uPAD_P0_* / uPAD_P1_* pad-cell multicycle/false-path block.
  * render_xdc() — Xilinx XDC for the Vivado FPGA flow.  Reproduces the hand
    nanosoc_multicore_timing.xdc intent: rmii_ref_clk create_clock, the
    mii_rx/tx + qspi_sclk generated clocks, the sys<->rmii async clock group,
    the RMII + QSPI io-delays, and the SWD/UART/LED/MDIO false_paths.

Both renderers are deterministic snippet generators; they are meant to be
``include``d / read into a project-level constraints file (the project keeps
ownership of pin placement and any board-specific overrides).
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..model import (
    ChipBoundary, ChipPad, Clock, ClockGroup, ConstraintSet, FalsePath,
    GeneratedClock, IoDelay, Module, MulticyclePath,
)
from ..utils import resolve_param_ref, write_if_changed


# Periods are quoted to this many decimals (matches the hand SDC 4.16667).
_PERIOD_DP = 5


def _hz_to_period_ns(freq_hz: float) -> float:
    return 1e9 / float(freq_hz)


def _scope_of(obj, default: str = 'both') -> str:
    """Per-entry scope override (set by the builder), else the set default."""
    return str(getattr(obj, '_scope', None) or default)


def _scope_matches(scope: str, tech: str) -> bool:
    """``tech`` is 'sdc' or 'xdc'; map scope keywords onto it."""
    scope = scope.lower()
    if scope in ('both', 'all', ''):
        return True
    if tech == 'sdc':
        return scope in ('asic', 'sdc')
    return scope in ('fpga', 'xdc')


class SoCConstraintsBackend:
    """Generates SDC + XDC timing-constraint snippets for a SoC top.

    Args:
      top_module : the SoC top (walked for ``clocks:`` + ``SYS_CLK_FREQ_HZ`` +
                   per-module ``constraints:`` blocks).
      boundary   : the ChipBoundary (pad groups + kinds) — optional; when
                   absent, only the model clocks + per-IP constraints drive the
                   output (no pad-classified io-delays / pad-cell block).
      sys_uncertainty : clock uncertainty for the primary clock (ns).
      inter_uncertainty : setup uncertainty between async clocks (ns).
    """

    def __init__(self, top_module: Module,
                 boundary: Optional[ChipBoundary] = None,
                 sys_uncertainty: float = 0.35,
                 inter_uncertainty: float = 0.1):
        self.top = top_module
        self.boundary = boundary
        self.sys_uncertainty = sys_uncertainty
        self.inter_uncertainty = inter_uncertainty
        self._residuals: List[str] = []

    # ------------------------------------------------------------------
    # frequency / period helpers
    # ------------------------------------------------------------------
    def _sys_freq_hz(self) -> Optional[int]:
        flat = {n: p.default for n, p in self.top.params.items()}
        f = flat.get('SYS_CLK_FREQ_HZ')
        return f if isinstance(f, int) else None

    def _clock_period_ns(self, clk: Clock,
                         default_hz: Optional[int] = None) -> Optional[float]:
        """Resolve a clock's period: explicit period_ns / freq_hz, else the
        SoC default frequency."""
        if clk.period_ns is not None:
            return float(clk.period_ns)
        if clk.freq_hz is not None:
            return _hz_to_period_ns(clk.freq_hz)
        if default_hz:
            return _hz_to_period_ns(default_hz)
        return None

    # ------------------------------------------------------------------
    # per-IP constraints aggregation (the pre-verified-IP story)
    # ------------------------------------------------------------------
    def aggregated_constraints(self) -> List[Tuple[str, ConstraintSet]]:
        """Walk the resolved hierarchy collecting every module ``constraints:``.

        Returns a list of (instance_path, ConstraintSet) in a deterministic
        (depth-first, declaration-order) order.  The top module's own
        ``constraints:`` (if any) is included first under the path ''.
        """
        out: List[Tuple[str, ConstraintSet]] = []
        if self.top.constraints:
            out.append(('', self.top.constraints))
        seen_modules: set = set()
        self._walk_constraints(self.top, '', out, seen_modules)
        return out

    def _walk_constraints(self, module: Module, hier_path: str,
                          out: List[Tuple[str, ConstraintSet]],
                          seen_modules: set):
        for inst in module.instances:
            child = inst.resolved_module
            if not child:
                continue
            child_path = (f"{hier_path}/{inst.instance_name}"
                          if hier_path else inst.instance_name)
            if child.constraints:
                out.append((child_path, child.constraints))
            # Recurse; guard against pathological cycles by module name.
            key = (child.name, child_path)
            if key in seen_modules:
                continue
            seen_modules.add(key)
            self._walk_constraints(child, child_path, out, seen_modules)

    # ------------------------------------------------------------------
    # boundary classification helpers
    # ------------------------------------------------------------------
    def _clock_ports(self) -> List[Clock]:
        """The top-level model clocks that are EXTERNAL/source clocks (have a
        physical port).  Excludes derived internal clocks (source != external)
        unless they carry an explicit port."""
        out: List[Clock] = []
        for c in self.top.clocks:
            if c.source == 'external' or c.port or c.freq_hz or c.period_ns:
                out.append(c)
        return out

    # ==================================================================
    # SDC (ASIC / Synopsys)
    # ==================================================================
    def render_sdc(self, header: bool = True) -> str:
        L: List[str] = []
        if header:
            L.append('#' + '-' * 76)
            L.append('# nanosoc constraints — TIMING (SDC, Synopsys/ASIC)')
            L.append('# AUTO-GENERATED by nanosoc_gen SoCConstraintsBackend.')
            L.append('# Scope: clocks / clock-groups / io-delays / false-paths /')
            L.append('#        multicycle / pad-cell constraints. NOT pin placement.')
            L.append('#' + '-' * 76)
            L.append('')

        sys_hz = self._sys_freq_hz()

        # ---- units ----
        L.append('set_units -time ns')
        L.append('set_units -capacitance pF')
        L.append('')

        # ---- primary + swd clocks (the hand EXTCLK / SWDCLK pair) ----
        L.append('#### CLOCK DEFINITION')
        # The system / external clock: the FIRST external model clock, or
        # sys_fclk by name, mapped to the boundary clock pad.
        ext_clk, ext_port = self._sdc_ext_clock()
        ext_period = None
        if ext_clk is not None:
            ext_period = self._clock_period_ns(ext_clk, sys_hz)
        if ext_clk is not None and ext_period is not None:
            L.append(
                f'create_clock -name {ext_clk.name} -period '
                f'{ext_period:.{_PERIOD_DP}f} '
                f'-waveform "0 {ext_period / 2:.{_PERIOD_DP}f}" '
                f'[get_ports {ext_port}]')
            L.append(
                f'set_clock_uncertainty {self.sys_uncertainty} '
                f'[get_clocks {ext_clk.name}]')
        else:
            L.append('# (no external system clock with a resolvable frequency '
                     'found in the model)')
            self._residuals.append(
                'SDC: no external system clock with a resolvable period '
                '(set SYS_CLK_FREQ_HZ or a clock freq_hz)')

        # SWD clock — declared in the per-IP constraints (dap/swd IP) OR
        # synthesised here from the boundary swd group at 1/4 of EXTCLK
        # (matches the hand SDC: SWDCLK_PERIOD = 4 * EXTCLK_PERIOD).
        swd_clk, swd_port = self._sdc_swd_clock()
        swd_period = None
        if swd_clk is not None:
            swd_period = self._clock_period_ns(swd_clk)
        if swd_period is None and ext_period is not None and swd_port:
            swd_period = ext_period * 4.0
        if swd_port and swd_period is not None:
            swd_name = swd_clk.name if swd_clk is not None else 'swdclk'
            L.append(
                f'create_clock -name {swd_name} -period '
                f'{swd_period:.{_PERIOD_DP}f} '
                f'-waveform "0 {swd_period / 2:.{_PERIOD_DP}f}" '
                f'[get_ports {swd_port}]')
            L.append(
                f'set_clock_uncertainty {self.sys_uncertainty} '
                f'[get_clocks {swd_name}]')
        L.append('')

        # ---- per-IP additional create_clock / generated_clock (sdc scope) ----
        agg = self.aggregated_constraints()
        ip_clock_lines = self._sdc_ip_clocks(agg)
        if ip_clock_lines:
            L.append('#### PER-IP CLOCKS (aggregated from module constraints:)')
            L.extend(ip_clock_lines)
            L.append('')

        # ---- inter-clock uncertainty + async groups + SWD<->EXT multicycle ----
        if ext_clk is not None and swd_port and swd_period is not None:
            swd_name = swd_clk.name if swd_clk is not None else 'swdclk'
            L.append('### Asynchronous SWD <-> system clock handoff')
            L.append(
                f'set_clock_uncertainty -setup {self.inter_uncertainty} '
                f'-rise_from [get_clocks {swd_name}] '
                f'-rise_to [get_clocks {ext_clk.name}]')
            L.append(
                f'set_clock_uncertainty -setup {self.inter_uncertainty} '
                f'-rise_from [get_clocks {ext_clk.name}] '
                f'-rise_to [get_clocks {swd_name}]')
            L.append('')
            L.append('### Multicycle path through asynchronous clock domains')
            L.append(f'set_multicycle_path 2 -setup -end -from {swd_name} '
                     f'-to {ext_clk.name}')
            L.append(f'set_multicycle_path 1 -hold -end -from {swd_name} '
                     f'-to {ext_clk.name}')
            L.append(f'set_multicycle_path 2 -setup -end -from {ext_clk.name} '
                     f'-to {swd_name}')
            L.append(f'set_multicycle_path 1 -hold -end -from {ext_clk.name} '
                     f'-to {swd_name}')
            L.append(f'set_false_path -hold -from {ext_clk.name} '
                     f'-to {swd_name}')
            L.append('')

        # ---- per-IP clock groups (async) ----
        cg_lines = self._cg_lines(agg, 'sdc')
        if cg_lines:
            L.append('### Per-IP asynchronous clock groups')
            L.extend(cg_lines)
            L.append('')

        # ---- ASIC pad-cell multicycle / false-path block ----
        pad_lines = self._sdc_pad_cells()
        if pad_lines:
            L.append('### Multicycle / false paths through pads (pad cells)')
            L.extend(pad_lines)
            L.append('')

        # ---- io-delays: model boundary + per-IP ----
        io_lines = self._sdc_io_delays(ext_clk, swd_clk, agg)
        if io_lines:
            L.append('#### DELAY DEFINITION')
            L.extend(io_lines)
            L.append('')

        # ---- per-IP false_paths / multicycle (sdc scope) ----
        ip_excepts = self._except_lines(agg, 'sdc')
        if ip_excepts:
            L.append('### Per-IP timing exceptions')
            L.extend(ip_excepts)
            L.append('')

        # ---- design rule defaults (match hand SDC tail) ----
        L.append('set_max_capacitance 3 [all_outputs]')
        L.append('set_max_fanout 10 [all_inputs]')

        return '\n'.join(L) + '\n'

    def _sdc_ext_clock(self) -> Tuple[Optional[Clock], str]:
        """Pick the external system clock + its boundary port (SDC).

        Prefers a top clock whose source is 'external'; the physical port is
        the upper-cased clock name (the hand SDC creates clk on [get_ports CLK]).
        """
        cand = None
        for c in self.top.clocks:
            if c.source == 'external':
                cand = c
                break
        if cand is None and self.top.clocks:
            cand = self.top.clocks[0]
        if cand is None:
            return None, ''
        port = cand.port or self._boundary_port_for(cand.name) or cand.name.upper()
        return cand, port

    def _sdc_swd_clock(self) -> Tuple[Optional[Clock], str]:
        """Find the SWD clock from the per-IP constraints, plus the swd port.

        The SWD clock port comes from the boundary 'swd' group (the input pad);
        the hand SDC names it SWDCK / creates 'swdclk' on it.
        """
        # explicit per-IP clock named like *swd*
        for _path, cs in self.aggregated_constraints():
            for c in cs.clocks:
                if 'swd' in c.name.lower():
                    return c, (c.port or 'SWDCK')
        # else derive port from the boundary swd group (input pad = clk)
        port = None
        for pad in (self.boundary.pads if self.boundary else []):
            if pad.group == 'swd' and pad.kind == 'input':
                port = pad.chip_port.upper()
                break
        if port:
            return None, port
        return None, ''

    def _boundary_port_for(self, net: str) -> Optional[str]:
        """Return the chip boundary port (upper-cased) for an inner clock net."""
        if not self.boundary:
            return None
        for pad in self.boundary.pads:
            if pad.in_net == net or pad.chip_port == net:
                return pad.chip_port.upper()
        return None

    def _sdc_ip_clocks(self, agg) -> List[str]:
        out: List[str] = []
        for path, cs in agg:
            for c in cs.clocks:
                if not _scope_matches(_scope_of(c, cs.scope), 'sdc'):
                    continue
                if 'swd' in c.name.lower():
                    continue  # handled in the main SWD block
                period = self._clock_period_ns(c)
                if period is None:
                    continue
                port = c.port or c.name.upper()
                out.append(
                    f'create_clock -name {c.name} -period '
                    f'{period:.{_PERIOD_DP}f} [get_ports {port}]'
                    + (f'  ;# {path}' if path else ''))
            for g in cs.generated_clocks:
                if not _scope_matches(_scope_of(g, cs.scope), 'sdc'):
                    continue
                out.append(self._gen_clock_sdc(g, path))
        return out

    def _gen_clock_sdc(self, g: GeneratedClock, path: str) -> str:
        parts = ['create_generated_clock', f'-name {g.name}']
        if g.divide_by is not None:
            parts.append(f'-divide_by {g.divide_by}')
        if g.multiply_by is not None:
            parts.append(f'-multiply_by {g.multiply_by}')
        src = g.source_pin or g.source
        if src:
            # a parent CLOCK name -> -source [get_pins ...] is design-specific;
            # for SDC use the source as a pin path if it looks hierarchical,
            # else as a source clock via -source [get_ports].
            if '/' in src:
                parts.append(f'-source [get_pins {src}]')
            else:
                parts.append(f'-source [get_ports {src.upper()}]')
        tgt = g.target_pin or ''
        if tgt:
            parts.append(f'[get_pins {tgt}]')
        line = ' '.join(parts)
        if path:
            line += f'  ;# {path}'
        return line

    def _sdc_pad_cells(self) -> List[str]:
        """ASIC pad-cell multicycle/false-path block on uPAD_* cells.

        Reproduces the hand-written ASIC constraints.sdc pad block:
          * SWDIO bidir pad: false_path -through + multicycle-2 -through +
            per-control-pin multicycle (I/IE/DS/OEN -> C),
          * GPIO buses (P0/P1 group, here 'hostio4'): per-control-pin mcp-2.

        Pad-cell instance names follow the pad-ring convention uPAD_<NAME>
        (and uPAD_<NAME>_<bit> for buses); the hand file uses uPAD_SWDIO_IO and
        the wildcard uPAD_P0_* / uPAD_P1_*.  We emit the SWDIO bidir cell by
        its boundary name and any GPIO/host bus group as a wildcard.
        """
        if not self.boundary:
            return []
        out: List[str] = []
        ctrl_pins = ('I', 'IE', 'DS', 'OEN')

        # SWDIO bidir pad
        swdio = None
        for pad in self.boundary.pads:
            if pad.group == 'swd' and pad.kind == 'bidir':
                swdio = pad
                break
        if swdio is not None:
            cell = f'uPAD_{swdio.chip_port.upper()}_IO'
            out.append(f'set_false_path -through {cell}')
            out.append(f'set_multicycle_path 2 -through {cell}')
            for p in ctrl_pins:
                out.append(f'set_multicycle_path 2 -from {cell}/{p} '
                           f'-to {cell}/C')

        # GPIO / host bidir buses -> wildcard per-control-pin multicycle.
        # The hand file constrains uPAD_P0_* / uPAD_P1_*; the multicore's
        # equivalent wide bidir bus is the hostio4 group.
        for pad in self.boundary.pads:
            if pad.kind == 'bidir' and pad is not swdio and pad.width >= 1 \
                    and pad.group in ('hostio4', 'gpio'):
                cell = f'uPAD_{pad.chip_port.upper()}_*'
                for p in ctrl_pins:
                    out.append(f'set_multicycle_path 2 -from {cell}/{p} '
                               f'-to {cell}/C')
        return out

    def _sdc_io_delays(self, ext_clk, swd_clk, agg) -> List[str]:
        out: List[str] = []
        sys_name = ext_clk.name if ext_clk is not None else None
        # Model-boundary input delays: every non-clock pure-input control pad,
        # referenced to the system clock (hand SDC: NRST/TEST/P0/P1 @ EXTCLK,
        # SWDIO @ SWDCLK).
        if self.boundary and sys_name:
            swd_name = (swd_clk.name if swd_clk is not None else 'swdclk')
            for pad in self.boundary.pads:
                if pad.kind != 'input':
                    continue
                if pad.group == 'clk':
                    continue  # clocks aren't input-delayed to themselves
                ref = swd_name if pad.group == 'swd' else sys_name
                out.append(
                    f'set_input_delay -clock [get_clocks {ref}] -add_delay '
                    f'0.1 [get_ports {pad.chip_port.upper()}]')
        # Per-IP io-delays (sdc scope)
        for path, cs in agg:
            for d in cs.io_delays:
                if not _scope_matches(_scope_of(d, cs.scope), 'sdc'):
                    continue
                out.extend(self._io_delay_lines(d, path, ports_upper=True))
        return out

    # ==================================================================
    # XDC (FPGA / Xilinx Vivado)
    # ==================================================================
    def render_xdc(self, header: bool = True) -> str:
        L: List[str] = []
        if header:
            L.append('#' + '#' * 78)
            L.append('## nanosoc constraints — TIMING (XDC, Xilinx/Vivado FPGA)')
            L.append('## AUTO-GENERATED by nanosoc_gen SoCConstraintsBackend.')
            L.append('## Scope: clocks / generated-clocks / async clock groups /')
            L.append('##        io-delays / false-paths. NOT pin placement (PACKAGE_PIN/')
            L.append('##        IOSTANDARD stay in the board pin XDC).')
            L.append('#' + '#' * 78)
            L.append('')

        sys_hz = self._sys_freq_hz()
        agg = self.aggregated_constraints()

        # ---- primary clocks (model external clocks + per-IP clocks) ----
        L.append('#-------------------------------------------------------------')
        L.append('# Clocks')
        L.append('#-------------------------------------------------------------')
        # External model clocks that carry an explicit FPGA frequency, e.g.
        # rmii_ref_clk @ 50 MHz; the 25 MHz system clock comes from the BD
        # clk_wiz (handled by Vivado), so the model clock without freq is noted.
        emitted_clocks = set()
        for c in self._clock_ports():
            period = self._clock_period_ns(c)
            if period is None:
                self._residuals.append(
                    f'XDC: clock {c.name} has no FPGA frequency (it is sourced '
                    f'by the BD clk_wiz / PS FCLK — declare freq_hz to emit a '
                    f'create_clock)')
                continue
            port = c.port or c.name
            L.append(
                f'create_clock -name {c.name} -period '
                f'{period:.{_PERIOD_DP}f} [get_ports {port}]')
            emitted_clocks.add(c.name)
        # Per-IP create_clock (xdc scope)
        for path, cs in agg:
            for c in cs.clocks:
                if not _scope_matches(_scope_of(c, cs.scope), 'xdc'):
                    continue
                period = self._clock_period_ns(c)
                if period is None or c.name in emitted_clocks:
                    continue
                port = c.port or c.name
                L.append(
                    f'create_clock -name {c.name} -period '
                    f'{period:.{_PERIOD_DP}f} [get_ports {port}]'
                    + (f'  ;# {path}' if path else ''))
                emitted_clocks.add(c.name)
        L.append('')

        # ---- generated clocks (per-IP) ----
        gen_lines: List[str] = []
        for path, cs in agg:
            for g in cs.generated_clocks:
                if not _scope_matches(_scope_of(g, cs.scope), 'xdc'):
                    continue
                gen_lines.extend(self._gen_clock_xdc(g, path))
        if gen_lines:
            L.append('#-------------------------------------------------------------')
            L.append('# Generated (derived) clocks — aggregated from module constraints:')
            L.append('#-------------------------------------------------------------')
            L.extend(gen_lines)
            L.append('')

        # ---- async clock groups (per-IP + model) ----
        cg_lines = self._cg_lines(agg, 'xdc')
        if cg_lines:
            L.append('#-------------------------------------------------------------')
            L.append('# Asynchronous clock groups')
            L.append('#-------------------------------------------------------------')
            L.extend(cg_lines)
            L.append('')

        # ---- io-delays (per-IP) ----
        io_lines: List[str] = []
        for path, cs in agg:
            for d in cs.io_delays:
                if not _scope_matches(_scope_of(d, cs.scope), 'xdc'):
                    continue
                io_lines.extend(self._io_delay_lines(d, path, ports_upper=False))
        if io_lines:
            L.append('#-------------------------------------------------------------')
            L.append('# I/O timing (RMII / QSPI / ... — aggregated from IPs)')
            L.append('#-------------------------------------------------------------')
            L.extend(io_lines)
            L.append('')

        # ---- false-paths (per-IP + model swd/uart) ----
        fp_lines = self._except_lines(agg, 'xdc', only_false=True)
        # Boundary-derived SWD false-paths use the boundary (chip) port names
        # (e.g. swdio / dap_swclktck). Suppress them when a constraints: block
        # already declares SWD false-paths with the FPGA-board names
        # (swd_clk / swd_dio) — otherwise the XDC carries both, one of which
        # matches nothing at the level it is applied.
        if not self._agg_declares_swd(agg):
            fp_lines.extend(self._xdc_model_false_paths())
        if fp_lines:
            L.append('#-------------------------------------------------------------')
            L.append('# False paths')
            L.append('#-------------------------------------------------------------')
            L.extend(fp_lines)
            L.append('')

        # ---- multicycle (per-IP, xdc) ----
        mcp_lines = []
        for path, cs in agg:
            for m in cs.multicycle_paths:
                if not _scope_matches(_scope_of(m, cs.scope), 'xdc'):
                    continue
                mcp_lines.append(self._mcp_line(m, path))
        if mcp_lines:
            L.append('#-------------------------------------------------------------')
            L.append('# Multicycle paths (aggregated from IPs)')
            L.append('#-------------------------------------------------------------')
            L.extend(mcp_lines)
            L.append('')

        return '\n'.join(L).rstrip('\n') + '\n'

    def _gen_clock_xdc(self, g: GeneratedClock, path: str) -> List[str]:
        out: List[str] = []
        parts = ['create_generated_clock', f'-name {g.name}']
        if g.divide_by is not None:
            parts.append(f'-divide_by {g.divide_by}')
        if g.multiply_by is not None:
            parts.append(f'-multiply_by {g.multiply_by}')
        src = g.source_pin
        if src:
            parts.append('-source [get_pins -hierarchical -filter '
                         f'{{NAME =~ "{src}"}}]')
        elif g.source:
            parts.append(f'-source [get_ports {g.source}]')
        if g.target_pin:
            parts.append('[get_pins -hierarchical -filter '
                         f'{{NAME =~ "{g.target_pin}"}}]')
        elif g.name:
            parts.append(f'[get_ports {g.name}]')
        line = ' '.join(parts)
        if path:
            out.append(f'# {path}')
        out.append(line)
        return out

    def _agg_declares_swd(self, agg) -> bool:
        """True if any aggregated xdc-scope false_path targets an SWD port.

        Detected by an 'swd' substring on any from/to/through (covers the
        board names swd_clk / swd_dio). Used to suppress the boundary-derived
        (chip-port-named) SWD false-paths so they are not emitted twice.
        """
        for _path, cs in agg:
            for f in cs.false_paths:
                if not _scope_matches(_scope_of(f, cs.scope), 'xdc'):
                    continue
                for port in (f.from_, f.to, f.through):
                    if port and 'swd' in str(port).lower():
                        return True
        return False

    def _xdc_model_false_paths(self) -> List[str]:
        """Boundary-derived SWD false-paths (hand XDC: swd_clk / swd_dio).

        Only emitted when the boundary carries a swd group AND no per-IP
        constraint already declared swd false-paths (avoids duplicates)."""
        if not self.boundary:
            return []
        out: List[str] = []
        swd_in = swd_bidir = None
        for pad in self.boundary.pads:
            if pad.group == 'swd' and pad.kind == 'input':
                swd_in = pad
            elif pad.group == 'swd' and pad.kind == 'bidir':
                swd_bidir = pad
        if swd_in is not None:
            out.append(f'set_false_path -from [get_ports {swd_in.chip_port}]')
        if swd_bidir is not None:
            out.append(f'set_false_path -from [get_ports {swd_bidir.chip_port}]')
            out.append(f'set_false_path -to   [get_ports {swd_bidir.chip_port}]')
        return out

    # ==================================================================
    # shared snippet emitters
    # ==================================================================
    def _io_delay_lines(self, d: IoDelay, path: str,
                        ports_upper: bool) -> List[str]:
        out: List[str] = []
        cmd = ('set_input_delay' if d.kind == 'input' else 'set_output_delay')
        for port in d.ports:
            p = port.upper() if ports_upper else port
            tag = f'  ;# {path}' if path else ''
            if d.min is not None:
                out.append(
                    f'{cmd} -clock [get_clocks {d.clock}] -min '
                    f'{d.min} [get_ports {{{p}}}]{tag}')
            if d.max is not None:
                out.append(
                    f'{cmd} -clock [get_clocks {d.clock}] -max '
                    f'{d.max} [get_ports {{{p}}}]{tag}')
            if d.min is None and d.max is None:
                out.append(
                    f'{cmd} -clock [get_clocks {d.clock}] -add_delay '
                    f'0.1 [get_ports {{{p}}}]{tag}')
        return out

    def _cg_lines(self, agg, tech: str) -> List[str]:
        out: List[str] = []
        for path, cs in agg:
            for cg in cs.clock_groups:
                if not _scope_matches(_scope_of(cg, cs.scope), tech):
                    continue
                parts = ['set_clock_groups']
                if cg.name:
                    parts.append(f'-name {cg.name}')
                parts.append('-asynchronous')
                for grp in cg.groups:
                    parts.append(f'-group {self._group_expr(grp)}')
                line = ' '.join(parts)
                if path:
                    out.append(f'# {path}')
                out.append(line)
        return out

    @staticmethod
    def _group_expr(grp) -> str:
        """Render one ``-group`` clock collection.

        A group entry containing ``/`` is treated as a hierarchical PIN glob and
        resolved with ``get_clocks -of_objects [get_pins ...]`` — robust to the
        Vivado BD ``clk_wiz`` auto-naming the system clock (mirroring the hand
        ``nanosoc_multicore_timing.xdc``, which looks the sys clock up by pin
        rather than by an unstable generated name). Plain entries are clock
        names emitted as ``[get_clocks {a b c}]``. A group mixing both yields a
        ``[concat ...]`` of the collections.
        """
        names = [g for g in grp if '/' not in str(g)]
        pins = [g for g in grp if '/' in str(g)]
        exprs: List[str] = []
        if names:
            exprs.append(f'[get_clocks {{{" ".join(names)}}}]')
        for p in pins:
            exprs.append('[get_clocks -of_objects [get_pins -hierarchical '
                         f'-filter {{NAME =~ "{p}"}}]]')
        if len(exprs) == 1:
            return exprs[0]
        return '[concat ' + ' '.join(exprs) + ']'

    def _except_lines(self, agg, tech: str,
                      only_false: bool = False) -> List[str]:
        out: List[str] = []
        for path, cs in agg:
            for f in cs.false_paths:
                if not _scope_matches(_scope_of(f, cs.scope), tech):
                    continue
                out.append(self._fp_line(f, path, tech))
            if only_false:
                continue
            for m in cs.multicycle_paths:
                if not _scope_matches(_scope_of(m, cs.scope), tech):
                    continue
                out.append(self._mcp_line(m, path))
        return out

    def _fp_line(self, f: FalsePath, path: str, tech: str) -> str:
        parts = ['set_false_path']
        if f.hold:
            parts.append('-hold')
        wrap = ((lambda x: f'[get_ports {x}]') if tech == 'xdc'
                else (lambda x: x))
        if f.from_:
            parts.append(f'-from {wrap(f.from_)}')
        if f.to:
            parts.append(f'-to {wrap(f.to)}')
        if f.through:
            parts.append(f'-through {f.through}')
        line = ' '.join(parts)
        if path:
            line += f'  ;# {path}'
        return line

    def _mcp_line(self, m: MulticyclePath, path: str) -> str:
        parts = ['set_multicycle_path', str(m.cycles)]
        if m.setup:
            parts.append('-setup')
        if m.hold:
            parts.append('-hold')
        if m.end:
            parts.append('-end')
        if m.from_:
            parts.append(f'-from {m.from_}')
        if m.to:
            parts.append(f'-to {m.to}')
        if m.through:
            parts.append(f'-through {m.through}')
        line = ' '.join(parts)
        if path:
            line += f'  ;# {path}'
        return line

    # ==================================================================
    # residuals + file emission
    # ==================================================================
    def ips_without_constraints(self) -> List[str]:
        """Direct child IP instances of the top that carry NO constraints:
        block — the pre-verified IPs that still need timing authored.

        Reported as a residual so the operator knows which IP YAMLs to extend.
        """
        out: List[str] = []
        for inst in self.top.instances:
            child = inst.resolved_module
            if child is None:
                continue
            if child.constraints is None:
                out.append(f'{inst.instance_name} ({child.name})')
        return out

    def residuals(self) -> List[str]:
        r: List[str] = []
        r.append('pin placement (PACKAGE_PIN / IOSTANDARD) is OUT OF SCOPE — '
                 'it stays in the board pin XDC (future pin-map-driven emitter)')
        if not self.boundary:
            r.append('no chip boundary supplied — pad-cell / boundary-classified '
                     'io-delays + swd false-paths not emitted (per-IP only)')
        # Report direct child IPs WITHOUT a constraints: block so the operator
        # knows which pre-verified IPs still need timing authored.
        missing = self.ips_without_constraints()
        if missing:
            r.append('IP instances with NO constraints: block (timing not '
                     'authored): ' + ', '.join(missing))
        r.extend(self._residuals)
        return r

    def generate(self, out_dir: Path,
                 flist_dir: Optional[Path] = None,
                 emit_sdc: bool = True,
                 emit_xdc: bool = True) -> List[Path]:
        """Write the SDC and/or XDC snippet files.  Returns written paths."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        written: List[Path] = []
        stem = self.top.name
        if emit_sdc:
            p = out_dir / f'{stem}_constraints.sdc'
            write_if_changed(p, self.render_sdc())
            written.append(p)
        if emit_xdc:
            p = out_dir / f'{stem}_constraints.xdc'
            write_if_changed(p, self.render_xdc())
            written.append(p)
        return written
