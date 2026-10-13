# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for the field-hint tooltip dict (backlog #4).

GuiService.field_hints() returns a flat dict mapping YAML field names to
human-readable descriptions so the Compose panel can populate ``title``
attributes without any knowledge of the schema.
"""

from pathlib import Path

import pytest

from soc_model.gui_service import GuiService

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'


@pytest.fixture()
def svc():
    return GuiService(base_dir=str(FIXTURES), lib_dirs=[str(FIXTURES)])


class TestFieldHints:
    """field_hints() must return a non-empty dict with at least the minimum
    required keys so the Compose panel's title attributes are always populated
    for the most common fields."""

    _REQUIRED = {'base', 'size', 'module', 'name', 'instance_name',
                 'region_type', 'sw_access', 'protocol'}

    def test_returns_dict(self, svc):
        h = svc.field_hints()
        assert isinstance(h, dict)

    def test_has_at_least_8_keys(self, svc):
        h = svc.field_hints()
        assert len(h) >= 8

    def test_required_keys_present(self, svc):
        h = svc.field_hints()
        missing = self._REQUIRED - set(h.keys())
        assert not missing, f'missing required hint keys: {missing}'

    def test_all_values_are_non_empty_strings(self, svc):
        h = svc.field_hints()
        bad = [k for k, v in h.items() if not isinstance(v, str) or not v.strip()]
        assert not bad, f'empty or non-string hint values for: {bad}'

    def test_base_hint_mentions_address(self, svc):
        # the 'base' hint should help users understand it's an address
        h = svc.field_hints()
        assert 'address' in h['base'].lower() or 'hex' in h['base'].lower() or 'addr' in h['base'].lower()

    def test_size_hint_mentions_size_or_power(self, svc):
        h = svc.field_hints()
        hint_lower = h['size'].lower()
        assert any(w in hint_lower for w in ('size', 'power', 'kb', 'byte'))

    def test_module_hint_mentions_lib_or_module(self, svc):
        h = svc.field_hints()
        hint_lower = h['module'].lower()
        assert any(w in hint_lower for w in ('module', 'lib', 'catalog', 'instantiat'))

    def test_static_method_callable_without_instance(self):
        # field_hints() is a static method — no GuiService instance needed
        h = GuiService.field_hints()
        assert 'name' in h

    def test_fastapi_endpoint(self):
        """GET /api/draft/{id}/hints returns the same dict wrapped in {hints:}."""
        try:
            import fastapi  # noqa: F401
        except ImportError:
            pytest.skip('fastapi not installed')
        import shutil
        from fastapi.testclient import TestClient
        from soc_model.configurator.app import create_app
        from soc_model.configurator.project import ProjectAdapter
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = pathlib.Path(tmp)
            top = tmp_p / 'sys_desc'
            top.mkdir()
            shutil.copy(FIXTURES / 'minimal_soc.yaml', top / 'minimal_soc.yaml')
            adapter = ProjectAdapter(root=str(tmp_p),
                                     top_yaml='sys_desc/minimal_soc.yaml',
                                     lib_dirs=[str(FIXTURES)])
            client = TestClient(create_app(adapter))
            # create a draft first
            draft_id = client.post('/api/drafts', json={'name': 'test'}).json()['id']
            r = client.get(f'/api/draft/{draft_id}/hints')
            assert r.status_code == 200
            body = r.json()
            assert 'hints' in body
            h = body['hints']
            assert isinstance(h, dict) and len(h) >= 8
            assert 'base' in h and 'size' in h and 'module' in h and 'name' in h
