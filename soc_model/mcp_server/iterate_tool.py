# Copyright 2026, SoC Labs (www.soclabs.org)
"""WP5: ``soc_iterate`` -- the edit/validate/generate/lint/test/sim loop in one call.

Stages run in order and stop at the first failure (``stop_on='first_fail'``,
the default) or run on regardless (``stop_on='never'``)::

    edit -> validate -> generate -> lint -> test -> sim -> debug

* ``edit``     each item of ``edits`` is ``{"tool": "add_component", ...args}``
               (tool = any soc_* edit tool, ``soc_`` prefix optional); applied as
               a chained draft, so the top YAML is untouched until validate passes.
* ``validate`` validation errors or a memory-map overlap stop the loop; the
               first finding names the overlapping targets. Then the draft is
               promoted to the top YAML (skipped when ``apply=False``).
* ``generate`` ``soc_generate`` (per-stage ``only``). Skipped with ``generate=False``.
* ``lint``     only with ``lint=True``.
* ``test``     ``test_run(only=test)`` when ``test`` is given.
* ``sim``      ``sim_run(testname=sim)`` when ``sim`` is given; Verilator only.
* ``debug``    ``sim_debug`` on the sim directory, only when the sim did not pass.

The result is small: per-stage status plus ``first_failure``.
"""

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_EDIT_TOOLS = {'add_component', 'remove_component', 'update_component',
               'set_irq_bit', 'set_param'}


def _stage(name: str, status: str, seconds: float = 0.0, **extra) -> Dict[str, Any]:
    d = {'name': name, 'status': status, 'seconds': round(seconds, 2)}
    d.update(extra)
    return d


