# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for the sim / fpga build stages (backlog #11).

The build pipeline now accepts 'sim' and 'fpga' as valid stage names in
addition to the original 'validate', 'soc', and 'firmware'.  The stages are
opt-in — default remains ['validate', 'soc'] — and the correct make command
must be launched when they are requested.
"""

import asyncio
from pathlib import Path

import pytest

from soc_model.configurator.jobs import JobRunner, Stage

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _run(stages):
    """Synchronously run a list of Stage objects through a fresh JobRunner."""
    events = []
    runner = JobRunner(listener=events.append)
    loop = asyncio.new_event_loop()
    try:
        job = loop.run_until_complete(runner.run(stages))
    finally:
        loop.close()
    return job, events


# ---------------------------------------------------------------------------
# JobRunner stage execution (stage-agnostic; just verifies the pipeline
# machinery works for arbitrary stage names)
# ---------------------------------------------------------------------------
class TestSimFpgaStagesViaRunner:
    def test_sim_stage_echoes_correct_target(self, tmp_path):
        """A 'sim' stage runs the configured make command and reports ok."""
        job, events = _run([
            Stage(name='sim', cmd='echo sim-target', cwd=str(tmp_path)),
        ])
        assert job.status == 'ok'
        lines = [e['line'] for e in events if e['type'] == 'line']
        assert 'sim-target' in lines

    def test_fpga_stage_echoes_correct_target(self, tmp_path):
        """An 'fpga' stage runs the configured make command and reports ok."""
        job, events = _run([
            Stage(name='fpga', cmd='echo fpga-target', cwd=str(tmp_path)),
        ])
        assert job.status == 'ok'
        lines = [e['line'] for e in events if e['type'] == 'line']
        assert 'fpga-target' in lines

    def test_stage_order_sim_then_fpga(self, tmp_path):
        """sim runs before fpga in a chained pipeline."""
        job, events = _run([
            Stage(name='sim',  cmd='echo sim-first', cwd=str(tmp_path)),
            Stage(name='fpga', cmd='echo fpga-second', cwd=str(tmp_path)),
        ])
        assert job.status == 'ok'
        lines = [e['line'] for e in events if e['type'] == 'line']
        assert lines.index('sim-first') < lines.index('fpga-second')

    def test_failed_sim_skips_fpga(self, tmp_path):
        """A failing sim stage marks the fpga stage as skipped."""
        job, events = _run([
            Stage(name='sim',  cmd='false', cwd=str(tmp_path)),
            Stage(name='fpga', cmd='echo should-not-run', cwd=str(tmp_path)),
        ])
        assert job.status == 'failed'
        assert job.results[0].status == 'failed'
        assert job.results[1].status == 'skipped'
        assert all(e.get('line') != 'should-not-run'
                   for e in events if e['type'] == 'line')


# ---------------------------------------------------------------------------
# FastAPI /api/build endpoint validation
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
    """TestClient wired to a project that declares all five make stages."""
    import shutil
    from fastapi.testclient import TestClient
    from soc_model.configurator.app import create_app
    from soc_model.configurator.project import ProjectAdapter

    top = tmp_path / 'sys_desc'
    top.mkdir()
    shutil.copy(FIXTURES / 'minimal_soc.yaml', top / 'minimal_soc.yaml')
    adapter = ProjectAdapter(
        root=str(tmp_path),
        top_yaml='sys_desc/minimal_soc.yaml',
        lib_dirs=[str(FIXTURES)],
        make={
            'validate': 'echo validate-ok',
            'soc':      'echo soc-ok',
            'firmware': 'echo firmware-ok',
            'sim':      'echo sim-ok',
            'fpga':     'echo fpga-ok',
        },
    )
    return TestClient(create_app(adapter))


@needs_fastapi
class TestBuildEndpoint:
    def test_unknown_stage_returns_400(self, client):
        r = client.post('/api/build', json={'stages': ['unknown-stage']})
        assert r.status_code == 400

    def test_sim_is_a_valid_stage(self, client):
        r = client.post('/api/build', json={'stages': ['sim']})
        assert r.status_code == 200
        assert 'sim' in r.json()['started']

    def test_fpga_is_a_valid_stage(self, client):
        r = client.post('/api/build', json={'stages': ['fpga']})
        assert r.status_code == 200
        assert 'fpga' in r.json()['started']

    def test_default_stages_unchanged(self, client):
        """No stages body => default ['validate', 'soc'] (unchanged behaviour)."""
        r = client.post('/api/build', json={})
        assert r.status_code == 200
        assert r.json()['started'] == ['validate', 'soc']

    def test_sim_and_fpga_together(self, client):
        r = client.post('/api/build', json={'stages': ['sim', 'fpga']})
        assert r.status_code == 200
        assert set(r.json()['started']) == {'sim', 'fpga'}

    def test_mixed_valid_and_invalid_returns_400(self, client):
        r = client.post('/api/build', json={'stages': ['sim', 'rm-rf']})
        assert r.status_code == 400
