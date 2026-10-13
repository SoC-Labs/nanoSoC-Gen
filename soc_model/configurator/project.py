# Copyright 2026, SoC Labs (www.soclabs.org)
"""Project adapter — binds the configurator to a concrete repo.

Loaded from ``<root>/configurator.yaml`` (YAML, not TOML — the host python
may predate tomllib and pyyaml is already a hard dependency)::

    top_yaml:  sys_desc/nanosoc_multicore_soc.yaml
    lib_dirs:  [nanosoc_arch_tech/sys_desc, ..., sys_desc]   # the project LAST
    build_dir: build_soc
    rules:     nanosoc_arch_tech/nanosoc_gen/lib/rules/nanosoc_completeness.yaml
    templates: [nanosoc_arch_tech/nanosoc_gen/tests/fixtures]
    env_script: set_env.sh                  # sourced before build stages
    make:
      validate: python3 -m soc_model {top} {libs} --validate-only
      soc:      make -C sys_desc soc_model_fpga
      firmware: cmake --build --preset gcc-m0plus-le --target generate_smoke_bootrom

``lib_dirs`` order matters: a module found in several lib dirs resolves from
the LAST one (soc_model/parser.py), so the project's own sys_desc goes last to
override arch_tech's. Reversed, arch_tech's module silently wins (the QuickStart
rendered a SoC without SWD that way; a connection to a port the winning module
lacks is now a validation error, GEN-BACKLOG 23).

When the file is absent, defaults are inferred: ``sys_desc/*.yaml`` (first
match) as the top, ``sys_desc`` as the lib dir — enough to browse/validate
any nanosoc_gen-shaped repo read-only.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

ADAPTER_FILENAME = 'configurator.yaml'


@dataclass
class ProjectAdapter:
    root: str
    top_yaml: Optional[str] = None          # relative to root
    lib_dirs: List[str] = field(default_factory=list)
    build_dir: str = 'build_soc'
    rules: Optional[str] = None             # rules file (relative ok)
    templates: List[str] = field(default_factory=list)
    env_script: Optional[str] = None        # sourced in build-stage shells
    make: Dict[str, str] = field(default_factory=dict)

    # -- resolved-path helpers -------------------------------------------
    def path(self, rel) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else Path(self.root) / p

    @property
    def drafts_dir(self) -> Path:
        return Path(self.root) / '.configurator' / 'drafts'

    @property
    def abs_lib_dirs(self) -> List[str]:
        return [str(self.path(d)) for d in self.lib_dirs]

    @property
    def abs_rules(self) -> Optional[str]:
        return str(self.path(self.rules)) if self.rules else None

    @property
    def abs_templates(self) -> List[str]:
        return [str(self.path(d)) for d in self.templates]

    def to_dict(self) -> Dict:
        return {
            'root': str(self.root),
            'top_yaml': self.top_yaml,
            'lib_dirs': self.lib_dirs,
            'build_dir': self.build_dir,
            'rules': self.rules,
            'templates': self.templates,
            'env_script': self.env_script,
            'make': dict(self.make),
        }


def load_project(root) -> ProjectAdapter:
    """Load ``<root>/configurator.yaml``, or infer defaults if absent."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f'project root not found: {root}')
    cfg_file = root / ADAPTER_FILENAME
    cfg = {}
    if cfg_file.is_file():
        cfg = yaml.safe_load(cfg_file.read_text()) or {}
        if not isinstance(cfg, dict):
            raise ValueError(f'{cfg_file}: top level is not a mapping')

    top = cfg.get('top_yaml')
    lib_dirs = cfg.get('lib_dirs')
    if top is None or lib_dirs is None:
        sys_desc = root / 'sys_desc'
        if top is None and sys_desc.is_dir():
            cands = sorted(sys_desc.glob('*_soc.yaml')) or \
                sorted(sys_desc.glob('*.yaml'))
            if cands:
                top = str(cands[0].relative_to(root))
        if lib_dirs is None:
            lib_dirs = ['sys_desc'] if sys_desc.is_dir() else []

    return ProjectAdapter(
        root=str(root),
        top_yaml=top,
        lib_dirs=list(lib_dirs or []),
        build_dir=cfg.get('build_dir', 'build_soc'),
        rules=cfg.get('rules'),
        templates=list(cfg.get('templates') or []),
        env_script=cfg.get('env_script'),
        make=dict(cfg.get('make') or {}),
    )
