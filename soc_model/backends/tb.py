# Copyright 2026, SoC Labs (www.soclabs.org)
"""Testbench generation backend — emits a cocotb env (tb_top.sv + Makefile +
default test battery + model summary) for the top-level module.

The boundary tie-offs are driven by the SAME ``protocol_utils`` signal tables
the ``SoCTopLevelBackend`` uses, so the TB DUT boundary cannot drift from the
generated RTL boundary (the single biggest win — see roadmap doc 04 §5.5).

What is emitted (into ``<out_dir>``, normally
``$HOME_SP/build_soc/verification/``):

  * ``tb_top.sv``       — clock/reset generation, the DUT instantiation with
                          every input tied off and every output probed, plus —
                          when the top exposes the 7-pin ``hostio4_p1_*`` pad
                          bus — an in-TB ``hostio4_target`` host bridge + a
                          tri-state pad mesh + ``CLK``/``NRST`` aliases so the
                          vendored ADP driver attaches at the TB scope (§5.4).
  * ``model_summary.py``— flat, importable dicts (CLOCKS / RESETS / TARGETS /
                          INITIATORS / REGMAPS / IRQS / ACCESS_MODE / ...) so
                          the test file self-checks against the same model that
                          built the RTL (config-independent).
  * ``test_default.py`` — the model-derived default test battery
                          (TBGEN_CLK_001 / BOOT / ENUM / REG / MEM / IRQ).
  * ``Makefile``        — a parameterised copy of the cocotb env boilerplate.

The backend is purely additive and READ-ONLY against ``top_module``.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..model import (
    Module, Interface, Instance, Interconnect, InterconnectTarget,
    AddressDecode, AddressDecodeSlot, RegisterMap, Register,
)
from ..utils import write_if_changed
from .protocol_utils import (
    AHB_INITIATOR_SIGNALS, AHB_TARGET_SIGNALS, AXIS_SIGNALS, AXIS_BYTE_SIGNALS,
    SWD_SIGNALS, DBGAHB_TARGET_SIGNALS, GPIO_SIGNALS,
    width_str as _width_str, param_width_str as _param_width_str,
)

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    Environment = None
    FileSystemLoader = None

_BACKEND_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _BACKEND_DIR / 'templates'

# The only hand-curated knowledge: input ports that must NOT tie to 0.
# Keyed by EXACT name. Mirrors cocotb/soc_smoke/tb_top.sv verbatim:
#   swj_enable/swditms/ntrst = 1 (DAP idle but chiplet active, soc_smoke:139,145,147)
#   qspi_io_i = 4'hF (soc_smoke:184)
# NOTE: pmuenable is NOT here — soc_smoke ties cpu_{0,1}_pmuenable to 1'b0
# (lines 98-99). There is deliberately NO blanket '_enable' suffix rule, which
# would wrongly drive the PMU enables high. Add any future must-be-1 enable HERE
# by exact name, never by suffix.
_TB_INPUT_TIEOFFS = {
    'dap_swj_enable': "1'b1",
    'dap_swditms':    "1'b1",
    'dap_ntrst':      "1'b1",
    'qspi_io_i':      "4'hF",
}
# Only AHB hready inputs are safe to infer by suffix (must signal ready).
_TB_INPUT_TIEOFF_SUFFIXES = (('hreadyin', "1'b1"), ('hready', "1'b1"))

# The five hostio4_target RTL sources the generated Makefile must add to
# VERILOG_SOURCES when the bridge is emitted (they are NOT on the flist —
# soc_multicore_hostio4/Makefile:22-28 adds them explicitly).
_HOSTIO4_TARGET_SOURCES = [
    'hostio4_target_sync',
    'hostio4_target_fsm',
    'hostio4_target_axis_rxport',
    'hostio4_target_axis_txport',
    'hostio4_target',
]

# Offsets of the CoreSight-style PID/CID identity registers (relative to a
# peripheral's base) used by TBGEN_REG_001.
_PID_OFFSETS = (0xFE0, 0xFE4, 0xFE8, 0xFEC)
_CID_OFFSETS = (0xFF0, 0xFF4, 0xFF8, 0xFFC)


class SoCTbBackend:
    """Generates a self-contained cocotb testbench env from a SoC model top."""

    def __init__(self, top_module: Module):
        self.top = top_module

    # ------------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------------
    def generate(self, out_dir, config_overrides: Optional[Dict[str, Any]] = None
                 ) -> Optional[Path]:
        """Emit the cocotb env into ``out_dir``.  Returns the tb_top.sv path
        (or None when jinja2 is unavailable, mirroring the other backends)."""
        if Environment is None:
            print("  WARNING: jinja2 not available — skipping TB generation")
            return None

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        ctx = self._build_context(config_overrides or {})

        write_if_changed(out_dir / 'tb_top.sv',
                         env.get_template('tb_top.sv.j2').render(**ctx))
        write_if_changed(out_dir / 'model_summary.py', self._emit_model_summary())
        write_if_changed(out_dir / 'test_default.py',
                         env.get_template('tb_test_default.py.j2').render(**ctx))
        write_if_changed(out_dir / 'Makefile',
                         env.get_template('tb_Makefile.j2').render(**ctx))
        return out_dir / 'tb_top.sv'

    # ------------------------------------------------------------------
    # clock / reset
    # ------------------------------------------------------------------
    def _primary_clock_name(self) -> str:
        if self.top.clocks:
            return self.top.clocks[0].name
        return 'sys_fclk'

    def _clock_period_ns(self) -> float:
        """Period of the primary clock in ns; defaults to 10 ns (100 MHz)
        when no frequency is resolvable (the proven soc_smoke default)."""
        if self.top.clocks:
            c = self.top.clocks[0]
            if c.period_ns is not None:
                return float(c.period_ns)
            if c.freq_hz:
                return 1e9 / float(c.freq_hz)
        f = self.top.flat_params.get('SYS_CLK_FREQ_HZ')
        if isinstance(f, int) and f > 0:
            return 1e9 / float(f)
        return 10.0

    def _reset_list(self) -> List[Dict[str, str]]:
        return [{'name': r.name, 'active': r.active or 'low'}
                for r in self.top.resets]

    # ------------------------------------------------------------------
    # boundary expansion (reuses the protocol_utils tables)
    # ------------------------------------------------------------------
    def _expand_ports(self) -> List[Dict[str, Any]]:
        """Expand the top's interfaces into flat (name, direction, width)
        ports — the SAME expansion the SoCTopLevelBackend performs — so the TB
        boundary tracks the RTL boundary exactly."""
        addr_w = self.top.flat_params.get('SYS_ADDR_W', 'SYS_ADDR_W')
        data_w = self.top.flat_params.get('SYS_DATA_W', 'SYS_DATA_W')
        ports: List[Dict[str, Any]] = []

        def _push(name, sig_dir, width):
            direction = 'input' if sig_dir in ('in',) else 'output'
            ports.append({'name': name, 'direction': direction, 'width': width})

        for iface in self.top.interfaces:
            t = iface.type
            if t == 'wire':
                width = iface.params.get('WIDTH', 1)
                direction = 'input' if iface.direction == 'in' else 'output'
                ports.append({'name': iface.name, 'direction': direction,
                              'width': width})
            elif t == 'swd':
                base = iface.name[:-4] if iface.name.endswith('_swd') else iface.name
                for sig, sig_dir, w in SWD_SIGNALS:
                    _push(f'{base}_{sig}', sig_dir, int(w))
            elif t == 'dbg_ahb':
                for sig, sig_dir, w in DBGAHB_TARGET_SIGNALS:
                    _push(f'{iface.name}_{sig}', sig_dir, int(w))
            elif t == 'gpio':
                width = iface.params.get('WIDTH', 16)
                for sig, sig_dir, _ in GPIO_SIGNALS:
                    _push(f'{iface.name}_{sig}', sig_dir, width)
            elif t == 'ahb':
                self._expand_ahb(iface, addr_w, data_w, ports)
            elif t in ('axis', 'axis_stream'):
                self._expand_axis(iface, AXIS_SIGNALS, data_w, ports)
            elif t == 'axis_byte':
                self._expand_axis(iface, AXIS_BYTE_SIGNALS, data_w, ports)
        return ports

    def _expand_ahb(self, iface, addr_w, data_w, ports):
        is_target = iface.direction in ('target', 'in')
        signals = AHB_TARGET_SIGNALS if is_target else AHB_INITIATOR_SIGNALS
        exclude = set(iface.params.get('EXCLUDE', []) or [])
        for sig, sig_dir, w in signals:
            if sig in exclude:
                continue
            if w == 'SYS_ADDR_W':
                w = addr_w
            elif w == 'SYS_DATA_W':
                w = data_w
            direction = 'input' if sig_dir == 'in' else 'output'
            ports.append({'name': f'{iface.name}_{sig}', 'direction': direction,
                          'width': w})

    def _expand_axis(self, iface, signals, data_w, ports):
        is_output = iface.direction in ('out', 'sender', 'initiator')
        for sig, sig_dir, w in signals:
            if w == 'SYS_DATA_W':
                w = data_w
            if is_output:
                direction = 'output' if sig_dir == 'out' else 'input'
            else:
                direction = 'input' if sig_dir == 'out' else 'output'
            ports.append({'name': f'{iface.name}_{sig}', 'direction': direction,
                          'width': w})

    @staticmethod
    def _tieoff_for(name: str, width: Any, direction: str) -> Optional[str]:
        """The constant an INPUT port is tied to (None for outputs/inout)."""
        if direction != 'input':
            return None
        if name in _TB_INPUT_TIEOFFS:
            return _TB_INPUT_TIEOFFS[name]
        for suf, val in _TB_INPUT_TIEOFF_SUFFIXES:
            if name.endswith(suf):
                return val
        if isinstance(width, int):
            return "1'b0" if width == 1 else f"{{{width}{{1'b0}}}}"
        # parameterised / expression width -> replicate a zero across the whole
        # width (the param IS the bit count, e.g. {SYS_DATA_W{1'b0}}).
        if isinstance(width, str):
            return f"{{{width.lstrip('$')}{{1'b0}}}}"
        return "1'b0"

    # ------------------------------------------------------------------
    # access mode + hostio4 bridge detection
    # ------------------------------------------------------------------
    def _access_mode(self) -> Tuple[str, bool]:
        """Returns (ACCESS_MODE, HOSTIO4_BRIDGE).

        Detects the HOSTIO4 *pad* bus on the real multicore top (NOT axis_* —
        those are not DUT ports, see roadmap §4.4); falls back to a direct-axis
        flat top, then a uart-marker smoke, then nothing.
        """
        names = {i.name for i in self.top.interfaces}
        if {'hostio4_p1_in', 'hostio4_p1_out', 'hostio4_p1_outen'} <= names:
            return ('adp', True)
        if any(n.startswith('axis_rx') for n in names):
            return ('adp_direct', False)
        if 'uart_txd' in names or any(i.type == 'uart' for i in self.top.interfaces):
            return ('uart', False)
        return ('none', False)

    def _uart_tx_port(self) -> Optional[str]:
        for i in self.top.interfaces:
            if i.name == 'uart_txd' or (i.type == 'wire' and i.name.endswith('uart_txd')):
                return i.name
        return None

    # ------------------------------------------------------------------
    # target -> register-map traversal (roadmap §5.3.1)
    # ------------------------------------------------------------------
    def _resolve_target_regmap(self, module: Module,
                               tgt: InterconnectTarget) -> Optional[RegisterMap]:
        """Resolve a target's register map two ways:
          (1) a resolved RegisterMap carried directly on the target, or
          (2) the child Module(instance).address_decode slot it maps to.
        """
        # (1) direct ref on the target (the parser may attach the resolved map).
        rm = getattr(tgt, 'resolved_register_map', None) or \
            getattr(tgt, 'register_map', None)
        if isinstance(rm, RegisterMap):
            return rm

        # (2) walk into the child module's address_decode.
        if tgt.instance:
            inst = module.get_instance(tgt.instance)
            child = inst.resolved_module if inst else None
            if child is not None and child.address_decode is not None:
                for slot in child.address_decode.slots:
                    if slot.resolved_register_map is not None and (
                            slot.name == tgt.name or slot.module == tgt.name):
                        return slot.resolved_register_map
                # fall back to the first slot that carries a resolved map.
                for slot in child.address_decode.slots:
                    if slot.resolved_register_map is not None:
                        return slot.resolved_register_map
        return None

    def _identity_from_regmap(self, rm: RegisterMap) -> Optional[Dict[str, list]]:
        """Extract {'PID': [...], 'CID': [...]} byte lists from PID0-3/CID0-3
        registers carrying non-None reset_value; None if not identifiable."""
        by_off = {r.offset: r for r in rm.registers}

        def _bytes(offsets):
            out = []
            for off in offsets:
                reg = by_off.get(off)
                if reg is None or reg.reset_value is None:
                    return None
                val = reg.reset_value
                if isinstance(val, str):
                    try:
                        val = int(val, 0)
                    except ValueError:
                        return None
                out.append(int(val) & 0xFF)
            return out

        pid = _bytes(_PID_OFFSETS)
        cid = _bytes(_CID_OFFSETS)
        if pid is None and cid is None:
            return None
        ident: Dict[str, list] = {}
        if pid is not None:
            ident['PID'] = pid
        if cid is not None:
            ident['CID'] = cid
        return ident

    def _collect_targets(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        for ic in self.top.interconnects:
            for tgt in ic.targets:
                if tgt.name in seen:
                    continue
                seen.add(tgt.name)
                regmap = None
                rm = self._resolve_target_regmap(self.top, tgt)
                if rm is not None:
                    regmap = self._identity_from_regmap(rm)
                out.append({
                    'name': tgt.name,
                    'base': tgt.base,
                    'size': tgt.size,
                    'access': tgt.sw_access or 'rw',
                    'region_type': tgt.region_type,
                    'regmap': regmap,
                })
        return out

    def _collect_initiators(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for ic in self.top.interconnects:
            for init in ic.initiators:
                out.append({'name': init.name,
                            'targets': list(init.target_names)})
        return out

    def _collect_irqs(self) -> List[str]:
        """Top-level wire OUTPUTS whose name carries 'irq'."""
        out: List[str] = []
        for iface in self.top.interfaces:
            if iface.type == 'wire' and iface.is_output and 'irq' in iface.name.lower():
                out.append(iface.name)
        return out

    # ------------------------------------------------------------------
    # template context
    # ------------------------------------------------------------------
    def _build_context(self, overrides: Dict[str, Any]) -> Dict[str, Any]:
        access_mode, hostio4_bridge = self._access_mode()
        clk_name = self._primary_clock_name()
        resets = self._reset_list()

        ports = self._expand_ports()
        clk_set = {c.name for c in self.top.clocks}
        rst_set = {r['name'] for r in resets}

        probe_wires = []
        dut_conns = []
        inout_wires = []
        for p in ports:
            name, direction, width = p['name'], p['direction'], p['width']
            wstr = _param_width_str(width) if isinstance(width, str) \
                else _width_str(width)
            if name in clk_set:
                dut_conns.append({'port': name, 'expr': name})
                continue
            if name in rst_set:
                dut_conns.append({'port': name, 'expr': name})
                continue
            if direction == 'input':
                if hostio4_bridge and name in (
                        'hostio4_p1_in', 'hostio4_p1_out', 'hostio4_p1_outen'):
                    dut_conns.append({'port': name, 'expr': name})
                    continue
                dut_conns.append({'port': name,
                                  'expr': self._tieoff_for(name, width, direction)})
            else:  # output -> probe wire
                if hostio4_bridge and name in (
                        'hostio4_p1_out', 'hostio4_p1_outen'):
                    dut_conns.append({'port': name, 'expr': name})
                    continue
                probe_wires.append({'name': name, 'width_str': wstr})
                dut_conns.append({'port': name, 'expr': name})

        dut_overrides = [{'name': k, 'value': v} for k, v in overrides.items()]

        return {
            'module_name': self.top.name,
            'clk_name': clk_name,
            'clk_period_ns': self._fmt_period(self._clock_period_ns()),
            'resets': resets,
            'probe_wires': probe_wires,
            'dut_conns': dut_conns,
            'dut_overrides': dut_overrides,
            'access_mode': access_mode,
            'hostio4_bridge': hostio4_bridge,
            'hostio4_sources': _HOSTIO4_TARGET_SOURCES,
            'targets': self._collect_targets(),
            'irqs': self._collect_irqs(),
            'uart_tx_port': self._uart_tx_port(),
            'boot_marker': 'hello',
        }

    @staticmethod
    def _fmt_period(period_ns: float) -> str:
        # integer periods render without a trailing .0 (FCLK_PERIOD_NS=10)
        if abs(period_ns - round(period_ns)) < 1e-9:
            return str(int(round(period_ns)))
        return f"{period_ns:.4f}"

    # ------------------------------------------------------------------
    # model_summary.py (string build — like firmware_adp.py emission)
    # ------------------------------------------------------------------
    def _emit_model_summary(self) -> str:
        access_mode, hostio4_bridge = self._access_mode()
        clk_name = self._primary_clock_name()
        resets = self._reset_list()
        targets = self._collect_targets()
        initiators = self._collect_initiators()
        irqs = self._collect_irqs()

        # REGMAPS: keyed by target name, only those carrying a derivable identity.
        regmaps = {t['name']: t['regmap'] for t in targets if t['regmap']}

        L: List[str] = []
        L.append('# AUTO-GENERATED by nanosoc_gen SoCTbBackend — model summary')
        L.append('# for testbench self-checks.  Do not edit (regenerate instead).')
        L.append(f'TOP_MODULE = {self.top.name!r}')
        L.append(f'ACCESS_MODE = {access_mode!r}')
        L.append(f'HOSTIO4_BRIDGE = {hostio4_bridge!r}')
        L.append(f'CLK_ALIAS = {clk_name!r}')
        L.append(f'NRST_ALIAS = {(resets[0]["name"] if resets else None)!r}')
        L.append(f'CLOCKS = {[c.name for c in self.top.clocks]!r}')
        L.append(f'RESETS = {resets!r}')
        L.append('TARGETS = [')
        for t in targets:
            L.append('    {'
                     f'"name": {t["name"]!r}, "base": {t["base"]:#x}, '
                     f'"size": {t["size"]:#x}, "access": {t["access"]!r}, '
                     f'"region_type": {t["region_type"]!r}, '
                     f'"regmap": {t["regmap"]!r}'
                     '},')
        L.append(']')
        L.append(f'INITIATORS = {initiators!r}')
        L.append(f'REGMAPS = {regmaps!r}')
        L.append(f'IRQS = {irqs!r}')
        return '\n'.join(L) + '\n'
