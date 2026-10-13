# Copyright 2026, SoC Labs (www.soclabs.org)
"""System-completeness rules engine (roadmap doc 06a, validation tier 3).

The semantic validator (:mod:`soc_model.validator`) answers "is this model
*consistent*?". This module answers a different, project-policy question:
"is this model a *complete system*?" — does it have a CPU, something
fetchable at the reset vector, clocks/resets, a debug path, …

Rules are declarative YAML so each project ships its own policy::

    rules:
      - id: cpu-initiator
        level: error                    # error | warning | info
        desc: At least one CPU master on an interconnect
        require: { initiator_module_matches: "(cpu|cm0|m0plus|eth_ss)" }
        hint: Add a CPU subsystem instance and list it as an initiator

Each ``require`` block contains exactly one predicate keyword (below).  The
engine evaluates every rule against a built :class:`~soc_model.model.Module`
and returns checklist entries; failures double as validation messages.

Predicates (one ~10-line check each):

``initiator_module_matches: <regex>``
    Some interconnect initiator whose name, instance name, or resolved
    instance module name matches the regex.
``instance_module_matches: <regex>``
    Some instance (recursive) whose instance/module name matches.
``target_at_address: <int>`` (optional ``region_type:``)
    Some interconnect target window covers the address (and has the region
    type, when given).
``target_role: <str>``
    Some target declares this ``role:`` (e.g. ``bootrom``).
``external_wires: [<glob>, ...]``
    For every glob, some module-boundary wire interface matches
    (fnmatch-style; ``a|b`` alternation supported).
``firmware_regions_resolve: true``
    Every firmware linker-profile region's ``target`` names a real
    interconnect target.
``apb_targets_have_config: true``
    Every ``protocol: apb`` target carries an ``apb_config:``.
``min_targets: {region_type: <str>, count: <int>}``
    At least N targets of the given region type exist.
"""

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from .model import Interconnect, Module

# Bundled default rules (projects override via their own rules file).
_PACKAGE_DIR = Path(__file__).resolve().parent.parent  # nanosoc_gen/
BUNDLED_RULES_DIR = _PACKAGE_DIR / 'lib' / 'rules'
DEFAULT_RULES_FILE = BUNDLED_RULES_DIR / 'nanosoc_completeness.yaml'

_LEVELS = ('error', 'warning', 'info')


@dataclass
class Rule:
    """One completeness rule loaded from the rules YAML."""
    id: str
    level: str = 'error'
    desc: str = ''
    require: Dict[str, Any] = field(default_factory=dict)
    hint: str = ''


@dataclass
class RuleResult:
    """Outcome of evaluating one rule — a checklist entry."""
    rule: Rule
    passed: bool
    detail: str = ''   # what satisfied / why it failed

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.rule.id,
            'level': self.rule.level,
            'desc': self.rule.desc,
            'passed': self.passed,
            'detail': self.detail,
            'hint': self.rule.hint if not self.passed else '',
        }


def load_rules(path) -> List[Rule]:
    """Load rules from a YAML file: ``{rules: [{id, level, require, ...}]}``."""
    data = yaml.safe_load(Path(path).read_text()) or {}
    rules: List[Rule] = []
    for raw in data.get('rules') or []:
        if not isinstance(raw, dict) or not raw.get('id'):
            raise ValueError(f"completeness rule without an id: {raw!r}")
        level = str(raw.get('level', 'error')).lower()
        if level not in _LEVELS:
            raise ValueError(
                f"rule '{raw['id']}': level must be one of {_LEVELS}")
        require = raw.get('require') or {}
        if not isinstance(require, dict) or len(require) < 1:
            raise ValueError(f"rule '{raw['id']}': missing require: block")
        unknown = [k for k in _predicate_keys(require) if k not in _PREDICATES]
        if unknown:
            raise ValueError(
                f"rule '{raw['id']}': unknown predicate(s) {unknown}; "
                f"known: {sorted(_PREDICATES)}")
        rules.append(Rule(id=str(raw['id']), level=level,
                          desc=str(raw.get('desc', '')),
                          require=require, hint=str(raw.get('hint', ''))))
    return rules


def evaluate(module: Module, rules: List[Rule]) -> List[RuleResult]:
    """Evaluate every rule against a built (instance-resolved) module."""
    results = []
    for rule in rules:
        key = _primary_predicate(rule.require)
        passed, detail = _PREDICATES[key](module, rule.require)
        results.append(RuleResult(rule=rule, passed=passed, detail=detail))
    return results


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
# Argument-only keys that qualify a primary predicate rather than name one.
_QUALIFIER_KEYS = {'region_type', 'count'}


def _predicate_keys(require: Dict[str, Any]) -> List[str]:
    return [k for k in require if k not in _QUALIFIER_KEYS]


def _primary_predicate(require: Dict[str, Any]) -> str:
    keys = _predicate_keys(require)
    if len(keys) != 1:
        raise ValueError(
            f"require: block must contain exactly one predicate, got {keys}")
    return keys[0]