def register(srv, S) -> None:
    from . import build_tools
    from .sim_debug_tool import sim_debug as _sim_debug

    async def _call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        import json
        res = await srv.call_tool(name, args)
        return json.loads(res.content[0].text)

    @srv.tool()
    async def soc_iterate(edits: Optional[List[Dict[str, Any]]] = None,
                          test: Optional[List[str]] = None,
                          sim: Optional[str] = 'hello',
                          simulator: str = 'verilator',
                          stop_on: str = 'first_fail',
                          only: Optional[List[str]] = None,
                          generate: bool = True, lint: bool = False,
                          apply: bool = True) -> Dict[str, Any]:
        """soc.iterate: edits (as a draft) -> validate -> generate -> lint -> test.run -> sim.run -> sim.debug in one call; returns per-stage status and the first failing finding. Stops at the first failure unless stop_on='never'. Verilator only."""
        if S.adapter is None:
            return {'ok': False, 'error': {
                'code': 'no_project', 'message': 'call soc_open_project first'}}
        if simulator != 'verilator':
            return {'ok': False, 'error': {
                'code': 'bad_input',
                'message': "simulator must be 'verilator' (no VCS in the loop)"}}
        if stop_on not in ('first_fail', 'never'):
            return {'ok': False, 'error': {
                'code': 'bad_input',
                'message': "stop_on must be 'first_fail' or 'never'"}}
        edits = edits or []
        for e in edits:
            t = str(e.get('tool', '')).removeprefix('soc_') if isinstance(e, dict) else ''
            if t not in _EDIT_TOOLS:
                return {'ok': False, 'error': {
                    'code': 'bad_input',
                    'message': 'edit needs "tool" in %s: %r'
                               % (sorted(_EDIT_TOOLS), e)}}

        t_all = time.time()
        stages: List[Dict[str, Any]] = []
        first: List[Optional[Dict[str, Any]]] = [None]
        stopped = [False]
        draft: List[Optional[str]] = [None]

        def fail(stage: str, finding: Any) -> None:
            if first[0] is None:
                first[0] = {'stage': stage, 'finding': finding}
            if stop_on == 'first_fail':
                stopped[0] = True

        def skip(name: str, why: str) -> None:
            stages.append(_stage(name, 'skipped', reason=why))

        def result() -> Dict[str, Any]:
            return {'ok': first[0] is None,
                    'verdict': 'PASS' if first[0] is None else 'FAIL',
                    'stages': stages, 'first_failure': first[0],
                    'draft_id': draft[0],
                    'seconds': round(time.time() - t_all, 2)}

        # edit
        t0 = time.time()
        if not edits:
            skip('edit', 'no edits')
        else:
            n = 0
            for e in edits:
                args = {k: v for k, v in e.items() if k != 'tool'}
                tool = 'soc_' + str(e['tool']).removeprefix('soc_')
                args['dry_run'] = True
                if draft[0]:
                    args['draft'] = draft[0]
                r = await _call(tool, args)
                if not r.get('ok'):
                    stages.append(_stage('edit', 'fail', time.time() - t0,
                                         applied=n))
                    fail('edit', r.get('error') or r)
                    break
                draft[0] = r['draft_id']
                n += 1
            else:
                stages.append(_stage('edit', 'ok', time.time() - t0,
                                     applied=n, draft_id=draft[0]))
        if stopped[0]:
            return result()

        # validate (the draft if there is one, else the top YAML)
        t0 = time.time()
        try:
            if draft[0]:
                data = S.svc.load_yaml(str(S.adapter.drafts_dir
                                           / (draft[0] + '.yaml')),
                                       S.adapter.root)
            else:
                data = S.svc.load_yaml(S.top, S.adapter.root)
            val = S.svc.validate(data)
            mm = S.svc.memory_map(data)
        except Exception as exc:  # unreadable YAML etc.
            stages.append(_stage('validate', 'fail', time.time() - t0))
            fail('validate', {'code': 'validate_exception',
                              'message': '%s: %s' % (type(exc).__name__, exc)})
            return result()
        overlaps = []
        if mm.get('ok'):
            overlaps = [{'interconnect': ic['name'], 'target': t['name'],
                         'with': t['overlaps']}
                        for ic in mm['interconnects'] for t in ic['targets']
                        if t['overlaps']]
        bad = (not val.get('ok', True)) or val.get('errors', 0) or overlaps
        if bad:
            stages.append(_stage('validate', 'fail', time.time() - t0,
                                 errors=val.get('errors', 0),
                                 overlaps=len(overlaps)))
            if overlaps:
                o = overlaps[0]
                fail('validate', {
                    'code': 'address_overlap',
                    'message': '%s overlaps %s on %s'
                               % (o['target'], ', '.join(o['with']),
                                  o['interconnect']),
                    'overlaps': overlaps})
            else:
                errs = [m for m in val.get('messages') or []
                        if isinstance(m, dict) and m.get('level') == 'error']
                fail('validate', {
                    'code': 'validation_failed',
                    'message': (errs[0].get('message') if errs else
                                '%d validation error(s)' % val.get('errors', 0)),
                    'errors': errs[:5]})
        else:
            extra = {}
            if draft[0] and apply:
                sv = await _call('soc_save', {'draft_id': draft[0]})
                if not sv.get('ok'):
                    stages.append(_stage('validate', 'fail', time.time() - t0))
                    fail('validate', sv.get('error') or sv)
                    return result()
                extra['saved'] = sv.get('saved')
                draft[0] = None
            elif draft[0]:
                extra['applied'] = False
            stages.append(_stage('validate', 'ok', time.time() - t0,
                                 warnings=val.get('warnings', 0), **extra))
        if stopped[0]:
            return result()
        if draft[0] and not apply:
            # dry run: nothing on disk changed, so later stages would test the old design
            for n in ('generate', 'lint', 'test', 'sim', 'debug'):
                skip(n, 'apply=False: draft not applied')
            return result()

        async def run_stage(name: str, mk, ok_key: str = 'ok') -> Optional[Dict[str, Any]]:
            if stopped[0]:
                skip(name, 'stopped at first failure')
                return None
            t0 = time.time()
            r = await mk()
            good = bool(r.get(ok_key))
            stages.append(_stage(name, 'ok' if good else 'fail', time.time() - t0))
            if not good:
                fail(name, r.get('error') or {
                    k: r[k] for k in ('verdict', 'failed', 'stop_time',
                                      'console_tail', 'tail', 'log') if k in r})
            return r

        if generate:
            await run_stage('generate', lambda: build_tools.soc_generate(only))
        else:
            skip('generate', 'generate=False')
        if lint:
            await run_stage('lint', build_tools.lint_run)
        else:
            skip('lint', 'lint=False')
        if test:
            await run_stage('test', lambda: build_tools.test_run(only=test))
        else:
            skip('test', 'no test requested')
        simr = None
        if sim:
            simr = await run_stage('sim', lambda: build_tools.sim_run(testname=sim))
        else:
            skip('sim', 'no sim requested')

        if simr is not None and not simr.get('ok'):
            d = Path(S.adapter.path('simulate/sim/%s/verilator' % sim))
            t0 = time.time()
            r = _sim_debug(str(d)) if d.is_dir() else {
                'ok': False, 'error': {'code': 'no_sim_dir',
                                       'message': 'no %s' % d}}
            stages.append(_stage('debug', 'ok' if r.get('ok') else 'fail',
                                 time.time() - t0,
                                 verdict=r.get('verdict')))
            if first[0] is not None and first[0]['stage'] == 'sim':
                first[0]['debug'] = (r.get('error') or
                                     {k: r[k] for k in ('verdict', 'findings')
                                      if k in r})
        else:
            skip('debug', 'sim passed or not run')
        return result()
