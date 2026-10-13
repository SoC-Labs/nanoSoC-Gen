# Copyright 2026, SoC Labs (www.soclabs.org)
"""FastAPI shell for the SoC Configurator (roadmap doc 06a §7).

Thin layer over :mod:`soc_model.gui_service` + :mod:`.jobs`. FastAPI/uvicorn
are an optional extra (``pip install nanosoc-gen[configurator]``); importing
this module without them raises a clear error, and nothing else in the
package depends on it.

Endpoints (all JSON unless noted)::

    GET  /                         the single-page UI
    GET  /api/project              adapter info + env health
    GET  /api/catalog              palette/dropdown data
    GET  /api/drafts               list drafts
    POST /api/drafts               create {name?, template?|from_top?}
    GET  /api/draft/{id}           {name, yaml}
    PUT  /api/draft/{id}           {yaml} -> save + validate response
    POST /api/draft/{id}/validate  validate current text
    GET  /api/draft/{id}/diagram   layout JSON (§4)
    GET  /api/draft/{id}/export    text/yaml download
    POST /api/draft/{id}/apply     {dry} -> diff vs project top yaml / write
    POST /api/build                {stages: [validate, soc, firmware]}
    GET  /api/build/status
    POST /api/build/cancel
    WS   /ws                       job lines + stage/job status events

Drafts are stored as **raw YAML text** under ``<root>/.configurator/drafts``
— text, not parsed dicts, so the user's comments/ordering survive until a
structural edit rewrites them.
"""

import asyncio
import difflib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

try:
    from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, PlainTextResponse
    from fastapi.staticfiles import StaticFiles
except ImportError as _exc:  # pragma: no cover - exercised only without extra
    raise ImportError(
        "The configurator web app needs its optional dependencies: "
        "pip install 'fastapi>=0.100' 'uvicorn>=0.20'  "
        "(or: pip install nanosoc-gen[configurator])") from _exc

import jinja2

from ..gui_service import GuiService
from .jobs import JobRunner, Stage
from .project import ProjectAdapter

_HERE = Path(__file__).resolve().parent
_SLUG = re.compile(r'[^a-zA-Z0-9_-]+')


