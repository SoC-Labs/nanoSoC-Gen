"""Performance-probe render backend for nanosoc_gen (perf-probe plan M2).

Emits passive AHB-Lite performance monitors for an interconnect. When an
interconnect declares a ``perf_probes:`` block AND at least one of its
initiators/targets carries ``probe: true``, this backend renders a
self-contained harness module

    <prefix>_perf_probe_harness.sv

that wraps one ``ahb_perf_probe`` per flagged port (taps are INPUTS ONLY —
they cannot perturb the monitored bus) plus one ``ahb_perf_probe_regs``
read-out slave (Option B: live counters, firmware-coherent reads — no shadow
registers). The harness presents a single AHB-Lite slave port (the read-out,
wired into the matrix as an ordinary target) plus per-probe tap input ports.

The backend ALSO computes the wiring plan (which matrix net each tap port
connects to), so the system-integration step can instantiate the harness and
connect its taps without re-deriving the matrix net-naming convention:

    initiator <i>: mon_hsel = 1'b1, mon_htrans = <i>_htrans,
                   mon_hready = <i>_hready, mon_hwrite = <i>_hwrite
    target    <t>: mon_hsel = <t>_hsel, mon_htrans = <t>_htrans,
                   mon_hready = <t>_hreadymux, mon_hwrite = <t>_hwrite

STRICT NO-OP: if no interconnect carries a perf_probes block with probed
ports, the backend emits nothing — so a model without probes regenerates
byte-identically (the M2 acceptance gate).

The ahb_perf_probe / ahb_perf_probe_regs primitives are ordinary project RTL
(currently ethernet-subsystem-ahb/src/rtl/perf_probe); this backend only
renders the SoC-specific harness and its flist. The primitive source dir is
referenced through ``probe_rtl_ref`` (an flist-expandable string).
"""

import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..model import (
    Connection, Instance, Interconnect, InterconnectInitiator,
    InterconnectInitiatorTarget, InterconnectTarget, Interface, Module,
)
from ..utils import write_if_changed

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:  # pragma: no cover
    Environment = None
    FileSystemLoader = None

_BACKEND_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _BACKEND_DIR / 'templates'

# Default flist-expandable reference to the perf_probe primitive RTL.
_DEFAULT_PROBE_RTL_REF = '${ETH_SS_HOME}/src/rtl/perf_probe'

_COUNTER_WIDTH = 32


def module_prefix(ic_name: str) -> str:
    """Derive the module prefix from an interconnect name (mirrors ahb.py)."""
    return ic_name.replace('_ahb_interconnect', '').replace('_interconnect', '')


def probe_plan(ic: Interconnect) -> List[Dict[str, Any]]:
    """Compute the ordered probe list + tap wiring for an interconnect.

    Returns one dict per probed port, in the deterministic order
    (initiators first, then targets, each in declaration order):

        { 'label':        <port name>,
          'is_initiator':  0 | 1,
          'taps': { 'hsel': <net>, 'htrans': <net>,
                    'hready': <net>, 'hwrite': <net> } }

    The tap values use the matrix-PORT base naming (e.g. ``cpu_0_htrans``,
    ``ethmac_0_hreadymux``). The system-integration step (M2b) maps these to
    the actual system-scope net names, which the SoCSystemBackend emits with
    an ``i_`` prefix — confirmed from generated build_soc/rtl/ethernet_ss_ahb.sv:
        initiator: i_cpu_0_htrans / i_cpu_0_hready / i_cpu_0_hwrite
        target:    i_ethmac_0_hsel / i_ethmac_0_htrans /
                   i_ethmac_0_hreadymux / i_ethmac_0_hwrite
    M2b must derive that net name via the system backend's naming helper (not
    hardcode ``i_``) and validate by real regen + elaboration.

    Empty list if the interconnect declares no probed ports.
    """
    plan: List[Dict[str, Any]] = []
    for init in ic.initiators:
        if getattr(init, 'probe', False):
            n = init.name
            plan.append({
                'label': n,
                'is_initiator': 1,
                'taps': {
                    'hsel':   "1'b1",            # a master is always "selected"
                    'htrans': f'{n}_htrans',
                    'hready': f'{n}_hready',     # matrix feedback to the master
                    'hwrite': f'{n}_hwrite',
                },
            })
    for tgt in ic.targets:
        if getattr(tgt, 'probe', False):
            n = tgt.name
            plan.append({
                'label': n,
                'is_initiator': 0,
                'taps': {
                    'hsel':   f'{n}_hsel',
                    'htrans': f'{n}_htrans',
                    'hready': f'{n}_hreadymux',  # HREADY the target actually sees
                    'hwrite': f'{n}_hwrite',
                },
            })
    return plan


