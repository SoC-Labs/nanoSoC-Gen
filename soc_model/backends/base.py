"""The Backend contract: Phase, GenContext, Backend ABC, BackendResult.

Roadmap doc 02, section "The `Backend` contract". A backend declares its
name, the phase it runs in, its `after` deps, and a single `run(ctx)` method.
The registry orders and invokes them. Constructors take nothing — all state
comes from the GenContext — which kills the historical ctor-divergence.

For backward-compat during the incremental migration, the leaf backends keep
their legacy ``SoCXxx(top_module)`` constructor (the optional ``top`` arg here
pre-seeds ``self.top``) and their legacy public methods. The new path is
``SoCXxx().run(ctx)``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..model import Module
from ._jinja import have_jinja, make_env
from ..utils import write_if_changed


class Phase(IntEnum):
    """Coarse ordering buckets. Within a phase, declared `after` deps refine order."""
    REPORT = 10        # html/text/python/sram — read-only analysis
    INTERCONNECT = 20  # ahb (produces RTL the rest reference)
    SUBSYSTEM = 30
    REGISTERS = 40     # discovery + build_info produce RegisterMaps; rdl consumes them
    PACKAGE = 50       # soc_config_pkg
    TOPLEVEL = 60      # toplevel / system / chip
    FIRMWARE = 70
    DOCS = 80
    LINT = 90


@dataclass
class GenContext:
    """Everything a backend may read. Backends MUST treat `top`/`system` as read-only."""
    top: Module
    build_dir: Path
    rtl_dir: Path
    flist_dir: Path
    reports_dir: Path
    system: Optional[Module] = None
    messages: List[Any] = field(default_factory=list)
    opts: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)

    def subdir(self, name: str) -> Path:
        p = Path(self.build_dir) / name
        p.mkdir(parents=True, exist_ok=True)
        return p


@dataclass
class BackendResult:
    files: List[Path] = field(default_factory=list)
    skipped: bool = False
    reason: str = ""


class Backend(ABC):
    name: str = ""                 # unique id, e.g. "sram"
    phase: Phase = Phase.REPORT
    after: tuple = ()              # backend names that must run first
    requires_jinja: bool = False   # base/registry handle the skip-with-warning

    # Backends with templates set this so emit_template can find them.
    template_dir: Optional[Path] = None

    @abstractmethod
    def run(self, ctx: "GenContext") -> "BackendResult":
        ...

    # ---- shared helpers ----
    def emit_template(self, ctx, template_name, out_path, **kw) -> Optional[Path]:
        """Render a Jinja2 template through the shared env and write-if-changed.

        Returns the written path, or None if jinja2 is unavailable.
        """
        env = make_env(self.template_dir)
        if env is None:
            return None
        template = env.get_template(template_name)
        content = template.render(**kw)
        write_if_changed(out_path, content)
        return Path(out_path)

    def enabled(self, ctx: "GenContext") -> bool:
        """Override to gate (e.g. system/chip only with --system-yaml)."""
        return True
