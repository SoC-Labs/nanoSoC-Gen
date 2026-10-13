# Copyright 2026, SoC Labs (www.soclabs.org)
"""Endpoint tests for the export/share/reuse loop:
#15 artifact zip, #12 save-as-template, #7 snapshot + shareable JSON."""

import io
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'

try:
    import fastapi  # noqa: F401
    _HAVE_FASTAPI = True
except ImportError:
    _HAVE_FASTAPI = False

needs_fastapi = pytest.mark.skipif(
    not _HAVE_FASTAPI, reason='configurator extra (fastapi) not installed')


@needs_fastapi
class TestExportShare:
    @pytest.fixture()
    def ctx(self, tmp_path):
        from fastapi.testclient import TestClient
        from soc_model.configurator.app import create_app
        from soc_model.configurator.project import ProjectAdapter
        tmpl = tmp_path / 'templates'
        tmpl.mkdir()
        adapter = ProjectAdapter(
            root=str(tmp_path), top_yaml='sys_desc/top.yaml',
            lib_dirs=[str(FIXTURES)], templates=[str(tmpl)],
            build_dir='build_soc')
        # seed a couple of artifact files
        for sub, fn in (('reports', 'r.txt'), ('flist', 'f.flist'),
                        ('constraints', 'c.sdc')):
            d = tmp_path / 'build_soc' / sub
            d.mkdir(parents=True)
            (d / fn).write_text('x')
        return TestClient(create_app(adapter)), tmpl

    # ---- #15 artifact zip ----
    def test_artifacts_zip(self, ctx):
        client, _ = ctx
        r = client.get('/api/artifacts/zip')
        assert r.status_code == 200
        assert r.headers['content-type'] == 'application/zip'
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = z.namelist()
        assert 'reports/r.txt' in names and 'flist/f.flist' in names
        assert 'constraints/c.sdc' in names

    def test_artifacts_zip_404_when_empty(self, tmp_path):
        from fastapi.testclient import TestClient
        from soc_model.configurator.app import create_app
        from soc_model.configurator.project import ProjectAdapter
        client = TestClient(create_app(ProjectAdapter(
            root=str(tmp_path), lib_dirs=[str(FIXTURES)])))
        assert client.get('/api/artifacts/zip').status_code == 404

    # ---- #7 snapshot + share ----
    def test_snapshot_creates_a_copy(self, ctx):
        client, _ = ctx
        did = client.post('/api/drafts',
                          json={'scratch': {'name': 'wip'}}).json()['id']
        snap = client.post(f'/api/draft/{did}/snapshot',
                           json={'name': 'v1'}).json()
        assert snap['from'] == did and snap['id'] != did
        drafts = client.get('/api/drafts').json()['drafts']
        assert snap['id'] in drafts and did in drafts
        # snapshot content matches the source
        assert client.get(f"/api/draft/{snap['id']}").json()['yaml'] == \
            client.get(f'/api/draft/{did}').json()['yaml']

    def test_share_blob(self, ctx):
        client, _ = ctx
        did = client.post('/api/drafts',
                          json={'scratch': {'name': 's'}}).json()['id']
        blob = client.get(f'/api/draft/{did}/share').json()
        assert blob['schema'] == 1 and blob['id'] == did
        assert 'module' in blob['yaml']
        assert 'errors' in blob['validation'] and 'adapter' in blob

    # ---- #12 save-as-template ----
    def test_save_as_template_and_reuse(self, ctx):
        client, tmpl = ctx
        did = client.post('/api/drafts',
                          json={'scratch': {'name': 'base'}}).json()['id']
        r = client.post(f'/api/draft/{did}/save-as-template',
                        json={'name': 'my tmpl'}).json()
        assert (tmpl / 'my-tmpl.yaml').is_file()
        # appears in the catalog templates and can seed a new draft
        cats = client.get('/api/catalog').json()
        assert any('my-tmpl.yaml' in t for t in cats['templates'])
        nd = client.post('/api/drafts',
                         json={'name': 'fromtmpl', 'template': r['template']})
        assert nd.status_code == 200

    def test_save_as_template_conflict(self, ctx):
        client, _ = ctx
        did = client.post('/api/drafts', json={'scratch': {'name': 'b'}}).json()['id']
        body = {'name': 'dup'}
        assert client.post(f'/api/draft/{did}/save-as-template',
                           json=body).status_code == 200
        assert client.post(f'/api/draft/{did}/save-as-template',
                           json=body).status_code == 409
