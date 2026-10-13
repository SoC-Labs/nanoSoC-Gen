# Copyright 2026, SoC Labs (www.soclabs.org)
"""WP2: build, test, simulate and lint tools over JobRunner.

Commands come from the project adapter's ``make:`` map (``soc``, ``test``,
``sim``, ``lint``); generator defaults apply when a key is absent. Every
tool returns a small dict; logs stay on disk. A run that did not finish
cleanly is never reported as PASS.
"""

import collections
import os
import re
import shlex
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..configurator.jobs import JobRunner, Stage
from .server import _S, _err, _require_project, srv

_NAME = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.-]*$')
_TAIL = 20
_busy = False


async def _exec(stage: Stage, keep: int = 400):
    """Run one stage. Returns (rc, last `keep` output lines, seconds).
    The single seam tests replace."""
    tail = collections.deque(maxlen=keep)
    runner = JobRunner(lambda e: tail.append(e['line'])
                       if e.get('type') == 'line' else None)
    job = await runner.run([stage])
    r = job.results[0]
    rc = r.rc if r.rc is not None else -1
    return rc, list(tail), r.seconds


def _stage(name: str, cmd: str, env: Optional[Dict[str, str]] = None) -> Stage:
    ad = _S.adapter
    e = {'SOCLABS_PROJECT_DIR': ad.root}
    e.update(env or {})
    return Stage(name=name, cmd=cmd, cwd=ad.root, env=e,
                 env_script=ad.env_script)


def _bad_names(names: Optional[List[str]], what: str):
    for n in names or []:
        if not isinstance(n, str) or not _NAME.match(n):
            return _err('bad_input', f'invalid {what} name: {n!r}')
    return None


def _snapshot(d: Path) -> Dict[str, tuple]:
    out = {}
    if d.is_dir():
        for p in d.rglob('*'):
            try:
                if p.is_file():
                    st = p.stat()
                    out[str(p.relative_to(d))] = (st.st_mtime_ns, st.st_size)
            except OSError:
                pass
    return out


async def _guarded(fn):
    """Project check, one build at a time, uniform error mapping."""
    global _busy
    bad = _require_project()
    if bad:
        return bad
    if _busy:
        return _err('busy', 'another build/test/sim tool call is running')
    _busy = True
    try:
        return await fn()
    finally:
        _busy = False


def _default_gen_cmd(only: Optional[List[str]]) -> str:
    ad = _S.adapter
    libs = ' '.join('-l ' + shlex.quote(d) for d in ad.abs_lib_dirs)
    cmd = (f'{shlex.quote(sys.executable)} -m soc_model '
           f'{shlex.quote(str(ad.path(ad.top_yaml)))} {libs} '
           f'-b {shlex.quote(str(ad.path(ad.build_dir)))}')
    if only:
        cmd += ' --only ' + ','.join(only)
    return cmd


@srv.tool()
async def soc_generate(only: Optional[List[str]] = None) -> Dict[str, Any]:
    """soc.generate: run the project's generate stage (make.soc, or `python -m soc_model [--only a,b]`); returns stages, seconds, files_changed, freshness."""
    bad = _bad_names(only, 'backend')
    if bad:
        return bad

    async def run():
        ad = _S.adapter
        if not ad.top_yaml:
            return _err('no_top', 'project has no top_yaml')
        cmd = (_default_gen_cmd(only) if only or 'soc' not in ad.make
               else ad.make['soc'])
        bdir = ad.path(ad.build_dir)
        before = _snapshot(bdir)
        t0 = time.time()
        pkg = str(Path(__file__).resolve().parents[2])
        pp = pkg + (os.pathsep + os.environ['PYTHONPATH']
                    if os.environ.get('PYTHONPATH') else '')
        rc, lines, secs = await _exec(
            _stage('generate', cmd, {'PYTHONPATH': pp}))
        after = _snapshot(bdir)
        changed = sorted(k for k, v in after.items() if before.get(k) != v)
        fresh = _S.svc.build_freshness(str(ad.path(ad.top_yaml)),
                                       ad.abs_lib_dirs, str(bdir))
        return {'ok': rc == 0,
                'stages': [{'name': 'generate', 'rc': rc,
                            'status': 'ok' if rc == 0 else 'failed',
                            'seconds': secs}],
                'seconds': round(time.time() - t0, 2),
                'files_changed': changed, 'build_dir': str(bdir),
                'freshness': fresh,
                'tail': lines[-_TAIL:] if rc else []}
    return await _guarded(run)


_LIST_ROW = re.compile(r'^\s+(\S+)\s+\[([^\]]+)\]\s+(.*)$')


def _test_base() -> str:
    return _S.adapter.make.get('test') or 'python3 verif/run_tests.py'


