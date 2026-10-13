# Copyright 2026, SoC Labs (www.soclabs.org)
"""Headless service layer for the SoC Configurator web GUI (roadmap doc 06a).

Pure Python — **no web-framework imports** — so everything here is exercised
by the normal pytest suite and reusable from CI (``python -m
soc_model.configurator check``). The FastAPI app in
:mod:`soc_model.configurator` is a thin shell over this module.

Editing model (doc 06/06a): the GUI's source of truth is the **raw YAML
dict** (the exact shape the parser loads). This service builds a *transient*
:class:`~soc_model.model.Module` from that dict for validation and
diagramming, and emits YAML by dumping the dict — there is deliberately no
model→YAML serializer.

Validation tiers (each message dict carries ``tier``):

1. ``structural_lint`` — instant dict-level schema checks the builder
   silently tolerates (missing names, bad enums, duplicate names, …).
2. The real :class:`~soc_model.validator.SoCValidator` — identical results
   to ``--validate-only``, so GUI-green == CI-green.
3. :mod:`soc_model.completeness` rules — "is this a complete system?"
   (returned both as messages for failures and as a full checklist).
"""

import copy
import hashlib
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .builder import SoCBuilder
from .completeness import (DEFAULT_RULES_FILE, evaluate, load_rules,
                           _primary_predicate)
from .expand import PresetLibrary, expand_module_dict, make_expander
from .model import Module
from .parser import SoCParser
from . import pad_packs
from .validator import SoCValidator

_PACKAGE_DIR = Path(__file__).resolve().parent.parent  # nanosoc_gen/

# --- structural-lint vocabularies (mirrors builder.py consumption + docs 4b) --
_IFACE_TYPES = {'wire', 'ahb', 'apb', 'axis', 'axis_stream', 'axis_byte',
                'swd', 'gpio', 'dbg_ahb'}
_IFACE_DIRECTIONS = {'in', 'out', 'inout', 'initiator', 'target',
                     'receiver', 'sender'}
_REGION_TYPES = {None, 'memory', 'periph'}
_PROTOCOLS = {'ahb', 'apb'}
# a "$NAME" token anywhere inside a string value (covers pure refs AND
# refs embedded in arithmetic expressions, mirroring utils.resolve_param_ref)
_PARAM_TOK = re.compile(r'\$(\w+)')


