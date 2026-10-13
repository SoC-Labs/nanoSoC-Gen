"""System-level SV module backend — generates the system integration wrapper.

Produces a structural SystemVerilog module that wraps the nanosoc core and
connects its expansion region port to a user-replaceable expansion region.
By default, a cmsdk_ahb_default_slave is placed on the expansion AHB port.

Generated files:
  - <module_name>.sv — system-level structural module
  - <module_name>_system.flist — file list
"""

import datetime
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..model import Instance, Interface, Module
from ..utils import parse_bit_slice, parse_conn_ref, resolve_param_ref, flatten_params, write_if_changed
from .protocol_utils import (
    AHB_INITIATOR_SIGNALS, AHB_TARGET_SIGNALS,
    AXIS_SIGNALS, AXIS_BYTE_SIGNALS,
    SWD_SIGNALS, GPIO_SIGNALS, DBGAHB_TARGET_SIGNALS,
    width_str, param_width_str,
)

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    Environment = None
    FileSystemLoader = None

_BACKEND_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _BACKEND_DIR / 'templates'


def boundary_net_map(module: Module) -> Dict[str, str]:
    """Map each child-instance port (lower-cased) to the net it binds.

    The system wrapper's OWN boundary port for a flattened bus member
    (dbg_ahb/gpio/axis/swd) must be spelled EXACTLY like the net the core
    instance drives/receives on that member -- its YAML ``conn:`` string.
    Verilog identifiers are case-sensitive, so a boundary port emitted with
    the protocol-table casing (``CPU_0_dbgahb_slvaddr``) while the instance
    binds the YAML net (``CPU_0_DBGAHB_SLVADDR``) never resolves to the same
    signal: the declared port is left unreferenced and the bound net becomes
    an implicit 1-bit wire -- the silent debug-bus / GPIO / ADP disconnect.
    Keyed case-insensitively because the child ``port:`` is lower-case (it
    matches the core's lower-case RTL ports) while the flattened member
    carries the interface's own casing.

    Module-level so the chip backend can name the SAME nets when it wires an
    ``swd`` interface through to the SWDIO/SWDCLK pads.
    """
    net_map: Dict[str, str] = {}
    for inst in module.instances:
        for conn in inst.connections:
            if conn.is_unconnected:
                continue
            net_map.setdefault(conn.port.lower(), conn.conn)
    return net_map


def boundary_port_name(member: str, net_map: Dict[str, str]) -> str:
    """Boundary port name for a flattened bus member.

    Returns the net the core instance binds to this member (preserving the
    YAML ``conn:`` casing) when that bind exists and is a plain identifier,
    so the declared port and the instance connection are the SAME net. Falls
    back to the member name itself when the member is not bound by any
    instance (an iface declared but not wired) or when the bound net is not
    a plain identifier (bit-slice / hierarchical ref, illegal as a port).
    """
    net = net_map.get(member.lower())
    if net and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', net):
        return net
    return member


