# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for the Builder-wizard enablers (doc 06a — from-scratch flow):
GuiService.new_soc / add_component / remove_component + their endpoints."""

from pathlib import Path

import pytest

from soc_model.gui_service import GuiService

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'


@pytest.fixture()
def svc():
    return GuiService(base_dir=str(FIXTURES), lib_dirs=[str(FIXTURES)])


# ---------------------------------------------------------------------------
# new_soc seed
# ---------------------------------------------------------------------------
class TestNewSoc:
    def test_seed_shape_and_validates(self, svc):
        data = GuiService.new_soc('demo_soc', clk_hz=25000000)
        m = data['module']
        assert m['name'] == 'demo_soc' and m['gen'] is True
        assert m['params']['SYS_CLK_FREQ_HZ']['default'] == 25000000
        assert m['interconnects'][0]['initiators'][0]['name'] == 'cpu_m'
        res = svc.validate(data)
        ids = {c['id']: c['passed'] for c in res['checklist']}
        # skeleton passes the wiring rules but not the content rules
        assert ids['cpu-initiator'] and ids['clock-present'] \
            and ids['reset-present']
        assert not ids['memory-target']

    def test_seed_builds_clean_model(self, svc):
        module, msgs = svc.build_model(GuiService.new_soc('x'))
        assert module is not None and not msgs
        assert module.interconnects[0].initiators[0].name == 'cpu_m'


# ---------------------------------------------------------------------------
# add_component
# ---------------------------------------------------------------------------
class TestAddComponent:
    def test_memory_add_wires_and_satisfies_rule(self, svc):
        res = svc.add_component(GuiService.new_soc('x'), 'tiny_region',
                                size=0x1000)
        assert res['instance'] == 'u_tiny_region'
        assert res['target'] == 'sram_block_0'
        assert set(res['wired']) == {'HCLK', 'HRESETn', 'ahb_slave'}
        v = svc.validate(res['data'])
        assert v['errors'] == 0
        ids = {c['id']: c['passed'] for c in v['checklist']}
        assert ids['memory-target']      # region has srams -> memory
        ic = res['data']['module']['interconnects'][0]
        t = ic['targets'][0]
        assert t['region_type'] == 'memory' and t['sw_access'] == 'rwx'
        # initiator granted visibility
        assert {'name': 'sram_block_0'} in ic['initiators'][0]['targets']

    def test_unique_names_and_free_base(self, svc):
        d = GuiService.new_soc('x')
        r1 = svc.add_component(d, 'tiny_region', size=0x1000)
        r2 = svc.add_component(r1['data'], 'tiny_region', size=0x1000)
        assert r2['instance'] == 'u_tiny_region_1'
        assert r2['target'] == 'sram_block_1'
        ts = r2['data']['module']['interconnects'][0]['targets']
        assert ts[1]['base'] >= ts[0]['base'] + ts[0]['size']  # no overlap
        assert svc.validate(r2['data'])['errors'] == 0

    def test_unknown_module_raises(self, svc):
        with pytest.raises(ValueError, match='unknown module'):
            svc.add_component(GuiService.new_soc('x'), 'no_such_ip')

    def test_input_not_mutated(self, svc):
        d = GuiService.new_soc('x')
        before = repr(d)
        svc.add_component(d, 'tiny_region')
        assert repr(d) == before

    def test_unwired_ports_reported(self, svc):
        res = svc.add_component(GuiService.new_soc('x'), 'tiny_apb_periph')
        assert any(p.startswith('APB_') for p in res['unwired'])


# ---------------------------------------------------------------------------
# remove_component
# ---------------------------------------------------------------------------
class TestRemoveComponent:
    def test_removes_instance_targets_and_refs(self, svc):
        d = svc.add_component(GuiService.new_soc('x'), 'tiny_region')['data']
        res = svc.remove_component(d, 'u_tiny_region')
        m = res['data']['module']
        assert res['removed_targets'] == ['sram_block_0']
        assert m['instances'] == []
        ic = m['interconnects'][0]
        assert ic['targets'] == []
        assert ic['initiators'][0]['targets'] == []
        assert svc.validate(res['data'])['errors'] >= 1  # memory rule again

    def test_unknown_instance_raises(self, svc):
        with pytest.raises(ValueError, match='no instance'):
            svc.remove_component(GuiService.new_soc('x'), 'u_ghost')


# ---------------------------------------------------------------------------
# endpoints (skip without fastapi — pattern from test_configurator.py)
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
    return TestClient(create_app(ProjectAdapter(
        root=str(tmp_path), lib_dirs=[str(FIXTURES)],
        templates=[str(FIXTURES)])))


@needs_fastapi
class TestBuilderEndpoints:
    def test_scratch_component_lifecycle(self, client):
        did = client.post('/api/drafts', json={
            'name': 'wiz', 'scratch': {'name': 'wiz_soc'}}).json()['id']
        r = client.post(f'/api/draft/{did}/component',
                        json={'module': 'tiny_region', 'size': 4096}).json()
        assert r['target'] == 'sram_block_0'
        assert r['validation']['errors'] == 0
        assert 'u_tiny_region' in client.get(f'/api/draft/{did}').json()['yaml']
        rm = client.delete(
            f'/api/draft/{did}/component/u_tiny_region').json()
        assert rm['removed_targets'] == ['sram_block_0']
        assert client.delete(f'/api/draft/{did}').json()['deleted'] == did
        assert did not in client.get('/api/drafts').json()['drafts']

    def test_bad_component_400_and_missing_404(self, client):
        did = client.post('/api/drafts', json={'scratch': {}}).json()['id']
        assert client.post(f'/api/draft/{did}/component',
                           json={'module': 'nope'}).status_code == 400
        assert client.delete(
            f'/api/draft/{did}/component/u_ghost').status_code == 404
        assert client.delete('/api/draft/ghostdraft').status_code == 404
