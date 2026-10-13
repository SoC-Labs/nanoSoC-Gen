# Copyright 2026, SoC Labs (www.soclabs.org)
"""FPGA-wrapper backend — generates the synthesizable Vivado IP wrapper.

Produces ``<module_name>.v`` (e.g. ``nanosoc_multicore_vivado_wrapper.v``): a
synthesizable wrapper that instantiates the inner SoC top
(``nanosoc_multicore_soc``) and presents IP-Integrator-friendly board ports.

This is the FPGA sibling of SoCChipWrapperBackend (doc-07 M4).  Both consume the
SAME ChipBoundary spec (lib/chip_boundary/<name>.yaml) — which inner SoC port is
BONDED / TIED / OPEN, plus each bidir pad's oe polarity.  The two differ ONLY in
emission:

  * SoCChipWrapperBackend (ASIC seam) re-exposes each bonded inner net 1:1
    (in/out/oe triples) for the downstream pad ring to turn into true `inout`.
  * SoCFpgaWrapperBackend (this) emits NO pad cells; each bonded bidir becomes
    the Vivado IOBUF triple ``<base>_i / <base>_o / <base>_t`` where ``_t = 1``
    means Hi-Z.  The Hi-Z enable is derived from the inner oe with the SAME
    boolean the pad ring's ``_z`` net uses:

        _t = ~oe   when oe_polarity == active_high   (VERIFIED vs hand file:
                                                       mdio_t  = ~md_padoe_o,
                                                       swd_dio_t = ~dap_swdoen,
                                                       qspi_io_t = ~qspi_io_e_int)
        _t =  oe   when oe_polarity == active_low

The BESPOKE FPGA/board layer (board pin renames, FPGA param overrides, debug
taps, dropped pads, the SYSRESETREQ self-loop, rtc_clk<-sys_fclk) is NOT
model-derivable; it is carried by an FpgaProfile (lib/fpga_profile/<name>.yaml)
and every applied item is reported via ``residuals()``.

Generated files:
  - <module_name>.v          — synthesizable FPGA wrapper
  - <module_name>.flist      — inline file list
"""

import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..model import ChipBoundary, FpgaProfile
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


