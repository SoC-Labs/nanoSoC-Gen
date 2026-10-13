"""Top-level SV module backend — generates the structural top-level SV from the SoC model.

Produces a purely structural SystemVerilog top-level module where:
  - All combinational glue logic is expressed as helper module instantiations
  - AHB/AXIS/APB bus bundles are auto-expanded from protocol definitions
  - Every wire has provenance comments (driver/consumer)

Generated files:
  - <module_name>.sv — top-level structural module
"""

import datetime
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..adapters.base import resolve_boundary_port
from ..adapters.registry import protocol_for
from ..model import (
    Connection, GlueLogicEntry, Instance, Interconnect,
    InterconnectInitiator, InterconnectTarget, Interface, Module,
)
from ..utils import parse_bit_slice, parse_conn_ref, resolve_param_ref, flatten_params, write_if_changed
from .protocol_utils import (
    AHB_INITIATOR_SIGNALS, AHB_TARGET_SIGNALS,
    AXIS_SIGNALS, AXIS_BYTE_SIGNALS,
    SWD_SIGNALS, GPIO_SIGNALS, DBGAHB_TARGET_SIGNALS,
    passthrough_initiator_uses_initiator_boundary,
    width_str as _width_str,
    param_width_str as _param_width_str,
)

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    Environment = None
    FileSystemLoader = None

_BACKEND_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _BACKEND_DIR / 'templates'


# ---------------------------------------------------------------------------
# mux2 glue: the select is a BUILD-TIME constant
# ---------------------------------------------------------------------------
# soc_glue_mux2 picks its leg with `parameter SEL` and a generate: there is no
# select port, so the select must be something elaboration can evaluate. Two
# forms are accepted, and nothing else:
#   * a literal whose value is 0 or 1: 0, 1, 1'b0, 1'b1, 'h1, 32'd0, '0, '1
#     (rendered as written);
#   * a reference to a parameter the module declares: $NAME (or bare NAME),
#     rendered as NAME[0] -- the old 1-bit `sel` port kept bit 0 of a 32-bit
#     parameter, and so does this, so the netlist does not change.
# A literal of any other value is refused (the port kept bit 0 of it; SEL != 0
# would not), and so is anything else: a net, a port, an expression, x/z.
_MUX2_LITERAL_RE = re.compile(
    r"(?:[0-9][0-9_]*)?'[sS]?(?:[bB][01_]+|[oO][0-7_]+|[dD][0-9_]+|[hH][0-9a-fA-F_]+)"
    r"|[0-9][0-9_]*|'[01]")
_MUX2_IDENT_RE = re.compile(r'\$?([A-Za-z_][A-Za-z0-9_]*)')
MUX2_RUNTIME_ADVICE = (
    "soc_glue_mux2 selects at BUILD time (parameter SEL, a generate): its "
    "select must be a literal 0/1 (0, 1, 1'b0, 1'b1) or a parameter of the "
    "module ($NAME). A select that changes at run time needs a separate "
    "runtime-mux glue type; none exists yet, so add one (do not put a port "
    "back on soc_glue_mux2) if a design needs it.")


class Mux2SelectError(ValueError):
    """A mux2 glue entry whose select is not a build-time constant."""


def _literal_value(text: str) -> Optional[int]:
    """The value of a Verilog integer literal, or None if it is not one."""
    if not _MUX2_LITERAL_RE.fullmatch(text):
        return None
    t = text.replace('_', '')
    if "'" not in t:
        return int(t, 10)
    digits = t.split("'", 1)[1].lstrip('sS')
    if digits in ('0', '1'):                   # '0 / '1 (fill literals)
        return int(digits)
    base = {'b': 2, 'o': 8, 'd': 10, 'h': 16}[digits[0].lower()]
    return int(digits[1:], base)


def mux2_select(gl: GlueLogicEntry, module: Module) -> str:
    """The SEL value a mux2 glue entry renders, or Mux2SelectError when its
    select is not a build-time constant (see the block comment above)."""
    sel = gl.select
    where = f"{module.name}: glue '{gl.name}' (mux2)"
    if sel is None:
        return "1'b0"          # no select: in0, as soc_glue_mux2 always did
    text = str(sel).strip()
    value = _literal_value(text)
    if value is not None:
        if value not in (0, 1):
            raise Mux2SelectError(
                f"{where}: select {text!r} is the literal {value}; a mux2 select "
                f"literal must be 0 or 1 (the old 1-bit select port kept only "
                f"bit 0 of it, SEL != 0 would not). {MUX2_RUNTIME_ADVICE}")
        return text
    m = _MUX2_IDENT_RE.fullmatch(text)
    if m and m.group(1) in module.params:
        return f"{m.group(1)}[0]"
    if m and text.startswith('$'):
        raise Mux2SelectError(
            f"{where}: select {text!r} names a parameter that {module.name} "
            f"does not declare ({', '.join(sorted(module.params)) or 'no params'}). "
            f"{MUX2_RUNTIME_ADVICE}")
    raise Mux2SelectError(
        f"{where}: select {text!r} is not a build-time constant (a net, a port "
        f"or an expression is a RUNTIME select). {MUX2_RUNTIME_ADVICE}")


def _rendered_modules(top: Module) -> List[Module]:
    """The modules whose glue the generator renders: the top, and every
    gen: True module with interconnects or instances below it (as
    SoCSubsystemBackend.generate_all walks them)."""
    out, seen = [top], {id(top)}

    def walk(mod: Module):
        for inst in mod.instances:
            sub = inst.resolved_module
            if sub is None:
                continue
            if sub.gen and (sub.interconnects or sub.instances) and id(sub) not in seen:
                seen.add(id(sub))
                out.append(sub)
            walk(sub)

    walk(top)
    return out


def _param_values(module: Module, overrides: Dict[str, Any]) -> Dict[str, Any]:
    """A module's parameter values as one instance elaborates them: the
    instance's overrides, then each default resolved in the module's scope."""
    params = dict(overrides)
    for name, p in module.params.items():
        if name in overrides:
            continue
        v = p.default
        for _ in range(16):                    # "$A" -> "$B" chains
            nxt = resolve_param_ref(v, params)
            if nxt == v:
                break
            v = nxt
        params[name] = v
    return params


def _mux2_param(gl: GlueLogicEntry, module: Module) -> Optional[str]:
    """The parameter a mux2 select names, or None for a literal."""
    m = _MUX2_IDENT_RE.fullmatch(str(gl.select).strip()) if gl.select is not None else None
    return m.group(1) if m and m.group(1) in module.params else None


def check_mux2_selects(top: Module, config_overrides: Optional[Dict[str, Any]] = None) -> List[str]:
    """Every rendered mux2 glue whose select is not a build-time constant OR
    resolves to a value other than 0 or 1, one message each (empty when all
    are 0/1 constants). __main__ runs it before any backend writes.

    A parameter select is checked at its module's own default (the RTL's) and
    at every instance of the module in this design, with the values the
    instance elaborates (its parent's overrides, and --config-override at the
    top): CLK_RST_CONSUMER is `type: int` with no range, so a 2 would have been
    narrowed to bit 0 (PRMU master) in silence."""
    errors: List[str] = []
    rendered = {id(m) for m in _rendered_modules(top)}
    seen_default = set()
    top_over = {k: v for k, v in (config_overrides or {}).items() if k in top.params}

    def check_module(mod: Module, params: Dict[str, Any], path: str):
        if id(mod) not in rendered:
            return
        defaults = None
        for gl in mod.glue_logic:
            if gl.type != 'mux2':
                continue
            try:
                mux2_select(gl, mod)
            except Mux2SelectError as e:
                if (id(mod), gl.name, 'form') not in seen_default:
                    seen_default.add((id(mod), gl.name, 'form'))
                    errors.append(str(e))
                continue
            name = _mux2_param(gl, mod)
            if name is None:
                continue
            if defaults is None:
                defaults = _param_values(mod, {})
            for where, scope in ((f"{mod.name} (its default)", defaults), (path, params)):
                key = (id(mod), gl.name, where)
                if key in seen_default:
                    continue
                seen_default.add(key)
                v = scope.get(name)
                if not (isinstance(v, int) and not isinstance(v, bool) and v in (0, 1)):
                    errors.append(
                        f"{mod.name}: glue '{gl.name}' (mux2): select ${name} is {v!r} at {where}; "
                        f"a build-time select must be 0 or 1 (SEL takes {name}[0], so any other "
                        f"value would pick a leg in silence). {MUX2_RUNTIME_ADVICE}")

    def walk(mod: Module, params: Dict[str, Any], path: str):
        check_module(mod, params, path)
        for inst in mod.instances:
            sub = inst.resolved_module
            if sub is None:
                continue
            over = {}
            for k, v in (inst.params or {}).items():
                for _ in range(16):
                    nxt = resolve_param_ref(v, params)
                    if nxt == v:
                        break
                    v = nxt
                over[k] = v
            walk(sub, _param_values(sub, over), f"{path}/{inst.instance_name}")

    walk(top, _param_values(top, top_over), top.name)
    return errors