@srv.tool()
async def test_list() -> Dict[str, Any]:
    """test.list: the items `run_tests.py --list` knows: name, group, description."""
    async def run():
        rc, lines, _ = await _exec(_stage('test_list',
                                          _test_base() + ' --list'))
        rows = []
        for ln in lines:
            m = _LIST_ROW.match(ln)
            if m:
                rows.append({'name': m.group(1), 'group': m.group(2),
                             'description': m.group(3).strip()})
        if rc != 0 or not rows:
            return _err('list_failed', f'--list exit {rc}, {len(rows)} rows',
                        tail=lines[-_TAIL:])
        return {'ok': True, 'items': rows}
    return await _guarded(run)


@srv.tool()
async def test_run(only: Optional[List[str]] = None,
                   skip: Optional[List[str]] = None,
                   jobs: int = 1) -> Dict[str, Any]:
    """test.run: `run_tests.py --only .. --jobs N`; returns the rows of build/qv/test/results.json (name, status, detail, log)."""
    for names, what in ((only, 'only'), (skip, 'skip')):
        bad = _bad_names(names, what)
        if bad:
            return bad
    if not isinstance(jobs, int) or not 1 <= jobs <= 32:
        return _err('bad_input', 'jobs must be 1..32')

    async def run():
        import json
        ad = _S.adapter
        res = ad.path('build/qv/test/results.json')
        cmd = f'{_test_base()} --jobs {jobs}'
        if only:
            cmd += ' --only ' + ','.join(only)
        if skip:
            cmd += ' --skip ' + ','.join(skip)
        rc, lines, secs = await _exec(_stage('test', cmd))
        if not res.is_file() or res.stat().st_mtime < time.time() - secs - 5:
            return _err('no_results', f'run_tests exit {rc}; no fresh '
                        f'results.json', tail=lines[-_TAIL:])
        doc = json.loads(res.read_text())
        rows = [{'name': i['name'], 'status': i['status'],
                 'detail': i.get('detail', ''), 'seconds': i.get('seconds'),
                 'log': i.get('log')} for i in doc.get('items', [])]
        failed = [r['name'] for r in rows if r['status'] == 'FAIL']
        return {'ok': rc == 0 and not failed, 'rc': rc, 'seconds': secs,
                'counts': dict(collections.Counter(r['status'] for r in rows)),
                'failed': failed, 'items': rows,
                'results_json': str(res)}
    return await _guarded(run)


_STOP = re.compile(r'\$stop at(?: time)? (\d+)\s*(\w+)?')


def sim_verdict(rc: int, lines: List[str]) -> Dict[str, Any]:
    """PASS only on rc 0 with the pass banner and no timeout/failure text."""
    text = '\n'.join(lines)
    stop = None
    for ln in lines:
        m = _STOP.search(ln)
        if m:
            stop = f'{m.group(1)} {m.group(2) or "ns"}'.strip()
    bad = re.search(r'timed out|TIMEOUT|FAILED|TEST FAILED|%Error', text)
    if rc == 0 and '** TEST PASSED **' in text and not bad:
        return {'verdict': 'PASS', 'stop_time': stop}
    return {'verdict': 'SETUP' if rc == 2 else 'FAIL', 'stop_time': stop}


@srv.tool()
async def sim_run(testname: str = 'hello', timeout: Optional[int] = None,
                  accelerator: Optional[str] = None,
                  accel: Optional[str] = None) -> Dict[str, Any]:
    """sim.run: `make sim SIM=verilator TESTNAME=..`; returns verdict, stop_time, 20-line console tail, log path."""
    if not _NAME.match(testname or ''):
        return _err('bad_input', f'invalid testname: {testname!r}')
    if timeout is not None and not (isinstance(timeout, int)
                                    and 1 <= timeout <= 86400):
        return _err('bad_input', 'timeout must be 1..86400 seconds')
    for v in (accelerator, accel):
        if v is not None and not _NAME.match(v):
            return _err('bad_input', f'invalid value: {v!r}')

    async def run():
        ad = _S.adapter
        base = ad.make.get('sim') or 'make sim SIM=verilator'
        cmd = f'{base} TESTNAME={testname}'
        if timeout:
            cmd += f' SIM_TIMEOUT={timeout}'
        if accelerator:
            cmd += f' ACCELERATOR={accelerator}'
        if accel:
            cmd += f' ACCEL={accel}'
        rc, lines, secs = await _exec(_stage('sim', cmd))
        out = sim_verdict(rc, lines)
        log = ad.path(f'simulate/sim/{testname}/verilator/logs/'
                      f'run_{testname}.log')
        out.update({'ok': out['verdict'] == 'PASS', 'rc': rc,
                    'seconds': secs, 'console_tail': lines[-_TAIL:],
                    'log': str(log), 'log_exists': log.is_file()})
        return out
    return await _guarded(run)


@srv.tool()
async def lint_run() -> Dict[str, Any]:
    """lint.run: `make lint` (Verilator, waivers); returns verdict, console tail."""
    async def run():
        cmd = _S.adapter.make.get('lint') or 'make lint'
        rc, lines, secs = await _exec(_stage('lint', cmd))
        return {'ok': rc == 0, 'verdict': 'PASS' if rc == 0 else 'FAIL',
                'rc': rc, 'seconds': secs, 'console_tail': lines[-_TAIL:]}
    return await _guarded(run)
