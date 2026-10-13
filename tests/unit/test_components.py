# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for the component-package system (doc 06a — droppable IP with
tweakable knobs + HARD min/max limits)."""

from pathlib import Path

import pytest

from soc_model import components as C
from soc_model.gui_service import GuiService

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'


@pytest.fixture()
def svc():
    return GuiService(base_dir=str(FIXTURES), lib_dirs=[str(FIXTURES)])


# ---------------------------------------------------------------------------
# package loading + helpers
# ---------------------------------------------------------------------------
class TestLoad:
    def test_bundled_and_fixture_packages_load(self):
        pkgs = C.load_components([str(FIXTURES)])
        # bundled (reference real modules) + fixture packages
        assert 'ethernet_subsystem' in pkgs
        assert pkgs['ethernet_subsystem'].module == 'ethernet_ss_ahb_rmii'
        assert 'sram_block' in pkgs and pkgs['sram_block'].module == 'tiny_region'
        assert pkgs['sram_block'].category == 'memory'

    def test_parse_size_and_human(self):
        assert C.parse_size('16M') == 16 * 1024 ** 2
        assert C.parse_size('0x1000') == 4096
        assert C.parse_size('64K') == 65536
        assert C.parse_size('bogus') is None
        assert C.human_size(65536) == '64 KB'
        assert C.human_size(512 * 1024 ** 2) == '512 MB'

    def test_knob_size_choices_respect_min_max(self):
        k = C.Knob(param='X', kind='addr_w', default=14, min=13, max=16)
        ch = k.size_choices()
        assert [c['value'] for c in ch] == [13, 14, 15, 16]
        assert ch[0]['label'] == '8 KB' and ch[-1]['bytes'] == 1 << 16


# ---------------------------------------------------------------------------
# hard-limit enforcement
# ---------------------------------------------------------------------------
class TestLimits:
    def test_check_knob_below_min_rejected(self):
        k = C.Knob(param='RAM_ADDR_W', kind='addr_w', default=12, min=10)
        ok, msg = C.check_knob(k, 8)
        assert not ok and '256 B' in msg and '1 KB' in msg

    def test_check_knob_in_range_and_above_max(self):
        k = C.Knob(param='X', kind='int', default=4, min=2, max=8)
        assert C.check_knob(k, 4)[0]
        assert not C.check_knob(k, 9)[0]

    def test_check_knob_non_numeric_passes(self):
        k = C.Knob(param='X', kind='int', min=2)
        assert C.check_knob(k, '$SYS_ADDR_W')[0]   # leave $PARAM to validator

    def test_no_limits_always_ok(self):
        assert C.check_knob(C.Knob(param='X'), 0)[0]


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------
class TestCatalog:
    def test_components_lists_packaged_and_bare(self, svc):
        cat = svc.components()
        by_id = {c['id']: c for c in cat}
        # fixture packages available; bundled real-module ones flagged n/a
        assert by_id['sram_block']['available'] is True
        assert by_id['sram_block']['category'] == 'memory'
        assert by_id['ethernet_subsystem']['available'] is False
        # bare-module fallback exists for an unpackaged fixture module
        assert 'qspi_flash_ip' in by_id  # fixture module, no package -> bare
        # grouped by category order (subsystem/cpu/memory/... first)
        cats = [c['category'] for c in cat]
        assert cats == sorted(cats, key=lambda c: list(C.CATEGORIES).index(c)
                              if c in C.CATEGORIES else 99)

    def test_packaged_knob_has_choices_and_limits(self, svc):
        sram = next(c for c in svc.components() if c['id'] == 'sram_block')
        k = sram['knobs'][0]
        assert k['param'] == 'RAM_ADDR_W' and k['min'] == 10 and k['max'] == 16
        assert k['choices'][0]['label'] == '1 KB'


# ---------------------------------------------------------------------------
# add_component with knobs
# ---------------------------------------------------------------------------
class TestDropWithKnobs:
    def test_drop_applies_defaults(self, svc):
        r = svc.add_component(GuiService.new_soc('x'), 'tiny_region',
                              component_id='sram_block')
        inst = r['data']['module']['instances'][0]
        assert inst['params']['RAM_ADDR_W'] == 12      # package default
        assert svc.validate(r['data'])['errors'] == 0

    def test_tweak_sizes_the_window(self, svc):
        r = svc.add_component(GuiService.new_soc('x'), 'tiny_region',
                              component_id='sram_block',
                              params={'RAM_ADDR_W': 14})
        ic = r['data']['module']['interconnects'][0]
        assert ic['targets'][0]['size'] == 1 << 14     # window tracks the knob
        assert r['data']['module']['instances'][0]['params']['RAM_ADDR_W'] == 14

    def test_below_min_is_hard_error(self, svc):
        with pytest.raises(ValueError, match='hard limit'):
            svc.add_component(GuiService.new_soc('x'), 'tiny_region',
                              component_id='sram_block',
                              params={'RAM_ADDR_W': 8})

    def test_above_max_is_hard_error(self, svc):
        with pytest.raises(ValueError, match='hard limit'):
            svc.add_component(GuiService.new_soc('x'), 'tiny_region',
                              component_id='sram_block',
                              params={'RAM_ADDR_W': 99})

    def test_target_name_uses_package_id(self, svc):
        r = svc.add_component(GuiService.new_soc('x'), 'tiny_region',
                              component_id='sram_block')
        assert r['target'] == 'sram_block_0'


# ---------------------------------------------------------------------------
# update_component (the canvas inline size knob)
# ---------------------------------------------------------------------------
class TestUpdateComponent:
    def _seeded(self, svc):
        return svc.add_component(GuiService.new_soc('x'), 'tiny_region',
                                 component_id='sram_block')['data']

    def test_resize_tracks_window_and_param(self, svc):
        data = self._seeded(svc)
        r = svc.update_component(data, 'u_tiny_region', {'RAM_ADDR_W': 15})
        assert r['size'] == 1 << 15
        inst = r['data']['module']['instances'][0]
        assert inst['params']['RAM_ADDR_W'] == 15
        ic = r['data']['module']['interconnects'][0]
        assert ic['targets'][0]['size'] == 1 << 15
        assert svc.validate(r['data'])['errors'] == 0

    def test_below_min_is_hard_error(self, svc):
        data = self._seeded(svc)
        with pytest.raises(ValueError, match='hard limit'):
            svc.update_component(data, 'u_tiny_region', {'RAM_ADDR_W': 8})

    def test_unknown_instance_raises(self, svc):
        with pytest.raises(ValueError, match='no instance'):
            svc.update_component(GuiService.new_soc('x'), 'u_ghost',
                                 {'RAM_ADDR_W': 14})

    def test_input_not_mutated(self, svc):
        data = self._seeded(svc)
        before = repr(data)
        svc.update_component(data, 'u_tiny_region', {'RAM_ADDR_W': 15})
        assert repr(data) == before


# ---------------------------------------------------------------------------
# endpoints
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
class TestComponentEndpoints:
    def test_components_endpoint(self, client):
        cat = client.get('/api/components').json()['components']
        ids = {c['id'] for c in cat}
        assert {'sram_block', 'cpu_subsystem', 'ethernet_subsystem'} <= ids

    def test_drop_with_params_and_limit(self, client):
        did = client.post('/api/drafts',
                          json={'scratch': {'name': 's'}}).json()['id']
        ok = client.post(f'/api/draft/{did}/component', json={
            'module': 'tiny_region', 'component_id': 'sram_block',
            'params': {'RAM_ADDR_W': 14}})
        assert ok.status_code == 200
        assert ok.json()['validation']['errors'] == 0
        bad = client.post(f'/api/draft/{did}/component', json={
            'module': 'tiny_region', 'component_id': 'sram_block',
            'params': {'RAM_ADDR_W': 8}})
        assert bad.status_code == 400 and 'hard limit' in bad.json()['detail']

    def test_inline_resize_put_and_limit(self, client):
        did = client.post('/api/drafts',
                          json={'scratch': {'name': 's'}}).json()['id']
        client.post(f'/api/draft/{did}/component', json={
            'module': 'tiny_region', 'component_id': 'sram_block'})
        ok = client.put(f'/api/draft/{did}/component/u_tiny_region',
                        json={'params': {'RAM_ADDR_W': 15}})
        assert ok.status_code == 200
        assert ok.json()['size'] == 1 << 15
        assert ok.json()['validation']['errors'] == 0
        bad = client.put(f'/api/draft/{did}/component/u_tiny_region',
                         json={'params': {'RAM_ADDR_W': 8}})
        assert bad.status_code == 400 and 'hard limit' in bad.json()['detail']
        missing = client.put(f'/api/draft/{did}/component/u_ghost',
                             json={'params': {'RAM_ADDR_W': 14}})
        assert missing.status_code == 404
