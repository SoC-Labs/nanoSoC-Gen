# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for the configurator shell (doc 06a, M3): project adapter,
job runner, headless `check` CLI, and — when fastapi is installed — the
FastAPI endpoints via TestClient (skipped otherwise)."""

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from soc_model.configurator.jobs import JobRunner, Stage
from soc_model.configurator.project import load_project

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'


# ---------------------------------------------------------------------------
# project adapter
# ---------------------------------------------------------------------------
class TestProjectAdapter:
    def test_loads_adapter_file(self, tmp_path):
        (tmp_path / 'configurator.yaml').write_text(
            "top_yaml: sys_desc/top.yaml\n"
            "lib_dirs: [sys_desc, ip/sys_desc]\n"
            "env_script: set_env.sh\n"
            "make: { soc: make -C sys_desc }\n"
            "rules: rules/r.yaml\n")
        a = load_project(tmp_path)
        assert a.top_yaml == 'sys_desc/top.yaml'
        assert a.abs_lib_dirs == [str(tmp_path / 'sys_desc'),
                                  str(tmp_path / 'ip/sys_desc')]
        assert a.make['soc'] == 'make -C sys_desc'
        assert a.abs_rules == str(tmp_path / 'rules/r.yaml')
        assert a.drafts_dir == tmp_path / '.configurator' / 'drafts'

    def test_infers_defaults_without_file(self, tmp_path):
        sd = tmp_path / 'sys_desc'
        sd.mkdir()
        (sd / 'my_soc.yaml').write_text('module: {name: my_soc}\n')
        a = load_project(tmp_path)
        assert a.top_yaml == 'sys_desc/my_soc.yaml'
        assert a.lib_dirs == ['sys_desc']

    def test_missing_root_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_project(tmp_path / 'nope')


# ---------------------------------------------------------------------------
# job runner
# ---------------------------------------------------------------------------
class TestJobRunner:
    def _run(self, stages):
        events = []
        runner = JobRunner(listener=events.append)
        job = asyncio.get_event_loop_policy().new_event_loop() \
            .run_until_complete(runner.run(stages))
        return job, events

    def test_streams_lines_and_succeeds(self, tmp_path):
        job, events = self._run([
            Stage(name='hello', cmd='echo one; echo two', cwd=str(tmp_path)),
        ])
        assert job.status == 'ok'
        lines = [e['line'] for e in events if e['type'] == 'line']
        assert lines == ['one', 'two']
        assert job.results[0].status == 'ok' and job.results[0].rc == 0

    def test_failure_stops_pipeline(self, tmp_path):
        job, events = self._run([
            Stage(name='bad', cmd='echo pre; false', cwd=str(tmp_path)),
            Stage(name='never', cmd='echo nope', cwd=str(tmp_path)),
        ])
        assert job.status == 'failed'
        assert job.results[0].status == 'failed'
        assert job.results[1].status == 'skipped'
        assert all(e['line'] != 'nope' for e in events if e['type'] == 'line')

    def test_env_script_sourced(self, tmp_path):
        (tmp_path / 'env.sh').write_text('export SCAFFOLD_TOKEN=hi42\n')
        job, events = self._run([
            Stage(name='env', cmd='echo "tok=$SCAFFOLD_TOKEN"',
                  cwd=str(tmp_path), env_script=str(tmp_path / 'env.sh')),
        ])
        assert job.status == 'ok'
        assert any(e.get('line') == 'tok=hi42' for e in events)

    def test_broken_listener_does_not_kill_job(self, tmp_path):
        def boom(event):
            raise RuntimeError('listener bug')
        runner = JobRunner(listener=boom)
        loop = asyncio.get_event_loop_policy().new_event_loop()
        job = loop.run_until_complete(
            runner.run([Stage(name='x', cmd='true', cwd=str(tmp_path))]))
        assert job.status == 'ok'


# ---------------------------------------------------------------------------
# headless `check` CLI (no fastapi needed)
# ---------------------------------------------------------------------------
class TestCheckCli:
    def _check(self, *args):
        return subprocess.run(
            [sys.executable, '-m', 'soc_model.configurator', 'check', *args],
            capture_output=True, text=True,
            cwd=str(FIXTURES.parent.parent))   # repo root for -m import

    def test_clean_fixture_exits_zero(self):
        r = self._check(str(FIXTURES / 'minimal_soc.yaml'),
                        '--lib-dir', str(FIXTURES))
        assert r.returncode == 0, r.stdout + r.stderr
        assert 'System completeness' in r.stdout
        assert '0 error(s)' in r.stdout

    def test_broken_yaml_exits_nonzero(self, tmp_path):
        data = yaml.safe_load((FIXTURES / 'minimal_soc.yaml').read_text())
        ic = data['module']['interconnects'][0]
        t2 = dict(ic['targets'][0]); t2['name'] = 'dup'
        ic['targets'].append(t2)                       # overlap -> tier2 error
        ic['initiators'][0]['targets'].append({'name': 'dup'})
        bad = tmp_path / 'bad.yaml'
        bad.write_text(yaml.dump(data, sort_keys=False))
        r = self._check(str(bad), '--lib-dir', str(FIXTURES))
        assert r.returncode == 1
        assert '[ERROR]' in r.stdout


# ---------------------------------------------------------------------------
# FastAPI app (runs only where the optional extra is installed)
# ---------------------------------------------------------------------------
try:
    import fastapi  # noqa: F401
    _HAVE_FASTAPI = True
except ImportError:
    _HAVE_FASTAPI = False

needs_fastapi = pytest.mark.skipif(
    not _HAVE_FASTAPI, reason='configurator extra (fastapi) not installed')


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient
    from soc_model.configurator.app import create_app
    from soc_model.configurator.project import ProjectAdapter

    # project root with a real top yaml + fixture lib dir
    top = tmp_path / 'sys_desc'
    top.mkdir()
    shutil.copy(FIXTURES / 'minimal_soc.yaml', top / 'minimal_soc.yaml')
    adapter = ProjectAdapter(root=str(tmp_path),
                             top_yaml='sys_desc/minimal_soc.yaml',
                             lib_dirs=[str(FIXTURES)],
                             templates=[str(FIXTURES)])
    return TestClient(create_app(adapter))


@needs_fastapi
class TestApp:
    def test_index_serves(self, client):
        r = client.get('/')
        assert r.status_code == 200
        assert 'SoC' in r.text and 'Configurator' in r.text

    def test_project_and_catalog(self, client):
        p = client.get('/api/project').json()
        assert p['health']['top_yaml_exists'] is True
        cat = client.get('/api/catalog').json()
        assert 'ahb_target_region' in cat['presets']

    def test_draft_lifecycle_and_validation(self, client):
        r = client.post('/api/drafts', json={'name': 'a', 'from_top': True})
        draft_id = r.json()['id']
        assert draft_id in client.get('/api/drafts').json()['drafts']
        text = client.get(f'/api/draft/{draft_id}').json()['yaml']
        assert 'minimal_soc' in text
        res = client.put(f'/api/draft/{draft_id}',
                         json={'yaml': text}).json()
        assert res['ok'] is True and res['checklist']
        d = client.get(f'/api/draft/{draft_id}/diagram').json()
        assert d['ok'] and d['spines'][0]['initiators'][0]['kind'] == 'cpu'

    def test_apply_dry_returns_diff(self, client):
        draft_id = client.post('/api/drafts',
                               json={'name': 'b', 'from_top': True}).json()['id']
        text = client.get(f'/api/draft/{draft_id}').json()['yaml']
        client.put(f'/api/draft/{draft_id}',
                   json={'yaml': text.replace('Minimal', 'Edited')})
        r = client.post(f'/api/draft/{draft_id}/apply',
                        json={'dry': True}).json()
        assert r['dry'] is True and 'Edited' in r['diff']

    def test_bad_yaml_is_422(self, client):
        draft_id = client.post('/api/drafts', json={'name': 'c'}).json()['id']
        r = client.put(f'/api/draft/{draft_id}', json={'yaml': ':\n  - ['})
        assert r.status_code == 422

    def test_unknown_build_stage_rejected(self, client):
        r = client.post('/api/build', json={'stages': ['rm-rf']})
        assert r.status_code == 400