class DraftStore:
    """File-backed drafts: one ``<id>.yaml`` text file per draft."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, draft_id: str) -> Path:
        if _SLUG.search(draft_id):
            raise HTTPException(400, f'bad draft id: {draft_id!r}')
        return self.root / f'{draft_id}.yaml'

    def list(self) -> List[str]:
        return sorted(p.stem for p in self.root.glob('*.yaml'))

    def read(self, draft_id: str) -> str:
        p = self._path(draft_id)
        if not p.is_file():
            raise HTTPException(404, f'no draft {draft_id!r}')
        return p.read_text()

    def write(self, draft_id: str, text: str):
        self._path(draft_id).write_text(text)

    def create(self, name: str, text: str) -> str:
        draft_id = _SLUG.sub('-', name).strip('-') or 'draft'
        base, n = draft_id, 1
        while self._path(draft_id).exists():
            n += 1
            draft_id = f'{base}-{n}'
        self.write(draft_id, text)
        return draft_id


def _parse_yaml(text: str) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise HTTPException(422, f'YAML parse error: {exc}')
    if not isinstance(data, dict):
        raise HTTPException(422, 'top level is not a mapping')
    return data


def _env_health(adapter: ProjectAdapter) -> Dict[str, Any]:
    import os
    checks = {
        'perl': bool(shutil.which('perl')),
        'make': bool(shutil.which('make')),
        'arm_ip_library_path': bool(os.environ.get('ARM_IP_LIBRARY_PATH')),
    }
    for mod in ('jinja2', 'systemrdl'):
        try:
            __import__(mod)
            checks[mod] = True
        except ImportError:
            checks[mod] = False
    checks['top_yaml_exists'] = bool(
        adapter.top_yaml and adapter.path(adapter.top_yaml).is_file())
    return checks


def create_app(adapter: ProjectAdapter) -> 'FastAPI':
    app = FastAPI(title='nanoSoC Configurator', docs_url='/api/docs')
    svc = GuiService(base_dir=adapter.root, lib_dirs=adapter.abs_lib_dirs,
                     rules_file=adapter.abs_rules)
    drafts = DraftStore(adapter.drafts_dir)
    sockets: List[WebSocket] = []
    loop_holder: Dict[str, Any] = {}

    def broadcast(event: Dict[str, Any]):
        loop = loop_holder.get('loop')
        if loop is None:
            return
        for ws in list(sockets):
            asyncio.run_coroutine_threadsafe(_send(ws, event), loop)

    async def _send(ws: WebSocket, event):
        try:
            await ws.send_json(event)
        except Exception:
            if ws in sockets:
                sockets.remove(ws)

    runner = JobRunner(listener=broadcast)
    app.state.adapter = adapter
    app.state.svc = svc
    app.state.runner = runner

    # Render with jinja2 directly — starlette's TemplateResponse signature
    # has drifted across versions and we don't need request context anyway.
    jenv = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_HERE / 'templates')),
        autoescape=True)
    app.mount('/static', StaticFiles(directory=str(_HERE / 'static')),
              name='static')

    # ------------------------------------------------------------- pages
    @app.get('/', response_class=HTMLResponse)
    async def index():
        return HTMLResponse(jenv.get_template('base.html').render(
            project=adapter.to_dict()))

    # --------------------------------------------------------------- api
    @app.get('/api/project')
    async def project():
        try:
            lib_shadows = svc.lib_shadow_report()['count']
        except Exception:
            lib_shadows = 0
        build_fresh = {'state': 'unknown', 'reason': ''}
        if adapter.top_yaml:
            try:
                build_fresh = svc.build_freshness(
                    adapter.path(adapter.top_yaml), adapter.abs_lib_dirs,
                    adapter.path(adapter.build_dir))
            except Exception as exc:
                build_fresh = {'state': 'unknown', 'reason': str(exc)}
        return {'adapter': adapter.to_dict(), 'health': _env_health(adapter),
                'lib_shadows': lib_shadows, 'build_fresh': build_fresh}

    @app.get('/api/catalog')
    async def catalog():
        return svc.catalogs(template_dirs=adapter.abs_templates)

    @app.get('/api/components')
    async def components():
        # the droppable-IP palette: packaged components (generic names, knobs,
        # hard min/max limits) + bare-module fallback, grouped by category.
        return {'components': svc.components()}

    @app.get('/api/drafts')
    async def list_drafts():
        return {'drafts': drafts.list()}

    @app.post('/api/drafts')
    async def create_draft(body: Dict[str, Any]):
        name = str(body.get('name') or 'draft')
        if body.get('template'):
            tpl = Path(body['template'])
            allowed = any(str(tpl).startswith(d) for d in adapter.abs_templates)
            if not (allowed and tpl.is_file()):
                raise HTTPException(400, 'unknown template')
            text = tpl.read_text()
        elif body.get('from_top') and adapter.top_yaml:
            text = adapter.path(adapter.top_yaml).read_text()
        elif body.get('scratch'):
            # the Builder wizard's "New SoC from scratch" seed
            sc = body['scratch'] if isinstance(body['scratch'], dict) else {}
            mod_name = re.sub(r'[^a-zA-Z0-9_]+', '_',
                              str(sc.get('name') or name)).strip('_') or 'my_soc'
            text = svc.to_yaml(svc.new_soc(
                mod_name,
                clk_hz=int(sc.get('clk_hz') or 50000000),
                addr_w=int(sc.get('addr_w') or 32),
                data_w=int(sc.get('data_w') or 32)))
        else:
            text = "module:\n  name: my_soc\n  gen: True\n"
        draft_id = drafts.create(name, text)
        return {'id': draft_id}

    @app.delete('/api/draft/{draft_id}')
    async def delete_draft(draft_id: str):
        drafts.read(draft_id)            # 404 if absent
        drafts._path(draft_id).unlink()
        return {'deleted': draft_id}

    # ---- component-level editing (the Builder wizard's add/remove) ----
    @app.post('/api/draft/{draft_id}/component')
    async def add_component(draft_id: str, body: Dict[str, Any]):
        data = _parse_yaml(drafts.read(draft_id))
        svc._snapshot(draft_id, data)   # undo snapshot (#8)
        try:
            res = svc.add_component(
                data, str(body.get('module', '')),
                instance_name=body.get('instance_name'),
                ic_name=body.get('interconnect'),
                size=body.get('size'), base=body.get('base'),
                params=body.get('params'),
                component_id=body.get('component_id'))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        drafts.write(draft_id, svc.to_yaml(res['data']))
        return {'instance': res['instance'], 'target': res['target'],
                'wired': res['wired'], 'unwired': res['unwired'],
                'applied_params': res.get('applied_params', {}),
                'validation': svc.validate(res['data'])}

    @app.put('/api/draft/{draft_id}/component/{instance_name}')
    async def update_component(draft_id: str, instance_name: str,
                               body: Dict[str, Any]):
        # inline knob re-tweak (the canvas size control) — enforces hard limits
        data = _parse_yaml(drafts.read(draft_id))
        svc._snapshot(draft_id, data)   # undo snapshot (#8)
        try:
            res = svc.update_component(data, instance_name,
                                       body.get('params') or {})
        except ValueError as exc:
            # "no instance ..." -> 404, "hard limit: ..." -> 400
            raise HTTPException(404 if str(exc).startswith('no instance')
                                else 400, str(exc))
        drafts.write(draft_id, svc.to_yaml(res['data']))
        return {'instance': res['instance'], 'target': res['target'],
                'size': res['size'],
                'applied_params': res.get('applied_params', {}),
                'validation': svc.validate(res['data'])}

    @app.delete('/api/draft/{draft_id}/component/{instance_name}')
    async def remove_component(draft_id: str, instance_name: str):
        data = _parse_yaml(drafts.read(draft_id))
        svc._snapshot(draft_id, data)   # undo snapshot (#8)
        try:
            res = svc.remove_component(data, instance_name)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        drafts.write(draft_id, svc.to_yaml(res['data']))
        return {'removed': instance_name,
                'removed_targets': res['removed_targets'],
                'validation': svc.validate(res['data'])}

    @app.get('/api/draft/{draft_id}')
    async def get_draft(draft_id: str):
        return {'id': draft_id, 'yaml': drafts.read(draft_id)}

    @app.post('/api/draft/{draft_id}/snapshot')
    async def snapshot_draft(draft_id: str, body: Dict[str, Any]):
        # named point-in-time copy of a draft (#7) — a new draft file
        text = drafts.read(draft_id)
        label = str((body or {}).get('name') or (draft_id + '-snap'))
        new_id = drafts.create(f'{draft_id}-{label}', text)
        return {'id': new_id, 'from': draft_id}

    @app.get('/api/draft/{draft_id}/share')
    async def share_draft(draft_id: str):
        # single-blob reproducible report (#7): yaml + validation + adapter
        text = drafts.read(draft_id)
        return {'schema': 1, 'id': draft_id, 'yaml': text,
                'validation': svc.validate(_parse_yaml(text)),
                'adapter': adapter.to_dict()}

    @app.post('/api/draft/{draft_id}/save-as-template')
    async def save_as_template(draft_id: str, body: Dict[str, Any]):
        # promote a draft into the adapter's templates dir (#12)
        if not adapter.abs_templates:
            raise HTTPException(400, 'project declares no templates: dir')
        text = drafts.read(draft_id)
        name = _SLUG.sub('-', str((body or {}).get('name') or draft_id)
                         ).strip('-') or 'template'
        tdir = Path(adapter.abs_templates[0])
        try:
            dest = svc._safe_path(name + '.yaml', tdir)
        except PermissionError as exc:
            raise HTTPException(400, str(exc))
        if dest.exists():
            raise HTTPException(409, f'template {name!r} already exists')
        tdir.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        return {'template': str(dest), 'name': name}

    @app.put('/api/draft/{draft_id}')
    async def put_draft(draft_id: str, body: Dict[str, Any]):
        text = str(body.get('yaml', ''))
        drafts.read(draft_id)            # 404 if absent
        drafts.write(draft_id, text)
        return svc.validate(_parse_yaml(text))

    @app.post('/api/draft/{draft_id}/validate')
    async def validate_draft(draft_id: str):
        return svc.validate(_parse_yaml(drafts.read(draft_id)))

    @app.get('/api/draft/{draft_id}/diagram')
    async def diagram(draft_id: str):
        return svc.diagram(_parse_yaml(drafts.read(draft_id)))

    @app.get('/api/draft/{draft_id}/compare')
    async def compare(draft_id: str, other: str):
        # multi-draft A/B (#14): unified YAML diff + validation deltas
        a_text = drafts.read(draft_id)
        b_text = drafts.read(other)
        diff = ''.join(difflib.unified_diff(
            a_text.splitlines(True), b_text.splitlines(True),
            fromfile=f'draft:{draft_id}', tofile=f'draft:{other}'))

        def _slim(v):
            return {'errors': v['errors'], 'warnings': v['warnings'],
                    'ok': v['ok'],
                    'checks': sum(1 for c in v['checklist'] if c['passed']),
                    'total': len(v['checklist'])}
        return {'a': draft_id, 'b': other, 'diff': diff,
                'a_validation': _slim(svc.validate(_parse_yaml(a_text))),
                'b_validation': _slim(svc.validate(_parse_yaml(b_text)))}

    @app.get('/api/draft/{draft_id}/deepdive', response_class=HTMLResponse)
    async def deepdive(draft_id: str):
        # full D3 connectivity page for the draft (#5) — the always-correct
        # fallback when the curated SVG diagram's heuristics fall short
        try:
            html = svc.deepdive_html(_parse_yaml(drafts.read(draft_id)))
        except ValueError as exc:
            raise HTTPException(400, f'cannot build model for deep-dive: {exc}')
        return HTMLResponse(html)

    @app.post('/api/draft/{draft_id}/fix')
    async def fix_rule(draft_id: str, body: Dict[str, Any]):
        # checklist fix-it quick action: apply the canonical dict mutation for
        # a failed completeness rule (#20)
        data = _parse_yaml(drafts.read(draft_id))
        try:
            res = svc.fix_rule(data, str(body.get('rule_id', '')))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        drafts.write(draft_id, svc.to_yaml(res['data']))
        return {'message': res['message'], 'validation': res['validation']}

    @app.get('/api/draft/{draft_id}/export')
    async def export(draft_id: str, mode: str = 'raw'):
        text = drafts.read(draft_id)
        if mode == 'canonical':       # doc-05 desugared form
            text = svc.to_yaml(svc.expand_preview(_parse_yaml(text)))
        elif mode != 'raw':
            raise HTTPException(400, f"unknown export mode '{mode}'")
        return PlainTextResponse(text, media_type='text/yaml')

    # ---- dict view (the Compose forms' read/write path; comments are
    # ---- preserved only until the first structural edit rewrites the text)
    @app.get('/api/draft/{draft_id}/hints')
    async def get_hints(draft_id: str):
        # field tooltips for the Compose panel (#4) — draft_id included in path
        # so the URL is consistent with other per-draft endpoints even though
        # the hints dict is currently the same for every draft.
        drafts.read(draft_id)            # 404 if draft is absent
        return {'hints': svc.field_hints()}

    @app.post('/api/draft/{draft_id}/undo')
    async def undo_draft(draft_id: str):
        # undo (#8): restore the pre-mutation state before the last editing op
        data = _parse_yaml(drafts.read(draft_id))
        restored = svc.undo(draft_id, data)
        drafts.write(draft_id, svc.to_yaml(restored))
        return {'yaml': svc.to_yaml(restored),
                'validation': svc.validate(restored)}

    @app.post('/api/draft/{draft_id}/redo')
    async def redo_draft(draft_id: str):
        # redo (#8): re-apply the most recently undone change
        data = _parse_yaml(drafts.read(draft_id))
        restored = svc.redo(draft_id, data)
        drafts.write(draft_id, svc.to_yaml(restored))
        return {'yaml': svc.to_yaml(restored),
                'validation': svc.validate(restored)}

    @app.get('/api/draft/{draft_id}/params')
    async def get_params(draft_id: str):
        # the top module's params for the PARAM_OVERRIDES panel (#9)
        return {'params': svc.params_info(_parse_yaml(drafts.read(draft_id)))}

    @app.get('/api/draft/{draft_id}/irqmap')
    async def get_irqmap(draft_id: str):
        # read-only IRQ matrix: concat glue -> NVIC consumers (#1)
        return svc.irq_map(_parse_yaml(drafts.read(draft_id)))

    @app.put('/api/draft/{draft_id}/irq')
    async def set_irq_bit(draft_id: str, body: Dict[str, Any]):
        # editing layer for the IRQ matrix (#1 phase 2): set/replace/clear the
        # source signal driving one NVIC concat bit
        data = _parse_yaml(drafts.read(draft_id))
        svc._snapshot(draft_id, data)   # undo snapshot (#8)
        try:
            res = svc.set_irq_bit(data, str(body.get('output', '')),
                                  body.get('bit'), body.get('signal'))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        drafts.write(draft_id, svc.to_yaml(res['data']))
        return {'output': res['output'], 'bit': res['bit'],
                'signal': res['signal'],
                'validation': svc.validate(res['data']),
                'irqmap': svc.irq_map(res['data'])}

    @app.get('/api/draft/{draft_id}/dict')
    async def get_dict(draft_id: str):
        return _parse_yaml(drafts.read(draft_id))

    @app.put('/api/draft/{draft_id}/dict')
    async def put_dict(draft_id: str, body: Dict[str, Any]):
        if not isinstance(body, dict) or ('module' not in body):
            raise HTTPException(422, "expected the full dict with 'module:'")
        drafts.read(draft_id)            # 404 if absent
        drafts.write(draft_id, svc.to_yaml(body))
        return svc.validate(body)

    @app.get('/api/draft/{draft_id}/memmap')
    async def memmap(draft_id: str):
        return svc.memory_map(_parse_yaml(drafts.read(draft_id)))

    @app.post('/api/draft/{draft_id}/pack')
    async def pack(draft_id: str, body: Dict[str, Any]):
        data = _parse_yaml(drafts.read(draft_id))
        try:
            res = svc.pack(data,
                           ic_name=str(body.get('interconnect', '')),
                           from_addr=body.get('from', 0),
                           align=body.get('align', 0),
                           repack_all=bool(body.get('repack_all', False)))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if body.get('apply'):
            drafts.write(draft_id, svc.to_yaml(res['data']))
        return {'applied': bool(body.get('apply')), 'table': res['table'],
                'validation': svc.validate(res['data'])}

    # ------------------------------------------------------- artifacts
    _ARTIFACT_DIRS = ('reports', 'constraints', 'docs', 'discovery',
                      'firmware', 'flist', 'build_info')
    _TEXT_EXT = {'.txt', '.md', '.yaml', '.yml', '.sdc', '.xdc', '.flist',
                 '.h', '.ld', '.mk', '.cmake', '.py', '.vh', '.rdl', '.html'}

    @app.get('/api/artifacts')
    async def artifacts():
        root = adapter.path(adapter.build_dir)
        out = []
        for sub in _ARTIFACT_DIRS:
            d = root / sub
            if not d.is_dir():
                continue
            for p in sorted(d.rglob('*')):
                if p.is_file() and len(out) < 500:
                    out.append({'path': str(p.relative_to(root)),
                                'size': p.stat().st_size,
                                'text': p.suffix.lower() in _TEXT_EXT})
        return {'build_dir': str(root), 'exists': root.is_dir(),
                'files': out}

    @app.get('/api/artifacts/zip')
    async def artifacts_zip():
        # stream a zip of the curated artifact dirs (#15) — hand a colleague
        # the flists/reports/constraints in one click
        import io
        import zipfile
        from fastapi.responses import Response
        root = adapter.path(adapter.build_dir)
        if not root.is_dir():
            raise HTTPException(404, 'no build_dir yet (run a build)')
        buf = io.BytesIO()
        n = 0
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            for sub in _ARTIFACT_DIRS:
                d = root / sub
                if not d.is_dir():
                    continue
                for p in sorted(d.rglob('*')):
                    if p.is_file():
                        z.write(p, arcname=str(p.relative_to(root)))
                        n += 1
        if n == 0:
            raise HTTPException(404, 'no artifacts to zip (run a build)')
        name = (Path(adapter.top_yaml).stem if adapter.top_yaml else 'soc')
        return Response(
            buf.getvalue(), media_type='application/zip',
            headers={'Content-Disposition':
                     f'attachment; filename="{name}_artifacts.zip"'})

    @app.get('/api/artifacts/file')
    async def artifact_file(path: str):
        root = adapter.path(adapter.build_dir)
        try:
            p = svc._safe_path(path, root)
        except PermissionError as e:
            raise HTTPException(403, str(e))
        if not p.is_file():
            raise HTTPException(404, f'no such artifact: {path}')
        if p.suffix.lower() not in _TEXT_EXT or p.stat().st_size > 2_000_000:
            raise HTTPException(415, 'not a previewable text artifact')
        media = 'text/html' if p.suffix.lower() == '.html' else 'text/plain'
        return PlainTextResponse(p.read_text(errors='replace'),
                                 media_type=media)

    # ------------------------------------------------- built SoC (viewer)
    # The generator emits <top>_model_view.json next to the connectivity
    # HTML (soc_model/backends/html.py build_payload, schema 1). These two
    # endpoints surface the BUILT SoC: raw payload + the full D3 viewer page
    # rendered on the fly from it.

    def _model_view_file() -> Path:
        reports = adapter.path(adapter.build_dir) / 'reports'
        cands = sorted(reports.glob('*_model_view.json'),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not cands:
            raise HTTPException(
                404, f'no *_model_view.json under {reports} — generate the '
                     'SoC first (Build panel / make soc)')
        return cands[0]            # newest wins when several tops were built

    def _model_view_payload():
        p = _model_view_file()
        try:
            payload = json.loads(p.read_text())
        except ValueError as exc:
            raise HTTPException(500, f'corrupt model-view JSON {p.name}: {exc}')
        if not isinstance(payload, dict):
            raise HTTPException(500, f'{p.name}: top level is not a mapping')
        return p, payload

    @app.get('/api/generated')
    async def generated():
        p, payload = _model_view_payload()
        meta = payload.setdefault('meta', {})
        # source provenance folded into meta so both the viewer page and the
        # panel get it from the one body (shape stays the raw payload).
        meta['source_file'] = str(p)
        meta['source_mtime'] = p.stat().st_mtime
        return payload

    @app.get('/generated', response_class=HTMLResponse)
    async def generated_viewer():
        from ..backends.html import render_viewer_html
        _p, payload = _model_view_payload()
        meta = payload.get('meta') or {}
        return HTMLResponse(render_viewer_html(
            str(meta.get('name') or 'soc'), '/api/generated',
            len(payload.get('validation') or [])))

    @app.post('/api/draft/{draft_id}/apply')
    async def apply(draft_id: str, body: Optional[Dict[str, Any]] = None):
        if not adapter.top_yaml:
            raise HTTPException(400, 'project has no top_yaml configured')
        text = drafts.read(draft_id)
        res = svc.validate(_parse_yaml(text))
        target = adapter.path(adapter.top_yaml)
        current = target.read_text() if target.is_file() else ''
        diff = ''.join(difflib.unified_diff(
            current.splitlines(True), text.splitlines(True),
            fromfile=str(adapter.top_yaml), tofile=f'draft:{draft_id}'))
        if (body or {}).get('dry', True):
            return {'dry': True, 'diff': diff, 'validation': res}
        if not res['ok']:
            raise HTTPException(409, 'validation errors — not applying')
        svc._safe_path(target, adapter.root)   # path guard
        target.write_text(text)
        return {'dry': False, 'applied': str(target), 'diff': diff,
                'validation': res}

    # ------------------------------------------------------------- build
    # 'sim' (cocotb smoke) and 'fpga' (Vivado bitstream) are opt-in stages
    # (backlog #11); default is still ['validate', 'soc'].
    _STAGE_ORDER = ['validate', 'soc', 'firmware', 'sim', 'fpga']

    def _stages_for(names: List[str], overrides=None) -> List[Stage]:
        stages = []
        env_script = (str(adapter.path(adapter.env_script))
                      if adapter.env_script else None)
        flags = svc.override_flags(overrides) if overrides else ''
        for name in _STAGE_ORDER:
            if name not in names:
                continue
            cmd = adapter.make.get(name)
            if name == 'validate' and not cmd:
                top = adapter.path(adapter.top_yaml) if adapter.top_yaml else None
                if top is None:
                    continue
                libs = ' '.join(f'--lib-dir {d}' for d in adapter.abs_lib_dirs)
                cmd = (f'python3 -m soc_model {top} {libs} '
                       '{overrides} --validate-only')
            if not cmd:
                raise HTTPException(
                    400, f"no make.{name} command in the project adapter")
            cmd = svc.apply_overrides_to_cmd(cmd, flags)
            stages.append(Stage(name=name, cmd=cmd, cwd=adapter.root,
                                env_script=env_script))
        return stages

    @app.post('/api/build')
    async def build(body: Optional[Dict[str, Any]] = None):
        if runner.busy:
            raise HTTPException(409, 'a build is already running')
        names = (body or {}).get('stages') or ['validate', 'soc']
        bad = [n for n in names if n not in _STAGE_ORDER]
        if bad:
            raise HTTPException(400, f'unknown stage(s): {bad}')
        stages = _stages_for(names, (body or {}).get('overrides'))
        loop_holder['loop'] = asyncio.get_running_loop()

        async def _run_and_stamp():
            job = await runner.run(stages)
            # record a build manifest after a successful soc (RTL) build so the
            # staleness banner (#3) can tell when the YAML drifts from build_soc
            ok_soc = any(r.name == 'soc' and r.status == 'ok'
                         for r in job.results)
            if ok_soc and adapter.top_yaml:
                try:
                    svc.record_build_manifest(
                        adapter.path(adapter.top_yaml), adapter.abs_lib_dirs,
                        adapter.path(adapter.build_dir))
                except Exception:
                    pass

        asyncio.get_running_loop().create_task(_run_and_stamp())
        return {'started': [s.name for s in stages]}

    @app.get('/api/build/status')
    async def build_status():
        job = runner.current
        return job.to_dict() if job else {'status': 'idle', 'stages': []}

    @app.post('/api/build/cancel')
    async def build_cancel():
        runner.cancel()
        return {'cancelling': runner.busy}

    # ---------------------------------------------------------------- ws
    @app.websocket('/ws')
    async def ws(websocket: WebSocket):
        await websocket.accept()
        loop_holder['loop'] = asyncio.get_running_loop()
        sockets.append(websocket)
        try:
            job = runner.current
            await websocket.send_json({'type': 'hello',
                                       'job': job.to_dict() if job else None})
            while True:
                await websocket.receive_text()   # keepalive / ignore input
        except WebSocketDisconnect:
            pass
        finally:
            if websocket in sockets:
                sockets.remove(websocket)

    return app