def _align_up_int(x: int, align: int) -> int:
    return ((x + align - 1) // align) * align if align > 0 else x


def _msg(tier: int, level: str, category: str, message: str,
         instance: str = '', port: str = '', rule: str = '',
         hint: str = '') -> Dict[str, Any]:
    out = {'tier': tier, 'level': level, 'category': category,
           'instance': instance, 'port': port, 'message': message}
    if rule:
        out['rule'] = rule
    if hint:
        out['hint'] = hint
    return out


class GuiService:
    """One configurator session context: lib dirs + presets + rules."""

    def __init__(self, base_dir, lib_dirs=None, rules_file=None):
        self.base_dir = str(base_dir)
        self.lib_dirs = [str(d) for d in (lib_dirs or [])]
        preset_dirs = ([_PACKAGE_DIR / 'lib' / 'presets'] +
                       [Path(d) / 'presets' for d in self.lib_dirs])
        self.presets = PresetLibrary.from_dirs(preset_dirs)
        rf = Path(rules_file) if rules_file else DEFAULT_RULES_FILE
        self.rules = load_rules(rf) if rf.is_file() else []
        self.rules_file = str(rf)

    # ------------------------------------------------------------------
    # model building
    # ------------------------------------------------------------------
    def _fresh_parser(self) -> SoCParser:
        return SoCParser(self.base_dir, list(self.lib_dirs),
                         expander=make_expander(self.presets))

    def build_model(self, data: Dict[str, Any]
                    ) -> Tuple[Optional[Module], List[Dict[str, Any]]]:
        """Build a transient Module from the dict; exceptions → messages.

        The doc-05 expander is applied first (the parser only expands what it
        loads from files, and this dict never touches the parser).
        """
        msgs: List[Dict[str, Any]] = []
        try:
            expanded = expand_module_dict(
                data if 'module' in data else {'module': data},
                presets=self.presets)
            builder = SoCBuilder(self._fresh_parser())   # fresh: cache by name
            module = builder.build_from_dict(copy.deepcopy(expanded))
            return module, msgs
        except Exception as exc:  # builder/expander raise ValueError on bad input
            msgs.append(_msg(2, 'error', 'build',
                             f'model build failed: {exc}'))
            return None, msgs

    # ------------------------------------------------------------------
    # tier 1 — structural lint (pure dict walking, no model build)
    # ------------------------------------------------------------------
    def structural_lint(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        msgs: List[Dict[str, Any]] = []

        def lint(level, cat, message, instance='', port=''):
            msgs.append(_msg(1, level, cat, message, instance, port))

        if not isinstance(data, dict):
            lint('error', 'schema', 'top level is not a mapping')
            return msgs
        mod = data.get('module', data)
        if not isinstance(mod, dict):
            lint('error', 'schema', "'module:' is not a mapping")
            return msgs
        if data.get('module') is None and 'module' in data:
            lint('error', 'schema', "'module:' is empty")
            return msgs
        if 'module' not in data and not mod.get('name'):
            lint('error', 'schema',
                 "missing top-level 'module:' key (or a module name)")
        if not mod.get('name'):
            lint('error', 'schema', "module has no 'name:'")

        # uses: presets must exist (the expander raises later, lint is nicer)
        uses = mod.get('uses')
        if uses is not None:
            names = [uses] if isinstance(uses, str) else (
                uses if isinstance(uses, list) else None)
            if names is None:
                lint('error', 'schema', "'uses:' must be a string or list")
            else:
                for n in names:
                    if self.presets.get(n) is None:
                        lint('error', 'reference',
                             f"unknown preset '{n}' "
                             f"(available: {', '.join(self.presets.names()) or '(none)'})")

        # interfaces
        seen_if = set()
        for i, iface in enumerate(mod.get('interfaces') or []):
            if not isinstance(iface, dict):
                lint('error', 'schema', f'interfaces[{i}] is not a mapping')
                continue
            name = iface.get('name', '')
            if not name:
                lint('error', 'schema', f'interfaces[{i}] has no name')
            elif name in seen_if:
                lint('error', 'schema', 'duplicate interface name', port=name)
            seen_if.add(name)
            t = iface.get('type', 'wire')
            if t not in _IFACE_TYPES:
                lint('error', 'schema',
                     f"unknown interface type '{t}'", port=name)
            d = iface.get('direction', 'in')
            if d not in _IFACE_DIRECTIONS:
                lint('error', 'schema',
                     f"unknown direction '{d}'", port=name)
            elif t == 'wire' and d in ('initiator', 'target', 'receiver',
                                       'sender'):
                lint('warning', 'schema',
                     f"direction '{d}' on a wire (bus directions are for "
                     f"bus types)", port=name)

        # instances
        seen_inst = set()
        for i, inst in enumerate(mod.get('instances') or []):
            if not isinstance(inst, dict):
                lint('error', 'schema', f'instances[{i}] is not a mapping')
                continue
            name = inst.get('instance_name', '')
            if not name:
                lint('error', 'schema', f'instances[{i}] has no instance_name')
            elif name in seen_inst:
                lint('error', 'schema', 'duplicate instance_name',
                     instance=name)
            seen_inst.add(name)
            has_mod = bool(inst.get('module'))
            has_rtl = bool(inst.get('rtl_module'))
            if not has_mod and not has_rtl:
                lint('error', 'schema',
                     "instance needs 'module:' or 'rtl_module:'",
                     instance=name)
            if has_mod and has_rtl:
                lint('warning', 'schema',
                     "both 'module:' and 'rtl_module:' set — rtl_module wins",
                     instance=name)
            if has_mod and inst.get('interfaces'):
                lint('warning', 'schema',
                     "inline 'interfaces:' are ignored on a 'module:' "
                     "instance (only honoured for rtl_module)",
                     instance=name)

        # interconnects
        for ic in mod.get('interconnects') or []:
            if not isinstance(ic, dict):
                continue
            icn = ic.get('name', '<unnamed>')
            has_auto = 'auto_base' in ic
            seen_t = set()
            tnames = set()
            for t in ic.get('targets') or []:
                if isinstance(t, str):
                    tnames.add(t)
                    continue
                if not isinstance(t, dict):
                    continue
                tn = t.get('name', '')
                if not tn:
                    lint('error', 'schema',
                         f'{icn}: target without a name', instance=icn)
                elif tn in seen_t:
                    lint('error', 'schema',
                         f'{icn}: duplicate target name', port=tn)
                seen_t.add(tn)
                tnames.add(tn)
                if (t.get('region_type') or None) not in _REGION_TYPES:
                    lint('warning', 'schema',
                         f"{icn}.{tn}: unusual region_type "
                         f"'{t.get('region_type')}'", port=tn)
                if str(t.get('protocol', 'ahb')).lower() not in _PROTOCOLS:
                    lint('error', 'schema',
                         f"{icn}.{tn}: unknown protocol "
                         f"'{t.get('protocol')}'", port=tn)
                for fld in ('base', 'size'):
                    v = t.get(fld)
                    if v is None or isinstance(v, int):
                        continue
                    if isinstance(v, str):
                        ok = bool(re.fullmatch(
                            r'(0[xX][0-9a-fA-F_]+|\d+(\.\d+)?\s*[kKmMgGtT]?)',
                            v.strip())) if has_auto else bool(re.fullmatch(
                                r'(0[xX][0-9a-fA-F_]+|\d+)', v.strip()))
                        if not ok and '$' not in v:
                            lint('error', 'schema',
                                 f"{icn}.{tn}: cannot parse {fld}: {v!r}",
                                 port=tn)
                if not has_auto and t.get('base') is None \
                        and t.get('passthrough') is None:
                    lint('error', 'schema',
                         f"{icn}.{tn}: no base: (and the interconnect has "
                         f"no auto_base)", port=tn)
            for ini in ic.get('initiators') or []:
                if not isinstance(ini, dict):
                    continue
                for tref in ini.get('targets') or []:
                    tname = tref if isinstance(tref, str) else (
                        tref.get('name') if isinstance(tref, dict) else None)
                    if tname and tname not in tnames:
                        lint('error', 'reference',
                             f"{icn}: initiator '{ini.get('name')}' "
                             f"references unknown target '{tname}'",
                             instance=icn, port=str(tname))
        return msgs

    # ------------------------------------------------------------------
    # tier-1: unresolved $PARAM linter (idea 06a-#16)
    # ------------------------------------------------------------------
    def param_lint(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Flag ``$NAME`` references that resolve against no known param.

        The nastiest silent failure in the flow: a typo'd ``$PARAM`` is left
        UNCHANGED by ``utils.resolve_param_ref`` and survives into the emitted
        RTL as literal text (a ``$`` in a Verilog port map), so it only blows
        up at elaboration. This walks every string value and reports any
        ``$NAME`` whose name is defined nowhere — plus the ``auto_base``
        literals-only rule the expander enforces. Permissive on scope (the
        union of every ``params:`` block, top + nested + preset-expanded) so it
        only fires on genuine typos, never on a legitimately-scoped ref.
        Warning-level — loud but non-blocking. Pure tier-1 (no build needed).
        """
        msgs: List[Dict[str, Any]] = []
        mod = data.get('module', data) if isinstance(data, dict) else None
        if not isinstance(mod, dict):
            return msgs

        known: set = set()

        def add_params(d):
            p = d.get('params') if isinstance(d, dict) else None
            if isinstance(p, dict):
                known.update(str(k) for k in p.keys())
            elif isinstance(p, list):
                for it in p:
                    if isinstance(it, dict) and it.get('name'):
                        known.add(str(it['name']))

        def collect(o):
            if isinstance(o, dict):
                add_params(o)
                for v in o.values():
                    collect(v)
            elif isinstance(o, list):
                for v in o:
                    collect(v)

        collect(mod)
        # union the preset-injected params the expander would materialise
        try:
            exp = self.expand_preview(data)
            collect(exp.get('module', exp) if isinstance(exp, dict) else exp)
        except Exception:
            pass

        seen: set = set()

        def walk(o, path, inst):
            if isinstance(o, dict):
                ni = o.get('instance_name') or o.get('name') or inst
                ab = o.get('auto_base')
                if isinstance(ab, dict):
                    for fld in ('from', 'align'):
                        v = ab.get(fld)
                        if isinstance(v, str) and '$' in v:
                            msgs.append(_msg(
                                1, 'error', 'reference',
                                f"auto_base.{fld} must be a literal number, "
                                f"not a $-expression: {v!r}", instance=str(ni),
                                hint='auto_base is evaluated before params are '
                                     'resolved — inline the constant'))
                for k, v in o.items():
                    walk(v, '%s.%s' % (path, k), ni)
            elif isinstance(o, list):
                for idx, v in enumerate(o):
                    walk(v, '%s[%d]' % (path, idx), inst)
            elif isinstance(o, str) and '$' in o:
                for m in _PARAM_TOK.finditer(o):
                    name = m.group(1)
                    if name in known:
                        continue
                    key = (str(inst), name, path)
                    if key in seen:
                        continue
                    seen.add(key)
                    msgs.append(_msg(
                        1, 'warning', 'reference',
                        f"unresolved $-reference '${name}' — not a known "
                        f"param; it survives into RTL as literal text",
                        instance=str(inst), port=path.split('.')[-1],
                        hint='check for a typo or a missing params: entry'))

        walk(mod, 'module', '')
        return msgs

    # ------------------------------------------------------------------
    # full validation (tiers 1–3)
    # ------------------------------------------------------------------
    def validate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        messages = self.structural_lint(data)
        messages.extend(self.param_lint(data))
        checklist: List[Dict[str, Any]] = []
        module = None
        if not any(m['level'] == 'error' and m['category'] == 'schema'
                   and not m['instance'] and not m['port']
                   for m in messages):
            module, build_msgs = self.build_model(data)
            messages.extend(build_msgs)
        if module is not None:
            for vm in SoCValidator(module).validate_all():
                messages.append(_msg(2, vm.level, vm.category, vm.message,
                                     vm.instance, vm.port))
            for res in evaluate(module, self.rules):
                entry = res.to_dict()
                if not res.passed:
                    entry['fixable'] = self._rule_fixable(res.rule, data)
                checklist.append(entry)
                if not res.passed:
                    messages.append(_msg(
                        3, res.rule.level, 'completeness',
                        f'{res.rule.desc}: {res.detail}',
                        rule=res.rule.id, hint=res.rule.hint))
        errors = sum(1 for m in messages if m['level'] == 'error')
        warnings = sum(1 for m in messages if m['level'] == 'warning')
        return {
            'ok': errors == 0,
            'errors': errors,
            'warnings': warnings,
            'messages': messages,
            'checklist': checklist,
        }

    # ------------------------------------------------------------------
    # build params / --config-override (doc 06a #9)
    # ------------------------------------------------------------------
    def params_info(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The top module's ``params:`` as a flat list for the overrides panel:
        [{name, default, type, desc}]. Handles dict-form ({NAME:{default,..}}),
        scalar-form ({NAME: 5}) and list-form ([{name,...}])."""
        mod = data.get('module', data) if isinstance(data, dict) else {}
        p = mod.get('params') if isinstance(mod, dict) else None
        out: List[Dict[str, Any]] = []
        if isinstance(p, dict):
            for name, spec in p.items():
                if isinstance(spec, dict):
                    out.append({'name': str(name), 'default': spec.get('default'),
                                'type': spec.get('type'),
                                'desc': spec.get('desc', '')})
                else:
                    out.append({'name': str(name), 'default': spec,
                                'type': None, 'desc': ''})
        elif isinstance(p, list):
            for it in p:
                if isinstance(it, dict) and it.get('name'):
                    out.append({'name': str(it['name']),
                                'default': it.get('default'),
                                'type': it.get('type'),
                                'desc': it.get('desc', '')})
        return out

    @staticmethod
    def override_flags(overrides) -> str:
        """Assemble safe ``--config-override NAME=VALUE`` flags from a dict,
        a list of ``NAME=VALUE`` strings, or a list of {name,value}. Names are
        restricted to ``\\w+`` and values to ``[\\w.-]+`` so the result is safe
        to splice into the build stage's shell command (no injection)."""
        items = []
        if isinstance(overrides, dict):
            items = list(overrides.items())
        elif isinstance(overrides, list):
            for o in overrides:
                if isinstance(o, str) and '=' in o:
                    k, v = o.split('=', 1)
                    items.append((k, v))
                elif isinstance(o, dict) and o.get('name'):
                    items.append((o['name'], o.get('value', '')))
        flags = []
        for k, v in items:
            k = str(k).strip()
            val = str('' if v is None else v).strip()
            if not re.fullmatch(r'\w+', k) or not re.fullmatch(r'[\w.\-]+', val):
                continue
            flags.append('--config-override %s=%s' % (k, val))
        return ' '.join(flags)

    @staticmethod
    def apply_overrides_to_cmd(cmd: str, flags: str) -> str:
        """Splice override flags into a build command at the ``{overrides}``
        slot. Commands without the slot are returned unchanged (overrides only
        take effect where the adapter opted in)."""
        if '{overrides}' in cmd:
            return re.sub(r'\s+', ' ', cmd.replace('{overrides}', flags)).strip()
        return cmd

    # ------------------------------------------------------------------
    # checklist fix-it quick actions (doc 06a #20)
    # ------------------------------------------------------------------
    # Failed completeness rules whose fix is a deterministic, no-new-IP dict
    # mutation. (cpu-initiator / debug-access / uart-present need a real IP
    # choice — those route to the canvas/builder add instead.)
    _FIXABLE_PREDICATES = {'external_wires', 'target_role'}

    @staticmethod
    def _all_targets(mod: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        for ic in mod.get('interconnects') or []:
            if isinstance(ic, dict):
                for t in ic.get('targets') or []:
                    if isinstance(t, dict):
                        out.append(t)
        return out

    def _rule_fixable(self, rule, data: Dict[str, Any]) -> bool:
        """Can this failed rule be auto-fixed by a deterministic dict edit
        (no new IP)? Used to decorate the checklist + guard fix_rule."""
        try:
            pred = _primary_predicate(rule.require)
        except Exception:
            return False
        if pred not in self._FIXABLE_PREDICATES:
            return False
        mod = data.get('module', data) if isinstance(data, dict) else {}
        if not isinstance(mod, dict):
            return False
        if pred == 'target_role':
            role = rule.require.get('target_role')
            ts = self._all_targets(mod)
            return (any(t.get('region_type') == 'memory' for t in ts) or
                    bool(ts)) and not any(t.get('role') == role for t in ts)
        return True   # external_wires: a boundary wire can always be added

    def fix_rule(self, data: Dict[str, Any], rule_id: str) -> Dict[str, Any]:
        """Apply the canonical dict mutation for a failed completeness rule.
        Returns {data, message, validation} — pure. Raises ValueError for an
        unknown rule or one with no automatic fix."""
        work = copy.deepcopy(data if 'module' in data else {'module': data})
        mod = work['module']
        rule = next((r for r in self.rules if r.id == rule_id), None)
        if rule is None:
            raise ValueError("unknown rule '%s'" % rule_id)
        pred = _primary_predicate(rule.require)
        if pred == 'external_wires':
            msg = self._fix_external_wire(mod, rule.require['external_wires'])
        elif pred == 'target_role':
            msg = self._fix_target_role(mod, rule.require['target_role'])
        else:
            raise ValueError("rule '%s' has no automatic fix" % rule_id)
        return {'data': work, 'message': msg, 'validation': self.validate(work)}

    def _fix_external_wire(self, mod: Dict[str, Any], globs) -> str:
        patt = (globs[0] if isinstance(globs, list) and globs else str(globs))
        low = patt.lower()
        if 'clk' in low:
            name, listkey = 'sys_hclk', 'clocks'
            entry = {'name': name, 'source': 'external', 'desc': 'AHB clock'}
        elif 'rst' in low or 'reset' in low:
            name, listkey = 'sys_hresetn', 'resets'
            entry = {'name': name, 'active': 'low', 'source': 'external',
                     'desc': 'AHB reset'}
        else:
            first = patt.split('|')[0].strip('*').strip() or 'sig'
            name = 'sys_' + re.sub(r'[^a-zA-Z0-9_]', '', first)
            listkey, entry = None, None
        ifaces = mod.setdefault('interfaces', [])
        if not any(isinstance(i, dict) and i.get('name') == name
                   for i in ifaces):
            ifaces.append({'name': name, 'type': 'wire', 'direction': 'in',
                           'params': {'WIDTH': 1}})
        if listkey and entry:
            lst = mod.setdefault(listkey, [])
            if not any(isinstance(e, dict) and e.get('name') == name
                       for e in lst):
                lst.append(entry)
        return "added boundary wire '%s'" % name

    def _fix_target_role(self, mod: Dict[str, Any], role: str) -> str:
        for ic in mod.get('interconnects') or []:
            if not isinstance(ic, dict):
                continue
            ts = [t for t in (ic.get('targets') or []) if isinstance(t, dict)]
            cands = [t for t in ts if t.get('region_type') == 'memory'] or ts
            if cands:
                cands[0]['role'] = role
                return "set role: %s on target '%s'" % (
                    role, cands[0].get('name'))
        raise ValueError("no target available to mark role: %s" % role)

    # ------------------------------------------------------------------
    # IRQ map (read-only matrix of concat glue -> NVIC, doc 06a #1)
    # ------------------------------------------------------------------
    _IRQ_PORT_RX = re.compile(r'irq|nvic|\bint\b|interrupt', re.I)

    def irq_map(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Read-only IRQ matrix: every ``glue_logic`` ``concat`` block as a
        bit→source-signal table, plus which instance/port consumes its output
        (so you can see at a glance whether an IRQ actually reaches a CPU's
        NVIC — the cc_periph incident was IRQs wired to the block boundary but
        never concatenated onto the NVIC). Pure dict walk — works on an
        incomplete draft. The editing layer (#1 phase 2) writes back via /dict.
        """
        mod = data.get('module', data) if isinstance(data, dict) else {}
        if not isinstance(mod, dict):
            return {'count': 0, 'concats': []}

        # net (base name, slice stripped) -> [(instance, port), ...]
        consumers: Dict[str, List] = {}
        for inst in mod.get('instances') or []:
            if not isinstance(inst, dict):
                continue
            iname = inst.get('instance_name', '')
            for c in inst.get('connections') or []:
                if isinstance(c, dict) and c.get('conn'):
                    base = str(c['conn']).split('[')[0].strip()
                    consumers.setdefault(base, []).append(
                        (iname, c.get('port', '')))

        concats = []
        for gl in mod.get('glue_logic') or []:
            if not isinstance(gl, dict) or gl.get('type') != 'concat':
                continue
            output = str(gl.get('output', ''))
            obase = output.split('[')[0].strip()
            bitmap, maxbit = {}, -1
            for it in gl.get('inputs') or []:
                if isinstance(it, dict) and 'bit' in it:
                    try:
                        b = int(it['bit'])
                    except (TypeError, ValueError):
                        continue
                    bitmap[b] = it.get('signal')
                    maxbit = max(maxbit, b)
            pad = gl.get('pad_zero_above')
            width = pad if isinstance(pad, int) else (maxbit + 1)
            width = max(width, 0)
            bits = [{'bit': i, 'signal': bitmap.get(i)} for i in range(width)]
            cons = consumers.get(obase) or []
            consumer = None
            if cons:
                inst, port = cons[0]
                consumer = {'instance': inst, 'port': port,
                            'is_irq': bool(self._IRQ_PORT_RX.search(str(port)))}
            concats.append({
                'name': gl.get('name', ''), 'output': output, 'width': width,
                'pad_zero_above': pad, 'mapped': len(bitmap), 'bits': bits,
                'consumer': consumer})
        # IRQ-feeding concats first
        concats.sort(key=lambda c: 0 if (c['consumer']
                     and c['consumer']['is_irq']) else 1)
        return {'count': len(concats), 'concats': concats}

    def set_irq_bit(self, data: Dict[str, Any], output: str, bit: int,
                    signal: Optional[str]) -> Dict[str, Any]:
        """Editing layer for the IRQ matrix (#1 phase 2): set/replace/clear the
        source signal driving one NVIC concat bit. Finds the ``glue_logic``
        ``concat`` whose ``output`` base-name matches ``output`` (same match
        irq_map reads with, so a set→re-read round-trips), then upserts
        ``{bit, signal}`` into its ``inputs`` — a None/'' ``signal`` removes the
        bit's mapping. Raises ValueError for an unknown output or an
        out-of-range/negative bit. Returns {data, output, bit, signal} — pure
        (does not mutate the input)."""
        work = copy.deepcopy(data if 'module' in data else {'module': data})
        mod = work['module']
        obase = str(output).split('[')[0].strip()
        gl = next((g for g in (mod.get('glue_logic') or [])
                   if isinstance(g, dict) and g.get('type') == 'concat'
                   and str(g.get('output', '')).split('[')[0].strip() == obase),
                  None)
        if gl is None:
            raise ValueError("no concat with output '%s'" % output)
        try:
            b = int(bit)
        except (TypeError, ValueError):
            raise ValueError("bit '%s' is not an integer" % bit)
        if b < 0:
            raise ValueError("bit %d is negative" % b)
        pad = gl.get('pad_zero_above')
        maxbit = -1
        for it in gl.get('inputs') or []:
            if isinstance(it, dict) and 'bit' in it:
                try:
                    maxbit = max(maxbit, int(it['bit']))
                except (TypeError, ValueError):
                    continue
        width = pad if isinstance(pad, int) else (maxbit + 1)
        if isinstance(pad, int) and b >= pad:
            raise ValueError("bit %d out of range (width %d)" % (b, pad))
        def _bit_of(it):
            try:
                return int(it.get('bit'))
            except (TypeError, ValueError):
                return None
        sig = signal if signal not in (None, '') else None
        inputs = [it for it in (gl.get('inputs') or [])
                  if isinstance(it, dict) and 'bit' in it
                  and _bit_of(it) is not None and _bit_of(it) != b]
        if sig is not None:
            inputs.append({'bit': b, 'signal': sig})
        inputs.sort(key=_bit_of)
        gl['inputs'] = inputs
        return {'data': work, 'output': output, 'bit': b, 'signal': sig}

    # ------------------------------------------------------------------
    # diagram (layout-neutral JSON for the curated SVG renderer)
    # ------------------------------------------------------------------
    _KIND_RULES = [
        ('cpu',   re.compile(r'cpu|cm0|m0plus|eth_ss', re.I)),
        ('debug', re.compile(r'dap|swj|debug|hostio', re.I)),
        ('dma',   re.compile(r'dma', re.I)),
    ]

    def _classify(self, *names) -> str:
        for kind, rx in self._KIND_RULES:
            if any(n and rx.search(n) for n in names):
                return kind
        return 'other'

    def deepdive_html(self, data: Dict[str, Any]) -> str:
        """Full D3 connectivity HTML for a draft — the always-correct deep-dive
        fallback (doc 06a §10.4 / idea #5). The curated SVG diagram is
        heuristic; this renders the real SoCVisualizer page from the built
        model. Raises ValueError if the model can't build."""
        module, msgs = self.build_model(data)
        if module is None:
            raise ValueError('; '.join(m['message'] for m in msgs)
                             or 'model build failed')
        import os
        import tempfile
        from .backends.html import SoCVisualizer
        fd, path = tempfile.mkstemp(suffix='.html', prefix='deepdive_')
        try:
            os.close(fd)
            SoCVisualizer(module).generate_html(path)
            with open(path) as f:
                return f.read()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def diagram(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Derive the demo-GUI-style layout: initiator row, bus spines,
        target grid grouped by region_type. Validation badges included so
        the renderer can dot the blocks."""
        module, build_msgs = self.build_model(data)
        if module is None:
            return {'ok': False, 'messages': build_msgs}
        badge_src = self.validate(data)
        badges: Dict[str, str] = {}
        for m in badge_src['messages']:
            key = m['instance'] or m['port']
            if key and m['level'] in ('error', 'warning'):
                # error outranks warning
                if badges.get(key) != 'error':
                    badges[key] = m['level']

        by_name = {i.instance_name: i for i in module.instances}
        spines = []
        for ic in module.interconnects:
            initiators = []
            for ini in ic.initiators:
                inst = by_name.get(ini.instance) if ini.instance else None
                initiators.append({
                    'name': ini.name,
                    'instance': ini.instance,
                    'module': inst.module_name if inst else None,
                    'kind': self._classify(ini.name, ini.instance,
                                           inst.module_name if inst else None),
                    'passthrough': ini.passthrough,
                    'reach': len(ini.targets),
                    'badge': badges.get(ini.instance or ini.name, ''),
                })
            groups: Dict[str, List[Dict[str, Any]]] = {}
            for t in ic.targets:
                group = ('subordinate' if t.subordinate_bus else
                         (t.region_type or 'other'))
                inst = by_name.get(t.instance) if t.instance else None
                groups.setdefault(group, []).append({
                    'name': t.name,
                    'instance': t.instance,
                    'module': inst.module_name if inst else None,
                    'base': t.base, 'size': t.size,
                    'base_hex': hex(t.base),
                    'end_hex': hex(t.base + max(t.size, 1) - 1),
                    'region_type': t.region_type,
                    'role': t.role,
                    'protocol': t.protocol,
                    'drill': bool(inst and inst.resolved_module
                                  and inst.resolved_module.gen),
                    'badge': badges.get(t.instance or t.name, ''),
                })
            spines.append({'name': ic.name, 'type': ic.type,
                           'initiators': initiators, 'groups': groups})
        # missing required blocks -> ghost placeholders
        ghosts = [c for c in badge_src['checklist'] if not c['passed']]
        return {
            'ok': True,
            'module': module.name,
            'desc': module.desc,
            'spines': spines,
            'ghosts': ghosts,
            'counts': {'errors': badge_src['errors'],
                       'warnings': badge_src['warnings']},
        }

    # ------------------------------------------------------------------
    # catalogs (palette / dropdown data)
    # ------------------------------------------------------------------
    def catalogs(self, template_dirs=None) -> Dict[str, Any]:
        def stems(*dirs):
            out = []
            for d in dirs:
                d = Path(d)
                if d.is_dir():
                    out.extend(sorted(p.stem for p in d.glob('*.yaml')
                                      if not p.name.startswith('.')))
            return sorted(set(out))

        lib = _PACKAGE_DIR / 'lib'
        parser = self._fresh_parser()
        modules = sorted({Path(f).stem for f in parser.list_module_files()})
        templates = []
        for d in (template_dirs or []):
            d = Path(d)
            if d.is_dir():
                templates.extend(sorted(str(p) for p in d.glob('*.yaml')))
        return {
            'modules': modules,
            'presets': self.presets.names(),
            # bundled example + every pad pack ($NANOSOC_PAD_TECH_DIR)
            'pad_techs': stems(lib / 'pad_tech',
                               *[d / 'pad_tech' for d in pad_packs.pack_dirs()]),
            'pin_maps': stems(lib / 'pin_map',
                              *[d / 'pin_map' for d in pad_packs.pack_dirs()]),
            'chip_boundaries': stems(lib / 'chip_boundary'),
            'fpga_profiles': stems(lib / 'fpga_profile'),
            'rules': [r.id for r in self.rules],
            'templates': templates,
            'shadows': self.lib_shadow_report(parser)['shadows'],
        }

    def lib_shadow_report(self, parser=None) -> Dict[str, Any]:
        """Lib-dir shadowing auditor (doc 06a #17): module name / filename
        stems claimed by more than one file across the lib dirs. Discovery is
        silent last-writer-wins, so with several overlapping lib dirs an edit
        to the wrong copy is a mystery — this names every collision and which
        file actually wins. Paths shown relative to base_dir where possible."""
        p = parser or self._fresh_parser()
        base = Path(self.base_dir).resolve()

        def rel(f):
            try:
                return str(Path(f).resolve().relative_to(base))
            except ValueError:
                return str(f)

        shadows = []
        for key, files in sorted(p.module_shadows().items()):
            rels = [rel(f) for f in files]
            shadows.append({'key': key, 'files': rels,
                            'winner': rels[-1], 'shadowed': rels[:-1]})
        return {'count': len(shadows), 'shadows': shadows}

    # ------------------------------------------------------------------
    # build freshness (diff-vs-built staleness, doc 06a #3)
    # ------------------------------------------------------------------
    _MANIFEST_REL = 'build_info/configurator_build.yaml'

    @staticmethod
    def _sha256_text(s: str) -> str:
        return hashlib.sha256(s.encode('utf-8', 'replace')).hexdigest()

    def record_build_manifest(self, top_path, lib_dirs, build_dir,
                              now: Optional[float] = None) -> Dict[str, Any]:
        """Stamp ``build_dir/build_info/configurator_build.yaml`` with the top
        YAML's hash + the build time, so a later edit (to the top OR any lib
        YAML) can be detected. Called by the app after a successful ``soc``
        stage. ``now`` is injectable for deterministic tests."""
        top = Path(top_path)
        manifest = {
            'schema': 1,
            'top_yaml': str(top_path),
            'top_sha256': self._sha256_text(top.read_text())
            if top.is_file() else '',
            'built_epoch': float(now if now is not None else time.time()),
        }
        out = Path(build_dir) / self._MANIFEST_REL
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(yaml.dump(manifest, sort_keys=False))
        return manifest

    def build_freshness(self, top_path, lib_dirs, build_dir) -> Dict[str, Any]:
        """Is ``build_dir`` still in step with the current YAML? Compares the
        recorded top hash (catches top edits regardless of mtime) and the
        mtimes of every ``*.yaml`` under the lib dirs against the build time
        (catches the lib-dir region edits ``set_env.sh``'s top-mtime auto-regen
        silently misses — doc 06a #3 / USER_GUIDE §8). States: ``fresh`` |
        ``stale`` | ``unknown`` (no/unreadable manifest)."""
        out = Path(build_dir) / self._MANIFEST_REL
        if not out.is_file():
            return {'state': 'unknown',
                    'reason': 'no build manifest yet (run a build)'}
        try:
            man = yaml.safe_load(out.read_text()) or {}
        except Exception:
            return {'state': 'unknown', 'reason': 'unreadable build manifest'}

        top = Path(top_path)
        cur_top = self._sha256_text(top.read_text()) if top.is_file() else ''
        if man.get('top_sha256') and cur_top != man['top_sha256']:
            return {'state': 'stale',
                    'reason': 'top YAML changed since the build',
                    'changed': [str(top_path)]}

        built = float(man.get('built_epoch') or 0)
        top_resolved = top.resolve() if top.exists() else None
        changed: List[str] = []
        for d in lib_dirs or []:
            dp = Path(d)
            if not dp.is_dir():
                continue
            for y in dp.rglob('*.yaml'):
                if top_resolved is not None and y.resolve() == top_resolved:
                    continue          # hash-checked above; skip mtime noise
                try:
                    if y.stat().st_mtime > built:
                        changed.append(str(y))
                except OSError:
                    pass
        if changed:
            return {'state': 'stale',
                    'reason': '%d lib YAML(s) edited since the build'
                              % len(changed),
                    'changed': sorted(changed)[:20]}
        return {'state': 'fresh', 'reason': 'build matches current YAML',
                'built_epoch': built}

    # ------------------------------------------------------------------
    # YAML I/O (path-guarded)
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_path(path, root) -> Path:
        root = Path(root).resolve()
        p = (root / path).resolve() if not Path(path).is_absolute() \
            else Path(path).resolve()
        try:
            p.relative_to(root)
        except ValueError:
            raise PermissionError(
                f"refusing path outside the project root: {p}")
        return p

    def load_yaml(self, path, root) -> Dict[str, Any]:
        p = self._safe_path(path, root)
        data = yaml.safe_load(p.read_text())
        if not isinstance(data, dict):
            raise ValueError(f'{p}: top level is not a mapping')
        return data

    @staticmethod
    def to_yaml(data: Dict[str, Any]) -> str:
        return yaml.dump(data, sort_keys=False, default_flow_style=False,
                         width=100)

    def save_yaml(self, data: Dict[str, Any], path, root) -> str:
        p = self._safe_path(path, root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_yaml(data))
        return str(p)

    def expand_preview(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Doc-05 canonical (desugared) form of the dict."""
        return expand_module_dict(
            data if 'module' in data else {'module': data},
            presets=self.presets)

    # ------------------------------------------------------------------
    # from-scratch seed (the Builder wizard's "New SoC")
    # ------------------------------------------------------------------
    @staticmethod
    def new_soc(name: str, clk_hz: int = 50000000,
                addr_w: int = 32, data_w: int = 32) -> Dict[str, Any]:
        """A minimal valid skeleton to build on: clock+reset boundary wires,
        one CPU master boundary port, one empty AHB-Lite interconnect with
        that CPU as its initiator. add_component() then populates it —
        memories first satisfy the completeness rules step by step.
        """
        ic_name = '%s_ahb_interconnect' % name
        return {'module': {
            'name': name,
            'gen': True,
            'desc': 'Generated by the SoC Configurator builder.',
            'params': {
                'SYS_CLK_FREQ_HZ': {'type': 'int', 'default': int(clk_hz),
                                    'desc': 'System clock frequency'},
                'SYS_ADDR_W': {'type': 'int', 'default': int(addr_w),
                               'desc': 'System address width'},
                'SYS_DATA_W': {'type': 'int', 'default': int(data_w),
                               'desc': 'System data width'},
            },
            'clocks': [{'name': 'sys_hclk', 'source': 'external',
                        'desc': 'AHB clock'}],
            'resets': [{'name': 'sys_hresetn', 'active': 'low',
                        'source': 'external', 'desc': 'AHB reset'}],
            'interfaces': [
                {'name': 'sys_hclk', 'type': 'wire', 'direction': 'in',
                 'params': {'WIDTH': 1}},
                {'name': 'sys_hresetn', 'type': 'wire', 'direction': 'in',
                 'params': {'WIDTH': 1}},
                {'name': 'cpu_m', 'type': 'ahb', 'direction': 'initiator',
                 'params': {'ADDR_WIDTH': '$SYS_ADDR_W',
                            'DATA_WIDTH': '$SYS_DATA_W'},
                 'desc': 'CPU AHB master'},
            ],
            'instances': [],
            'interconnects': [{
                'name': ic_name, 'gen': True, 'type': 'ahb_lite',
                'params': {'SYS_ADDR_W': '$SYS_ADDR_W',
                           'SYS_DATA_W': '$SYS_DATA_W'},
                'connections': [
                    {'port': 'sys_hclk', 'conn': 'sys_hclk'},
                    {'port': 'sys_hresetn', 'conn': 'sys_hresetn'},
                ],
                'targets': [],
                'initiators': [{'name': 'cpu_m', 'targets': []}],
            }],
        }}

    # ------------------------------------------------------------------
    # component-level editing (the Builder panel's add/remove)
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # component catalog (droppable IP/subsystems, doc 06a)
    # ------------------------------------------------------------------
    def components(self) -> List[Dict[str, Any]]:
        """The droppable-component catalog: authored packages (with knobs +
        hard limits) for modules present in the lib dirs, plus a bare-module
        fallback for everything else. Grouped/ordered by category for the
        palette. Unavailable packages (module not in this project) are
        flagged but still listed so the IP library is discoverable."""
        from . import components as C
        parser = self._fresh_parser()
        avail = {Path(f).stem for f in parser.list_module_files()}
        pkgs = C.load_components(self.lib_dirs)
        covered = {s.module for s in pkgs.values()}
        out: List[Dict[str, Any]] = []
        for spec in pkgs.values():
            out.append(spec.to_dict(available=spec.module in avail))
        for mname in sorted(avail):
            if mname in covered:
                continue
            raw = parser.parse_module(mname)
            if not raw:
                continue
            mdef = raw.get('module', raw)
            out.append(C.bare_component(mname, mdef).to_dict(available=True))
        order = {c: i for i, c in enumerate(C.CATEGORIES)}
        out.sort(key=lambda c: (order.get(c['category'], 99),
                                0 if c['available'] else 1,
                                c['label'].lower()))
        return out

    def module_spec(self, module_name: str):
        """Public: (expanded module dict, package spec) for a library module.

        The same resolution update_component uses: parse the module from the
        lib dirs, expand presets, then pick the authored package (or a bare
        one). ``mdef`` is ``{}`` when the module is not in the lib dirs.
        """
        raw = self._fresh_parser().parse_module(module_name)
        mdef = raw.get('module', raw) if raw else {}
        if raw:
            mdef = expand_module_dict({'module': mdef},
                                      presets=self.presets)['module']
        return mdef, self._spec_for(module_name, mdef)

    def _spec_for(self, module_name: str, mdef: Dict[str, Any]):
        """The authored package whose module == module_name, else a bare one.
        (If several packages target the same module, first by id wins.)"""
        from . import components as C
        for spec in C.load_components(self.lib_dirs).values():
            if spec.module == module_name:
                return spec
        return C.bare_component(module_name, mdef)

    def add_component(self, data: Dict[str, Any], module_name: str,
                      instance_name: Optional[str] = None,
                      ic_name: Optional[str] = None,
                      size: Optional[int] = None,
                      base: Optional[int] = None,
                      params: Optional[Dict[str, Any]] = None,
                      component_id: Optional[str] = None) -> Dict[str, Any]:
        """Add an instance of a module with SMART default wiring.

        Looks the module up in the lib dirs, auto-wires clk/reset boundary
        wires by name heuristic, creates an addressable target window at the
        next free aligned slot of the chosen (default: first) interconnect,
        grants every initiator visibility, and reports which ports it could
        NOT wire (those surface as validation messages for the Compose detail
        editor).

        ``component_id`` selects an authored package (for generic-named
        catalog entries that map to a real module); otherwise the package is
        found by module. ``params`` are knob overrides applied to the instance
        and validated against the package's HARD min/max limits — a value
        below a knob's minimum (e.g. shrinking a memory below its floor) is a
        ValueError, not a silent clamp. Returns
        {data, instance, target, wired, unwired, applied_params} — pure.
        """
        from . import components as C
        work = copy.deepcopy(data if 'module' in data else {'module': data})
        mod = work['module']
        raw = self._fresh_parser().parse_module(module_name)
        if not raw:
            raise ValueError("unknown module '%s' (not found in lib dirs)"
                             % module_name)
        mdef = raw.get('module', raw)
        mdef = expand_module_dict({'module': mdef},
                                  presets=self.presets)['module']

        # resolve the package (for knobs + limits) — by id if given, else module
        spec = None
        if component_id:
            spec = C.load_components(self.lib_dirs).get(component_id)
        if spec is None:
            spec = self._spec_for(module_name, mdef)

        # merge knob defaults with caller overrides; ENFORCE hard limits
        applied = dict(spec.default_params())
        for k, v in (params or {}).items():
            applied[k] = v
        for pname, val in applied.items():
            knob = spec.knob(pname)
            if knob is not None:
                ok, msg = C.check_knob(knob, val)
                if not ok:
                    raise ValueError("hard limit: " + msg)

        existing = set()
        for i in mod.get('instances') or []:
            if isinstance(i, dict) and i.get('instance_name'):
                existing.add(i['instance_name'])
        stem = instance_name or ('u_' + module_name)
        name, n = stem, 0
        while name in existing:
            n += 1
            name = '%s_%d' % (stem, n)

        # clk/reset wiring: match the component's wire inputs onto the top's
        # boundary wires by name class.
        top_wires = [i.get('name', '') for i in (mod.get('interfaces') or [])
                     if isinstance(i, dict)
                     and i.get('type', 'wire') == 'wire'
                     and i.get('direction', 'in') == 'in']

        def find_top(*pats):
            for pat in pats:           # priority order
                for w in top_wires:
                    if pat in w.lower():
                        return w
            return None

        top_clk = find_top('hclk', 'fclk', 'clk')
        top_rst = find_top('hresetn', 'resetn', 'rst')
        conns: List[Dict[str, str]] = []
        slave_port = None
        for i in mdef.get('interfaces') or []:
            if not isinstance(i, dict):
                continue
            nm = i.get('name', '')
            ty = i.get('type', 'wire')
            dirn = i.get('direction', 'in')
            lnm = nm.lower()
            if ty == 'wire' and dirn == 'in':
                if ('clk' in lnm) and top_clk:
                    conns.append({'port': nm, 'conn': top_clk})
                    continue
                if ('reset' in lnm or 'rst' in lnm) and top_rst:
                    conns.append({'port': nm, 'conn': top_rst})
                    continue
            if ty in ('ahb', 'apb') and dirn == 'target' \
                    and slave_port is None:
                slave_port = nm

        # target window on the chosen interconnect
        ics = [i for i in (mod.get('interconnects') or [])
               if isinstance(i, dict)]
        ic = next((i for i in ics if i.get('name') == ic_name),
                  ics[0] if ics else None)
        region = 'memory' if mdef.get('srams') else 'periph'
        # window size: caller arg > primary addr_w knob (memory) > package
        # window_size > default. The knob keeps the decode window in step with
        # the tweaked memory depth.
        if size is None:
            prim = next((k for k in spec.knobs if k.kind == 'addr_w'
                         and k.param in applied), None)
            if region == 'memory' and prim is not None:
                try:
                    size = 1 << int(applied[prim.param])
                except (TypeError, ValueError):
                    size = None
            if size is None and spec.window_size:
                size = spec.window_size
        size = 0x1000 if size is None else int(size)

        target_name = None
        if ic is not None and slave_port:
            tstem = (spec.id if spec.packaged else module_name).replace('-', '_')
            tnames = {t.get('name') for t in ic.get('targets') or []
                      if isinstance(t, dict)}
            k = 0
            target_name = '%s_%d' % (tstem, k)
            while target_name in tnames:
                k += 1
                target_name = '%s_%d' % (tstem, k)
            if base is None:
                ends = [t['base'] + t['size']
                        for t in ic.get('targets') or []
                        if isinstance(t, dict)
                        and isinstance(t.get('base'), int)
                        and isinstance(t.get('size'), int)]
                cur = max(ends) if ends else 0
                align = max(size, 0x1000)
                base = _align_up_int(cur, align)
            ic.setdefault('targets', []).append({
                'name': target_name, 'instance': name,
                'base': int(base), 'size': size,
                'sw_access': 'rwx' if region == 'memory' else 'rw',
                'region_type': region,
            })
            conns.append({'port': slave_port,
                          'conn': '%s.%s' % (ic.get('name'), target_name)})
            for ini in ic.get('initiators') or []:
                if isinstance(ini, dict):
                    ini.setdefault('targets', []).append(
                        {'name': target_name})

        inst = {'instance_name': name, 'module': module_name,
                'addressable': bool(target_name), 'connections': conns}
        # apply tweaked knob params to the instance (overrides module defaults)
        knob_params = {p: v for p, v in applied.items()
                       if spec.knob(p) is not None and v is not None}
        if knob_params:
            inst['params'] = knob_params
        mod.setdefault('instances', []).append(inst)
        wired = [c['port'] for c in conns]
        unwired = [i.get('name') for i in mdef.get('interfaces') or []
                   if isinstance(i, dict) and i.get('name')
                   and i.get('name') not in wired]
        return {'data': work, 'instance': name, 'target': target_name,
                'wired': wired, 'unwired': unwired, 'applied_params': applied}

    def remove_component(self, data: Dict[str, Any],
                         instance_name: str) -> Dict[str, Any]:
        """Remove an instance plus its target windows + initiator refs."""
        work = copy.deepcopy(data if 'module' in data else {'module': data})
        mod = work['module']
        insts = [i for i in (mod.get('instances') or [])
                 if isinstance(i, dict)]
        if not any(i.get('instance_name') == instance_name for i in insts):
            raise ValueError("no instance named '%s'" % instance_name)
        mod['instances'] = [i for i in (mod.get('instances') or [])
                            if not (isinstance(i, dict)
                                    and i.get('instance_name')
                                    == instance_name)]
        removed: List[str] = []
        for ic in mod.get('interconnects') or []:
            if not isinstance(ic, dict):
                continue
            keep = []
            for t in ic.get('targets') or []:
                if isinstance(t, dict) and t.get('instance') == instance_name:
                    removed.append(t.get('name'))
                else:
                    keep.append(t)
            ic['targets'] = keep
            for ini in ic.get('initiators') or []:
                if isinstance(ini, dict):
                    ini['targets'] = [
                        x for x in ini.get('targets') or []
                        if (x.get('name') if isinstance(x, dict) else x)
                        not in removed]
        return {'data': work, 'removed_targets': removed}

    def update_component(self, data: Dict[str, Any], instance_name: str,
                         params: Dict[str, Any]) -> Dict[str, Any]:
        """Re-tweak an existing instance's knob params in place.

        Merges ``params`` over the instance's current knob values, ENFORCES
        the package's HARD min/max limits (a below-floor memory size is a
        ValueError, not a clamp), writes the knob params back onto the
        instance, and resizes its address window to track the primary
        ``addr_w`` knob — the canvas' inline size control uses this instead of
        a remove+re-add (which would drop manual wiring). Returns
        {data, instance, target, size, applied_params} — pure."""
        from . import components as C
        work = copy.deepcopy(data if 'module' in data else {'module': data})
        mod = work['module']
        inst = next((i for i in (mod.get('instances') or [])
                     if isinstance(i, dict)
                     and i.get('instance_name') == instance_name), None)
        if inst is None:
            raise ValueError("no instance named '%s'" % instance_name)
        module_name = inst.get('module') or inst.get('rtl_module') or ''
        raw = self._fresh_parser().parse_module(module_name)
        mdef = raw.get('module', raw) if raw else {}
        if raw:
            mdef = expand_module_dict({'module': mdef},
                                      presets=self.presets)['module']
        spec = self._spec_for(module_name, mdef)

        applied = dict(spec.default_params())
        applied.update(inst.get('params') or {})
        for k, v in (params or {}).items():
            applied[k] = v
        for pname, val in applied.items():
            knob = spec.knob(pname)
            if knob is not None:
                ok, msg = C.check_knob(knob, val)
                if not ok:
                    raise ValueError("hard limit: " + msg)

        knob_params = {p: v for p, v in applied.items()
                       if spec.knob(p) is not None and v is not None}
        if knob_params:
            inst['params'] = knob_params
        elif 'params' in inst:
            del inst['params']

        # resize this instance's window from the primary addr_w knob
        region = 'memory' if mdef.get('srams') else 'periph'
        new_size = None
        prim = next((k for k in spec.knobs if k.kind == 'addr_w'
                     and k.param in applied), None)
        if region == 'memory' and prim is not None:
            try:
                new_size = 1 << int(applied[prim.param])
            except (TypeError, ValueError):
                new_size = None
        target_name = None
        if new_size is not None:
            for ic in mod.get('interconnects') or []:
                if not isinstance(ic, dict):
                    continue
                for t in ic.get('targets') or []:
                    if isinstance(t, dict) \
                            and t.get('instance') == instance_name:
                        t['size'] = new_size
                        target_name = t.get('name')
        return {'data': work, 'instance': instance_name,
                'target': target_name, 'size': new_size,
                'applied_params': knob_params}

    # ------------------------------------------------------------------
    # memory map (the address-ruler view)
    # ------------------------------------------------------------------
    def memory_map(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Per-interconnect address windows, sorted, with overlap marking —
        feeds the memory-map ruler panel."""
        module, msgs = self.build_model(data)
        if module is None:
            return {'ok': False, 'messages': msgs, 'interconnects': []}
        ics = []
        for ic in module.interconnects:
            entries = []
            for t in sorted((t for t in ic.targets if t.size),
                            key=lambda t: t.base):
                entries.append({
                    'name': t.name, 'instance': t.instance,
                    'base': t.base, 'size': t.size,
                    'end': t.base + t.size - 1,
                    'base_hex': hex(t.base), 'size_hex': hex(t.size),
                    'end_hex': hex(t.base + t.size - 1),
                    'region_type': t.region_type, 'role': t.role,
                    'sw_access': t.sw_access, 'overlaps': [],
                })
            for i, a in enumerate(entries):
                for b in entries[i + 1:]:
                    if (a['base'] < b['base'] + b['size']
                            and b['base'] < a['base'] + a['size']):
                        a['overlaps'].append(b['name'])
                        b['overlaps'].append(a['name'])
            span = ({'lo': entries[0]['base'],
                     'hi': max(e['end'] for e in entries)}
                    if entries else {'lo': 0, 'hi': 0})
            ics.append({'name': ic.name, 'targets': entries, 'span': span})
        return {'ok': True, 'interconnects': ics}

    # ------------------------------------------------------------------
    # field hints / tooltips (backlog #4)
    # ------------------------------------------------------------------
    @staticmethod
    def field_hints() -> Dict[str, str]:
        """Human-readable descriptions for common YAML field names.

        Used by the Compose panel to populate ``title`` attributes on form
        fields so users understand what each key does without leaving the GUI.
        Returns a flat dict mapping field name -> one-line description.
        """
        return {
            'name': 'Unique identifier for this module or interconnect target (no spaces)',
            'base': 'Base address in hex (auto-assigned if the interconnect uses auto_base)',
            'size': 'Address space size as a power of 2 (e.g. 0x1000 for 4 KB, 0x10000 for 64 KB)',
            'module': 'Module name to instantiate — must exist in a lib_dir (see the catalog)',
            'instance_name': 'Instance label used in port connections and the address map',
            'region_type': 'memory: RAM/ROM-style; periph: memory-mapped registers',
            'sw_access': 'Software permissions: rwx (code+data RAM), rw (regs), rx (ROM), ro/r/w',
            'protocol': 'Bus protocol for this target: ahb (native) or apb (bridged)',
            'role': 'Semantic tag used by completeness rules (e.g. boot-rom, heap)',
            'instance': 'Instance name whose slave port is routed to this target window',
            'desc': 'Human-readable description embedded in generated RTL and documentation',
            'gen': 'True = the generator owns this item and may rewrite it on each run',
            'addressable': 'True = this instance owns a slice of the address map (has a target window)',
            'connections': 'Port-to-net wiring list: [{port: <if_name>, conn: <net_or_ic.target>}]',
            'params': 'Parameter overrides applied to this instance (knobs, widths, depths)',
            'auto_base': 'Auto-pack the targets starting from this address with the given alignment',
            'type': 'Interconnect fabric type: ahb_lite (default), or interface type: wire/ahb/apb/…',
            'direction': 'Interface direction: in, out, inout (wires) or initiator/target (buses)',
            'targets': 'Ordered list of address-map slots on this interconnect',
            'initiators': 'Masters on this interconnect and the target names they can reach',
            'passthrough': 'True = this initiator/target is a boundary port, not a local instance',
            'clocks': 'Clock domain declarations consumed by the generator for CDC checks',
            'resets': 'Reset domain declarations (active: low/high, source: external/internal)',
            'interfaces': 'Boundary port list: each entry is a wire or bus with name/type/direction',
            'instances': 'Sub-module instances placed inside this module',
            'interconnects': 'AHB/APB bus matrices connecting the instances',
            'glue_logic': 'Inline RTL snippets: concat (NVIC vectors), tie, slice, …',
            'uses': 'Preset name(s) to expand into this module before other processing',
        }

    # ------------------------------------------------------------------
    # undo / redo stacks (backlog #8)
    # ------------------------------------------------------------------
    _UNDO_DEPTH = 20

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

    def _undo_stacks(self):
        """Lazy-init per-draft undo stack dict (stored on the instance)."""
        if not hasattr(self, '_undo_stack'):
            self._undo_stack: Dict[str, list] = {}
        return self._undo_stack

    def _redo_stacks(self):
        """Lazy-init per-draft redo stack dict (stored on the instance)."""
        if not hasattr(self, '_redo_stack'):
            self._redo_stack: Dict[str, list] = {}
        return self._redo_stack

    def _snapshot(self, draft_id: str, data: Dict[str, Any]):
        """Push a snapshot of *data* onto the undo stack for *draft_id*.

        Called BEFORE any mutating operation so that undo() restores the
        pre-mutation state. Trims the stack to _UNDO_DEPTH entries and
        clears the redo stack (a new edit invalidates the redo history).
        """
        stack = self._undo_stacks().setdefault(draft_id, [])
        stack.append(copy.deepcopy(data))
        if len(stack) > self._UNDO_DEPTH:
            stack[:] = stack[-self._UNDO_DEPTH:]
        # new edit invalidates redo history
        self._redo_stacks().pop(draft_id, None)

    def undo(self, draft_id: str, current: Dict[str, Any]) -> Dict[str, Any]:
        """Pop the top of the undo stack and return the restored dict.

        Pushes *current* onto the redo stack first so it can be re-applied
        via redo(). If the undo stack is empty, returns *current* unchanged
        (no-op rather than error — idempotent for the UI).
        """
        u_stack = self._undo_stacks().setdefault(draft_id, [])
        r_stack = self._redo_stacks().setdefault(draft_id, [])
        if not u_stack:
            return current
        r_stack.append(copy.deepcopy(current))
        if len(r_stack) > self._UNDO_DEPTH:
            r_stack[:] = r_stack[-self._UNDO_DEPTH:]
        return u_stack.pop()

    def redo(self, draft_id: str, current: Dict[str, Any]) -> Dict[str, Any]:
        """Re-apply the most recently undone state.

        Pushes *current* back onto the undo stack. Returns *current* unchanged
        if the redo stack is empty (no-op).
        """
        u_stack = self._undo_stacks().setdefault(draft_id, [])
        r_stack = self._redo_stacks().setdefault(draft_id, [])
        if not r_stack:
            return current
        u_stack.append(copy.deepcopy(current))
        if len(u_stack) > self._UNDO_DEPTH:
            u_stack[:] = u_stack[-self._UNDO_DEPTH:]
        return r_stack.pop()

    # ------------------------------------------------------------------
    # auto_base packing (the "auto-pack" button)
    # ------------------------------------------------------------------
    def pack(self, data: Dict[str, Any], ic_name: str, from_addr,
             align, repack_all: bool = False) -> Dict[str, Any]:
        """Pack an interconnect's targets with doc-05 ``auto_base``.

        Default packs only targets *missing* ``base:`` (pinned ones stay);
        ``repack_all=True`` strips every non-passthrough ``base:`` first and
        repacks the lot in YAML order. Returns the rewritten full dict plus a
        before/after table — the caller decides whether to apply it.
        """
        work = copy.deepcopy(data if 'module' in data else {'module': data})
        mod = work['module']
        ic = next((i for i in (mod.get('interconnects') or [])
                   if isinstance(i, dict) and i.get('name') == ic_name), None)
        if ic is None:
            raise ValueError(f"no interconnect named '{ic_name}'")
        before = {t.get('name'): t.get('base')
                  for t in ic.get('targets') or [] if isinstance(t, dict)}
        if repack_all:
            for t in ic.get('targets') or []:
                if isinstance(t, dict) and t.get('passthrough') is None:
                    t.pop('base', None)
        ic['auto_base'] = {'from': from_addr, 'align': align}
        expanded = expand_module_dict(work, presets=self.presets)
        new_ic = next(i for i in expanded['module']['interconnects']
                      if isinstance(i, dict) and i.get('name') == ic_name)
        table = []
        for t in new_ic.get('targets') or []:
            if not isinstance(t, dict):
                continue
            name = t.get('name')
            table.append({'name': name,
                          'before': before.get(name),
                          'after': t.get('base'),
                          'after_hex': hex(t['base'])
                          if isinstance(t.get('base'), int) else None})
        return {'data': expanded, 'table': table}
