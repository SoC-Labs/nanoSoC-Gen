# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for checklist fix-it quick actions (doc 06a idea #20):
GuiService.fix_rule applies the canonical dict mutation for a failed
completeness rule; validate() decorates failed checklist items with `fixable`.
"""

from pathlib import Path

import pytest

from soc_model.gui_service import GuiService

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'


@pytest.fixture()
def svc():
    return GuiService(base_dir=str(FIXTURES), lib_dirs=[str(FIXTURES)])


def _no_clk_reset(name='demo'):
    d = GuiService.new_soc(name)
    d['module']['interfaces'] = [
        i for i in d['module']['interfaces']
        if 'clk' not in i['name'] and 'resetn' not in i['name']]
    d['module'].pop('clocks', None)
    d['module'].pop('resets', None)
    return d


def _ids(v):
    return {c['id']: c for c in v['checklist']}


class TestFixable:
    def test_external_wires_rules_are_fixable(self, svc):
        v = svc.validate(_no_clk_reset())
        ids = _ids(v)
        assert ids['clock-present']['fixable'] is True
        assert ids['reset-present']['fixable'] is True

    def test_ip_needing_rules_not_fixable(self, svc):
        v = svc.validate(_no_clk_reset())
        ids = _ids(v)
        # cpu/debug/uart/memory need a real IP choice — not a dict fix
        assert ids['cpu-initiator'].get('fixable') in (False, None)
        assert ids['memory-target'].get('fixable') in (False, None)

    def test_bootrom_role_fixable_only_with_a_memory(self, svc):
        # no memory yet -> not fixable
        v0 = svc.validate(GuiService.new_soc('x'))
        assert _ids(v0)['bootrom-role'].get('fixable') in (False, None)
        # add a memory -> now fixable
        d = svc.add_component(GuiService.new_soc('x'), 'tiny_region',
                              size=0x1000)['data']
        assert _ids(svc.validate(d))['bootrom-role']['fixable'] is True


class TestFixRule:
    def test_fix_clock_then_reset(self, svc):
        d = _no_clk_reset()
        r = svc.fix_rule(d, 'clock-present')
        assert 'sys_hclk' in r['message']
        assert _ids(r['validation'])['clock-present']['passed'] is True
        r2 = svc.fix_rule(r['data'], 'reset-present')
        assert _ids(r2['validation'])['reset-present']['passed'] is True

    def test_fix_bootrom_role_marks_a_memory(self, svc):
        d = svc.add_component(GuiService.new_soc('x'), 'tiny_region',
                              size=0x1000)['data']
        r = svc.fix_rule(d, 'bootrom-role')
        assert 'bootrom' in r['message']
        assert _ids(r['validation'])['bootrom-role']['passed'] is True
        # the role landed on the actual target dict
        ts = GuiService._all_targets(r['data']['module'])
        assert any(t.get('role') == 'bootrom' for t in ts)

    def test_unknown_rule_raises(self, svc):
        with pytest.raises(ValueError, match='unknown rule'):
            svc.fix_rule(GuiService.new_soc('x'), 'no-such-rule')

    def test_unfixable_rule_raises(self, svc):
        with pytest.raises(ValueError, match='no automatic fix'):
            svc.fix_rule(GuiService.new_soc('x'), 'uart-present')

    def test_input_not_mutated(self, svc):
        d = _no_clk_reset()
        before = repr(d)
        svc.fix_rule(d, 'clock-present')
        assert repr(d) == before


# ---------------------------------------------------------------------------
# endpoint (skip without fastapi)
# ---------------------------------------------------------------------------
try:
    import fastapi  # noqa: F401
    _HAVE_FASTAPI = True
except ImportError:
    _HAVE_FASTAPI = False

needs_fastapi = pytest.mark.skipif(
    not _HAVE_FASTAPI, reason='configurator extra (fastapi) not installed')


@needs_fastapi
class TestFixEndpoint:
    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient
        from soc_model.configurator.app import create_app
        from soc_model.configurator.project import ProjectAdapter
        return TestClient(create_app(ProjectAdapter(
            root=str(tmp_path), lib_dirs=[str(FIXTURES)],
            templates=[str(FIXTURES)])))

    def test_fix_endpoint_applies_and_revalidates(self, client):
        import yaml
        d = _no_clk_reset('s')
        did = client.post('/api/drafts', json={'scratch': {'name': 's'}}).json()['id']
        client.put(f'/api/draft/{did}/dict', json=d)
        r = client.post(f'/api/draft/{did}/fix', json={'rule_id': 'clock-present'})
        assert r.status_code == 200
        ids = {c['id']: c for c in r.json()['validation']['checklist']}
        assert ids['clock-present']['passed'] is True
        # persisted
        assert 'sys_hclk' in client.get(f'/api/draft/{did}').json()['yaml']

    def test_fix_endpoint_400_on_unfixable(self, client):
        did = client.post('/api/drafts', json={'scratch': {}}).json()['id']
        r = client.post(f'/api/draft/{did}/fix', json={'rule_id': 'uart-present'})
        assert r.status_code == 400
