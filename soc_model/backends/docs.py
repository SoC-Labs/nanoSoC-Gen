"""Markdown documentation backend — generates a design document listing all
instantiated modules, their parameters, interfaces, and address maps.

Generated files:
  - <module_name>_design_doc.md — Markdown documentation
"""

import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..model import (
    AddressDecode, Instance, Interconnect, InterconnectTarget,
    Module, Param,
)
from ..utils import flatten_params, write_if_changed
from ._jinja import Environment, make_env

_BACKEND_DIR = Path(__file__).resolve().parent
_TEMPLATE_DIR = _BACKEND_DIR / 'templates'


# size formatter shared with text.py (doc 02 M5) — kept as `_fmt_size` so the
# call sites + the jinja filter registration below are unchanged.
from .walk import fmt_size as _fmt_size


class SoCDocsBackend:
    """Generates Markdown design documentation from the SoC model."""

    def __init__(self, top_module: Module):
        self.top = top_module

    def generate(self, output_dir: Path) -> Optional[Path]:
        """Generate design documentation markdown."""
        if Environment is None:
            print("  WARNING: jinja2 not available — skipping documentation generation")
            return None

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        env = make_env(_TEMPLATE_DIR)
        env.filters['fmt_size'] = _fmt_size
        env.filters['hex8'] = lambda v: f"0x{v:08X}" if isinstance(v, int) else str(v)

        template = env.get_template('design_doc.md.j2')

        ctx = self._build_context()
        content = template.render(**ctx)

        out_path = output_dir / f'{self.top.name}_design_doc.md'
        write_if_changed(out_path, content)
        return out_path

    def _build_context(self) -> Dict[str, Any]:
        """Build the full template context."""
        return {
            'module_name': self.top.name,
            'module_desc': self.top.desc,
            'generated_date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'params': self._build_params(),
            'clocks': self._build_clocks(),
            'resets': self._build_resets(),
            'interfaces': self._build_interfaces(),
            'instances': self._build_instances(),
            'memory_map': self._build_memory_map(),
            'interconnects': self._build_interconnects(),
            'hierarchy': self._build_hierarchy(),
        }

    def _build_params(self) -> List[Dict[str, Any]]:
        """Build parameter table."""
        params = []
        for name, p in self.top.params.items():
            default = p.default
            if isinstance(default, int) and default >= 256:
                default = f"0x{default:X}"
            params.append({
                'name': name,
                'type': p.type,
                'default': default,
                'desc': p.desc,
            })
        return params

    def _build_clocks(self) -> List[Dict[str, Any]]:
        """Build clock domain table."""
        return [
            {'name': c.name, 'source': c.source, 'desc': c.desc}
            for c in self.top.clocks
        ]

    def _build_resets(self) -> List[Dict[str, Any]]:
        """Build reset domain table."""
        return [
            {'name': r.name, 'active': r.active, 'source': r.source, 'desc': r.desc}
            for r in self.top.resets
        ]

    def _build_interfaces(self) -> List[Dict[str, Any]]:
        """Build external interface table."""
        ifaces = []
        for i in self.top.interfaces:
            width = i.params.get('WIDTH', i.params.get('DATA_WIDTH', '-'))
            ifaces.append({
                'name': i.name,
                'type': i.type,
                'direction': i.direction,
                'width': width,
                'desc': i.desc,
            })
        return ifaces

    def _build_instances(self) -> List[Dict[str, Any]]:
        """Build instance detail blocks."""
        instances = []
        for inst in self.top.instances:
            inst_data = {
                'instance_name': inst.instance_name,
                'module_name': inst.module_name,
                'is_rtl_module': inst.is_rtl_module,
                'addressable': inst.addressable,
                'condition': inst.condition,
                'desc': '',
                'params': [],
                'interfaces': [],
                'address_regions': [],
            }

            # Get description from resolved module
            if inst.resolved_module:
                inst_data['desc'] = inst.resolved_module.desc

            # Parameter overrides
            for pname, pval in inst.params.items():
                inst_data['params'].append({
                    'name': pname,
                    'value': pval,
                })

            # Key interfaces from resolved module
            if inst.resolved_module:
                for iface in inst.resolved_module.interfaces:
                    inst_data['interfaces'].append({
                        'name': iface.name,
                        'type': iface.type,
                        'direction': iface.direction,
                        'desc': iface.desc,
                    })
            elif inst.inline_interfaces:
                for iface in inst.inline_interfaces:
                    inst_data['interfaces'].append({
                        'name': iface.name,
                        'type': iface.type,
                        'direction': iface.direction,
                        'desc': iface.desc,
                    })

            # Address regions from interconnects
            if inst.addressable:
                for ic in self.top.interconnects:
                    for tgt in ic.targets:
                        if tgt.instance == inst.instance_name:
                            end = tgt.base + tgt.size - 1
                            inst_data['address_regions'].append({
                                'name': tgt.name,
                                'base': f"0x{tgt.base:08X}",
                                'end': f"0x{end:08X}",
                                'size': _fmt_size(tgt.size),
                                'access': tgt.sw_access,
                            })

            instances.append(inst_data)
        return instances

    def _build_memory_map(self) -> List[Dict[str, Any]]:
        """Build full memory map table."""
        regions = []
        for ic in self.top.interconnects:
            for tgt in sorted(ic.targets, key=lambda t: t.base):
                end = tgt.base + tgt.size - 1
                regions.append({
                    'interconnect': ic.name,
                    'target': tgt.name,
                    'instance': tgt.instance or '',
                    'base': f"0x{tgt.base:08X}",
                    'end': f"0x{end:08X}",
                    'size': _fmt_size(tgt.size),
                    'access': tgt.sw_access,
                    'type': tgt.region_type or '',
                })
                # Sub-regions from child instances
                if tgt.instance:
                    inst = self.top.get_instance(tgt.instance)
                    if inst and inst.resolved_module:
                        self._collect_sub_regions(
                            inst.resolved_module, tgt.base, regions
                        )
        return regions

    def _collect_sub_regions(self, module: Module, parent_base: int,
                             regions: List[Dict]):
        """Recursively collect sub-regions."""
        for child_ic in module.interconnects:
            for ct in sorted(child_ic.targets, key=lambda x: x.base):
                abs_base = parent_base + ct.base
                abs_end = abs_base + ct.size - 1
                regions.append({
                    'interconnect': child_ic.name,
                    'target': f"  {ct.name}",
                    'instance': ct.instance or '',
                    'base': f"0x{abs_base:08X}",
                    'end': f"0x{abs_end:08X}",
                    'size': _fmt_size(ct.size),
                    'access': ct.sw_access,
                    'type': ct.region_type or '',
                })

        if module.address_decode:
            for slot in sorted(module.address_decode.slots, key=lambda s: s.offset):
                abs_base = parent_base + slot.offset
                abs_end = abs_base + slot.size - 1
                regions.append({
                    'interconnect': '',
                    'target': f"  {slot.name}",
                    'instance': slot.module,
                    'base': f"0x{abs_base:08X}",
                    'end': f"0x{abs_end:08X}",
                    'size': _fmt_size(slot.size),
                    'access': '',
                    'type': '',
                })

    def _build_interconnects(self) -> List[Dict[str, Any]]:
        """Build interconnect connectivity table."""
        ics = []
        for ic in self.top.interconnects:
            ic_data = {
                'name': ic.name,
                'type': ic.type,
                'initiators': [],
            }
            for init in ic.initiators:
                ic_data['initiators'].append({
                    'name': init.name,
                    'instance': init.instance or '',
                    'targets': ', '.join(init.target_names),
                })
            ics.append(ic_data)
        return ics

    def _build_hierarchy(self) -> str:
        """Build an ASCII hierarchy tree string."""
        lines = [f"{self.top.name} (top)"]
        self._render_tree(self.top, "", lines)
        return '\n'.join(lines)

    def _render_tree(self, module: Module, prefix: str, lines: List[str]):
        """Recursively render the instance tree."""
        instances = module.instances
        for i, inst in enumerate(instances):
            is_last = (i == len(instances) - 1)
            connector = "└── " if is_last else "├── "
            addr_tag = " [addressable]" if inst.addressable else ""
            cond_tag = f" [if {inst.condition}]" if inst.condition else ""
            rtl_tag = " (RTL)" if inst.is_rtl_module else ""

            lines.append(
                f"{prefix}{connector}{inst.instance_name} : "
                f"{inst.module_name}{rtl_tag}{addr_tag}{cond_tag}"
            )

            child = inst.resolved_module
            if child and child.instances:
                child_prefix = prefix + ("    " if is_last else "│   ")
                self._render_tree(child, child_prefix, lines)