class SoCTopLevelBackend:
    """Generates the top-level structural SV module from the SoC model."""

    def __init__(self, top_module: Module):
        self.top = top_module
        self.flat_params = flatten_params({n: p.default for n, p in top_module.params.items()})
        # ref base ('u_a.X') -> canonical flat wire for duplicate-link cycles
        # discovered by _resolve_conn_to_wire (a link declared from BOTH
        # endpoints must resolve to ONE net regardless of entry point).
        self._xref_cycle_canon: Dict[str, str] = {}

    def generate(self, rtl_dir: Path, flist_dir: Path = None) -> Optional[Path]:
        """Generate nanosoc.sv in rtl_dir and flist in flist_dir.

        Returns the path to the generated SV file.
        """
        if Environment is None:
            print("  WARNING: jinja2 not available — skipping top-level generation")
            return None

        rtl_dir = Path(rtl_dir)
        rtl_dir.mkdir(parents=True, exist_ok=True)

        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        template = env.get_template('soc_toplevel.sv.j2')

        ctx = self._build_context()
        content = template.render(**ctx)

        out_path = rtl_dir / f'{self.top.name}.sv'
        write_if_changed(out_path, content)

        # Also generate the flist
        if flist_dir is None:
            flist_dir = rtl_dir.parent / 'flist'
        flist_dir = Path(flist_dir)
        flist_dir.mkdir(parents=True, exist_ok=True)
        flist_template = env.get_template('soc_toplevel.flist.j2')
        flist_content = flist_template.render(**ctx)
        flist_path = flist_dir / f'{self.top.name}_toplevel.flist'
        write_if_changed(flist_path, flist_content)

        return out_path

    def _build_context(self) -> Dict[str, Any]:
        """Build the full template context."""
        # Build xref wires first — this also populates self._output_port_to_xref
        # which maps top-level output port names to intermediate xref wire names.
        # This mapping is used by _build_instances to rewire source instance
        # connections that would otherwise drive the output port directly.
        self._output_port_to_xref = {}
        # inst_name -> [(port, flat_wire), ...]: connections to inject on a
        # referenced instance whose port is tapped by a cross-instance xref
        # but not otherwise connected. For a referenced OUTPUT the injection
        # DRIVES the flat xref wire (else it is undriven); for a referenced
        # INPUT it CONSUMES the wire (else the input floats — the one-way
        # auto-bind defect that left USRT PREADY / FT1248 control inputs
        # dangling downstream).
        self._xref_port_injections = {}
        # [(lhs, rhs, desc), ...]: continuous assigns driving top-level OUTPUT
        # ports whose source-instance connection was rewired onto an xref wire
        # by _build_instances (else the boundary port is declared but undriven
        # — sim reads Z, FPGA reads 0). Populated by _build_instances.
        self._xref_output_assigns = []
        xref_wires = self._build_xref_wires()
        # _build_instances must run before the context dict is finalised so
        # that _xref_output_assigns is fully populated.
        instances = self._build_instances()

        child_link_gates = self._child_link_htrans_gates()
        taken = ({p['name'] for p in self._build_ports()}
                 | {w['name'] for w in xref_wires}
                 | {w['name'] for w in self._build_internal_wires()})
        for g in child_link_gates:
            if g['wire'] in taken:
                raise ValueError(
                    f"{self.top.name}: the hsel-gated HTRANS wire {g['wire']} "
                    f"for {g['inst']}.{g['port']} collides with an existing "
                    f"port or wire of the same name")

        return {
            'module_name': self.top.name,
            'generated_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'params': self._build_params(),
            'ports': self._build_ports(),
            'internal_wires': self._build_internal_wires(),
            'bus_wires': self._build_bus_wires(),
            'xref_wires': xref_wires,
            'glue_instances': self._build_glue_instances(),
            'instances': instances,
            'interconnect': self._build_interconnect(),
            'passthrough_tieoffs': self._build_passthrough_tieoffs(),
            'xref_output_assigns': self._xref_output_assigns,
            'child_link_gates': child_link_gates,
        }

    def _build_params(self) -> List[Dict[str, Any]]:
        """Build parameter list for module declaration."""
        params = []
        for name, p in self.top.params.items():
            default = p.default
            # Format large integers as sized hex to avoid signed overflow warnings
            if p.type == 'int' and isinstance(default, int) and default >= 2**31:
                default = f"32'h{default:08X}"
            params.append({
                'name': name,
                'type': p.type,
                'default': default,
                'desc': p.desc,
            })
        return params

    def _build_ports(self) -> List[Dict[str, Any]]:
        """Build port list, expanding protocol interfaces into individual signals."""
        ports = []
        for iface in self.top.interfaces:
            if iface.type == 'wire':
                width = iface.params.get('WIDTH', 1)
                direction = 'input' if iface.direction == 'in' else 'output'
                ports.append({
                    'name': iface.name,
                    'direction': direction,
                    'width': width,
                    'width_str': _width_str(width),
                    'desc': iface.desc,
                })
            elif iface.type == 'swd':
                for sig_name, sig_dir, sig_width in SWD_SIGNALS:
                    direction = 'input' if sig_dir == 'in' else 'output'
                    ports.append({
                        'name': f'{iface.name[:-4]}_{sig_name}' if iface.name.endswith('_swd') else f'{iface.name}_{sig_name}',
                        'direction': direction,
                        'width': int(sig_width),
                        'width_str': '',
                        'desc': f'{iface.desc} — {sig_name}',
                    })
            elif iface.type == 'dbg_ahb':
                # Cortex-M debug-slave bus (slcorem0 EXTERNAL_DAP=1 DBGAHB_*)
                for sig_name, sig_dir, sig_width in DBGAHB_TARGET_SIGNALS:
                    direction = 'input' if sig_dir == 'in' else 'output'
                    ports.append({
                        'name': f'{iface.name}_{sig_name}',
                        'direction': direction,
                        'width': int(sig_width),
                        'width_str': _width_str(int(sig_width)),
                        'desc': f'{iface.desc} — {sig_name}',
                    })
            elif iface.type == 'gpio':
                width = iface.params.get('WIDTH', 16)
                for sig_name, sig_dir, _ in GPIO_SIGNALS:
                    direction = 'input' if sig_dir == 'in' else 'output'
                    ports.append({
                        'name': f'{iface.name}_{sig_name}',
                        'direction': direction,
                        'width': width,
                        'width_str': _width_str(width),
                        'desc': f'{iface.desc} — {sig_name}',
                    })
            elif iface.type == 'ahb':
                self._expand_ahb_port(iface, ports)
            elif iface.type in ('axis', 'axis_stream'):
                self._expand_axis_port(iface, ports, full=True)
            elif iface.type == 'axis_byte':
                self._expand_axis_port(iface, ports, full=False)
        return ports

    def _external_target_socket_ifaces(self) -> set:
        """Top-level AHB iface names that are the boundary SOCKET of an
        interconnect target with no instance, no passthrough, and no
        instance connection driving it (e.g. the `exp` expansion region).

        For such a target the generated bus matrix's target-side port group
        merges with the expanded top-level ports BY NAME
        (_build_interconnect emits `.{tgt}_{sig} ({tgt}_{sig})` and
        _build_bus_wires skips wires that collide with top ports), so the
        module hosts the INITIATOR side of the socket: the matrix DRIVES
        hsel/haddr/htrans/... (and hreadymux -> {tgt}_hready) OUT to the
        external slave and RECEIVES hrdata/hresp/hreadyout back IN. The
        boundary declaration must invert the raw target-table directions
        accordingly — emitting them un-inverted declares inputs that are
        internally driven and outputs that are internally consumed
        (simulators coerce the conflict; Vivado does not back-propagate
        through coerced ports, so the socket is unusable on FPGA).
        """
        bound_ic_ports = set()
        for inst in self.top.instances:
            for conn in inst.connections:
                if conn.is_unconnected:
                    continue
                inst_ref, port_name, _, _ = parse_conn_ref(conn.conn)
                if inst_ref and any(ic.name == inst_ref
                                    for ic in self.top.interconnects):
                    bound_ic_ports.add(port_name)
        names = set()
        for ic in self.top.interconnects:
            for tgt in ic.targets:
                if tgt.instance or tgt.passthrough:
                    continue
                if tgt.name in bound_ic_ports:
                    continue
                names.add(tgt.name)
        return names

    def _passthrough_initiator_ifaces(self) -> set:
        """Module-iface names that are the passthrough endpoint of an
        interconnect *initiator*.

        Such a port is declared `direction: target` at the module level
        (the subsystem is the addressable side) but the generated bus-
        matrix wrapper exposes it with the cmsdk *initiator* boundary
        (no hsel / no hreadyout; hrdata/hready/hresp are module outputs)
        because, to the matrix, an external master driving transactions
        IN is an initiator. The module boundary MUST match the wrapper
        or the top-level instantiation leaves hsel dangling, hreadyout
        undriven and hready direction-conflicts. Single source of truth
        shared with SoCSubsystemBackend so the two backends can't drift.
        """
        names = set()
        for ic in self.top.interconnects:
            for init in ic.initiators:
                if init.passthrough:
                    names.add(init.passthrough)
        return names

    def _expand_ahb_port(self, iface: Interface, ports: List[Dict]):
        """Expand an AHB interface into individual port signals."""
        is_target = iface.direction in ('target', 'in')
        # Only the no-hready-exclude variant takes the cmsdk *initiator*
        # boundary (no hsel/hreadyout, bare hready out). A
        # passthrough-initiator iface that excludes hready (cpu_ss)
        # wants the slave/target boundary minus hready instead — see
        # passthrough_initiator_uses_initiator_boundary.
        is_passthrough_initiator = (
            iface.name in self._passthrough_initiator_ifaces()
            and passthrough_initiator_uses_initiator_boundary(iface.params)
        ) or self._iface_drives_child_passthrough_initiator(iface)
        addr_w = iface.params.get('ADDR_WIDTH', iface.params.get('ADDR_W', 'SYS_ADDR_W'))
        data_w = iface.params.get('DATA_WIDTH', iface.params.get('DATA_W', 'SYS_DATA_W'))
        exclude = set(iface.params.get('EXCLUDE', []))

        if is_passthrough_initiator:
            # Subsystem is the receiving (slave) end of an external
            # initiator. Use AHB_INITIATOR_SIGNALS with directions
            # inverted: initiator-perspective 'out' (master drives) is a
            # module input; 'in' (master receives) is a module output.
            # AHB_INITIATOR_SIGNALS carries no hsel/hreadyout, so those
            # spurious slave-boundary signals are never emitted — the
            # boundary then matches the matrix wrapper exactly.
            signals = AHB_INITIATOR_SIGNALS
            invert = True
        elif is_target and iface.name in self._external_target_socket_ifaces():
            # External target SOCKET (instance-less, passthrough-less
            # interconnect target merged onto this iface by name — the
            # `exp` expansion region): the module hosts the INITIATOR side
            # of the socket, so the target-table directions invert —
            # hsel/haddr/htrans/.../hready(mux) are module OUTPUTS driven
            # by the bus matrix; hrdata/hresp/hreadyout are module INPUTS
            # returned by the external slave. See
            # _external_target_socket_ifaces for why un-inverted
            # directions make the socket unusable (Vivado port coercion).
            signals = AHB_TARGET_SIGNALS
            invert = True
        else:
            signals = AHB_TARGET_SIGNALS if is_target else AHB_INITIATOR_SIGNALS
            invert = False

        for sig_name, sig_dir, sig_width in signals:
            if sig_name in exclude:
                continue
            # Resolve width
            w = sig_width
            if w == 'SYS_ADDR_W':
                w = addr_w
            elif w == 'SYS_DATA_W':
                w = data_w

            # Signal directions from protocol definitions match module port
            # directions directly: 'in' signals are module inputs, 'out' are
            # outputs. This holds for both target and initiator interfaces
            # because the protocol signal tables define directions from each
            # role's perspective. For a passthrough-initiator endpoint the
            # module sits at the *opposite* end of that role, so invert.
            if invert:
                direction = 'input' if sig_dir == 'out' else 'output'
            else:
                direction = 'input' if sig_dir == 'in' else 'output'

            ports.append({
                'name': f'{iface.name}_{sig_name}',
                'direction': direction,
                'width': w,
                'width_str': _param_width_str(w),
                'desc': f'{iface.desc} — {sig_name}',
            })

    def _expand_axis_port(self, iface: Interface, ports: List[Dict], full: bool = True):
        """Expand an AXIS interface into individual port signals."""
        signals = AXIS_SIGNALS if full else AXIS_BYTE_SIGNALS
        is_output = iface.direction in ('out', 'sender', 'initiator')
        data_w = iface.params.get('DATA_WIDTH', iface.params.get('DATA_W', 'SYS_DATA_W'))
        has_flush = iface.params.get('HAS_FLUSH', 0)

        for sig_name, sig_dir, sig_width in signals:
            w = sig_width
            if w == 'SYS_DATA_W':
                w = data_w

            if is_output:
                direction = 'output' if sig_dir == 'out' else 'input'
            else:
                direction = 'input' if sig_dir == 'out' else 'output'

            ports.append({
                'name': f'{iface.name}_{sig_name}',
                'direction': direction,
                'width': w,
                'width_str': _param_width_str(w),
                'desc': f'{iface.desc} — {sig_name}',
            })

        # Add flush signal for input AXIS streams with HAS_FLUSH
        if full and has_flush and not is_output:
            ports.append({
                'name': f'{iface.name}_flush',
                'direction': 'output',
                'width': 1,
                'width_str': '',
                'desc': f'{iface.desc} — flush',
            })

    def _build_internal_wires(self) -> List[Dict[str, Any]]:
        """Build internal wire declarations from the model."""
        # Collect top-level port names to avoid duplicate declarations
        port_names = {iface.name for iface in self.top.interfaces}
        wires = []
        for w in self.top.internal_wires:
            if w.name in port_names:
                continue  # already declared as a top-level port
            width = w.params.get('WIDTH', 1)
            # Resolve parameterised widths
            if isinstance(width, str) and width.startswith('$'):
                width = resolve_param_ref(width, self.flat_params)
            wires.append({
                'name': w.name,
                'width': width,
                'width_str': _param_width_str(width),
                'desc': w.desc,
            })
        return wires

    def _build_bus_wires(self) -> List[Dict[str, Any]]:
        """Build AHB/AXIS bus wire bundles needed for internal instance connections."""
        bus_wires = []
        seen = set()

        # Collect top-level port names (expanded from protocol interfaces) to
        # avoid re-declaring wires that are already module ports.
        top_port_names = set()
        for iface in self.top.interfaces:
            if iface.type == 'ahb':
                is_target = iface.direction in ('target', 'in')
                signals = AHB_TARGET_SIGNALS if is_target else AHB_INITIATOR_SIGNALS
                for sig, _, _ in signals:
                    top_port_names.add(f'{iface.name}_{sig}')
            elif iface.type == 'wire':
                top_port_names.add(iface.name)

        # Walk all instance connections to find bus references that need wires
        for inst in self.top.instances:
            if inst.resolved_module is None:
                continue
            for conn in inst.connections:
                if conn.is_unconnected:
                    continue
                inst_ref, port_name, high, low = parse_conn_ref(conn.conn)
                # If it references an interconnect port, we need the wire bundle
                if inst_ref and any(ic.name == inst_ref for ic in self.top.interconnects):
                    ic = next(ic for ic in self.top.interconnects if ic.name == inst_ref)
                    # Determine if it's an initiator or target port
                    wire_prefix = port_name
                    if wire_prefix not in seen:
                        seen.add(wire_prefix)
                        # Check if it's an initiator port
                        is_init = any(init.name == port_name for init in ic.initiators)
                        if is_init:
                            for sig, sig_dir, sig_w in AHB_INITIATOR_SIGNALS:
                                wire_name = f'{wire_prefix}_{sig}'
                                if wire_name not in top_port_names:
                                    bus_wires.append({
                                        'name': wire_name,
                                        'width_str': _param_width_str(sig_w),
                                        'desc': f'{wire_prefix} AHB initiator — {sig}',
                                    })
                        else:
                            # Target port
                            for sig, sig_dir, sig_w in AHB_TARGET_SIGNALS:
                                wire_name = f'{wire_prefix}_{sig}'
                                if wire_name not in top_port_names:
                                    bus_wires.append({
                                        'name': wire_name,
                                        'width_str': _param_width_str(sig_w),
                                        'desc': f'{wire_prefix} AHB target — {sig}',
                                    })
        # Also generate wires for passthrough targets/initiators in
        # interconnects.  Passthrough ports are not referenced by any instance
        # connection, so the loop above misses them.  All signals use the
        # target's own name as prefix (e.g. system_haddr, system_hready) to
        # avoid multi-driver conflicts with top-level ports or initiator
        # feedback signals that share the passthrough name (e.g. cpu_0_haddr).
        for ic in self.top.interconnects:
            for tgt in ic.targets:
                if not tgt.passthrough:
                    continue
                if tgt.name not in seen:
                    seen.add(tgt.name)
                    for sig, sig_dir, sig_w in AHB_TARGET_SIGNALS:
                        wire_name = f'{tgt.name}_{sig}'
                        if wire_name not in top_port_names:
                            bus_wires.append({
                                'name': wire_name,
                                'width_str': _param_width_str(sig_w),
                                'desc': f'{tgt.name} AHB passthrough target — {sig}',
                            })
            for init in ic.initiators:
                if not init.passthrough:
                    continue
                wire_prefix = init.passthrough  # e.g. 'eth_ss_0'
                if init.name not in seen:
                    seen.add(init.name)
                    for sig, sig_dir, sig_w in AHB_INITIATOR_SIGNALS:
                        wire_name = f'{wire_prefix}_{sig}'
                        if wire_name not in top_port_names:
                            bus_wires.append({
                                'name': wire_name,
                                'width_str': _param_width_str(sig_w),
                                'desc': f'{init.name} AHB passthrough initiator — {sig}',
                            })

        # Generate internal wires for initiators with passthrough target
        # collisions (e.g. cpu_0_ic_haddr, cpu_0_ic_hrdata).  ALL signals
        # need internal wires to avoid multi-driver conflicts with the
        # passthrough target assigns that drive the top-level ports.
        for ic in self.top.interconnects:
            pt_target_prefixes = {
                tgt.passthrough for tgt in ic.targets if tgt.passthrough
            }
            for init in ic.initiators:
                wire_prefix = init.passthrough if init.passthrough else init.name
                if wire_prefix in pt_target_prefixes:
                    for sig, sig_dir, sig_w in AHB_INITIATOR_SIGNALS:
                        wire_name = f'{wire_prefix}_ic_{sig}'
                        if wire_name not in top_port_names and wire_name not in seen:
                            seen.add(wire_name)
                            bus_wires.append({
                                'name': wire_name,
                                'width_str': _param_width_str(sig_w),
                                'desc': f'{init.name} bus matrix internal — {sig}',
                            })

        # HSEL-qualified HTRANS for a target-boundary passthrough initiator of
        # this module (driven in _build_passthrough_tieoffs, read by the
        # interconnect's initiator htrans port in _build_interconnect).
        for init_name in self._hsel_gated_passthrough_initiators():
            wire_name = f'{init_name}_htrans_gated'
            if wire_name not in top_port_names and wire_name not in seen:
                seen.add(wire_name)
                bus_wires.append({
                    'name': wire_name,
                    'width_str': _param_width_str(2),
                    'desc': f'{init_name} passthrough initiator — htrans qualified by hsel',
                })

        bus_wires.extend(self._child_link_htrans_gate_wires())
        return bus_wires

    def _child_link_htrans_gate_wires(self) -> List[Dict[str, Any]]:
        """Declarations for the hsel-qualified HTRANS wires driven by
        _child_link_htrans_gates (shared by the subsystem backend)."""
        return [{
            'name': g['wire'],
            'width_str': _param_width_str(2),
            'desc': f"{g['tgt']} -> {g['inst']}.{g['port']} — htrans qualified by hsel",
        } for g in self._child_link_htrans_gates()]

    def _build_xref_wires(self) -> List[Dict[str, Any]]:
        """Build wire declarations for cross-instance references.

        Scans all instance connections and glue logic for hierarchical
        references like 'u_cpu_0.SYS_HCLK' and declares flat wires
        (e.g. 'u_cpu_0_SYS_HCLK') so the generated RTL is synthesisable.
        """
        xref_wires = []
        seen = set()

        # Collect top-level port names to avoid duplicates
        top_port_names = set()
        for iface in self.top.interfaces:
            if iface.type == 'wire':
                top_port_names.add(iface.name)

        def _add_xref(ref_str):
            """Extract and register a cross-instance wire from a reference string.

            Only declares a wire if the reference cannot be resolved to an
            existing wire or port via the source instance's connections.
            """
            base, high, low = parse_bit_slice(ref_str)
            if '.' not in base:
                return

            flat_name = base.replace('.', '_')

            # Try resolving — if it resolves to an existing signal (not a
            # flat xref wire), no new wire declaration is needed
            resolved = self._resolve_conn_to_wire(ref_str)
            resolved_base, _, _ = parse_bit_slice(resolved)
            if '.' not in resolved_base and resolved_base != flat_name:
                return  # resolved to a real wire/port name, no declaration needed
            if flat_name in seen or flat_name in top_port_names:
                return
            seen.add(flat_name)

            # Resolve the referenced name against the instance's boundary:
            # an exact interface match, or an expanded bus/stream MEMBER
            # port (axis_byte tvalid/tdata/..., apb prdata/pready/..., ahb
            # members, ...). Members were previously invisible here, which
            # is what severed apb/axis sub-signal references downstream.
            inst_name, port_name = base.split('.', 1)
            inst_obj = self.top.get_instance(inst_name)
            port_iface = member = None
            if inst_obj is not None:
                hit = self._resolve_instance_port(inst_obj, port_name)
                if hit is not None:
                    port_iface, member = hit

            # Infer width: wire interfaces from their WIDTH param; members
            # from their protocol-table row resolved against the iface's
            # params. Member references previously fell through to the
            # 1-bit default (the collapsed axis_byte TDATA defect).
            width = 1
            if port_iface is not None:
                if member is None:
                    w = port_iface.params.get('WIDTH', 1)
                else:
                    w = self._member_width(port_iface, member)
                if isinstance(w, str):
                    w = resolve_param_ref(w, self.flat_params)
                if isinstance(w, int):
                    width = w

            xref_wires.append({
                'name': flat_name,
                'width': width,
                'width_str': _param_width_str(width),
                'desc': f'cross-instance wire: {ref_str}',
            })

            # Record mapping: if the source instance connects this port to a
            # top-level output, the source instance must drive the xref wire
            # instead. The glue logic passthrough will copy xref -> output port.
            if inst_obj is not None:
                connected = False
                for c in inst_obj.connections:
                    if c.is_unconnected:
                        continue
                    c_base, _, _ = parse_bit_slice(c.port)
                    if c_base != port_name:
                        continue
                    # Any connection on the port (including a bit-sliced
                    # one) counts as bound — do not inject a second,
                    # full-width connection on top of it.
                    connected = True
                    if c.port == port_name:
                        conn_base, _, _ = parse_bit_slice(c.conn)
                        top_iface = self._find_top_interface(conn_base)
                        if top_iface and top_iface.is_output:
                            self._output_port_to_xref[conn_base] = flat_name
                    break
                if not connected and port_iface is not None and not (
                        member is None and protocol_for(port_iface)):
                    # The xref taps a port the instance does not otherwise
                    # connect. Bind that endpoint so the flat wire is not
                    # left half-connected:
                    #   * referenced OUTPUT: the instance must DRIVE the
                    #     wire (else consumers read X/0),
                    #   * referenced INPUT: the instance must CONSUME the
                    #     wire (else the input floats — previously only
                    #     outputs were bound: the one-way auto-bind defect).
                    # Whole-bundle references (a protocol-typed iface with
                    # no member suffix) are not injectable — `.IFACE(wire)`
                    # is not a real RTL port; the validator reports those.
                    self._xref_port_injections.setdefault(
                        inst_name, []).append((port_name, flat_name))

        # Scan instance connections
        for inst in self.top.instances:
            for conn in inst.connections:
                if conn.is_unconnected:
                    continue
                ref = conn.conn
                base, _, _ = parse_bit_slice(ref)
                if '.' in base:
                    # Check it's not an interconnect reference (those are handled by bus_wires)
                    inst_ref = base.split('.')[0]
                    if not any(ic.name == inst_ref for ic in self.top.interconnects):
                        _add_xref(ref)

        # Scan glue logic inputs/outputs
        for gl in self.top.glue_logic:
            if gl.input:
                _add_xref(gl.input)
            for inp in gl.inputs:
                _add_xref(inp)

        return xref_wires

    def _build_glue_instances(self) -> List[Dict[str, Any]]:
        """Build glue logic helper module instantiations."""
        instances = []
        for gl in self.top.glue_logic:
            inst = {
                'name': gl.name,
                'type': gl.type,
                'output': gl.output,
                'desc': gl.desc,
            }
            if gl.type == 'passthrough':
                inst['input'] = self._resolve_conn_to_wire(gl.input or '')
                inst['width'] = self._infer_width(gl.input or '', gl.output)
            elif gl.type == 'or_reduce':
                inst['input'] = self._resolve_conn_to_wire(gl.input or '')
                inst['width'] = self._infer_width(gl.input or '', None)
            elif gl.type == 'or_combine':
                inst['inputs'] = [self._resolve_conn_to_wire(i) for i in gl.inputs]
                inst['n_inputs'] = len(gl.inputs)
                inst['width'] = self._infer_width(gl.inputs[0] if gl.inputs else '', gl.output)
            elif gl.type == 'and_gate':
                inst['input_a'] = self._resolve_conn_to_wire(gl.inputs[0] if len(gl.inputs) > 0 else '')
                inst['input_b'] = self._resolve_conn_to_wire(gl.inputs[1] if len(gl.inputs) > 1 else '')
                inst['width'] = self._infer_width(gl.inputs[0] if gl.inputs else '', gl.output)
            elif gl.type == 'mux2':
                # 2:1 selector — inputs = [in0 (SEL==0), in1 (SEL!=0)]. The
                # select is the BUILD-TIME parameter SEL of soc_glue_mux2: a
                # literal 0/1, or a declared parameter narrowed to bit 0.
                #
                # Why PARAM[0]: a YAML `type: int` parameter is an untyped
                # Verilog parameter, i.e. 32-bit signed. The old 1-bit `sel`
                # port kept bit 0 of it; a bit-select keeps exactly that, so
                # the change is netlist-neutral. Narrow at the USE site, not by
                # retyping the parameter: a typeless parameter re-widens to
                # whatever a parent override supplies, so `parameter
                # CLK_RST_CONSUMER = 1'b0` would be silently defeated by
                # `.CLK_RST_CONSUMER(1)`. A bit-select cannot be.
                # Anything else (a net: a RUNTIME select) is refused here, as
                # check_mux2_selects() refuses it before anything is written.
                inst['input0'] = self._resolve_conn_to_wire(gl.inputs[0] if len(gl.inputs) > 0 else '')
                inst['input1'] = self._resolve_conn_to_wire(gl.inputs[1] if len(gl.inputs) > 1 else '')
                inst['select'] = mux2_select(gl, self.top)
                inst['width'] = self._infer_width(gl.inputs[0] if gl.inputs else '', gl.output)
            elif gl.type == 'reset_sync':
                # async-assert / sync-deassert reset synchroniser.
                # inputs = [arstn (async resetn in), clk (dest clock domain)].
                inst['arstn'] = self._resolve_conn_to_wire(gl.inputs[0] if len(gl.inputs) > 0 else '')
                inst['clk'] = self._resolve_conn_to_wire(gl.inputs[1] if len(gl.inputs) > 1 else '')
                # `width` repurposed as the STAGES depth (default 3).
                inst['stages'] = gl.width or 3
            elif gl.type == 'constant':
                inst['value'] = gl.value
                inst['width'] = gl.width or 1
            elif gl.type == 'concat':
                # Resolve the output net width (int or $PARAM via _infer_width).
                width = self._infer_width(gl.output, None)
                # Map bit position -> resolved signal expression.
                bit_map = {}
                for item in gl.concat_inputs:
                    if not isinstance(item, dict):
                        continue
                    bit = item.get('bit')
                    sig = item.get('signal')
                    if bit is None or sig is None:
                        continue
                    bit_map[int(bit)] = self._resolve_conn_to_wire(str(sig))
                pad = gl.pad_zero_above
                # Build bits MSB->LSB so the SV concatenation { ... } has
                # bit0 as the rightmost (last) element.
                bits = []
                for b in range(width - 1, -1, -1):
                    if b in bit_map and (pad is None or b < pad):
                        bits.append(bit_map[b])
                    else:
                        bits.append("1'b0")
                inst['width'] = width
                inst['bits'] = bits
            instances.append(inst)
        return instances

    def _infer_width(self, signal: str, output: Optional[str] = None) -> int:
        """Infer the width of a signal from bit slices or internal_wires."""
        # Check for bit slice
        _, high, low = parse_bit_slice(signal)
        if high is not None and low is not None:
            return high - low + 1

        # Check output bit slice
        if output:
            _, high, low = parse_bit_slice(output)
            if high is not None and low is not None:
                return high - low + 1

        # Build list of candidate base names from both input and output signals
        base, _, _ = parse_bit_slice(signal)
        inst_name = None
        if '.' in base:
            inst_name, base = base.split('.', 1)
        candidates = [base]
        if output:
            out_base, _, _ = parse_bit_slice(output)
            if out_base and out_base != base:
                candidates.append(out_base)

        # Check internal_wires
        for cand in candidates:
            for w in self.top.internal_wires:
                if w.name == cand:
                    width = w.params.get('WIDTH', 1)
                    if isinstance(width, str):
                        width = resolve_param_ref(width, self.flat_params)
                    if isinstance(width, int):
                        return width

        # Check top-level interfaces (direct name match)
        for cand in candidates:
            for iface in self.top.interfaces:
                if iface.name == cand:
                    width = iface.params.get('WIDTH', 1)
                    if isinstance(width, int):
                        return width

        # Check GPIO-expanded top-level port names (e.g. p0_out from gpio iface p0)
        # Check both input base and output base since either might be the GPIO port
        for check_name in candidates:
            if not check_name:
                continue
            for iface in self.top.interfaces:
                if iface.type == 'gpio':
                    gpio_width = iface.params.get('WIDTH', 16)
                    for suffix in ('in', 'out', 'outen'):
                        if check_name == f'{iface.name}_{suffix}':
                            return gpio_width if isinstance(gpio_width, int) else 16

        # Check instance module interfaces for hierarchical references
        if inst_name:
            inst_obj = self.top.get_instance(inst_name)
            if inst_obj and inst_obj.resolved_module:
                for mod_iface in inst_obj.resolved_module.interfaces:
                    if mod_iface.name == base:
                        width = mod_iface.params.get('WIDTH', 1)
                        if isinstance(width, str):
                            width = resolve_param_ref(width, self.flat_params)
                        if isinstance(width, int):
                            return width

        return 1  # default

    def _build_instances(self) -> List[Dict[str, Any]]:
        """Build instance data for template rendering."""
        instances = []
        for inst in self.top.instances:
            module_name = inst.module_name
            inst_data = {
                'instance_name': inst.instance_name,
                'module_name': module_name,
                'params': [],
                'connections': [],
                'condition': inst.condition,
            }

            # Build parameter overrides
            for pname, pval in inst.params.items():
                # Strip $ prefix from parameter references
                val = pval
                if isinstance(val, str) and val.startswith('$'):
                    val = val[1:]
                inst_data['params'].append({
                    'name': pname,
                    'value': val,
                })

            # Build port connections
            for conn in inst.connections:
                port = conn.port
                signal = conn.conn

                # Handle intentionally unconnected ports
                if conn.is_unconnected:
                    inst_data['connections'].append({
                        'port': port,
                        'signal': None,
                        'desc': conn.desc,
                    })
                    continue

                # Resolve connection references
                inst_ref, port_name, high, low = parse_conn_ref(signal)

                if inst_ref:
                    # Check if it's an interconnect bus reference
                    is_ic = any(ic.name == inst_ref for ic in self.top.interconnects)
                    if is_ic:
                        # This is a bus connection — expand it
                        self._expand_bus_connection(inst, inst_data, port, port_name, inst_ref)
                        continue
                    # Otherwise it's an instance.port cross-reference — resolve to wire name
                    # These are resolved to intermediate wires by the wire mapping
                    signal = self._resolve_conn_to_wire(signal)
                else:
                    # Bare signal name — check if it maps to a top-level protocol interface
                    top_iface = self._find_top_interface(port_name)
                    if top_iface and top_iface.type in ('axis', 'axis_stream', 'axis_byte', 'swd'):
                        self._expand_protocol_connection(inst, inst_data, port, top_iface)
                        continue
                    # An `ahb`-typed top-level iface connected by bare
                    # name (e.g. { port: eth_ss_1, conn: eth_ss_0 }) is a
                    # bus bundle: expand into the individual AHB signals
                    # matching the child instance's boundary form so the
                    # parent instantiation lists real ports (not a single
                    # bundled `.eth_ss_1 (eth_ss_0)` that downstream
                    # tooling has to guess-expand).
                    if top_iface and top_iface.type == 'ahb':
                        self._expand_ahb_top_connection(
                            inst, inst_data, port, top_iface)
                        continue

                # If this signal is a top-level output port that has an xref
                # intermediate wire, the source instance should drive the xref
                # wire instead of the output port directly.
                if hasattr(self, '_output_port_to_xref'):
                    sig_base, _, _ = parse_bit_slice(signal)
                    if sig_base in self._output_port_to_xref:
                        original = signal
                        signal = signal.replace(sig_base, self._output_port_to_xref[sig_base])
                        # The source instance now drives the xref wire, NOT the
                        # top-level output port — emit `assign <port> = <xref>;`
                        # so the boundary output is still driven (otherwise it
                        # is declared-but-undriven: Z in sim, 0 on FPGA).
                        # Skip if explicit glue logic already drives the port.
                        self._record_xref_output_assign(original, signal)

                inst_data['connections'].append({
                    'port': port,
                    'signal': signal,
                    'desc': conn.desc,
                })

            # Inject connections for ports that a cross-instance xref taps but
            # the instance does not otherwise connect, so the flat xref wire is
            # bound on BOTH endpoints (see _xref_port_injections: outputs drive
            # the wire, inputs consume it).
            # getattr-guarded like _output_port_to_xref above: _build_instances
            # may be called directly (unit tests) without _build_context having
            # initialised the map.
            for inj_port, inj_wire in getattr(
                    self, '_xref_port_injections', {}).get(
                    inst.instance_name, []):
                if not any(c['port'] == inj_port for c in inst_data['connections']):
                    inst_data['connections'].append({
                        'port': inj_port,
                        'signal': inj_wire,
                        'desc': 'bind cross-instance xref wire',
                    })

            # Post-process: coalesce port bit-selects into valid SV connections
            inst_data['connections'] = self._coalesce_bit_select_ports(
                inst_data['connections'], inst)

            instances.append(inst_data)
        return instances

    def _record_xref_output_assign(self, lhs: str, rhs: str) -> None:
        """Record a continuous assign driving a top-level output port whose
        source-instance connection was rewired onto a cross-instance xref wire.

        Without this assign the boundary port is declared but never driven
        (Z in simulation, 0 on FPGA — the `system_htrans` defect class).
        Skipped when explicit YAML glue logic already drives the port, or when
        an identical LHS has already been recorded (dedupe).
        """
        if not hasattr(self, '_xref_output_assigns'):
            # _build_instances may be called directly (unit tests) without
            # _build_context having initialised the list.
            return
        lhs_base, _, _ = parse_bit_slice(lhs)
        for gl in self.top.glue_logic:
            gl_base, _, _ = parse_bit_slice(gl.output or '')
            if gl_base == lhs_base:
                return  # explicit glue passthrough copies xref -> output port
        if any(a['lhs'] == lhs for a in self._xref_output_assigns):
            return
        self._xref_output_assigns.append({
            'lhs': lhs,
            'rhs': rhs,
            'desc': 'boundary output driven via xref wire',
        })

    def _child_port_is_pt_initiator(self, inst: Instance, port: str,
                                    inst_iface) -> bool:
        """True iff `port` on `inst` is a passthrough-initiator endpoint of
        the child module's OWN interconnect that takes the cmsdk *initiator*
        boundary (no hsel/hreadyout, bare hready).

        Single source of truth for the `child_pt_init` decision shared by
        `_expand_ahb_top_connection`, `_expand_bus_connection` and the
        toplevel port-declaration expander (`_expand_ahb_port` via
        `_iface_drives_child_passthrough_initiator`) so the child module
        boundary, the parent instantiation and the parent's own external
        port declaration cannot drift.
        """
        return (
            inst_iface is not None
            and inst.resolved_module is not None
            and any(
                init.passthrough == port
                for cic in inst.resolved_module.interconnects
                for init in cic.initiators
            )
            and passthrough_initiator_uses_initiator_boundary(
                inst_iface.params)
        )

    def _iface_drives_child_passthrough_initiator(self, iface) -> bool:
        """True iff this top-level external AHB iface is the bare-name
        `conn:` of some instance connection whose `port` is a child
        passthrough-initiator taking the cmsdk *initiator* boundary.

        Such an iface (e.g. the external `eth_ss_0` wired by
        `{ port: eth_ss_1, conn: eth_ss_0 }` to the eth-ss submodule's
        initiator-boundary `eth_ss_1` port) must have its toplevel PORT
        DECLARATION emit the cmsdk initiator boundary too — matching what
        `_expand_ahb_top_connection` already emits for the instantiation —
        or hsel dangles, hreadyout floats and hready direction-conflicts.
        Reuses `_child_port_is_pt_initiator` so this predicate cannot drift
        from the instantiation/connection path.
        """
        if iface.type != 'ahb':
            return False
        for inst in self.top.instances:
            if inst.resolved_module is None:
                continue
            for conn in inst.connections:
                if conn.is_unconnected:
                    continue
                if conn.conn != iface.name:
                    continue
                inst_iface = self._find_instance_interface(inst, conn.port)
                if self._child_port_is_pt_initiator(
                        inst, conn.port, inst_iface):
                    return True
        return False

    def _ic_target_wire(self, ic_port: str, sig: str) -> str:
        """Name of the net carrying signal `sig` of interconnect target port
        `ic_port` in this module (the subsystem backend prefixes `i_`)."""
        return f'{ic_port}_{sig}'

    def _hsel_gated_passthrough_initiators(self) -> Dict[str, str]:
        """Passthrough initiators whose HTRANS must be qualified by HSEL.

        Returns {interconnect initiator name: module iface name} for every
        passthrough initiator of THIS module's interconnect whose module
        boundary is the AHB-Lite slave/target form (``EXCLUDE: [hready]``,
        e.g. cpu_ss) and which keeps its ``hsel`` input. Shared by the
        top-level backend (a top module that owns such a port, e.g.
        passthrough_soc ext_s) and SoCSubsystemBackend (036d381).

        That boundary is driven by a PARENT bus-matrix *target* port. An Arm
        bus-matrix output stage muxes HTRANS from whichever master its
        registered arbiter still points at, and HSEL from that master's
        decode for this port, so it legally shows HTRANS=NONSEQ with HSEL=0
        for a cycle whenever that master moves on to another slave. Our own
        interconnect's initiator port has no HSEL (the matrix ties it high),
        so it would accept that cycle as a real transfer: the phantom goes to
        the default slave, whose ERROR wait state is invisible to the parent
        (its HREADYMUX only follows this port while it is selected), and the
        next real transfer into the module is dropped and answered with a
        truncated ERROR. Present IDLE unless the parent selected us — the
        same rule the outward SYSTEM link already applies
        (``{tgt}_hsel ? {tgt}_htrans : 2'b00``, _build_passthrough_tieoffs).

        The cmsdk *initiator* boundary (no hready EXCLUDE, e.g. eth_ss_0) has
        no hsel port, so nothing can be gated here; the PARENT gates that
        form (_child_link_htrans_gates).
        """
        gated = {}
        for ic in self.top.interconnects:
            for init in ic.initiators:
                if not init.passthrough:
                    continue
                top_iface = self._find_top_interface(init.passthrough)
                params = top_iface.params if top_iface else {}
                if passthrough_initiator_uses_initiator_boundary(params):
                    continue
                if 'hsel' in set(params.get('EXCLUDE', [])):
                    continue
                gated[init.name] = init.passthrough
        return gated

    def _child_link_htrans_gates(self) -> List[Dict[str, str]]:
        """HSEL gates for every interconnect TARGET port of this module that
        drives a child instance's passthrough INITIATOR.

        Returns one entry per link, in instance/connection order:
          {'inst', 'port', 'tgt', 'wire', 'value', 'desc'}
        where `wire` (`<inst>_<port>_htrans`) is what the child's HTRANS
        input receives instead of the raw `<tgt>_htrans`.

        A bus-matrix output stage muxes HTRANS from the master its registered
        arbiter still points at, and HSEL from that master's decode for the
        port. It therefore shows HTRANS=NONSEQ with HSEL=0 for a cycle
        whenever that master moves on to another slave. A leaf slave ignores
        that cycle because it reads HSEL. A child subsystem's passthrough
        initiator feeds its OWN matrix, whose initiator port ties HSEL high,
        so the child takes the cycle as a real ("phantom") transfer: it may
        be executed (a duplicate write of zero data through the child's
        outward system window) and the ERROR/wait state it produces is
        invisible to this matrix (its HREADYMUX follows the port only while
        selected), so the next genuine transfer can be dropped.

        Two child boundary forms exist and BOTH are gated here:
          * initiator boundary (no hready EXCLUDE, e.g. eth_ss_0): the child
            has no hsel port at all, so this module is the only place the
            gate can go. Before this gate `<tgt>_hsel` had no reader.
          * target boundary (EXCLUDE: [hready], e.g. cpu_ss): the child
            also gates internally (subsystem.py
            _hsel_gated_passthrough_initiators). The second gate is
            idempotent (hsel ? (hsel ? t : 0) : 0 == hsel ? t : 0) and keeps
            the link safe when the child is not a regenerated wrapper.

        Same rule this module already applies to its own outward passthrough
        targets (`{tgt}_hsel ? {tgt}_htrans : 2'b00`, _build_passthrough_tieoffs).
        HREADY is not qualified: once gated, the child is idle
        (HREADYOUT=1) whenever the port is unselected, which is what the
        matrix assumes. HMASTLOCK is left ungated (lock must persist across
        IDLE); the address-phase signals matter only while HTRANS is active.
        """
        gates = []
        ic_by_name = {ic.name: ic for ic in self.top.interconnects}
        for inst in self.top.instances:
            child = inst.resolved_module
            if child is None:
                continue
            child_pt_inits = {
                init.passthrough
                for cic in child.interconnects
                for init in cic.initiators
                if init.passthrough
            }
            if not child_pt_inits:
                continue
            for conn in inst.connections:
                if conn.is_unconnected or conn.port not in child_pt_inits:
                    continue
                ic_ref, ic_port, _, _ = parse_conn_ref(conn.conn)
                ic = ic_by_name.get(ic_ref)
                if ic is None or not any(t.name == ic_port for t in ic.targets):
                    continue
                hsel = self._ic_target_wire(ic_port, 'hsel')
                htrans = self._ic_target_wire(ic_port, 'htrans')
                gates.append({
                    'inst': inst.instance_name,
                    'port': conn.port,
                    'tgt': ic_port,
                    'wire': f'{inst.instance_name}_{conn.port}_htrans',
                    'value': f"{hsel} ? {htrans} : 2'b00",
                    'desc': (f'{ic_port} -> {inst.instance_name}.{conn.port} '
                             f'(child passthrough initiator): htrans qualified '
                             f'by {hsel}'),
                })
        return gates

    def _child_link_htrans_gate_for(self, inst: Instance, port: str) -> Optional[str]:
        """The gated HTRANS wire for `inst.port`, or None if the link is not
        a matrix target driving a child passthrough initiator."""
        for g in self._child_link_htrans_gates():
            if g['inst'] == inst.instance_name and g['port'] == port:
                return g['wire']
        return None

    def _ahb_iface_boundary_signals(self, iface, is_child_pt_init: bool):
        """Return (signals, invert) for an AHB iface's port boundary.

        `is_child_pt_init` is True when the iface is a passthrough-
        initiator endpoint that takes the cmsdk *initiator* boundary
        (no hsel/hreadyout, bare hready) — i.e. the no-hready-exclude
        variant. Otherwise the AHB-Lite *target* boundary is used
        (the hready-excluded passthrough-initiator, like cpu_ss, falls
        here and the caller drops hready via the iface EXCLUDE).
        """
        if is_child_pt_init:
            # initiator-perspective signals; module sits at the slave end
            return AHB_INITIATOR_SIGNALS, True
        is_target = iface.direction in ('target', 'in')
        return (AHB_TARGET_SIGNALS if is_target
                else AHB_INITIATOR_SIGNALS), False

    def _expand_ahb_top_connection(self, inst: Instance, inst_data: Dict,
                                   port: str, top_iface):
        """Expand a bare-name AHB bundle connection into per-signal ports.

        Handles `{ port: <child_ahb_port>, conn: <top_ahb_iface> }`.
        Both ends share the same boundary form (the child instance's
        AHB port mirrors the top-level passthrough-initiator iface), so
        emit `.<port>_<sig> (<top_iface>_<sig>)` for the signal set that
        boundary actually exposes — matching exactly the child module's
        generated port list.
        """
        inst_iface = self._find_instance_interface(inst, port)
        exclude = set(inst_iface.params.get('EXCLUDE', [])) if inst_iface else set()

        # Is the child instance port a passthrough-initiator iface on the
        # child module's own interconnect, no-hready-exclude variant?
        child_pt_init = self._child_port_is_pt_initiator(
            inst, port, inst_iface)

        signals, _ = self._ahb_iface_boundary_signals(
            inst_iface if inst_iface is not None else top_iface,
            child_pt_init)

        for sig, _sig_dir, _w in signals:
            if sig in exclude:
                continue
            inst_port = self._ahb_inst_port_name(inst, port, sig)
            inst_data['connections'].append({
                'port': inst_port,
                'signal': f'{top_iface.name}_{sig}',
            })

    def _expand_bus_connection(self, inst: Instance, inst_data: Dict, port: str,
                               ic_port: str, ic_name: str):
        """Expand a bus interface connection into individual signal connections."""
        ic = next(ic for ic in self.top.interconnects if ic.name == ic_name)

        # Determine if this instance connects as initiator or target
        is_init = any(init.name == ic_port for init in ic.initiators)

        if is_init:
            # Instance is an initiator — its port drives the bus
            signals = AHB_INITIATOR_SIGNALS

            # Check for passthrough target collision (e.g. CPU 'cpu_0' initiator
            # + system target passthrough 'cpu_0'). Feedback signals need unique
            # internal wire names to avoid colliding with top-level ports.
            pt_target_prefixes = {
                tgt.passthrough for tgt in ic.targets if tgt.passthrough
            }
            has_pt_collision = ic_port in pt_target_prefixes

            for sig, sig_dir, _ in signals:
                inst_port = self._ahb_inst_port_name(inst, port, sig)
                if has_pt_collision:
                    wire_name = f'{ic_port}_ic_{sig}'
                else:
                    wire_name = f'{ic_port}_{sig}'
                inst_data['connections'].append({
                    'port': inst_port,
                    'signal': wire_name,
                })
        else:
            # Instance is a target — the bus drives its port
            inst_iface = self._find_instance_interface(inst, port)
            exclude = set(inst_iface.params.get('EXCLUDE', [])) if inst_iface else set()

            # A child-instance AHB port that is a *passthrough-initiator*
            # iface on the child module's OWN interconnect does NOT have
            # the AHB-Lite slave boundary the parent would otherwise
            # assume from the parent interconnect target role. Its
            # generated child boundary (subsystem.py::_expand_ahb_port)
            # is the cmsdk *initiator* boundary (no hsel/hreadyout, bare
            # hready) UNLESS it excludes hready (cpu_ss → slave/target
            # boundary minus hready). Match the child boundary exactly so
            # the instantiation does not leave hsel/hreadyout dangling /
            # direction-conflicted (the eth_ss_0/eth_ss_1 UPIMI-E).
            child_pt_init = self._child_port_is_pt_initiator(
                inst, port, inst_iface)
            # HTRANS of a link into a child passthrough initiator (either
            # boundary form) takes the hsel-qualified copy
            # (_child_link_htrans_gates), never the raw matrix output.
            gated_htrans = self._child_link_htrans_gate_for(inst, port)

            if child_pt_init:
                # cmsdk initiator boundary: signals named like an
                # AHB master (no hsel/hreadyout); the parent still
                # drives/receives them via the {ic_port}_{sig} target
                # wires the bus matrix exposes.
                for sig, sig_dir, _ in AHB_INITIATOR_SIGNALS:
                    if sig in exclude:
                        continue
                    inst_port = self._ahb_inst_port_name(inst, port, sig)
                    # The bus-matrix slave-side wire for an AHB target
                    # uses {ic_port}_hreadymux for the mux HREADY input;
                    # the child initiator-boundary `hready` output maps
                    # onto the matrix target `hreadyout`.
                    if sig == 'hready':
                        wire_name = f'{ic_port}_hreadyout'
                    elif sig == 'htrans' and gated_htrans:
                        wire_name = gated_htrans
                    else:
                        wire_name = f'{ic_port}_{sig}'
                    inst_data['connections'].append({
                        'port': inst_port,
                        'signal': wire_name,
                    })
                return

            signals = AHB_TARGET_SIGNALS
            # Check for excluded signals (e.g. EXCLUDE: [hready, hburst])
            for sig, sig_dir, _ in signals:
                if sig in exclude:
                    continue
                inst_port = self._ahb_inst_port_name(inst, port, sig)
                wire_name = f'{ic_port}_{sig}'
                if sig == 'htrans' and gated_htrans:
                    wire_name = gated_htrans
                # Slice HADDR to a target instance whose ADDRWIDTH param is
                # narrower than the interconnect bus (e.g. a cmsdk_ahb_to_apb
                # bridge over a 14/16-bit window). Without this the full-width
                # interconnect HADDR net width-mismatches the narrow bridge port.
                # Keyed on the instance ADDRWIDTH (not the ahb_slave ADDR_WIDTH)
                # so the interconnect's target net is untouched. Backward-
                # compatible: no-op for targets with no narrow ADDRWIDTH param.
                if sig == 'haddr':
                    aw = inst.params.get('ADDRWIDTH')
                    if isinstance(aw, str):
                        aw = resolve_param_ref(aw, self.flat_params)
                    bus_aw = self.flat_params.get('SYS_ADDR_W', 32)
                    if isinstance(bus_aw, str):
                        bus_aw = resolve_param_ref(bus_aw, self.flat_params)
                    if isinstance(aw, int) and isinstance(bus_aw, int) and aw < bus_aw:
                        wire_name = f'{wire_name}[{aw - 1}:0]'
                inst_data['connections'].append({
                    'port': inst_port,
                    'signal': wire_name,
                })

    def _resolve_conn_to_wire(self, conn: str, _chain=None) -> str:
        """Resolve a cross-instance reference to a flat wire name.

        For 'u_cpu_0.SYS_HCLK', looks up what u_cpu_0's SYS_HCLK port
        is connected to in the YAML. If the target is a top-level input
        port or an internal wire, resolves directly to that name. If the
        target is a top-level output port (e.g. sys_hclk), an intermediate
        wire is needed because the output port is driven by glue logic —
        in that case, falls back to a flat wire name (u_cpu_0_SYS_HCLK).

        This ensures the generated RTL is synthesisable (no hierarchical
        Verilog path references) and structurally correct (outputs are not
        multiply driven).

        Cycles (two instances mutually referencing each other's ports —
        a link DECLARED FROM BOTH ENDPOINTS, e.g. u_a.X → u_b.Y → u_a.X)
        are broken by CANONICALISING over the cycle members: every entry
        point into the cycle resolves to the lexicographically smallest
        member's flat name. Previously the cycle break returned the flat
        name of whichever reference the resolution happened to START from,
        so the two sides of a double-declared link landed on two DISJOINT
        wires carrying the same logical signal (the severed debug<->hostio4
        ADP byte streams, pynq_z2_build.md §8.5c). `_chain` is the ordered
        resolution path, so only the true cycle members (not any lead-in
        prefix) participate in the canonical choice — a third instance
        referencing INTO the cycle joins the same net.
        """
        if _chain is None:
            _chain = []
        base, high, low = parse_bit_slice(conn)
        if '.' not in base:
            return conn
        if base in _chain:
            # Cycle detected. Canonicalise over the cycle members only —
            # the suffix of the chain starting at the first occurrence of
            # `base` — so the result is independent of the entry point.
            # Record the choice for every member: the outer recursion
            # frames (and any later resolution entering the cycle at a
            # different member) consult this map in their fallback path.
            cycle = _chain[_chain.index(base):]
            flat = min(m.replace('.', '_') for m in cycle)
            for member in cycle:
                self._xref_cycle_canon[member] = flat
            if high is not None and low is not None:
                if high == low:
                    return f'{flat}[{high}]'
                return f'{flat}[{high}:{low}]'
            return flat
        _chain = _chain + [base]

        inst_name, port_name = base.split('.', 1)

        # Check it's not an interconnect reference (those are handled separately)
        if any(ic.name == inst_name for ic in self.top.interconnects):
            return conn

        # Look up the source instance's connection for this port
        inst_obj = self.top.get_instance(inst_name)
        if inst_obj:
            for c in inst_obj.connections:
                if c.port == port_name and not c.is_unconnected:
                    resolved = c.conn
                    # If the resolved target is itself a cross-ref, recurse
                    if '.' in resolved:
                        resolved = self._resolve_conn_to_wire(resolved, _chain)
                        break

                    # Check if the resolved name is a top-level output port.
                    # If so, we must NOT connect other instances to it directly
                    # because only the source instance should drive it. Use an
                    # intermediate wire instead.
                    resolved_base, _, _ = parse_bit_slice(resolved)
                    top_iface = self._find_top_interface(resolved_base)
                    if top_iface and top_iface.is_output:
                        break  # fall through to flat wire name below

                    # Also check expanded AHB output port names
                    is_output_port = False
                    for iface in self.top.interfaces:
                        if iface.type == 'ahb' and iface.is_output:
                            for sig, _, _ in AHB_INITIATOR_SIGNALS:
                                if resolved_base == f'{iface.name}_{sig}':
                                    is_output_port = True
                                    break
                        if is_output_port:
                            break
                    if is_output_port:
                        break  # fall through to flat wire name below

                    # Safe to resolve directly — it's an input port or internal wire
                    if high is not None and low is not None:
                        if high == low:
                            return f'{resolved}[{high}]'
                        return f'{resolved}[{high}:{low}]'
                    return resolved

        # Fallback: flatten to a wire name (e.g. u_cpu_0_SYS_HCLK) — unless
        # this reference is a member of a detected duplicate-link cycle, in
        # which case use the cycle's canonical wire so both declarations of
        # the link (and any lead-in reference) land on ONE net.
        flat = self._xref_cycle_canon.get(base) or base.replace('.', '_')
        if high is not None and low is not None:
            if high == low:
                return f'{flat}[{high}]'
            return f'{flat}[{high}:{low}]'
        return flat

    def _find_top_interface(self, name: str) -> Optional[Interface]:
        """Find a top-level interface by name."""
        return next((i for i in self.top.interfaces if i.name == name), None)

    def _find_instance_interface(self, inst: Instance, port_name: str) -> Optional[Interface]:
        """Find an interface on an instance's module by port name."""
        if inst.resolved_module:
            for iface in inst.resolved_module.interfaces:
                if iface.name == port_name:
                    return iface
        for iface in inst.inline_interfaces:
            if iface.name == port_name:
                return iface
        return None

    def _resolve_instance_port(self, inst: Instance, port_name: str):
        """Resolve `port_name` on an instance's boundary.

        Returns (iface, None) for an exact interface-name match, (iface,
        ResolvedMember) for an expanded bus/stream member port (e.g. the
        `<axis_byte iface>_TDATA` sub-signal), or None when the name is
        neither. Delegates to adapters.resolve_boundary_port — the same
        matcher the validator uses, so binder and diagnostics cannot drift.
        """
        ifaces = []
        if inst.resolved_module is not None:
            ifaces.extend(inst.resolved_module.interfaces)
        ifaces.extend(inst.inline_interfaces)
        return resolve_boundary_port(ifaces, port_name)

    def _member_width(self, iface: Interface, member) -> Any:
        """Raw width of a resolved bus/stream member, from its protocol-table
        width expression resolved against the OWNING iface's params. Returns
        an int where fully resolvable, else the unresolved expression (the
        caller applies resolve_param_ref against the top-level params)."""
        w = member.width
        if isinstance(w, str) and w.isdigit():
            return int(w)
        if w == 'SYS_DATA_W':
            w = iface.params.get('DATA_WIDTH', iface.params.get('DATA_W', 'SYS_DATA_W'))
        elif w == 'SYS_ADDR_W':
            w = iface.params.get('ADDR_WIDTH', iface.params.get('ADDR_W', 'SYS_ADDR_W'))
        elif w == 'WIDTH':
            w = iface.params.get('WIDTH', 1)
        return w

    def _ahb_inst_port_name(self, inst: Instance, port: str, sig: str) -> str:
        """Determine the actual AHB port name on an instance.

        The YAML interface name (e.g. 'ahb_master') is a logical grouping.
        The actual RTL port might be:
          - Bare uppercase: HADDR, HTRANS (e.g. slcorem0, region modules)
          - Prefixed lowercase: ahb_master_haddr (e.g. inline rtl_module defs)
          - Prefixed uppercase: SOC_PERIPHERAL_HADDR

        For pre-existing RTL modules (gen: False with a resolved YAML module),
        the convention is bare uppercase AHB port names. Inline rtl_module
        instances (no resolved module) use the prefixed form matching their
        interface definitions.

        A pre-existing module whose hand-written RTL keeps PREFIXED AHB port
        names (e.g. arch_tech's nanosoc_ss_debug.v DEBUG_HADDR... or
        nanosoc_ss_systemctrl.v SOC_PERIPHERAL_HSEL...) opts out of the bare
        conventions by setting `INST_PORT_PREFIXED: True` on the AHB iface in
        its module YAML — the same knob SoCSubsystemBackend already honours
        for ahb_master bundles. Without the flag both bare rules apply
        unchanged, so existing SoCs are unaffected.
        """
        bare_upper = sig.upper()  # e.g. HADDR

        iface = self._find_instance_interface(inst, port)
        prefixed = bool(iface is not None and isinstance(iface.params, dict)
                        and iface.params.get('INST_PORT_PREFIXED'))

        if not prefixed:
            # Pre-existing RTL modules (gen: False) use bare uppercase AHB
            # ports, but only if the module has a single AHB interface. If it
            # has multiple (e.g. ahb_slave + ahb_master), ports must be
            # prefixed to avoid clashes.
            if inst.resolved_module and not inst.resolved_module.gen:
                ahb_ifaces = [i for i in inst.resolved_module.interfaces if i.type == 'ahb']
                if len(ahb_ifaces) <= 1:
                    return bare_upper

            # ahb_slave on region modules -> bare uppercase (convention)
            if port == 'ahb_slave':
                return bare_upper

        # Uppercase prefix (e.g. SOC_PERIPHERAL -> SOC_PERIPHERAL_HADDR)
        if port.isupper():
            return f'{port}_{bare_upper}'

        # Default: lowercase prefixed (e.g. ahb_master_haddr)
        return f'{port}_{sig}'

    def _top_iface_signal_name(self, iface: Interface, sig_name: str) -> str:
        """Get the top-level port signal name for a protocol interface signal.

        SWD interfaces named '*_swd' strip the '_swd' suffix before appending
        the signal name (e.g. cpu_0_swd + swdi -> cpu_0_swdi).
        """
        if iface.type == 'swd' and iface.name.endswith('_swd'):
            return f'{iface.name[:-4]}_{sig_name}'
        return f'{iface.name}_{sig_name}'

    def _expand_protocol_connection(self, inst: Instance, inst_data: Dict,
                                     port: str, top_iface: Interface):
        """Expand a protocol interface connection into individual signal connections."""
        if top_iface.type in ('axis', 'axis_stream'):
            for sig_name, _, _ in AXIS_SIGNALS:
                inst_data['connections'].append({
                    'port': f'{port}_{sig_name}',
                    'signal': self._top_iface_signal_name(top_iface, sig_name),
                })
            # Add flush signal if either side declares HAS_FLUSH
            inst_iface = self._find_instance_interface(inst, port)
            has_flush = top_iface.params.get('HAS_FLUSH', 0)
            if not has_flush and inst_iface:
                has_flush = inst_iface.params.get('HAS_FLUSH', 0)
            if has_flush:
                inst_data['connections'].append({
                    'port': f'{port}_flush',
                    'signal': self._top_iface_signal_name(top_iface, 'flush'),
                })
        elif top_iface.type == 'axis_byte':
            for sig_name, _, _ in AXIS_BYTE_SIGNALS:
                inst_data['connections'].append({
                    'port': f'{port}_{sig_name}',
                    'signal': self._top_iface_signal_name(top_iface, sig_name),
                })
        elif top_iface.type == 'swd':
            for sig_name, _, _ in SWD_SIGNALS:
                # SWD ports strip '_swd' suffix: cpu_0_swd + swdi -> cpu_0_swdi
                if port.endswith('_swd'):
                    inst_port = f'{port[:-4]}_{sig_name}'
                else:
                    inst_port = f'{port}_{sig_name}'
                inst_data['connections'].append({
                    'port': inst_port,
                    'signal': self._top_iface_signal_name(top_iface, sig_name),
                })
        elif top_iface.type == 'dbg_ahb':
            # Cortex-M debug-slave bus: instance port is the iface name,
            # boundary signal is iface_name_<sig>. e.g.,
            #   { port: cpu_0_dbgahb, conn: cpu_0_dbgahb }
            #   -> .cpu_0_dbgahb_slvaddr(cpu_0_dbgahb_slvaddr), ...
            for sig_name, _, _ in DBGAHB_TARGET_SIGNALS:
                inst_data['connections'].append({
                    'port': f'{port}_{sig_name}',
                    'signal': self._top_iface_signal_name(top_iface, sig_name),
                })

    def _coalesce_bit_select_ports(self, connections: List[Dict],
                                    inst: Instance) -> List[Dict]:
        """Coalesce port bit-select connections into valid SV port connections.

        Converts .port[0](signal) into .port(signal) when the port is 1-bit,
        or groups multiple bit connections into a concatenation.
        """
        from collections import OrderedDict

        result = []
        bit_groups: Dict[str, List[Tuple]] = OrderedDict()

        for conn in connections:
            if conn.get('signal') is None:
                result.append(conn)
                continue
            port = conn['port']
            base, high, low = parse_bit_slice(port)
            if high is not None:
                if base not in bit_groups:
                    bit_groups[base] = []
                bit_groups[base].append((high, low, conn['signal'], conn.get('desc')))
            else:
                result.append(conn)

        for base, bits in bit_groups.items():
            # Sort by bit position descending (MSB first for concatenation)
            bits.sort(key=lambda x: x[0], reverse=True)

            # Check if this is a single-bit port (resolves the FT1248_WIDTH=1 case)
            port_width = self._get_instance_port_width(inst, base)
            total_connected = sum(h - l + 1 for h, l, _, _ in bits)

            if port_width is not None and port_width == 1 and len(bits) == 1:
                # 1-bit port — just use the base port name
                result.append({'port': base, 'signal': bits[0][2]})
            elif len(bits) == 1 and port_width is not None and total_connected == port_width:
                # Single connection covering the full port width
                result.append({'port': base, 'signal': bits[0][2]})
            elif len(bits) == 1:
                # Single partial bit — connect directly (best effort)
                result.append({'port': base, 'signal': bits[0][2]})
            else:
                # Multiple bit connections — build concatenation {msb, ..., lsb}
                concat_parts = [sig for _, _, sig, _ in bits]
                result.append({'port': base, 'signal': '{' + ', '.join(concat_parts) + '}'})

        return result

    def _get_instance_port_width(self, inst: Instance, port_name: str) -> Optional[int]:
        """Get resolved width of a port on an instance's module."""
        iface = self._find_instance_interface(inst, port_name)
        if iface is None:
            return None
        width = iface.params.get('WIDTH', 1)
        if isinstance(width, str):
            width = resolve_param_ref(width, self.flat_params)
        return width if isinstance(width, int) else None

    def _build_interconnect(self) -> Optional[Dict[str, Any]]:
        """Build interconnect instance data."""
        if not self.top.interconnects:
            return None

        ic = self.top.interconnects[0]  # Assume single interconnect for now
        ic_data = {
            'name': ic.name,
            'module_name': f'{ic.name.replace("_ahb_interconnect", "").replace("_interconnect", "")}_interconnect',
            'params': [],
            'connections': [],
            'initiators': [],
            'targets': [],
        }

        # Parameters
        for pname, pval in ic.params.items():
            val = pval
            if isinstance(val, str) and val.startswith('$'):
                val = val[1:]
            ic_data['params'].append({'name': pname, 'value': val})

        # Fixed connections (clock, reset, scan, remap)
        for conn in ic.connections:
            signal = self._resolve_conn_to_wire(conn.conn)
            ic_data['connections'].append({
                'port': conn.port,
                'signal': signal,
            })

        # Build flat port list for the interconnect to avoid nested loop.parent issues in Jinja2
        ic_ports = []

        # Collect passthrough target names to detect naming collisions
        passthrough_target_prefixes = {
            tgt.passthrough for tgt in ic.targets if tgt.passthrough
        }

        for init in ic.initiators:
            # For passthrough initiators, map signals to the top-level interface
            wire_prefix = init.passthrough if init.passthrough else init.name
            ic_ports.append({'type': 'comment', 'text': f'{init.name} Master Port'})

            # Check if this initiator's name collides with a passthrough target
            # (e.g. CPU initiator 'cpu_0' + system target passthrough 'cpu_0').
            # If so, ALL signals must use unique internal wire names
            # (cpu_0_ic_{sig}) to avoid multi-driver conflicts with the
            # passthrough target assigns that drive the top-level ports.
            has_pt_collision = wire_prefix in passthrough_target_prefixes

            # A passthrough initiator whose module iface takes the AHB-Lite
            # slave/target boundary (EXCLUDE: [hready], the cpu_ss form) has
            # no bare `<iface>_hready`: the ready goes out on
            # `<iface>_hreadyout`, and HTRANS is qualified by `<iface>_hsel`
            # (_hsel_gated_passthrough_initiators). Same rule as the
            # subsystem backend; before this a top module wired such a port
            # as the cmsdk initiator form (hsel unread, hreadyout undriven,
            # HREADY into an undeclared `<iface>_hready`).
            target_boundary = False
            if init.passthrough and not has_pt_collision:
                pt_iface = self._find_top_interface(init.passthrough)
                target_boundary = (
                    pt_iface is not None and pt_iface.type == 'ahb'
                    and not passthrough_initiator_uses_initiator_boundary(pt_iface.params))
            hsel_gated = init.name in self._hsel_gated_passthrough_initiators()

            for sig, sig_dir, sig_w in AHB_INITIATOR_SIGNALS:
                if has_pt_collision:
                    signal_name = f'{wire_prefix}_ic_{sig}'
                elif sig == 'htrans' and hsel_gated:
                    signal_name = f'{init.name}_htrans_gated'
                elif sig == 'hready' and target_boundary:
                    signal_name = f'{wire_prefix}_hreadyout'
                else:
                    signal_name = f'{wire_prefix}_{sig}'
                ic_ports.append({
                    'type': 'port',
                    'port': f'{init.name}_{sig}',
                    'signal': signal_name,
                })

        for tgt in ic.targets:
            # For passthrough targets, use the target's own name for all
            # internal wires (e.g. system_haddr) to avoid multi-driver
            # conflicts with top-level ports sharing the passthrough name.
            # For non-passthrough targets, use the target name directly.
            wire_prefix = tgt.name if tgt.passthrough else tgt.name
            ic_ports.append({'type': 'comment', 'text': f'{tgt.name} Target Port'})
            for sig, sig_dir, sig_w in AHB_TARGET_SIGNALS:
                if sig == 'hready':
                    ic_ports.append({
                        'type': 'port',
                        'port': f'{tgt.name}_hreadymux',
                        'signal': f'{tgt.name}_hready',
                    })
                else:
                    ic_ports.append({
                        'type': 'port',
                        'port': f'{tgt.name}_{sig}',
                        'signal': f'{wire_prefix}_{sig}',
                    })

        ic_data['ports'] = ic_ports
        return ic_data

    def _build_passthrough_tieoffs(self) -> List[Dict[str, Any]]:
        """Build assigns for passthrough target signals.

        For passthrough targets, all interconnect target port signals use
        internal wires named with the target prefix (e.g. system_haddr).
        This method generates assigns to:
          1. Connect output signals (hsel, haddr, htrans, etc.) from the
             internal wires to the corresponding top-level output ports
          2. Connect input signals (hrdata, hresp) from top-level input
             ports back to the internal wires
          3. Drive hreadyout from the ENDPOINT's ready: the slave-form
             `<iface>_hreadyout` for a target-boundary passthrough, or the bare
             `<iface>_hready` INPUT for an initiator-boundary one. Only when the
             endpoint has neither — nothing off-module can stall us — is it
             tied to 1'b1. Tying it unconditionally (the old behaviour) left an
             initiator-boundary endpoint's `hready` dangling, so an off-module
             target could never insert a wait state.
          4. Tie hready (bus matrix mux input) to hreadyout
        """
        tieoffs = []
        top_port_names = set()
        for iface in self.top.interfaces:
            if iface.type == 'ahb':
                is_target = iface.direction in ('target', 'in')
                signals = AHB_TARGET_SIGNALS if is_target else AHB_INITIATOR_SIGNALS
                for sig, _, _ in signals:
                    top_port_names.add(f'{iface.name}_{sig}')
            elif iface.type == 'wire':
                top_port_names.add(iface.name)

        for ic in self.top.interconnects:
            for tgt in ic.targets:
                if not tgt.passthrough:
                    continue
                pt = tgt.passthrough  # e.g. 'cpu_0'

                # Connect passthrough target signals to/from top-level ports.
                # The interconnect target port uses {tgt.name}_{sig} wires
                # (e.g. system_haddr) while top-level ports use {pt}_{sig}
                # (e.g. cpu_0_haddr).
                for sig, sig_dir, sig_w in AHB_TARGET_SIGNALS:
                    tgt_wire = f'{tgt.name}_{sig}'
                    port_wire = f'{pt}_{sig}'

                    if sig == 'hready':
                        # hready is handled separately below (mux input)
                        continue
                    elif sig == 'hreadyout':
                        # hreadyout: the ready the OFF-MODULE target hands back
                        # to our bus matrix. Three cases, in priority order.
                        if port_wire in top_port_names:
                            # (1) Target-boundary passthrough: the top-level
                            # iface is `type: ahb, direction: target`, so it
                            # carries a slave-form `<iface>_hreadyout`.
                            tieoffs.append({
                                'wire': tgt_wire,
                                'value': port_wire,
                                'desc': f'{tgt.name} passthrough target hreadyout from port',
                            })
                        elif f'{pt}_hready' in top_port_names:
                            # (2) Initiator-boundary passthrough: the top-level
                            # iface is `direction: initiator`, and
                            # AHB_INITIATOR_SIGNALS has NO `hreadyout` — the
                            # external slave's ready arrives on the bare
                            # `<iface>_hready` INPUT. Import it.
                            #
                            # Tying 1'b1 here (the old fallthrough) told the
                            # matrix an off-die target is ALWAYS ready, so
                            # `<iface>_hready` was left completely dangling and
                            # the target could never insert a wait state. Any
                            # multi-cycle slave (a die-to-die link, an off-chip
                            # bridge) would then have its read data sampled a
                            # cycle after address phase — i.e. garbage.
                            #
                            # This mirrors the passthrough-INITIATOR fix in the
                            # subsystem backend (see subsystem.py, "Hardcoding
                            # `_hreadyout` here (the old behaviour) left the
                            # initiator-boundary module's `<iface>_hready`
                            # output undriven"). Same class of bug, other
                            # direction.
                            tieoffs.append({
                                'wire': tgt_wire,
                                'value': f'{pt}_hready',
                                'desc': f'{tgt.name} passthrough target hreadyout from {pt}_hready (initiator boundary)',
                            })
                        else:
                            # (3) No external ready at all — nothing off-module
                            # can stall us, so always ready.
                            tieoffs.append({
                                'wire': tgt_wire,
                                'value': "1'b1",
                                'desc': f'{tgt.name} passthrough target default ready',
                            })
                    elif sig_dir == 'in':
                        # Signals driven by the interconnect into the target
                        # (hsel, haddr, htrans, etc.) — drive top-level ports.
                        # HTRANS is GATED by the interconnect's hsel decode: a
                        # passthrough subsystem must forward a live transfer ONLY
                        # for an access it actually selected. An ungated HTRANS
                        # lets a core's local-bus traffic broadcast onto the shared
                        # system bus and corrupt another master (the silicon
                        # HTRANS-broadcast / read-0 bug). The {tgt.name}_* wires are
                        # driven by the interconnect (see subsystem backend), so the
                        # gate has exactly one driver — critical because an undriven
                        # system_htrans is Z in sim but 0 in synth, collapsing every
                        # CPU write to a shared peripheral to IDLE on FPGA.
                        if port_wire in top_port_names:
                            if sig == 'htrans':
                                value = f"{tgt.name}_hsel ? {tgt_wire} : 2'b00"
                            else:
                                value = tgt_wire
                            tieoffs.append({
                                'wire': port_wire,
                                'value': value,
                                'desc': f'{tgt.name} passthrough: {sig} to top-level port',
                            })
                    elif sig_dir == 'out':
                        # Response signals from external port back to interconnect
                        # (hrdata, hresp) — drive internal wires from top-level ports
                        if port_wire in top_port_names:
                            tieoffs.append({
                                'wire': tgt_wire,
                                'value': port_wire,
                                'desc': f'{tgt.name} passthrough: {sig} from top-level port',
                            })

                # system_hready: driven by the interconnect's hreadymux output
                # (not tied off here — the bus matrix generates this signal)

        # HSEL gate on each target-boundary passthrough initiator's HTRANS
        # (_hsel_gated_passthrough_initiators).
        for init_name, module_iface in self._hsel_gated_passthrough_initiators().items():
            tieoffs.append({
                'wire': f'{init_name}_htrans_gated',
                'value': f"{module_iface}_hsel ? {module_iface}_htrans : 2'b00",
                'desc': f'{init_name} passthrough initiator: htrans qualified by {module_iface}_hsel',
            })
        return tieoffs
