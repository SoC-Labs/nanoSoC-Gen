# Copyright 2026, SoC Labs (www.soclabs.org)
"""MCP server (read tools) over GuiService.

Tool names use underscores (MCP forbids dots); the docstring's first word is
the plan's dotted name. Every tool returns a dict; failures are
``{"ok": False, "error": {"code", "message"}}`` rather than exceptions.
"""

import copy
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from mcp.server.mcpserver import MCPServer

from ..configurator.project import load_project
from ..gui_service import GuiService

srv = MCPServer(
    "nanosoc",
    instructions="Inspect a nanosoc_gen project: call soc_open_project first, "
                 "then soc_validate / soc_memory_map / soc_irq_map / "
                 "soc_catalog / soc_describe / soc_free_resources.")


class _Session:
    adapter = None      # ProjectAdapter
    top: Optional[str] = None       # absolute top yaml path
    svc: Optional[GuiService] = None


_S = _Session()


def _err(code: str, message: str, **extra) -> Dict[str, Any]:
    e = {'code': code, 'message': message}
    e.update(extra)
    return {'ok': False, 'error': e}


def _require_project():
    if _S.adapter is None:
        return _err('no_project', 'call soc_open_project first')
    return None


def _load(top: Optional[str] = None):
    """(data, top_path) for the session top or an override relative to root."""
    root = _S.adapter.root
    rel = top or _S.top
    if not rel:
        raise FileNotFoundError('project has no top_yaml; pass top=')
    data = _S.svc.load_yaml(rel, root)
    return data, str(_S.svc._safe_path(rel, root))


def _guard(fn):
    """Run fn() after the project check; map known failures to error dicts."""
    bad = _require_project()
    if bad:
        return bad
    try:
        return fn()
    except PermissionError as exc:
        return _err('path_outside_project', str(exc))
    except FileNotFoundError as exc:
        return _err('file_not_found', str(exc))
    except (ValueError, yaml.YAMLError) as exc:
        return _err('bad_input', str(exc))


@srv.tool()
def soc_open_project(path: str, top_yaml: Optional[str] = None,
                     lib_dirs: Optional[List[str]] = None) -> Dict[str, Any]:
    """soc.open_project: load <path>/configurator.yaml (or infer sys_desc/*) and make it the session project."""
    try:
        ad = load_project(path)
    except (FileNotFoundError, NotADirectoryError, ValueError,
            yaml.YAMLError) as exc:
        return _err('project_not_found', str(exc), path=str(path))
    if top_yaml:
        ad.top_yaml = top_yaml
    if lib_dirs is not None:
        ad.lib_dirs = list(lib_dirs)
    top_abs = str(ad.path(ad.top_yaml)) if ad.top_yaml else None
    if top_abs and not Path(top_abs).is_file():
        return _err('file_not_found', f'top yaml not found: {top_abs}')
    base = str(Path(top_abs).parent) if top_abs else ad.root
    _S.adapter, _S.top = ad, ad.top_yaml
    _S.svc = GuiService(base_dir=base, lib_dirs=ad.abs_lib_dirs,
                        rules_file=ad.abs_rules)
    out = ad.to_dict()
    out['ok'] = True
    return out


@srv.tool()
def soc_validate(top: Optional[str] = None) -> Dict[str, Any]:
    """soc.validate: tier 1-3 validation (same result as `configurator check`): ok, error/warning counts, messages, checklist."""
    return _guard(lambda: _S.svc.validate(_load(top)[0]))


@srv.tool()
def soc_memory_map(top: Optional[str] = None) -> Dict[str, Any]:
    """soc.memory_map: per-interconnect address windows, sorted, with overlaps marked."""
    def run():
        res = _S.svc.memory_map(_load(top)[0])
        if res.get('ok'):
            res['overlaps'] = [
                {'interconnect': ic['name'], 'target': t['name'],
                 'with': t['overlaps']}
                for ic in res['interconnects'] for t in ic['targets']
                if t['overlaps']]
            # an overlap makes the map unusable: report it as not ok
            res['ok'] = not res['overlaps']
        return res
    return _guard(run)


@srv.tool()
def soc_irq_map(top: Optional[str] = None) -> Dict[str, Any]:
    """soc.irq_map: every glue_logic concat as a bit-to-signal table plus its consumer."""
    return _guard(lambda: dict(_S.svc.irq_map(_load(top)[0]), ok=True))


