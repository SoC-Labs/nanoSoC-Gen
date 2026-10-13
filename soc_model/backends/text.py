"""Text-based memory map and hierarchy output backend."""

from typing import List, Optional

from ..model import AddressDecode, Module
from ..utils import write_if_changed
from .base import Backend, BackendResult, GenContext, Phase
from .registry import register_backend


# size formatter shared with docs.py (doc 02 M5) — kept as `_fmt_size` so the
# report-formatting call sites below are unchanged.
from .walk import fmt_size as _fmt_size


@register_backend
class SoCTextBackend(Backend):
    """Generates text-based memory maps and hierarchy."""

    name = "text"
    phase = Phase.REPORT

    def __init__(self, top_module: Optional[Module] = None):
        self.top = top_module

    def run(self, ctx: GenContext) -> BackendResult:
        self.top = ctx.top
        memmap = ctx.reports_dir / f"{ctx.top.name}_memory_map.txt"
        hierarchy = ctx.reports_dir / f"{ctx.top.name}_hierarchy.txt"
        self.generate_memory_map(str(memmap))
        self.generate_hierarchy(str(hierarchy))
        return BackendResult(files=[memmap, hierarchy])

    def generate_memory_map(self, output_path: str):
        """Generate a text-based memory map file."""
        lines = []
        lines.append(f"{'=' * 80}")
        lines.append(f"  {self.top.name} — Memory Map")
        lines.append(f"{'=' * 80}")
        lines.append("")

        for ic in self.top.interconnects:
            lines.append(f"Interconnect: {ic.name} ({ic.type})")
            lines.append(f"{'-' * 80}")
            lines.append(f"  {'Target':<20s} {'Instance':<22s} {'Base':>12s}  {'End':>12s}  {'Size':>10s}  {'Access':<5s} {'Type':<8s}")
            lines.append(f"  {'─' * 20} {'─' * 22} {'─' * 12}  {'─' * 12}  {'─' * 10}  {'─' * 5} {'─' * 8}")

            for t in sorted(ic.targets, key=lambda t: t.base):
                end = t.base + t.size - 1
                inst_str = t.instance or ''
                lines.append(
                    f"  {t.name:<20s} {inst_str:<22s} 0x{t.base:08X}  0x{end:08X}  {_fmt_size(t.size):>10s}  {t.sw_access:<5s} {(t.region_type or ''):8s}"
                )

                # Show sub-regions from child interconnects and address decodes
                if t.instance:
                    inst = self.top.get_instance(t.instance)
                    if inst and inst.resolved_module:
                        self._render_sub_regions(inst.resolved_module, t.base, 1, lines)

            lines.append("")

            # Initiator visibility
            lines.append(f"  Initiator Connectivity:")
            lines.append(f"  {'─' * 76}")
            for init in ic.initiators:
                inst_str = f" ({init.instance})" if init.instance else ""
                tgt_str = ", ".join(init.target_names)
                lines.append(f"    {init.name}{inst_str}: {tgt_str}")
            lines.append("")

        lines.append(f"{'=' * 80}")

        write_if_changed(output_path, '\n'.join(lines) + '\n')

    def generate_hierarchy(self, output_path: str):
        """Generate a text-based component hierarchy file."""
        lines = []
        lines.append(f"{'=' * 60}")
        lines.append(f"  {self.top.name} — Component Hierarchy")
        lines.append(f"{'=' * 60}")
        lines.append("")
        lines.append(f"  {self.top.name} (top)")

        self._render_tree(self.top, "  ", lines)
        lines.append("")

        write_if_changed(output_path, '\n'.join(lines) + '\n')

    def _render_sub_regions(self, module: Module, parent_base: int, depth: int, lines: List[str]):
        """Recursively render sub-regions from child interconnects and address decodes."""
        indent = "  " + "  " * depth
        prefix = "└─ "

        # Child interconnect targets
        for child_ic in module.interconnects:
            for ct in sorted(child_ic.targets, key=lambda x: x.base):
                abs_base = parent_base + ct.base
                abs_end = abs_base + ct.size - 1
                lines.append(
                    f"{indent}{prefix}{ct.name:<{18 - depth * 2}s} {'':22s} 0x{abs_base:08X}  0x{abs_end:08X}  {_fmt_size(ct.size):>10s}  {ct.sw_access:<5s} {(ct.region_type or ''):8s}"
                )
                # Recurse into child instance
                if ct.instance:
                    inst = module.get_instance(ct.instance)
                    if inst and inst.resolved_module:
                        self._render_sub_regions(inst.resolved_module, abs_base, depth + 1, lines)

        # Address decode slots
        if module.address_decode:
            self._render_address_decode_slots(module.address_decode, parent_base, depth, lines)

        # Search addressable child instances for address_decode hierarchies
        if not module.interconnects and not module.address_decode:
            for inst in module.instances:
                if inst.addressable and inst.resolved_module:
                    self._render_sub_regions(inst.resolved_module, parent_base, depth, lines)

    def _render_address_decode_slots(self, ad: AddressDecode, parent_base: int, depth: int, lines: List[str]):
        """Render address decode slots as sub-regions."""
        indent = "  " + "  " * depth
        prefix = "└─ "

        for slot in sorted(ad.slots, key=lambda s: s.offset):
            abs_base = parent_base + slot.offset
            abs_end = abs_base + slot.size - 1
            regmap_tag = ""
            if slot.resolved_register_map:
                regmap_tag = f"  [{slot.resolved_register_map.name}]"
            lines.append(
                f"{indent}{prefix}{slot.name:<{18 - depth * 2}s} {slot.module:<22s} 0x{abs_base:08X}  0x{abs_end:08X}  {_fmt_size(slot.size):>10s}  {'':5s} {'':8s}{regmap_tag}"
            )

            # Show register details if register map is present
            if slot.resolved_register_map:
                reg_indent = "  " + "  " * (depth + 1)
                for reg in slot.resolved_register_map.registers:
                    reg_addr = abs_base + reg.offset
                    lines.append(
                        f"{reg_indent}  0x{reg_addr:08X}  {reg.name:<20s} {reg.access:<4s} {reg.desc}"
                    )

            # Recurse into nested address decode
            if slot.address_decode:
                self._render_address_decode_slots(slot.address_decode, abs_base, depth + 1, lines)

    def _render_tree(self, module: Module, prefix: str, lines: List[str]):
        """Recursively render the instance tree."""
        instances = module.instances
        for i, inst in enumerate(instances):
            is_last = (i == len(instances) - 1)
            connector = "└── " if is_last else "├── "
            addr_tag = " [addressable]" if inst.addressable else ""
            cond_tag = f" [if {inst.condition}]" if inst.condition else ""
            rtl_tag = " (RTL)" if inst.is_rtl_module else ""

            lines.append(f"{prefix}{connector}{inst.instance_name} : {inst.module_name}{rtl_tag}{addr_tag}{cond_tag}")

            child = inst.resolved_module
            if child and child.instances:
                child_prefix = prefix + ("    " if is_last else "│   ")
                self._render_tree(child, child_prefix, lines)
