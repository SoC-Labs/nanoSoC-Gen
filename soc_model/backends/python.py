"""Python object backend — serialises the SoC model as an importable .py file.

The generated file reconstructs the full model object tree using the dataclass
constructors from soc_model.model, so downstream tools can do:

    from build_soc.model.nanosoc_model import top_module
    for inst in top_module.instances:
        print(inst.instance_name, inst.module_name)
"""

import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..model import (
    AddressDecode, AddressDecodeSlot, Assign, Clock, Connection, Firmware,
    Instance, Interconnect, InterconnectInitiator, InterconnectInitiatorTarget,
    InterconnectTarget, Interface, LinkerProfile, LinkerRegion, Module, Param,
    Register, RegisterField, RegisterMap, Reset,
)
from ..utils import write_if_changed
from .base import Backend, BackendResult, GenContext, Phase
from .registry import register_backend


@register_backend
class SoCPythonBackend(Backend):
    """Generates a Python file that reconstructs the full SoC model."""

    name = "python"
    phase = Phase.REPORT

    def __init__(self, top_module: Optional[Module] = None):
        self.top = top_module

    def run(self, ctx: GenContext) -> BackendResult:
        self.top = ctx.top
        out = ctx.reports_dir / f"{ctx.top.name}_model.py"
        self.generate(str(out))
        return BackendResult(files=[out])

    def generate(self, output_path: str):
        """Write the model as a .py file."""
        lines = [
            '"""Auto-generated SoC model — do not edit.',
            '',
            f'Source: {self.top.source_file}',
            f'Module: {self.top.name}',
            '"""',
            '',
            'from soc_model.model import (',
            '    AddressDecode, AddressDecodeSlot, Assign, Clock, Connection,',
            '    Firmware, Instance, Interconnect, InterconnectInitiator,',
            '    InterconnectInitiatorTarget, InterconnectTarget, Interface,',
            '    LinkerProfile, LinkerRegion, Module, Param, Register,',
            '    RegisterField, RegisterMap, Reset,',
            ')',
            '',
            '',
        ]

        # Emit all modules (top + resolved children), deduped by name
        emitted = set()
        module_vars = {}
        self._collect_modules(self.top, emitted, module_vars, lines)

        # Top-level binding
        lines.append(f'top_module = {module_vars[self.top.name]}')
        lines.append('')

        write_if_changed(output_path, '\n'.join(lines))

    def _collect_modules(
        self,
        module: Module,
        emitted: set,
        module_vars: Dict[str, str],
        lines: List[str],
    ):
        """Recursively emit Module definitions, children first."""
        if module.name in emitted:
            return
        emitted.add(module.name)

        # Emit children first so they can be referenced
        for inst in module.instances:
            if inst.resolved_module and inst.resolved_module.name not in emitted:
                self._collect_modules(inst.resolved_module, emitted, module_vars, lines)

        var_name = _var_name(module.name)
        module_vars[module.name] = var_name
        lines.append(f'{var_name} = {self._repr_module(module, module_vars)}')
        lines.append('')

    def _repr_module(self, m: Module, module_vars: Dict[str, str]) -> str:
        """Generate the Module(...) constructor string."""
        parts = [
            f'name={_r(m.name)}',
            f'gen={_r(m.gen)}',
            f'desc={_r(m.desc)}',
            f'source_file={_r(m.source_file)}',
        ]

        # Params
        if m.params:
            param_entries = []
            for name, p in m.params.items():
                param_entries.append(
                    f'{_r(name)}: Param(name={_r(p.name)}, type={_r(p.type)}, '
                    f'default={_r(p.default)}, desc={_r(p.desc)})'
                )
            parts.append('params={\n' + _indent(',\n'.join(param_entries)) + '\n    }')

        # Clocks
        if m.clocks:
            items = [f'Clock(name={_r(c.name)}, source={_r(c.source)}, desc={_r(c.desc)})' for c in m.clocks]
            parts.append('clocks=[\n' + _indent(',\n'.join(items)) + '\n    ]')

        # Resets
        if m.resets:
            items = [f'Reset(name={_r(r.name)}, active={_r(r.active)}, source={_r(r.source)}, desc={_r(r.desc)})' for r in m.resets]
            parts.append('resets=[\n' + _indent(',\n'.join(items)) + '\n    ]')

        # Interfaces
        if m.interfaces:
            items = [self._repr_interface(i) for i in m.interfaces]
            parts.append('interfaces=[\n' + _indent(',\n'.join(items)) + '\n    ]')

        # Instances
        if m.instances:
            items = [self._repr_instance(inst, module_vars) for inst in m.instances]
            parts.append('instances=[\n' + _indent(',\n'.join(items)) + '\n    ]')

        # Assigns
        if m.assigns:
            items = [self._repr_assign(a) for a in m.assigns]
            parts.append('assigns=[\n' + _indent(',\n'.join(items)) + '\n    ]')

        # Interconnects
        if m.interconnects:
            items = [self._repr_interconnect(ic) for ic in m.interconnects]
            parts.append('interconnects=[\n' + _indent(',\n'.join(items)) + '\n    ]')

        # AddressDecode
        if m.address_decode:
            parts.append(f'address_decode={self._repr_address_decode(m.address_decode)}')

        # Firmware
        if m.firmware:
            parts.append(f'firmware={self._repr_firmware(m.firmware)}')

        return 'Module(\n' + _indent(',\n'.join(parts)) + '\n)'

    def _repr_interface(self, i: Interface) -> str:
        return (f'Interface(name={_r(i.name)}, type={_r(i.type)}, '
                f'direction={_r(i.direction)}, params={_r(i.params)}, desc={_r(i.desc)})')

    def _repr_connection(self, c: Connection) -> str:
        return f'Connection(port={_r(c.port)}, conn={_r(c.conn)})'

    def _repr_assign(self, a: Assign) -> str:
        parts = [f'target={_r(a.target)}', f'expr={_r(a.expr)}']
        if a.type:
            parts.append(f'type={_r(a.type)}')
        if a.bit is not None:
            parts.append(f'bit={_r(a.bit)}')
        if a.combining:
            parts.append(f'combining={_r(a.combining)}')
        if a.origin:
            parts.append(f'origin={_r(a.origin)}')
        parts.append(f'desc={_r(a.desc)}')
        return f'Assign({", ".join(parts)})'

    def _repr_instance(self, inst: Instance, module_vars: Dict[str, str]) -> str:
        parts = [
            f'instance_name={_r(inst.instance_name)}',
            f'module_name={_r(inst.module_name)}',
            f'is_rtl_module={_r(inst.is_rtl_module)}',
            f'addressable={_r(inst.addressable)}',
        ]
        if inst.condition:
            parts.append(f'condition={_r(inst.condition)}')
        if inst.params:
            parts.append(f'params={_r(inst.params)}')
        if inst.connections:
            conn_items = [self._repr_connection(c) for c in inst.connections]
            parts.append('connections=[\n' + _indent(',\n'.join(conn_items), 12) + '\n        ]')
        if inst.inline_interfaces:
            iface_items = [self._repr_interface(i) for i in inst.inline_interfaces]
            parts.append('inline_interfaces=[\n' + _indent(',\n'.join(iface_items), 12) + '\n        ]')
        # Reference the resolved module variable
        if inst.resolved_module and inst.resolved_module.name in module_vars:
            parts.append(f'resolved_module={module_vars[inst.resolved_module.name]}')

        return 'Instance(\n' + _indent(',\n'.join(parts), 8) + '\n    )'

    def _repr_interconnect(self, ic: Interconnect) -> str:
        parts = [
            f'name={_r(ic.name)}',
            f'gen={_r(ic.gen)}',
            f'type={_r(ic.type)}',
            f'desc={_r(ic.desc)}',
        ]
        if ic.params:
            parts.append(f'params={_r(ic.params)}')
        if ic.arbitration_scheme:
            parts.append(f'arbitration_scheme={_r(ic.arbitration_scheme)}')
        if ic.connections:
            conn_items = [self._repr_connection(c) for c in ic.connections]
            parts.append('connections=[\n' + _indent(',\n'.join(conn_items), 8) + '\n    ]')
        if ic.targets:
            items = []
            for t in ic.targets:
                items.append(
                    f'InterconnectTarget(name={_r(t.name)}, instance={_r(t.instance)}, '
                    f'base={_h(t.base)}, size={_h(t.size)}, phys_size={_r(t.phys_size)}, '
                    f'sw_access={_r(t.sw_access)}, region_type={_r(t.region_type)}, '
                    f'role={_r(t.role)}, subordinate_bus={_r(t.subordinate_bus)}, desc={_r(t.desc)})'
                )
            parts.append('targets=[\n' + _indent(',\n'.join(items), 8) + '\n    ]')
        if ic.initiators:
            items = []
            for init in ic.initiators:
                tgt_items = [
                    f'InterconnectInitiatorTarget(name={_r(t.name)}, visibility={_r(t.visibility)})'
                    for t in init.targets
                ]
                tgts_str = '[' + ', '.join(tgt_items) + ']'
                items.append(
                    f'InterconnectInitiator(name={_r(init.name)}, '
                    f'instance={_r(init.instance)}, targets={tgts_str})'
                )
            parts.append('initiators=[\n' + _indent(',\n'.join(items), 8) + '\n    ]')

        return 'Interconnect(\n' + _indent(',\n'.join(parts), 8) + '\n    )'

    def _repr_address_decode(self, ad: AddressDecode) -> str:
        parts = [f'type={_r(ad.type)}', f'module={_r(ad.module)}']
        if ad.bridge:
            parts.append(f'bridge={_r(ad.bridge)}')
        if ad.slots:
            items = [self._repr_slot(s) for s in ad.slots]
            parts.append('slots=[\n' + _indent(',\n'.join(items), 8) + '\n    ]')
        return 'AddressDecode(\n' + _indent(',\n'.join(parts), 8) + '\n    )'

    def _repr_slot(self, s: AddressDecodeSlot) -> str:
        parts = [
            f'slot={_r(s.slot)}', f'name={_r(s.name)}', f'module={_r(s.module)}',
            f'offset={_h(s.offset)}', f'size={_h(s.size)}',
        ]
        if s.register_map:
            parts.append(f'register_map={_r(s.register_map)}')
        if s.resolved_register_map:
            parts.append(f'resolved_register_map={self._repr_register_map(s.resolved_register_map)}')
        if s.desc:
            parts.append(f'desc={_r(s.desc)}')
        if s.address_decode:
            parts.append(f'address_decode={self._repr_address_decode(s.address_decode)}')
        return f'AddressDecodeSlot({", ".join(parts)})'

    def _repr_register_map(self, rm: RegisterMap) -> str:
        parts = [
            f'name={_r(rm.name)}', f'module={_r(rm.module)}',
            f'desc={_r(rm.desc)}',
            f'address_width={rm.address_width}', f'data_width={rm.data_width}',
            f'source_file={_r(rm.source_file)}',
        ]
        if rm.registers:
            items = [self._repr_register(reg) for reg in rm.registers]
            parts.append('registers=[\n' + _indent(',\n'.join(items), 8) + '\n    ]')
        return 'RegisterMap(\n' + _indent(',\n'.join(parts), 8) + '\n    )'

    def _repr_register(self, reg: Register) -> str:
        parts = [
            f'name={_r(reg.name)}', f'offset={_h(reg.offset)}',
            f'width={reg.width}', f'access={_r(reg.access)}',
        ]
        if reg.reset_value is not None:
            parts.append(f'reset_value={_r(reg.reset_value)}')
        if reg.desc:
            parts.append(f'desc={_r(reg.desc)}')
        if reg.fields:
            items = [
                f'RegisterField(name={_r(f.name)}, bits={_r(f.bits)}, access={_r(f.access)}, desc={_r(f.desc)})'
                for f in reg.fields
            ]
            parts.append('fields=[\n' + _indent(',\n'.join(items), 8) + '\n    ]')
        return f'Register({", ".join(parts)})'

    def _repr_firmware(self, fw: Firmware) -> str:
        parts = [f'cpu_initiator={_r(fw.cpu_initiator)}']
        if fw.linker_profiles:
            items = []
            for lp in fw.linker_profiles:
                regions = [
                    f'LinkerRegion(target={_r(r.target)}, address_select={_r(r.address_select)}'
                    + (f', linker_name={_r(r.linker_name)}' if r.linker_name else '')
                    + (f', software_access={_r(r.software_access)}' if r.software_access else '')
                    + (f', size_adjust={_r(r.size_adjust)}' if r.size_adjust else '')
                    + (f', phys_size={_r(r.phys_size)}' if r.phys_size else '')
                    + (f', origin={_r(r.origin)}' if r.origin is not None else '')
                    + ')'
                    for r in lp.regions
                ]
                items.append(
                    f'LinkerProfile(name={_r(lp.name)}, regions=[\n'
                    + _indent(',\n'.join(regions), 12) + '\n        ])'
                )
            parts.append('linker_profiles=[\n' + _indent(',\n'.join(items), 8) + '\n    ]')
        if fw.adp:
            parts.append(f'adp={_r(fw.adp)}')
        if fw.hex_adjust:
            parts.append(f'hex_adjust={_r(fw.hex_adjust)}')
        return 'Firmware(\n' + _indent(',\n'.join(parts), 8) + '\n    )'


def _r(val: Any) -> str:
    """repr() a value."""
    return repr(val)


def _h(val: int) -> str:
    """Hex representation for addresses."""
    if isinstance(val, int) and val >= 0x100:
        return f'0x{val:08X}'
    return repr(val)


def _var_name(name: str) -> str:
    """Convert a module name to a valid Python variable name."""
    return 'mod_' + name.replace('-', '_').replace('.', '_')


def _indent(text: str, spaces: int = 8) -> str:
    """Indent every line of text."""
    prefix = ' ' * spaces
    return '\n'.join(prefix + line for line in text.split('\n'))
