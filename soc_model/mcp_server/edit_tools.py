# Copyright 2026, SoC Labs (www.soclabs.org)
"""MCP edit tools (WP4): GuiService edits with dry-run drafts and diffs.

``register(srv, session)`` adds the tools to an MCPServer. ``session`` is any
object with ``adapter`` (ProjectAdapter), ``top`` (top yaml, relative to the
root) and ``svc`` (GuiService) -- WP1's ``server._S`` fits.

Every edit tool takes ``dry_run`` (default True). A dry run writes the result
to ``<root>/.configurator/drafts/<id>.yaml`` and returns
``{draft_id, diff, changed, validate}``; the top YAML is not touched.
``dry_run=False`` writes the top YAML directly. ``draft`` lets an edit build
on an earlier draft (chained edits). ``soc_save`` promotes a draft to the top
YAML. A rejected edit (unknown module, knob below its floor, bad bit) is an
error dict and writes nothing -- it is never clamped.
"""

import copy
import difflib
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

_DRAFT_RE = re.compile(r'^[0-9a-f]{8}$')


def _err(code: str, message: str, **extra) -> Dict[str, Any]:
    e = {'code': code, 'message': message}
    e.update(extra)
    return {'ok': False, 'error': e}


def _changed_paths(a: Any, b: Any, path: str = '') -> List[str]:
    """Dotted paths whose value differs. Lists of named dicts are keyed by
    name/instance_name so an insertion does not shift every later path."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: List[str] = []
        for k in list(a) + [k for k in b if k not in a]:
            p = '%s.%s' % (path, k) if path else str(k)
            if k not in a or k not in b:
                out.append(p)
            else:
                out += _changed_paths(a[k], b[k], p)
        return out
    if isinstance(a, list) and isinstance(b, list):
        def key(x, i):
            if isinstance(x, dict):
                for kk in ('instance_name', 'name', 'port', 'bit'):
                    if kk in x:
                        return '%s=%s' % (kk, x[kk])
            return '#%d' % i
        ka = {key(x, i): x for i, x in enumerate(a)}
        kb = {key(x, i): x for i, x in enumerate(b)}
        out = []
        for k in list(ka) + [k for k in kb if k not in ka]:
            p = '%s[%s]' % (path, k)
            if k not in ka or k not in kb:
                out.append(p)
            else:
                out += _changed_paths(ka[k], kb[k], p)
        return out
    return [] if a == b else [path]


def register(srv, S) -> None:
    """Add soc_add_component / soc_remove_component / soc_update_component /
    soc_set_irq_bit / soc_set_param / soc_save to ``srv``."""

    def _draft_path(draft_id: str) -> Path:
        if not _DRAFT_RE.match(str(draft_id)):
            raise ValueError("bad draft id '%s'" % draft_id)
        return S.adapter.drafts_dir / ('%s.yaml' % draft_id)

    def _base(draft: Optional[str], top: Optional[str]):
        root = S.adapter.root
        if draft:
            p = _draft_path(draft)
            if not p.is_file():
                raise FileNotFoundError("no draft '%s'" % draft)
            return S.svc.load_yaml(str(p), root), str(p)
        rel = top or S.top
        if not rel:
            raise FileNotFoundError('project has no top_yaml; pass top=')
        return S.svc.load_yaml(rel, root), rel

    def _top_rel(top: Optional[str]) -> str:
        rel = top or S.top
        if not rel:
            raise FileNotFoundError('project has no top_yaml; pass top=')
        return rel

    def _run(edit, dry_run: bool, draft: Optional[str], top: Optional[str]):
        if S.adapter is None:
            return _err('no_project', 'call soc_open_project first')
        try:
            base, src = _base(draft, top)
            res = edit(base)
            new = res.pop('data')
            old_txt = S.svc.to_yaml(base)
            new_txt = S.svc.to_yaml(new)
            diff = ''.join(difflib.unified_diff(
                old_txt.splitlines(True), new_txt.splitlines(True),
                fromfile='before', tofile='after', n=2))
            out = dict(res)
            out.update({'ok': True, 'dry_run': bool(dry_run),
                        'diff': diff,
                        'changed': _changed_paths(base, new),
                        'validate': S.svc.validate(new)})
            if dry_run:
                did = uuid.uuid4().hex[:8]
                out['draft_id'] = did
                out['draft_path'] = S.svc.save_yaml(
                    new, str(_draft_path(did)), S.adapter.root)
            else:
                out['saved'] = S.svc.save_yaml(new, _top_rel(top),
                                               S.adapter.root)
            return out
        except PermissionError as exc:
            return _err('path_outside_project', str(exc))
        except FileNotFoundError as exc:
            return _err('file_not_found', str(exc))
        except (ValueError, KeyError, yaml.YAMLError) as exc:
            return _err('rejected', str(exc))

    @srv.tool()
    def soc_add_component(module_name: str,
                          instance_name: Optional[str] = None,
                          ic_name: Optional[str] = None,
                          size: Optional[int] = None,
                          base: Optional[int] = None,
                          params: Optional[Dict[str, Any]] = None,
                          component_id: Optional[str] = None,
                          dry_run: bool = True,
                          draft: Optional[str] = None,
                          top: Optional[str] = None) -> Dict[str, Any]:
        """soc.add_component: add a module instance with default wiring; dry_run writes a draft and returns {draft_id, diff, changed, validate}. Params below a knob floor are rejected, not clamped."""
        return _run(lambda d: S.svc.add_component(
            d, module_name, instance_name=instance_name, ic_name=ic_name,
            size=size, base=base, params=params, component_id=component_id),
            dry_run, draft, top)

    @srv.tool()
    def soc_remove_component(instance_name: str, dry_run: bool = True,
                             draft: Optional[str] = None,
                             top: Optional[str] = None) -> Dict[str, Any]:
        """soc.remove_component: remove an instance with its target windows and initiator references."""
        return _run(lambda d: S.svc.remove_component(d, instance_name),
                    dry_run, draft, top)

    @srv.tool()
    def soc_update_component(instance_name: str, params: Dict[str, Any],
                             dry_run: bool = True,
                             draft: Optional[str] = None,
                             top: Optional[str] = None) -> Dict[str, Any]:
        """soc.update_component: change an instance's knob params (hard min/max enforced) and resize its window."""
        return _run(lambda d: S.svc.update_component(d, instance_name, params),
                    dry_run, draft, top)

    @srv.tool()
    def soc_set_irq_bit(output: str, bit: int, signal: Optional[str] = None,
                        dry_run: bool = True, draft: Optional[str] = None,
                        top: Optional[str] = None) -> Dict[str, Any]:
        """soc.set_irq_bit: set (or clear, with signal null) the source driving one bit of an interrupt concat."""
        return _run(lambda d: S.svc.set_irq_bit(d, output, bit, signal),
                    dry_run, draft, top)

    def _set_param(d: Dict[str, Any], name: str, value: Any,
                   instance: Optional[str]) -> Dict[str, Any]:
        work = copy.deepcopy(d if 'module' in d else {'module': d})
        mod = work['module']
        if instance is None:
            p = (mod.get('params') or {}).get(name)
            if p is None:
                raise ValueError("no module param '%s'" % name)
            if isinstance(p, dict):
                p['default'] = value
            else:
                mod['params'][name] = value
            return {'data': work, 'param': name, 'value': value}
        inst = next((i for i in mod.get('instances') or []
                     if isinstance(i, dict)
                     and i.get('instance_name') == instance), None)
        if inst is None:
            raise ValueError("no instance named '%s'" % instance)
        module_name = inst.get('module') or inst.get('rtl_module') or ''
        mdef, spec = S.svc.module_spec(module_name)
        if spec.knob(name) is not None:
            # knob: floor/ceiling enforced and the window resized
            r = S.svc.update_component(work, instance, {name: value})
            return {'data': r['data'], 'param': name, 'value': value,
                    'instance': instance, 'size': r['size']}
        if mdef and name not in (mdef.get('params') or {}):
            raise ValueError("module '%s' has no param '%s'"
                             % (module_name, name))
        inst.setdefault('params', {})[name] = value
        return {'data': work, 'param': name, 'value': value,
                'instance': instance}

    @srv.tool()
    def soc_set_param(name: str, value: Any, instance: Optional[str] = None,
                      dry_run: bool = True, draft: Optional[str] = None,
                      top: Optional[str] = None) -> Dict[str, Any]:
        """soc.set_param: set a top-level param default, or with instance= one instance's param (knobs enforce their hard limits)."""
        return _run(lambda d: _set_param(d, name, value, instance),
                    dry_run, draft, top)

    @srv.tool()
    def soc_save(draft_id: str, force: bool = False,
                 top: Optional[str] = None) -> Dict[str, Any]:
        """soc.save: promote a draft to the top YAML. Refused while the draft has validation errors unless force=true."""
        if S.adapter is None:
            return _err('no_project', 'call soc_open_project first')
        try:
            p = _draft_path(draft_id)
            if not p.is_file():
                return _err('file_not_found', "no draft '%s'" % draft_id)
            data = S.svc.load_yaml(str(p), S.adapter.root)
            val = S.svc.validate(data)
            if not force and val.get('errors', 0):
                return _err('validation_failed',
                            'draft has %d validation error(s); fix or '
                            'force=true' % val['errors'], validate=val)
            saved = S.svc.save_yaml(data, _top_rel(top), S.adapter.root)
            p.unlink()
            return {'ok': True, 'saved': saved, 'validate': val}
        except PermissionError as exc:
            return _err('path_outside_project', str(exc))
        except (ValueError, yaml.YAMLError) as exc:
            return _err('bad_input', str(exc))
