# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for PARAM_OVERRIDES (doc 06a idea #9): GuiService.params_info /
override_flags / apply_overrides_to_cmd + the /params and override-aware build
endpoints."""

from pathlib import Path

import pytest

from soc_model.gui_service import GuiService

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'


@pytest.fixture()
def svc():
    return GuiService(base_dir=str(FIXTURES), lib_dirs=[str(FIXTURES)])


class TestParamsInfo:
    def test_dict_form(self, svc):
        info = {p['name']: p for p in svc.params_info(GuiService.new_soc('x'))}
        assert info['SYS_ADDR_W']['default'] == 32
        assert info['SYS_CLK_FREQ_HZ']['desc']

    def test_scalar_and_list_forms(self, svc):
        assert {p['name']: p['default'] for p in
                svc.params_info({'module': {'params': {'A': 5}}})} == {'A': 5}
        lst = svc.params_info({'module': {'params': [
            {'name': 'B', 'default': 7, 'desc': 'd'}]}})
        assert lst[0] == {'name': 'B', 'default': 7, 'type': None, 'desc': 'd'}

    def test_no_params(self, svc):
        assert svc.params_info({'module': {'name': 'x'}}) == []


class TestOverrideFlags:
    def test_dict_and_lists(self):
        assert GuiService.override_flags({'A': 14}) == '--config-override A=14'
        assert GuiService.override_flags(['A=1', 'B=2']) == \
            '--config-override A=1 --config-override B=2'
        assert GuiService.override_flags([{'name': 'X', 'value': 3}]) == \
            '--config-override X=3'

    def test_injection_is_rejected(self):
        # space in name, shell metachars in value -> dropped, not spliced
        assert GuiService.override_flags({'bad name': '1'}) == ''
        assert GuiService.override_flags({'OK': 'a; rm -rf /'}) == ''
        assert GuiService.override_flags({'OK': '$(whoami)'}) == ''
        assert GuiService.override_flags({'OK': '2.0-rc1'}) == \
            '--config-override OK=2.0-rc1'

    def test_empty(self):
        assert GuiService.override_flags(None) == ''
        assert GuiService.override_flags({}) == ''


class TestApplyOverrides:
    def test_placeholder_substituted(self):
        assert GuiService.apply_overrides_to_cmd(
            'make x {overrides}', '--config-override A=1') == \
            'make x --config-override A=1'

    def test_no_placeholder_unchanged(self):
        assert GuiService.apply_overrides_to_cmd(
            'make soc_model_fpga', '--config-override A=1') == \
            'make soc_model_fpga'

    def test_empty_flags_collapse_whitespace(self):
        assert GuiService.apply_overrides_to_cmd(
            'py {overrides} --validate-only', '') == 'py --validate-only'


# ---------------------------------------------------------------------------
# endpoints (skip without fastapi)
# ---------------------------------------------------------------------------
try:
    import fastapi  # noqa: F401
    _HAVE_FASTAPI = True
except ImportError:
    _HAVE_FASTAPI = False

needs_fastapi = pytest.mark.skipif(
    not _HAVE_FASTAPI, reason='configurator extra (fastapi) not installed')


@needs_fastapi
class TestEndpoints:
    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient
        from soc_model.configurator.app import create_app
        from soc_model.configurator.project import ProjectAdapter
        # a harmless soc stage with the {overrides} slot so a build is safe
        return TestClient(create_app(ProjectAdapter(
            root=str(tmp_path), lib_dirs=[str(FIXTURES)],
            templates=[str(FIXTURES)],
            make={'soc': 'echo built {overrides}'})))

    def test_params_endpoint(self, client):
        did = client.post('/api/drafts',
                          json={'scratch': {'name': 's'}}).json()['id']
        params = client.get(f'/api/draft/{did}/params').json()['params']
        assert {p['name'] for p in params} >= {'SYS_ADDR_W', 'SYS_CLK_FREQ_HZ'}

    def test_build_accepts_overrides(self, client):
        client.post('/api/drafts', json={'scratch': {'name': 's'}})
        r = client.post('/api/build',
                        json={'stages': ['soc'], 'overrides': {'A': '1'}})
        assert r.status_code == 200 and 'soc' in r.json()['started']
