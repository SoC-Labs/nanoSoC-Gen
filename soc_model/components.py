# Copyright 2026, SoC Labs (www.soclabs.org)
"""Component packages — droppable, tweakable IP/subsystems for the configurator.

A *component package* wraps a nanosoc_gen module with the metadata the GUI
needs to drop it in and let a user tweak it safely: a friendly label +
category, and a set of **knobs** (the params worth exposing) each with a
default and — crucially — **hard min/max limits** so memories can't be shrunk
below what their firmware needs.

Packages are declarative YAML, discovered from the bundled
``nanosoc_gen/lib/components/`` plus every ``<lib-dir>/components/``::

    component:
      id: ethernet_subsystem
      module: ethernet_ss_ahb_rmii      # the underlying nanosoc_gen module
      category: subsystem               # subsystem|memory|peripheral|cpu|debug|dma|other
      label: "Ethernet Subsystem (RMII + PTP)"
      desc: "Cortex-M0+ core + MAC + HA1588 PTP, RMII PHY boundary."
      window_size: 512M                 # default address aperture (optional)
      knobs:
        - param: IMEM_RAM_ADDR_W
          label: "Instruction memory"
          kind: addr_w                  # addr_w | bytes | int | enum
          default: 16
          min: 15                       # HARD minimum (32 KB) — cannot go below
          max: 18
          desc: "CPU0 IMEM; the udp_echo app needs ~47 KB."

A package whose ``module`` isn't resolvable in the active lib dirs is reported
``available: false`` — bundled IP definitions light up only for projects that
ship the RTL. Modules with no package fall back to a bare entry (params become
limit-free knobs), so everything in the lib is still droppable.
"""

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

_PACKAGE_DIR = Path(__file__).resolve().parent.parent  # nanosoc_gen/
BUNDLED_COMPONENTS_DIR = _PACKAGE_DIR / 'lib' / 'components'

CATEGORIES = ('subsystem', 'cpu', 'memory', 'peripheral', 'debug', 'dma',
              'other')