class SoCSystemBackend:
    """Generates the system-level structural SV module from the SoC model."""

    def __init__(self, system_module: Module, core_module: Optional[Module] = None):
        self.system = system_module
        self.core = core_module
        self.flat_params = flatten_params(
            {n: p.default for n, p in system_module.params.items()}
        )

    def generate(self, rtl_dir: Path, flist_dir: Path = None) -> Optional[Path]:
        """Generate nanosoc_system.sv in rtl_dir and flist in flist_dir.

        Returns the path to the generated SV file.
        """
        if Environment is None:
            print("  WARNING: jinja2 not available — skipping system-level generation")
            return None

        rtl_dir = Path(rtl_dir)
        rtl_dir.mkdir(parents=True, exist_ok=True)

        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        template = env.get_template('soc_system.sv.j2')

        ctx = self._build_context()
        content = template.render(**ctx)

        out_path = rtl_dir / f'{self.system.name}.sv'
        write_if_changed(out_path, content)

        # Also generate the flist
        if flist_dir is None:
            flist_dir = rtl_dir.parent / 'flist'
        flist_dir = Path(flist_dir)
        flist_dir.mkdir(parents=True, exist_ok=True)
        flist_content = f"// Auto-generated file list for {self.system.name}\n"
        flist_content += f"$SOCLABS_NANOSOC_SOC_DIR/build_soc/rtl/{self.system.name}.sv\n"
        flist_path = flist_dir / f'{self.system.name}_system.flist'
        write_if_changed(flist_path, flist_content)

        return out_path

    def _build_context(self) -> Dict[str, Any]:
        """Build the full template context."""
        return {
            'module_name': self.system.name,
            'module_desc': self.system.desc,
            'generated_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'params': self._build_params(),
            'ports': self._build_ports(),
            'internal_wires': self._build_internal_wires(),
            'instances': self._build_instances(),
        }

    def _build_params(self) -> List[Dict[str, Any]]:
        """Build parameter list for module declaration."""
        params = []
        for name, p in self.system.params.items():
            default = p.default
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
        """Build port list, expanding protocol interfaces.

        A flattened bus member's boundary port is named after the net the core
        instance binds to it (its YAML ``conn:``), NOT the protocol-table
        casing, so the declared port and the instance connection are the SAME
        case-sensitive Verilog net. See _boundary_net_map.
        """
        ports = []
        net_map = self._boundary_net_map()
        for iface in self.system.interfaces:
            if iface.type == 'wire':
                w = iface.params.get('WIDTH', 1)
                direction = 'input' if iface.direction == 'in' else 'output'
                ports.append({
                    'name': iface.name,
                    'direction': direction,
                    'width': w,
                    'width_str': width_str(w),
                    'desc': iface.desc,
                })
            elif iface.type == 'swd':
                for sig_name, sig_dir, sig_width in SWD_SIGNALS:
                    direction = 'input' if sig_dir == 'in' else 'output'
                    base = iface.name[:-4] if iface.name.endswith('_swd') else iface.name
                    ports.append({
                        'name': self._boundary_port_name(f'{base}_{sig_name}', net_map),
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
                        'name': self._boundary_port_name(f'{iface.name}_{sig_name}', net_map),
                        'direction': direction,
                        'width': int(sig_width),
                        'width_str': width_str(int(sig_width)),
                        'desc': f'{iface.desc} — {sig_name}',
                    })
            elif iface.type == 'gpio':
                w = iface.params.get('WIDTH', 16)
                for sig_name, sig_dir, _ in GPIO_SIGNALS:
                    direction = 'input' if sig_dir == 'in' else 'output'
                    ports.append({
                        'name': self._boundary_port_name(f'{iface.name}_{sig_name}', net_map),
                        'direction': direction,
                        'width': w,
                        'width_str': width_str(w),
                        'desc': f'{iface.desc} — {sig_name}',
                    })
            elif iface.type in ('axis', 'axis_stream', 'axis_byte'):
                # ADP / AXI-Stream byte channels. This branch did not exist, so
                # the ADP_* boundary ports were never declared and the core's
                # adp_* binds dangled as implicit 1-bit nets. Direction logic
                # mirrors SoCTopLevelBackend._expand_axis_port.
                signals = (AXIS_BYTE_SIGNALS if iface.type == 'axis_byte'
                           else AXIS_SIGNALS)
                is_output = iface.direction in ('out', 'sender', 'initiator')
                data_w = iface.params.get(
                    'DATA_WIDTH', iface.params.get('DATA_W', 'SYS_DATA_W'))
                for sig_name, sig_dir, sig_width in signals:
                    w = data_w if sig_width == 'SYS_DATA_W' else sig_width
                    if is_output:
                        direction = 'output' if sig_dir == 'out' else 'input'
                    else:
                        direction = 'input' if sig_dir == 'out' else 'output'
                    ports.append({
                        'name': self._boundary_port_name(
                            f'{iface.name}_{sig_name}', net_map),
                        'direction': direction,
                        'width': w,
                        'width_str': param_width_str(w),
                        'desc': f'{iface.desc} — {sig_name}',
                    })
        return ports

    def _boundary_net_map(self) -> Dict[str, str]:
        """See :func:`boundary_net_map` (module-level, shared with chip.py)."""
        return boundary_net_map(self.system)

    def _boundary_port_name(self, member: str, net_map: Dict[str, str]) -> str:
        """See :func:`boundary_port_name` (module-level, shared with chip.py)."""
        return boundary_port_name(member, net_map)

    def _build_internal_wires(self) -> List[Dict[str, Any]]:
        """Build internal wire declarations."""
        port_names = {iface.name for iface in self.system.interfaces}
        wires = []
        for w in self.system.internal_wires:
            if w.name in port_names:
                continue
            wire_width = w.params.get('WIDTH', 1)
            if isinstance(wire_width, str) and wire_width.startswith('$'):
                wire_width = resolve_param_ref(wire_width, self.flat_params)
            wires.append({
                'name': w.name,
                'width': wire_width,
                'width_str': param_width_str(wire_width),
                'desc': w.desc,
            })
        return wires

    def _build_instances(self) -> List[Dict[str, Any]]:
        """Build instance data for template rendering."""
        instances = []
        for inst in self.system.instances:
            inst_data = {
                'instance_name': inst.instance_name,
                'module_name': inst.module_name,
                'is_expansion': 'region_exp' in inst.module_name,
                'params': [],
                'connections': [],
            }

            # Build parameter overrides
            for pname, pval in inst.params.items():
                val = pval
                if isinstance(val, str) and val.startswith('$'):
                    val = val[1:]
                inst_data['params'].append({
                    'name': pname,
                    'value': val,
                })

            # Build port connections
            for conn in inst.connections:
                if conn.is_unconnected:
                    inst_data['connections'].append({
                        'port': conn.port,
                        'signal': None,
                        'desc': conn.desc,
                    })
                    continue

                inst_data['connections'].append({
                    'port': conn.port,
                    'signal': conn.conn,
                    'desc': conn.desc,
                })

            instances.append(inst_data)
        return instances
