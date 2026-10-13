"""SRAM location report backend.

Walks the module hierarchy and produces a text report listing every SRAM
instance in the design, including its hierarchy path, size, and depth.
This is intended for ASIC backend flows where every generic SRAM must be
substituted with a technology-specific macro.
"""

from typing import Any, Dict, List, Optional, Tuple

from ..model import Module, ResolvedSram, SramEntry
from ..utils import resolve_param_ref, write_if_changed
from .base import Backend, BackendResult, GenContext, Phase
from .registry import register_backend


def _fmt_size(size: int) -> str:
    """Format a byte size as human-readable.

    NOTE (doc 02 M5): intentionally NOT consolidated with backends/walk.py's
    shared fmt_size — this variant omits the GB branch because SRAM-macro sizes
    are always < 1 GB, whereas the shared one formats multi-GB address windows.
    Merging would change this formatter's contract; reconciling them is a
    reviewed behaviour decision, not part of the byte-identical refactor.
    """
    if size >= 1024 * 1024:
        v = size / (1024 * 1024)
        return f"{v:.0f} MB" if v == int(v) else f"{v:.1f} MB"
    if size >= 1024:
        v = size / 1024
        return f"{v:.0f} KB" if v == int(v) else f"{v:.1f} KB"
    return f"{size} B"


@register_backend
class SoCSramBackend(Backend):
    """Generates a report of all SRAM instances in the design hierarchy."""

    name = "sram"
    phase = Phase.REPORT

    def __init__(self, top_module: Optional[Module] = None):
        # Backward-compat: legacy callers pass top_module; the registry path
        # constructs with no args and feeds top via run(ctx).
        self.top = top_module

    def run(self, ctx: GenContext) -> BackendResult:
        self.top = ctx.top
        out = ctx.reports_dir / f"{ctx.top.name}_sram_report.txt"
        self.generate(str(out))
        return BackendResult(files=[out])

    def generate(self, output_path: str):
        """Generate the SRAM location report."""
        entries: List[Tuple[str, ResolvedSram]] = []
        top_flat = {n: p.default for n, p in self.top.params.items()}
        self._walk(self.top, self.top.name, top_flat, entries)

        lines = []
        lines.append(f"{'=' * 100}")
        lines.append(f"  {self.top.name} — SRAM Location Report")
        lines.append(f"{'=' * 100}")
        lines.append("")

        if not entries:
            lines.append("  No SRAMs declared in the design hierarchy.")
            lines.append("  (Add 'srams:' sections to module YAML files to track SRAM instances.)")
        else:
            lines.append(f"  {'SRAM Name':<24s} {'Hierarchy Path':<48s} {'Depth':>8s}  {'Width':>6s}  {'Size':>10s}  {'Description'}")
            lines.append(f"  {'─' * 24} {'─' * 48} {'─' * 8}  {'─' * 6}  {'─' * 10}  {'─' * 30}")

            for hier_path, sram in entries:
                lines.append(
                    f"  {sram.name:<24s} {hier_path:<48s} {sram.depth:>8d}  {sram.data_width:>4d}-b  {_fmt_size(sram.size_bytes):>10s}  {sram.desc}"
                )

            lines.append("")
            lines.append(f"  {'─' * 100}")
            total_bytes = sum(s.size_bytes for _, s in entries)
            lines.append(f"  Total: {len(entries)} SRAM(s), {_fmt_size(total_bytes)}")

        lines.append("")
        lines.append(f"{'=' * 100}")

        write_if_changed(output_path, '\n'.join(lines) + '\n')

    def _walk(self, module: Module, hier_path: str,
              effective_params: Dict[str, Any],
              entries: List[Tuple[str, ResolvedSram]]):
        """Recursively walk the module hierarchy collecting SRAM entries.

        effective_params contains the module's params with instance-level
        overrides already applied by the caller.
        """
        for sram in module.srams:
            aw = resolve_param_ref(sram.addr_width, effective_params)
            dw = resolve_param_ref(sram.data_width, effective_params)
            resolved = ResolvedSram(
                name=sram.name,
                addr_width=aw if isinstance(aw, int) else 14,
                data_width=dw if isinstance(dw, int) else 32,
                desc=sram.desc,
                addr_unit=sram.unit,
            )
            entries.append((f"{hier_path}.{sram.name}", resolved))

        for inst in module.instances:
            child = inst.resolved_module
            if child:
                child_path = f"{hier_path}.{inst.instance_name}"
                # Build effective params for the child: start from child
                # defaults, then overlay instance overrides resolved against
                # the current module's effective params.
                child_defaults = {n: p.default for n, p in child.params.items()}
                overrides = {}
                for k, v in inst.params.items():
                    overrides[k] = resolve_param_ref(v, effective_params)
                child_effective = {**child_defaults, **overrides}
                self._walk(child, child_path, child_effective, entries)
