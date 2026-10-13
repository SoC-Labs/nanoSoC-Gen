# Copyright 2026, SoC Labs (www.soclabs.org)
"""Endpoint test for multi-draft A/B compare (doc 06a #14)."""
from pathlib import Path
import pytest
pytestmark = pytest.mark.unit
FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'
try:
    import fastapi  # noqa: F401
    _HAVE = True
except ImportError:
    _HAVE = False
needs_fastapi = pytest.mark.skipif(not _HAVE, reason='configurator extra not installed')


@needs_fastapi
class TestCompare:
    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient
        from soc_model.configurator.app import create_app
        from soc_model.configurator.project import ProjectAdapter
        return TestClient(create_app(ProjectAdapter(
            root=str(tmp_path), lib_dirs=[str(FIXTURES)], templates=[str(FIXTURES)])))

    def test_compare_diff_and_validation(self, client):
        a = client.post('/api/drafts', json={'scratch': {'name': 'a'}}).json()['id']
        b = client.post('/api/drafts', json={'scratch': {'name': 'b'}}).json()['id']
        # make b differ from a (add a memory)
        client.post(f'/api/draft/{b}/component',
                    json={'module': 'tiny_region', 'component_id': 'sram_block'})
        r = client.get(f'/api/draft/{a}/compare?other={b}').json()
        assert r['a'] == a and r['b'] == b
        assert r['diff']                       # they differ
        assert 'tiny_region' in r['diff'] or 'sram_block' in r['diff']
        assert set(r['a_validation']) >= {'errors', 'warnings', 'ok', 'checks', 'total'}

    def test_compare_identical_empty_diff(self, client):
        a = client.post('/api/drafts', json={'scratch': {'name': 'x'}}).json()['id']
        b = client.post('/api/drafts', json={'scratch': {'name': 'x'}}).json()['id']  # same seed
        r = client.get(f'/api/draft/{a}/compare?other={b}').json()
        assert r['diff'] == ''                 # identical content