@srv.tool()
def soc_catalog() -> Dict[str, Any]:
    """soc.catalog: modules, presets, pad techs, rules, templates and droppable components available to the project."""
    def run():
        cat = _S.svc.catalogs(_S.adapter.abs_templates)
        cat['components'] = [
            {k: c.get(k) for k in ('id', 'label', 'module', 'category',
                                   'available')}
            for c in _S.svc.components()]
        cat['ok'] = True
        return cat
    return _guard(run)


def _mod(data: Dict[str, Any]) -> Dict[str, Any]:
    m = data.get('module', data)
    return m if isinstance(m, dict) else {}


@srv.tool()
def soc_describe(top: Optional[str] = None) -> Dict[str, Any]:
    """soc.describe: instances, params, interconnects and targets read from the raw YAML dict."""
    def run():
        data, path = _load(top)
        mod = _mod(data)
        insts = [{'name': i.get('instance_name'), 'module': i.get('module'),
                  'addressable': bool(i.get('addressable')),
                  'params': i.get('params') or {}}
                 for i in mod.get('instances') or [] if isinstance(i, dict)]
        ics = []
        for ic in mod.get('interconnects') or []:
            if not isinstance(ic, dict):
                continue
            ics.append({
                'name': ic.get('name'), 'type': ic.get('type'),
                'targets': [{k: t.get(k) for k in
                             ('name', 'instance', 'base', 'size',
                              'sw_access', 'region_type')}
                            for t in ic.get('targets') or []
                            if isinstance(t, dict)],
                'initiators': [i.get('name') for i in ic.get('initiators') or []
                               if isinstance(i, dict)]})
        params = {k: (v.get('default') if isinstance(v, dict) else v)
                  for k, v in (mod.get('params') or {}).items()}
        return {'ok': True, 'file': path, 'name': mod.get('name'),
                'params': params, 'instances': insts, 'interconnects': ics}
    return _guard(run)


def _align_up(x: int, a: int) -> int:
    return (x + a - 1) // a * a


@srv.tool()
def soc_free_resources(top: Optional[str] = None) -> Dict[str, Any]:
    """soc.free_resources: per interconnect the next free aligned base and address gaps; per IRQ concat the unmapped bits."""
    def run():
        data, _ = _load(top)
        mod = _mod(data)
        slots = []
        for ic in mod.get('interconnects') or []:
            if not isinstance(ic, dict):
                continue
            wins = sorted(
                (t['base'], t['base'] + t['size'])
                for t in ic.get('targets') or []
                if isinstance(t, dict) and isinstance(t.get('base'), int)
                and isinstance(t.get('size'), int))
            gaps, cur = [], 0
            for lo, hi in wins:
                if lo > cur:
                    gaps.append({'base': cur, 'size': lo - cur,
                                 'base_hex': hex(cur), 'size_hex': hex(lo - cur)})
                cur = max(cur, hi)
            nxt = _align_up(cur, 0x1000)     # add_component's placement rule
            slots.append({'interconnect': ic.get('name'),
                          'targets': len(wins), 'next_free_base': nxt,
                          'next_free_base_hex': hex(nxt), 'gaps': gaps})
        irqs = []
        for c in _S.svc.irq_map(copy.deepcopy(data))['concats']:
            free = [b['bit'] for b in c['bits'] if b['signal'] is None]
            irqs.append({'name': c['name'], 'output': c['output'],
                         'width': c['width'], 'free_bits': free,
                         'next_bit': c['width'] if c['pad_zero_above'] is None
                         else (free[0] if free else None),
                         'is_irq': bool(c['consumer']
                                        and c['consumer']['is_irq'])})
        return {'ok': True, 'address_slots': slots, 'irq_bits': irqs}
    return _guard(run)


@srv.resource("soc://yaml/{relpath}")
def yaml_file(relpath: str) -> str:
    """Raw text of a YAML file inside the open project."""
    if _S.adapter is None:
        raise ValueError('call soc_open_project first')
    p = GuiService._safe_path(relpath, _S.adapter.root)
    return p.read_text()


from . import build_tools  # noqa: E402,F401  (registers WP2 tools)
from . import edit_tools, iterate_tool, sim_debug_tool  # noqa: E402

edit_tools.register(srv, _S)            # WP4 edit tools
sim_debug_tool.register(srv)            # WP3 sim_debug
iterate_tool.register(srv, _S)          # WP5 composite loop


def main() -> None:
    srv.run("stdio")


if __name__ == '__main__':
    main()
