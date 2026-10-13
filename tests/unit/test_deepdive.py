# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for the draft deep-dive (doc 06a #5): GuiService.deepdive_html
renders the full D3 connectivity page from a built draft; the /deepdive endpoint
serves it."""

from pathlib import Path
import pytest
from soc_model.gui_service import GuiService

pytestmark = pytest.mark.unit
FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'


@pytest.fixture()
def svc():
    return GuiService(base_dir=str(FIXTURES), lib_dirs=[str(FIXTURES)])


class TestDeepdive:
    def test_renders_html_for_buildable_draft(self, svc):
        d = svc.add_component(GuiService.new_soc('dd'), 'tiny_region',
                              size=0x1000)['data']
        html = svc.deepdive_html(d)
        assert html.lstrip().lower().startswith('<!doctype html')
        assert 'dd' in html            # the SoC name appears
        assert 'd3' in html.lower() or '<svg' in html.lower() or 'data' in html.lower()

    def test_unbuildable_raises(self, svc, monkeypatch):
        # when the model can't build, deepdive surfaces it as ValueError
        monkeypatch.setattr(svc, 'build_model',
                            lambda data: (None, [{'message': 'boom'}]))
        with pytest.raises(ValueError, match='boom'):
            svc.deepdive_html(GuiService.new_soc('x'))


try:
    import fastapi  # noqa: F401
    _HAVE_FASTAPI = True
except ImportError:
    _HAVE_FASTAPI = False

needs_fastapi = pytest.mark.skipif(
    not _HAVE_FASTAPI, reason='configurator extra not installed')


@needs_fastapi
class TestDeepdiveEndpoint:
    def test_endpoint_serves_html(self, tmp_path):
        from fastapi.testclient import TestClient
        from soc_model.configurator.app import create_app
        from soc_model.configurator.project import ProjectAdapter
        c = TestClient(create_app(ProjectAdapter(
            root=str(tmp_path), lib_dirs=[str(FIXTURES)], templates=[str(FIXTURES)])))
        did = c.post('/api/drafts', json={'scratch': {'name': 'dd'}}).json()['id']
        c.post(f'/api/draft/{did}/component',
               json={'module': 'tiny_region', 'component_id': 'sram_block'})
        r = c.get(f'/api/draft/{did}/deepdive')
        assert r.status_code == 200
        assert 'text/html' in r.headers['content-type']
        assert '<!doctype html' in r.text.lstrip().lower()