_SIZE_SUFFIX = {'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3, 'T': 1024 ** 4}


def parse_size(val: Any) -> Optional[int]:
    """int / 0x-hex / NN[K|M|G|T] -> bytes; None on unparseable."""
    if isinstance(val, bool) or val is None:
        return None
    if isinstance(val, int):
        return val
    s = str(val).strip()
    try:
        if s[:2].lower() == '0x':
            return int(s, 16)
        if s and s[-1].upper() in _SIZE_SUFFIX:
            return int(float(s[:-1]) * _SIZE_SUFFIX[s[-1].upper()])
        return int(s, 10)
    except (ValueError, IndexError):
        return None


def human_size(n: int) -> str:
    """Bytes -> compact human label (e.g. 65536 -> '64 KB')."""
    for suf, mult in (('TB', 1024 ** 4), ('GB', 1024 ** 3),
                      ('MB', 1024 ** 2), ('KB', 1024)):
        if n >= mult and n % mult == 0:
            return '%d %s' % (n // mult, suf)
    return '%d B' % n


@dataclass
class Knob:
    """A tweakable parameter on a component, with hard limits."""
    param: str
    label: str = ''
    kind: str = 'int'              # addr_w | bytes | int | enum
    default: Any = None
    min: Any = None               # HARD lower bound (inclusive)
    max: Any = None               # HARD upper bound (inclusive)
    desc: str = ''
    options: Optional[List[Any]] = None   # enum choices
    unit: str = ''                # display unit override

    # ---- addr_w helpers: the param is an address width N, size = 2^N bytes ---
    def bytes_for(self, n: int) -> int:
        return 1 << int(n)

    def size_choices(self) -> List[Dict[str, Any]]:
        """For an addr_w/bytes knob: selectable {value,label,bytes} >= min."""
        out: List[Dict[str, Any]] = []
        if self.kind == 'addr_w':
            lo = int(self.min) if self.min is not None else int(self.default) - 2
            hi = int(self.max) if self.max is not None else int(self.default) + 3
            for n in range(lo, hi + 1):
                out.append({'value': n, 'bytes': 1 << n,
                            'label': human_size(1 << n)})
        elif self.kind == 'bytes' and self.options:
            for b in self.options:
                bv = parse_size(b)
                out.append({'value': bv, 'bytes': bv, 'label': human_size(bv)})
        elif self.options:
            for o in self.options:
                out.append({'value': o, 'label': str(o)})
        return out

    def to_dict(self) -> Dict[str, Any]:
        d = {'param': self.param, 'label': self.label or self.param,
             'kind': self.kind, 'default': self.default,
             'min': self.min, 'max': self.max, 'desc': self.desc,
             'unit': self.unit}
        ch = self.size_choices()
        if ch:
            d['choices'] = ch
        if self.kind in ('addr_w',) and isinstance(self.default, int):
            d['default_bytes'] = 1 << self.default
        return d


@dataclass
class ComponentSpec:
    """A packaged, droppable IP/subsystem."""
    id: str
    module: str
    category: str = 'other'
    label: str = ''
    desc: str = ''
    window_size: Optional[int] = None
    knobs: List[Knob] = field(default_factory=list)
    packaged: bool = True        # False for bare-module fallback entries

    def knob(self, param: str) -> Optional[Knob]:
        return next((k for k in self.knobs if k.param == param), None)

    def default_params(self) -> Dict[str, Any]:
        return {k.param: k.default for k in self.knobs if k.default is not None}

    def to_dict(self, available: bool = True) -> Dict[str, Any]:
        return {
            'id': self.id, 'module': self.module, 'category': self.category,
            'label': self.label or self.id, 'desc': self.desc,
            'window_size': self.window_size, 'packaged': self.packaged,
            'available': available,
            'knobs': [k.to_dict() for k in self.knobs],
        }


def _knob_from_raw(raw: Dict[str, Any]) -> Knob:
    return Knob(
        param=raw['param'],
        label=str(raw.get('label', '')),
        kind=str(raw.get('kind', 'int')),
        default=raw.get('default'),
        min=raw.get('min'), max=raw.get('max'),
        desc=str(raw.get('desc', '')),
        options=raw.get('options'),
        unit=str(raw.get('unit', '')))


def load_components(dirs) -> Dict[str, ComponentSpec]:
    """Load component packages from each dir's ``components/`` + the bundled
    set. Later dirs win on id collision (project overrides bundled)."""
    specs: Dict[str, ComponentSpec] = {}
    search = [BUNDLED_COMPONENTS_DIR] + [Path(d) / 'components' for d in dirs]
    for d in search:
        d = Path(d)
        if not d.is_dir():
            continue
        for f in sorted(d.glob('*.yaml')):
            if f.name.startswith('.'):
                continue
            try:
                data = yaml.safe_load(f.read_text()) or {}
            except (yaml.YAMLError, OSError):
                continue
            raw = data.get('component') if isinstance(data, dict) else None
            if not isinstance(raw, dict) or not raw.get('id') \
                    or not raw.get('module'):
                continue
            cat = str(raw.get('category', 'other'))
            specs[raw['id']] = ComponentSpec(
                id=str(raw['id']), module=str(raw['module']),
                category=cat if cat in CATEGORIES else 'other',
                label=str(raw.get('label', '')),
                desc=str(raw.get('desc', '')),
                window_size=parse_size(raw.get('window_size')),
                knobs=[_knob_from_raw(k) for k in raw.get('knobs') or []
                       if isinstance(k, dict) and k.get('param')])
    return specs


def bare_component(module_name: str, mdef: Dict[str, Any]) -> ComponentSpec:
    """A fallback package for a lib module with no authored package: int params
    become limit-free knobs; category guessed from name/srams."""
    name = module_name.lower()
    if mdef.get('srams') and ('mem' in name or 'ram' in name
                              or 'region' in name):
        cat = 'memory'
    elif any(t in name for t in ('cpu', 'cm0', 'm0plus', 'core')):
        cat = 'cpu'
    elif 'subsystem' in name or name.endswith('_ss'):
        cat = 'subsystem'
    elif any(t in name for t in ('dap', 'swj', 'debug', 'hostio')):
        cat = 'debug'
    elif 'dma' in name:
        cat = 'dma'
    elif any(t in name for t in ('uart', 'timer', 'spi', 'gpio', 'periph',
                                 'apb', 'qspi', 'phc', 'ipc')):
        cat = 'peripheral'
    else:
        cat = 'other'
    knobs = []
    for pname, pv in (mdef.get('params') or {}).items():
        dflt = pv.get('default') if isinstance(pv, dict) else pv
        if not isinstance(dflt, int):
            continue
        is_aw = pname.upper().endswith('ADDR_W') or pname.upper().endswith('AW')
        knobs.append(Knob(
            param=pname, label=pname, kind='addr_w' if is_aw else 'int',
            default=dflt,
            desc=pv.get('desc', '') if isinstance(pv, dict) else ''))
    return ComponentSpec(id=module_name, module=module_name, category=cat,
                         label=module_name, desc=mdef.get('desc', ''),
                         knobs=knobs, packaged=False)


def check_knob(knob: Knob, value: Any):
    """Enforce a knob's HARD limits. Returns (ok, message). addr_w/int compare
    numerically; below ``min`` is a hard error (the whole point)."""
    if knob.min is None and knob.max is None:
        return True, ''
    try:
        v = int(value)
    except (TypeError, ValueError):
        return True, ''   # non-numeric (e.g. $PARAM) — leave to the validator
    if knob.min is not None and v < int(knob.min):
        if knob.kind == 'addr_w':
            return False, ("%s = %s (%s) is below the minimum %s (%s)"
                           % (knob.param, v, human_size(1 << v),
                              knob.min, human_size(1 << int(knob.min))))
        return False, ("%s = %s is below the minimum %s"
                       % (knob.param, v, knob.min))
    if knob.max is not None and v > int(knob.max):
        return False, ("%s = %s exceeds the maximum %s"
                       % (knob.param, v, knob.max))
    return True, ''