def _tap_net(tap_value: str) -> str:
    """Map a probe_plan tap value to its system-scope net name.

    probe_plan returns matrix-PORT base names (cpu_0_htrans, ethmac_0_hreadymux)
    or the literal constant 1'b1; the system backend emits interconnect nets
    with an ``i_`` prefix (verified from build_soc/rtl/ethernet_ss_ahb.sv), so
    everything but a literal gets the prefix. Connection conn strings are
    emitted verbatim by the system backend, so this is the final net name.
    """
    if tap_value.startswith("1'") or tap_value.startswith("0'"):
        return tap_value
    return 'i_' + tap_value


def _clk_reset_conns(ic: Interconnect):
    """Reuse the interconnect's own clock/reset connection targets."""
    clk = rst = None
    for c in ic.connections:
        if c.port == 'sys_hclk':
            clk = c.conn
        elif c.port == 'sys_hresetn':
            rst = c.conn
    return clk or 'u_cpu_0.SYS_HCLK', rst or 'u_cpu_0.SYS_HRESETn'


def inject_perf_probes(top_module: Module) -> int:
    """Mutate the model in place so probed interconnects get a harness.

    For every interconnect that declares a ``perf_probes:`` block AND >=1
    probed initiator/target, add (a) the read-out InterconnectTarget (+ grant
    it to every initiator) and (b) the harness Instance, wired to the existing
    matrix nets. Returns the number of harnesses injected.

    STRICT NO-OP when no interconnect declares probes -> the model is
    untouched -> RTL regenerates byte-identically (the M2 acceptance gate).
    Call once, after build_system() and before the AHB/system/top backends.
    """
    return _inject_walk(top_module, set())


def _inject_walk(module: Module, seen: set) -> int:
    if id(module) in seen:
        return 0
    seen.add(id(module))
    count = 0
    for ic in module.interconnects:
        if ic.perf_probes and probe_plan(ic):
            _inject_one(module, ic)
            count += 1
    for inst in module.instances:
        if inst.resolved_module:
            count += _inject_walk(inst.resolved_module, seen)
    return count


def _inject_one(module: Module, ic: Interconnect):
    plan = probe_plan(ic)
    prefix = module_prefix(ic.name)
    harness_module = f'{prefix}_perf_probe_harness'
    pp = ic.perf_probes or {}
    readout = pp.get('target_name', f'{prefix}_perf_probe')
    inst_name = pp.get('instance_name', f'u_{prefix}_perf_probe_0')
    clk_conn, rst_conn = _clk_reset_conns(ic)

    # (a) read-out target + initiator visibility (idempotent)
    if not any(t.name == readout for t in ic.targets):
        ic.targets.append(InterconnectTarget(
            name=readout, instance=inst_name,
            base=pp.get('base', 0), size=pp.get('size', 0),
            protocol='ahb', sw_access='rw', region_type='periph',
            desc='Performance-probe read-out (ahb_perf_probe_regs)',
        ))
        for init in ic.initiators:
            if readout not in [t.name for t in init.targets]:
                init.targets.append(InterconnectInitiatorTarget(name=readout))

    # (b) harness instance — AHB slave (matrix target) + tap inputs
    if any(i.instance_name == inst_name for i in module.instances):
        return  # already injected
    interfaces = [
        Interface(name='HCLK', type='wire', direction='in', params={'WIDTH': 1}),
        Interface(name='HRESETn', type='wire', direction='in', params={'WIDTH': 1}),
        Interface(name='ahb_slave', type='ahb', direction='target',
                  params={'ADDR_WIDTH': '$SYS_ADDR_W', 'DATA_WIDTH': '$SYS_DATA_W'}),
    ]
    connections = [
        Connection(port='HCLK', conn=clk_conn),
        Connection(port='HRESETn', conn=rst_conn),
        Connection(port='ahb_slave', conn=f'{ic.name}.{readout}'),
    ]
    for i, p in enumerate(plan):
        for sig in ('hsel', 'htrans', 'hready', 'hwrite'):
            interfaces.append(Interface(
                name=f'm{i}_{sig}', type='wire', direction='in',
                params={'WIDTH': 2 if sig == 'htrans' else 1}))
            connections.append(Connection(
                port=f'm{i}_{sig}', conn=_tap_net(p['taps'][sig])))

    harness_inst = Instance(
        instance_name=inst_name, module_name=harness_module,
        is_rtl_module=True, addressable=True,
        inline_interfaces=interfaces, connections=connections,
    )
    # Attach a stub resolved_module exactly as SoCBuilder._resolve_instances
    # does for rtl_module instances. Without it the subsystem internal-wire
    # scan (which skips `resolved_module is None`) never sees the harness's
    # ahb_slave connection, so the i_<readout>_* AHB nets are never sized and
    # default to 1-bit — truncating the harness's multi-bit AHB ports (HADDR/
    # HTRANS/...) so the read-out never captures a transfer (reads 0).
    stub = Module(name=harness_module, gen=False,
                  desc=f'Standalone RTL module: {harness_module}')
    stub.interfaces.extend(interfaces)
    harness_inst.resolved_module = stub
    module.instances.append(harness_inst)