class SoCFpgaWrapperBackend:
    """Generates the synthesizable FPGA Vivado wrapper from a boundary spec."""

    def __init__(self, boundary: ChipBoundary,
                 profile: Optional[FpgaProfile] = None,
                 soc_ports: Optional[List[Dict[str, Any]]] = None):
        """`soc_ports` is the inner SoC's expanded boundary port list (see
        ``SoCTopLevelBackend._build_ports()``). Supplying it enables
        :meth:`validate_soc_ports`; it is optional so unit tests can drive the
        backend from a spec alone.
        """
        self.boundary = boundary
        self.profile = profile or FpgaProfile()
        self.soc_ports: Dict[str, Dict[str, Any]] = {
            p['name']: p for p in (soc_ports or [])
        }
        # residual log: bespoke (non-derivable) items actually applied.
        self._residuals: List[str] = []

    # ------------------------------------------------------------------
    # spec <-> SoC cross-check
    # ------------------------------------------------------------------
    def validate_soc_ports(self) -> None:
        """Raise unless every SoC port is accounted for, and vice versa.

        Mirrors ``SoCChipWrapperBackend.validate_soc_ports``. On FPGA the
        stakes are higher than in simulation: Vivado ties an unconnected input
        to 0, so a dropped `hready` does not float — it wedges the bus.
        """
        if not self.soc_ports:
            return
        classified = {c['port'] for c in self.instance_connections()}
        actual = set(self.soc_ports)

        unclassified = sorted(actual - classified)
        stale = sorted(classified - actual)
        if not unclassified and not stale:
            return

        lines = [f"fpga wrapper '{self.module_name}' does not match inner "
                 f"module '{self.inner_module}':"]
        if unclassified:
            lines.append(f"  {len(unclassified)} SoC port(s) unclassified "
                         f"(silently dropped; Vivado ties inputs to 0):")
            lines += [f"      {self.soc_ports[n]['direction']:6} {n}"
                      for n in unclassified]
            lines.append("  -> add each to pads / ties / opens / terminators.")
        if stale:
            lines.append(f"  {len(stale)} classified name(s) are not SoC ports "
                         f"(the wrapper would not elaborate):")
            lines += [f"      {n}" for n in stale]
            lines.append("  -> the SoC was renamed; update the spec.")
        raise ValueError("\n".join(lines))

    def _terminated_inner_ports(self) -> List[str]:
        """Inner SoC port names driven/read by a terminator helper instance."""
        names: List[str] = []
        for t in self.boundary.terminators:
            names.extend(t.soc_ports.values())
        return names

    def _terminator_width(self, term, soc_port: str) -> int:
        """Wire width for a terminated port: the spec's, cross-checked.

        Identical rules to SoCChipWrapperBackend._terminator_width — the spec is
        authoritative (a wrapper cannot reference the SoC's parameters), and a
        disagreement with the model is a spec bug, not something to paper over.
        """
        declared = term.widths.get(soc_port)
        actual = None
        if self.soc_ports:
            try:
                actual = int(self.soc_ports[soc_port]['width'])
            except (KeyError, TypeError, ValueError):
                actual = None
        if declared is not None and actual is not None and declared != actual:
            raise ValueError(
                f"terminator '{term.instance}': width of '{soc_port}' declared "
                f"as {declared} but the SoC port is {actual} bits wide")
        if declared is not None:
            return declared
        return actual if actual is not None else 1

    def terminator_context(self) -> List[Dict[str, Any]]:
        """Terminator instances + wire declarations for the template.

        `bonded_refs` are resolved through the profile's `port_renames`, so a
        terminator that asks for `sys_sysresetn` picks up the board's `nrst`.
        """
        out: List[Dict[str, Any]] = []
        for t in self.boundary.terminators:
            wires = [
                {'name': soc_port,
                 'width_str': _wstr(self._terminator_width(t, soc_port))}
                for soc_port in t.soc_ports.values()
            ]
            conns = [{'port': p, 'expr': n} for p, n in t.soc_ports.items()]
            conns += [{'port': p, 'expr': self._rename(n)}
                      for p, n in t.bonded_refs.items()]
            out.append({
                'module': t.module,
                'instance': t.instance,
                'desc': t.desc,
                'params': [{'name': k, 'value': v} for k, v in t.params.items()],
                'wires': wires,
                'conns': conns,
            })
        return out

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @property
    def module_name(self) -> str:
        return (self.profile.module_name
                or f'{self.boundary.chip_module}_vivado_wrapper')

    @property
    def inner_module(self) -> str:
        return self.boundary.inner_module

    def _rename(self, net: str) -> str:
        """Apply the profile's board-pin rename to an inner net name."""
        return self.profile.port_renames.get(net, net)

    def _excluded(self) -> set:
        return set(self.profile.exclude_pads or [])

    def _feature_drives(self) -> Dict[str, Dict[str, Any]]:
        """Map of inner-input-port -> feature-block that drives it.

        Features override the boundary's tie/open for the named inner port.
        """
        out: Dict[str, Dict[str, Any]] = {}
        for fname, fdef in (self.profile.features or {}).items():
            if not isinstance(fdef, dict) or not fdef.get('enabled', False):
                continue
            drives = fdef.get('drives')
            if drives:
                out[drives] = {'feature': fname, **fdef}
        return out

    def _tap_sources(self) -> Dict[str, Dict[str, str]]:
        """Map of inner-output-port -> debug-tap that binds it on the instance.

        Only ``soc_out`` taps bind the inner output DIRECTLY to the tap port on
        the instance.  ``soc_wire`` taps bind the inner output to an internal
        wire (handled in internal_wires / the assign block); ``wire`` taps just
        re-export an existing wrapper port (no instance change).
        """
        out: Dict[str, Dict[str, str]] = {}
        for tap in (self.profile.debug_taps or []):
            if tap.get('kind') == 'soc_out' and tap.get('from'):
                out[tap['from']] = tap
        return out

    def _tap_assigns(self) -> List[Dict[str, str]]:
        """``assign <tap> = <src>;`` lines for soc_wire / wire taps."""
        assigns: List[Dict[str, str]] = []
        for tap in (self.profile.debug_taps or []):
            if tap.get('kind') in ('soc_wire', 'wire') and tap.get('from'):
                assigns.append({'lhs': tap['name'], 'rhs': tap['from'],
                                'comment': tap.get('comment', '')})
        return assigns

    # ------------------------------------------------------------------
    # boundary resolution (FPGA emission)
    # ------------------------------------------------------------------
    def wrapper_ports(self) -> List[Dict[str, Any]]:
        """Ordered FPGA wrapper port list (after renames / excludes / taps).

        Per bonded pad kind:
          input        -> 1 input  (renamed in_net)
          output       -> 1 output (renamed out_net)
          tristate_out -> _o (output) + _t (output)         [Vivado triple, no _i]
          bidir        -> _i (input) + _o (output) + _t (output)
        Excluded pads emit nothing.  Debug taps + the SYSRESETREQ status export
        are appended last.  Each dict: {name, direction, width, width_str,
        group, role, base}.
        """
        excluded = self._excluded()
        ports: List[Dict[str, Any]] = []

        def _emit(name, direction, width, group, role, base=None):
            ports.append({
                'name': name,
                'direction': direction,
                'width': int(width),
                'width_str': _wstr(width),
                'group': group,
                'role': role,
                'base': base,
            })

        for pad in self.boundary.pads:
            if pad.chip_port in excluded:
                continue
            if pad.kind == 'input':
                _emit(self._rename(pad.in_net), 'input', pad.width,
                      pad.group, 'in')
            elif pad.kind == 'output':
                _emit(self._rename(pad.out_net), 'output', pad.width,
                      pad.group, 'out')
            elif pad.kind == 'tristate_out':
                base = self._rename(pad.out_net)
                _emit(f'{base}_o', 'output', pad.width, pad.group, 'o', base)
                _emit(f'{base}_t', 'output', pad.width, pad.group, 't', base)
            elif pad.kind == 'bidir':
                base = self._rename(pad.in_net)
                _emit(f'{base}_i', 'input', pad.width, pad.group, 'i', base)
                _emit(f'{base}_o', 'output', pad.width, pad.group, 'o', base)
                _emit(f'{base}_t', 'output', pad.width, pad.group, 't', base)
            else:
                raise ValueError(
                    f"fpga wrapper pad '{pad.chip_port}': unknown kind "
                    f"'{pad.kind}'")

        # debug taps appended as outputs
        for tap in (self.profile.debug_taps or []):
            _emit(tap['name'], 'output', 1, 'debug', 'tap')

        return ports

    def _bidir_legs(self, pad) -> Dict[str, Any]:
        """Per-leg disposition for one bidir/tristate pad.

        Returns a dict describing the _i/_o/_t legs and whether each binds the
        inner port DIRECTLY to the wrapper port (when the renamed wrapper port
        name == the inner net name, no internal wire/assign is needed) or via an
        internal wire + continuous assign:

          {base, port_i, port_o, port_t,
           in_direct  (bool), out_direct (bool),
           out_wire   (str|None), oe_wire (str|None),
           o_assign   (dict|None), t_assign (dict)}

        - port_i = <base>_i ; port_o = <base>_o ; port_t = <base>_t.
        - in_direct  : True iff pad.in_net == port_i  (bind .<in_net>(port_i)).
        - out_direct : True iff pad.out_net == port_o (bind .<out_net>(port_o));
                       otherwise the inner out_net is an internal wire and
                       `assign port_o = out_net;` re-exports it.
        - The oe leg is ALWAYS a continuous assign (port_t computed from oe);
          the inner oe binds to an internal wire of its own name.
        """
        base = self._rename(pad.in_net if pad.kind == 'bidir' else pad.out_net)
        port_i = f'{base}_i'
        port_o = f'{base}_o'
        port_t = f'{base}_t'
        w = _wstr(pad.width)

        in_direct = (pad.kind == 'bidir' and pad.in_net == port_i)
        out_direct = (pad.out_net == port_o)

        o_assign = None
        out_wire = None
        if not out_direct:
            out_wire = pad.out_net
            o_assign = {'lhs': port_o, 'rhs': pad.out_net, 'width_str': w}

        oe_wire = pad.oe_net if pad.oe_net else None
        t_assign = None
        if pad.oe_net:
            rhs = (pad.oe_net if pad.oe_polarity == 'active_low'
                   else f'~{pad.oe_net}')
            t_assign = {'lhs': port_t, 'rhs': rhs, 'width_str': w}

        return {
            'base': base, 'port_i': port_i, 'port_o': port_o, 'port_t': port_t,
            'in_direct': in_direct, 'out_direct': out_direct,
            'out_wire': out_wire, 'oe_wire': oe_wire,
            'o_assign': o_assign, 't_assign': t_assign,
        }

    def tristate_assigns(self) -> List[Dict[str, Any]]:
        """``assign <base>_<leg> = <expr>;`` lines for every bidir/tristate pad.

        - data-out leg: ``assign <base>_o = <out_net>;`` ONLY when the wrapper
          port name differs from the inner out_net (else bound directly).
        - hi-Z enable leg: ``assign <base>_t = ~<oe_net>`` (active_high) or
          ``= <oe_net>`` (active_low) — always emitted.
        """
        excluded = self._excluded()
        assigns: List[Dict[str, Any]] = []
        for pad in self.boundary.pads:
            if pad.chip_port in excluded:
                continue
            if pad.kind not in ('bidir', 'tristate_out'):
                continue
            legs = self._bidir_legs(pad)
            if legs['o_assign']:
                assigns.append(legs['o_assign'])
            if legs['t_assign']:
                assigns.append(legs['t_assign'])
        return assigns

    def internal_wires(self) -> List[Dict[str, Any]]:
        """Internal wire declarations the wrapper body needs.

        For each non-excluded bidir/tristate pad, the inner out_net (when it is
        NOT bound directly to the _o port) and the inner oe_net bind to internal
        wires that the tristate_assigns read.  Feature taps (cpu_*_sysresetreq)
        and soc_wire status exports (sys_hclk, sys_hresetn) are also declared.
        """
        excluded = self._excluded()
        wires: List[Dict[str, Any]] = []
        seen: set = set()

        def _w(name, width):
            if name and name not in seen:
                seen.add(name)
                wires.append({'name': name, 'width': int(width),
                              'width_str': _wstr(width)})

        for pad in self.boundary.pads:
            if pad.chip_port in excluded:
                continue
            if pad.kind in ('bidir', 'tristate_out'):
                legs = self._bidir_legs(pad)
                _w(legs['out_wire'], pad.width)
                _w(legs['oe_wire'], pad.width)
        # feature-tapped inner outputs (e.g. cpu_*_sysresetreq) become wires
        for port in self._feature_taps():
            _w(port, 1)
        # soc_wire status exports (sys_hclk, sys_hresetn) become internal wires
        for tap in (self.profile.debug_taps or []):
            if tap.get('kind') == 'soc_wire' and tap.get('from'):
                _w(tap['from'], 1)
        return wires

    def instance_connections(self) -> List[Dict[str, Any]]:
        """Inner-SoC instance connection list, FPGA-classified per inner port.

        Each entry: {port, expr, kind, comment}.  Precedence (highest first):
          feature   -> a feature block drives this inner input (e.g. self-loop)
          tap       -> a soc_out debug tap binds this inner output to a port
          bonded    -> a (non-excluded) boundary pad -> renamed wrapper net
          excluded  -> an excluded pad's inner port -> tie (input) / open (out)
          tied      -> boundary tie constant (unless overridden by a feature)
          open      -> boundary open (unless overridden by a tap)
        """
        excluded = self._excluded()
        feat = self._feature_drives()
        taps = self._tap_sources()
        conns: List[Dict[str, Any]] = []
        seen: set = set()

        def _add(port, expr, kind, comment=''):
            if port in seen:
                return
            seen.add(port)
            conns.append({'port': port, 'expr': expr, 'kind': kind,
                          'comment': comment})

        # --- bonded pads (skip excluded; their inner ports handled below) ---
        for pad in self.boundary.pads:
            if pad.chip_port in excluded:
                continue
            if pad.kind == 'input':
                _add(pad.in_net, self._rename(pad.in_net), 'bonded')
            elif pad.kind == 'output':
                # if an output is also a tap source, prefer the tap binding
                if pad.out_net in taps:
                    _add(pad.out_net, taps[pad.out_net]['name'], 'tap',
                         f"debug tap {taps[pad.out_net]['name']}")
                else:
                    _add(pad.out_net, self._rename(pad.out_net), 'bonded')
            elif pad.kind == 'tristate_out':
                legs = self._bidir_legs(pad)
                # core out: direct to _o port, or to an internal wire
                _add(pad.out_net,
                     legs['port_o'] if legs['out_direct'] else pad.out_net,
                     'bonded')
                if pad.oe_net:
                    _add(pad.oe_net, pad.oe_net, 'bonded')  # oe -> internal wire
            elif pad.kind == 'bidir':
                legs = self._bidir_legs(pad)
                # pad -> core: the inner in always binds to the _i input port
                _add(pad.in_net, legs['port_i'], 'bonded')
                # core -> pad: direct to _o port, or to an internal wire
                _add(pad.out_net,
                     legs['port_o'] if legs['out_direct'] else pad.out_net,
                     'bonded')
                if pad.oe_net:
                    _add(pad.oe_net, pad.oe_net, 'bonded')  # oe -> internal wire

        # --- excluded pad inner ports: tie inputs, open outputs ----------
        ex_ties = self.profile.exclude_ties or {}

        def _tie_in(net):
            """Drive an excluded inner input: feature > exclude_ties > 1'b0."""
            if net in feat:
                _add(net, _feature_expr(feat[net]), 'feature',
                     f"feature {feat[net]['feature']}")
            else:
                _add(net, ex_ties.get(net, "1'b0"), 'tied',
                     'excluded pad -> tied')

        for pad in self.boundary.pads:
            if pad.chip_port not in excluded:
                continue
            if pad.kind == 'input':
                _tie_in(pad.in_net)
            elif pad.kind == 'output':
                _add(pad.out_net, '', 'open', 'excluded pad -> open')
            elif pad.kind in ('bidir', 'tristate_out'):
                # leave the bidir/tristate inner ports open/tied
                if pad.in_net:
                    _tie_in(pad.in_net)
                if pad.out_net:
                    _add(pad.out_net, '', 'open', 'excluded pad -> open')
                if pad.oe_net:
                    _add(pad.oe_net, '', 'open', 'excluded pad -> open')

        # --- ties (feature override wins) --------------------------------
        for tie in self.boundary.ties:
            port = tie['soc_port']
            if port in feat:
                _add(port, _feature_expr(feat[port]), 'feature',
                     f"feature {feat[port]['feature']}")
            else:
                _add(port, tie['const'], 'tied')

        # --- opens (tap / feature override wins) -------------------------
        wire_taps = self._tap_wire_sources()
        for name in self.boundary.opens:
            if name in taps:
                # soc_out tap: bind inner output directly to the tap port
                _add(name, taps[name]['name'], 'tap',
                     f"debug tap {taps[name]['name']}")
            elif name in wire_taps:
                # soc_wire tap: bind inner output to an internal wire; an
                # `assign <port> = <wire>;` re-exports it.
                _add(name, name, 'tap',
                     f"status export -> {wire_taps[name]}")
            elif name in self._feature_taps():
                # tapped by a feature (e.g. SYSRESETREQ self-loop) -> wire it out
                _add(name, name, 'feature_tap',
                     f"feature tap {self._feature_taps()[name]}")
            else:
                _add(name, '', 'open')

        # --- terminators: inner ports wired to a helper responder instance ---
        for port in self._terminated_inner_ports():
            _add(port, port, 'terminated', 'terminator wire')

        return conns

    def _tap_wire_sources(self) -> Dict[str, str]:
        """Map of inner-output-port -> tap port for soc_wire taps."""
        out: Dict[str, str] = {}
        for tap in (self.profile.debug_taps or []):
            if tap.get('kind') == 'soc_wire' and tap.get('from'):
                out[tap['from']] = tap['name']
        return out

    def _feature_taps(self) -> Dict[str, str]:
        """Map of inner-output-port -> feature name that taps it (reads it)."""
        out: Dict[str, str] = {}
        for fname, fdef in (self.profile.features or {}).items():
            if not isinstance(fdef, dict) or not fdef.get('enabled', False):
                continue
            for t in fdef.get('taps', []) or []:
                out[t] = fname
        return out

    # ------------------------------------------------------------------
    # reporting
    # ------------------------------------------------------------------
    def residuals(self) -> List[str]:
        """Bespoke (non-model-derivable) items applied — the honest report.

        Each line is a deviation from a pure boundary-spec emission that came
        from the FpgaProfile (board glue) rather than the model.
        """
        r: List[str] = []
        if self.profile.param_overrides:
            r.append(
                "param_overrides: " + ", ".join(
                    f"{p['name']}={p['default']}"
                    for p in self.profile.param_overrides))
        if self.profile.port_renames:
            r.append(
                "port_renames: " + ", ".join(
                    f"{k}->{v}" for k, v in self.profile.port_renames.items()))
        if self.profile.exclude_pads:
            r.append("exclude_pads: " + ", ".join(self.profile.exclude_pads))
        if self.profile.debug_taps:
            r.append(
                "debug_taps: " + ", ".join(
                    f"{t['name']}<-{t['from']}" for t in self.profile.debug_taps))
        for fname, fdef in (self.profile.features or {}).items():
            if isinstance(fdef, dict) and fdef.get('enabled', False):
                r.append(f"feature: {fname}")
        return r

    def ip_xact_physical_names(self) -> List[str]:
        """The curated IP-XACT physical_name set the wrapper must expose."""
        return list(self.profile.ip_xact.get('physical_names', []))

    # ------------------------------------------------------------------
    # render
    # ------------------------------------------------------------------
    def generate(self, rtl_dir: Path,
                 flist_dir: Path = None) -> Optional[Path]:
        """Render <module_name>.v + an inline flist.  None when jinja2 absent."""
        if Environment is None:
            print("  WARNING: jinja2 not available — skipping fpga-wrapper "
                  "generation")
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
        template = env.get_template('fpga_vivado_wrapper.v.j2')
        content = template.render(**self._build_context())

        v_name = f'{self.module_name}.v'
        v_path = rtl_dir / v_name
        write_if_changed(v_path, content)

        if flist_dir is None:
            flist_dir = rtl_dir.parent / 'flist'
        flist_dir = Path(flist_dir)
        flist_dir.mkdir(parents=True, exist_ok=True)
        flist_content = (
            f"// Auto-generated file list for FPGA wrapper {self.module_name}\n"
            f"$SOCLABS_NANOSOC_SOC_DIR/build_soc/rtl/{v_name}\n"
        )
        flist_path = flist_dir / f'{self.module_name}.flist'
        write_if_changed(flist_path, flist_content)

        return v_path

    def _build_context(self) -> Dict[str, Any]:
        now = datetime.datetime.now()
        ports = self.wrapper_ports()
        conns = self.instance_connections()
        feat = self.profile.features or {}
        selfloop = feat.get('sysresetreq_selfloop')
        selfloop_on = bool(isinstance(selfloop, dict)
                           and selfloop.get('enabled', False))
        # Round-8 HW bug #1: with `isolate: true` the feature does NOT loop a
        # core's SYSRESETREQ back into the global sys_sysresetreq (the boundary
        # tie sys_sysresetreq=1'b0 wins). Per-core self-reset is handled inside
        # the SoC by nanosoc_reset_ctrl. The old self-loop block is suppressed;
        # the tapped cpu_*_sysresetreq outputs are still observed via a harmless
        # unused sink so they do not dangle.
        isolate = bool(selfloop_on and selfloop.get('isolate', False))
        sysresetreq_isolate = None
        if isolate:
            sysresetreq_isolate = {'taps': list(selfloop.get('taps', []) or [])}
        return {
            'module_name': self.module_name,
            'inner_module': self.inner_module,
            'param_overrides': self.profile.param_overrides,
            'ports': ports,
            'connections': conns,
            'terminators': self.terminator_context(),
            'internal_wires': self.internal_wires(),
            'tristate_assigns': self.tristate_assigns(),
            'tap_assigns': self._tap_assigns(),
            'debug_taps': self.profile.debug_taps,
            'features': feat,
            'selfloop': selfloop if (selfloop_on and not isolate) else None,
            'sysresetreq_isolate': sysresetreq_isolate,
            'residuals': self.residuals(),
            'ip_xact_physical_names': self.ip_xact_physical_names(),
            'generated_date': now.strftime('%Y-%m-%d %H:%M:%S'),
            'generated_year': now.strftime('%Y'),
        }


# ---------------------------------------------------------------------------
# module-level helpers (kept out of the class for template-free reuse)
# ---------------------------------------------------------------------------
def _feature_expr(fdef: Dict[str, Any]) -> str:
    """The RHS a feature block drives onto its inner-input port."""
    fname = fdef.get('feature', '')
    if fname == 'sysresetreq_selfloop':
        return 'sysresetreq_filt'
    if 'source' in fdef:
        return fdef['source']
    return "1'b0"