def _all_interconnects(module: Module, _seen=None) -> List[Interconnect]:
    """This module's interconnects plus those of resolved gen:True children."""
    if _seen is None:
        _seen = set()
    if id(module) in _seen:
        return []
    _seen.add(id(module))
    out = list(module.interconnects)
    for inst in module.instances:
        if inst.resolved_module is not None:
            out.extend(_all_interconnects(inst.resolved_module, _seen))
    return out


def _all_instances(module: Module, _seen=None):
    """(instance, resolved_module_name) recursively."""
    if _seen is None:
        _seen = set()
    if id(module) in _seen:
        return
    _seen.add(id(module))
    for inst in module.instances:
        yield inst
        if inst.resolved_module is not None:
            for sub in _all_instances(inst.resolved_module, _seen):
                yield sub


def _glob_match(name: str, pattern: str) -> bool:
    """fnmatch glob, with `a|b` alternation."""
    return any(fnmatch.fnmatchcase(name, alt) for alt in pattern.split('|'))


# ---------------------------------------------------------------------------
# predicates — each returns (passed, detail)
# ---------------------------------------------------------------------------
def _p_initiator_module_matches(module, require):
    rx = re.compile(require['initiator_module_matches'], re.IGNORECASE)
    by_name = {i.instance_name: i for i in _all_instances(module)}
    for ic in _all_interconnects(module):
        for ini in ic.initiators:
            names = [ini.name, ini.instance or '']
            inst = by_name.get(ini.instance) if ini.instance else None
            if inst is not None:
                names.append(inst.module_name)
            hit = next((n for n in names if n and rx.search(n)), None)
            if hit:
                return True, f"initiator '{ini.name}' on {ic.name} ({hit})"
    return False, "no interconnect initiator matches"


def _p_instance_module_matches(module, require):
    rx = re.compile(require['instance_module_matches'], re.IGNORECASE)
    for inst in _all_instances(module):
        for n in (inst.instance_name, inst.module_name):
            if n and rx.search(n):
                return True, f"instance '{inst.instance_name}' ({inst.module_name})"
    return False, "no instance matches"


def _p_target_at_address(module, require):
    addr = int(require['target_at_address'])
    want_type = require.get('region_type')
    for ic in _all_interconnects(module):
        for t in ic.targets:
            if t.size and t.base <= addr < t.base + t.size:
                if want_type and (t.region_type or '') != want_type:
                    continue
                return True, (f"'{t.name}' on {ic.name} covers "
                              f"{hex(addr)} ({hex(t.base)}+{hex(t.size)})")
    suffix = f" of region_type {want_type}" if want_type else ''
    return False, f"no target window{suffix} covers {hex(addr)}"


def _p_target_role(module, require):
    role = require['target_role']
    for ic in _all_interconnects(module):
        for t in ic.targets:
            if (t.role or '') == role:
                return True, f"'{t.name}' on {ic.name} has role {role}"
    return False, f"no target declares role: {role}"


def _p_external_wires(module, require):
    patterns = require['external_wires']
    if isinstance(patterns, str):
        patterns = [patterns]
    wires = [i.name for i in module.interfaces if i.type == 'wire']
    missing = [p for p in patterns
               if not any(_glob_match(w, p) for w in wires)]
    if missing:
        return False, f"no boundary wire matches: {', '.join(missing)}"
    return True, f"matched {len(patterns)} pattern(s)"


def _p_firmware_regions_resolve(module, require):
    if not module.firmware:
        return True, "no firmware: section (nothing to resolve)"
    target_names = {t.name for ic in _all_interconnects(module)
                    for t in ic.targets}
    bad = [f"{prof.name}:{reg.target}"
           for prof in module.firmware.linker_profiles
           for reg in prof.regions if reg.target not in target_names]
    if bad:
        return False, f"linker regions name unknown targets: {', '.join(bad)}"
    n = sum(len(p.regions) for p in module.firmware.linker_profiles)
    return True, f"all {n} linker region(s) resolve"


def _p_apb_targets_have_config(module, require):
    bad = [f"{t.name} on {ic.name}"
           for ic in _all_interconnects(module)
           for t in ic.targets
           if t.protocol == 'apb' and not t.apb_config]
    if bad:
        return False, f"apb targets missing apb_config: {', '.join(bad)}"
    return True, "all apb targets carry apb_config"


def _p_min_targets(module, require):
    spec = require['min_targets']
    if not isinstance(spec, dict):       # `min_targets: 3` shorthand
        spec = {'count': spec}
    want_type = spec.get('region_type')
    count = int(spec.get('count', 1))
    n = sum(1 for ic in _all_interconnects(module) for t in ic.targets
            if not want_type or (t.region_type or '') == want_type)
    kind = f"region_type={want_type} " if want_type else ''
    if n >= count:
        return True, f"{n} {kind}target(s) (need {count})"
    return False, f"only {n} {kind}target(s), need {count}"


_PREDICATES: Dict[str, Callable[[Module, Dict[str, Any]], Any]] = {
    'initiator_module_matches': _p_initiator_module_matches,
    'instance_module_matches': _p_instance_module_matches,
    'target_at_address': _p_target_at_address,
    'target_role': _p_target_role,
    'external_wires': _p_external_wires,
    'firmware_regions_resolve': _p_firmware_regions_resolve,
    'apb_targets_have_config': _p_apb_targets_have_config,
    'min_targets': _p_min_targets,
}