class SoCPerfProbeBackend:
    """Render perf-probe harnesses for every interconnect that declares them."""

    def __init__(self, top_module: Module,
                 probe_rtl_ref: str = _DEFAULT_PROBE_RTL_REF):
        self.top = top_module
        self.probe_rtl_ref = probe_rtl_ref

    # -- discovery -----------------------------------------------------------
    def _walk(self, module: Module, found: List[Interconnect]):
        for ic in module.interconnects:
            if ic.perf_probes and probe_plan(ic):
                found.append(ic)
        for inst in module.instances:
            if inst.resolved_module:
                self._walk(inst.resolved_module, found)

    def interconnects_with_probes(self) -> List[Interconnect]:
        found: List[Interconnect] = []
        self._walk(self.top, found)
        return found

    # -- generation ----------------------------------------------------------
    def generate_all(self, build_dir: Path) -> List[Tuple[str, Path]]:
        """Emit a harness + flist for each probed interconnect.

        STRICT NO-OP: returns [] (emitting nothing) when no interconnect
        declares probes — so probe-free models regenerate byte-identically.
        """
        ics = self.interconnects_with_probes()
        if not ics:
            return []
        if Environment is None:  # pragma: no cover
            raise RuntimeError("jinja2 is required to render perf-probe harnesses")

        generated: List[Tuple[str, Path]] = []
        for ic in ics:
            generated.append(self._generate_one(ic, build_dir))
        return generated

    def _generate_one(self, ic: Interconnect, build_dir: Path) -> Tuple[str, Path]:
        prefix = module_prefix(ic.name)
        harness_module = f'{prefix}_perf_probe_harness'
        plan = probe_plan(ic)

        pp = ic.perf_probes or {}
        readout = {
            'instance_name': pp.get('instance_name', f'u_{prefix}_perf_probe_0'),
            'base': pp.get('base'),
            'size': pp.get('size'),
        }

        rtl_dir = build_dir / 'rtl' / ic.name
        flist_dir = build_dir / 'flist'
        rtl_dir.mkdir(parents=True, exist_ok=True)
        flist_dir.mkdir(parents=True, exist_ok=True)

        ctx = {
            'source_yaml':    f'soc_model ({ic.name})',
            'template_name':  'perf_probe_harness.sv.j2',
            'generated_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'module_name':    harness_module,
            'interconnect_name': ic.name,
            'cw':             _COUNTER_WIDTH,
            'n_probes':       len(plan),
            'probes':         plan,
            'probe_rtl_ref':  self.probe_rtl_ref,
            'readout':        readout,
        }

        self._render('perf_probe_harness.sv.j2', ctx, rtl_dir / f'{harness_module}.sv')
        self._render('perf_probe.flist.j2', ctx, flist_dir / f'{prefix}_perf_probe.flist')
        return (ic.name, rtl_dir / f'{harness_module}.sv')

    def _render(self, template_name: str, context: dict, output_path: Path):
        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        output = env.get_template(template_name).render(**context)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if write_if_changed(output_path, output):
            print(f"  Generated: {output_path}")
        else:
            print(f"  Skipped (unchanged): {output_path}")
