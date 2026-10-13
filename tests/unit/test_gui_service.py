# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for the configurator service layer (roadmap doc 06a, M1).

Covers: build_from_dict, the three validation tiers through
GuiService.validate(), the diagram JSON, catalogs, YAML I/O path guarding,
and the doc-05 expand preview. Hermetic — fixtures only.
"""

import copy
from pathlib import Path

import pytest
import yaml

from soc_model.builder import SoCBuilder
from soc_model.gui_service import GuiService
from soc_model.parser import SoCParser

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'


def _load(name):
    return yaml.safe_load((FIXTURES / name).read_text())


@pytest.fixture()
def svc():
    return GuiService(base_dir=str(FIXTURES), lib_dirs=[str(FIXTURES)])


@pytest.fixture()
def minimal():
    return _load('minimal_soc.yaml')


# ---------------------------------------------------------------------------
# build_from_dict (M1 builder hook)
# ---------------------------------------------------------------------------
class TestBuildFromDict:
    def test_builds_and_resolves(self, minimal):
        parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
        module = SoCBuilder(parser).build_from_dict(minimal)
        assert module.name == 'minimal_soc'
        inst = module.get_instance('u_sram_0')
        assert inst is not None and inst.resolved_module is not None
        assert inst.resolved_module.name == 'tiny_region'

    def test_accepts_bare_module_dict(self, minimal):
        parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
        module = SoCBuilder(parser).build_from_dict(minimal['module'])
        assert module.name == 'minimal_soc'

    def test_rejects_non_dict(self):
        parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
        with pytest.raises(ValueError):
            SoCBuilder(parser).build_from_dict("nope")


# ---------------------------------------------------------------------------
# validate() — tiers
# ---------------------------------------------------------------------------
class TestValidate:
    def test_clean_fixture_ok(self, svc, minimal):
        res = svc.validate(minimal)
        assert res['errors'] == 0
        assert res['ok'] is True
        # tier-3 checklist evaluated (bundled default rules)
        assert {c['id'] for c in res['checklist']} >= {
            'cpu-initiator', 'memory-target', 'clock-present'}

    def test_tier1_missing_instance_name(self, svc, minimal):
        bad = copy.deepcopy(minimal)
        del bad['module']['instances'][0]['instance_name']
        msgs = svc.structural_lint(bad)
        assert any(m['tier'] == 1 and 'instance_name' in m['message']
                   for m in msgs)

    def test_tier1_bad_direction_and_type(self, svc, minimal):
        bad = copy.deepcopy(minimal)
        bad['module']['interfaces'][0]['direction'] = 'sideways'
        bad['module']['interfaces'][1]['type'] = 'avalon'
        msgs = svc.structural_lint(bad)
        assert any("direction 'sideways'" in m['message'] for m in msgs)
        assert any("type 'avalon'" in m['message'] for m in msgs)

    def test_tier1_duplicate_target_and_unknown_ref(self, svc, minimal):
        bad = copy.deepcopy(minimal)
        ic = bad['module']['interconnects'][0]
        ic['targets'].append(dict(ic['targets'][0]))           # duplicate
        ic['initiators'][0]['targets'].append({'name': 'ghost_0'})
        msgs = svc.structural_lint(bad)
        assert any('duplicate target name' in m['message'] for m in msgs)
        assert any("unknown target 'ghost_0'" in m['message'] for m in msgs)

    def test_tier1_module_rtl_module_exclusive(self, svc, minimal):
        bad = copy.deepcopy(minimal)
        bad['module']['instances'][0]['rtl_module'] = 'raw_leaf'
        msgs = svc.structural_lint(bad)
        assert any('rtl_module wins' in m['message'] for m in msgs)

    def test_tier1_missing_base_without_auto_base(self, svc, minimal):
        bad = copy.deepcopy(minimal)
        del bad['module']['interconnects'][0]['targets'][0]['base']
        msgs = svc.structural_lint(bad)
        assert any('no base:' in m['message'] for m in msgs)

    def test_tier1_unknown_preset(self, svc, minimal):
        bad = copy.deepcopy(minimal)
        bad['module']['uses'] = ['no_such_preset']
        msgs = svc.structural_lint(bad)
        assert any("unknown preset 'no_such_preset'" in m['message']
                   for m in msgs)

    def test_tier2_address_overlap(self, svc, minimal):
        bad = copy.deepcopy(minimal)
        ic = bad['module']['interconnects'][0]
        t2 = dict(ic['targets'][0])
        t2['name'] = 'sram_1'
        # same base/size -> overlap; also visible to the initiator
        ic['targets'].append(t2)
        ic['initiators'][0]['targets'].append({'name': 'sram_1'})
        res = svc.validate(bad)
        assert any(m['tier'] == 2 and m['category'] == 'address'
                   and m['level'] == 'error' for m in res['messages'])
        assert res['ok'] is False

    def test_build_failure_is_message_not_exception(self, svc):
        res = svc.validate({'module': {'name': 'x',
                                       'interconnects': 'not-a-list'}})
        assert res['ok'] is False
        assert any(m['category'] in ('build', 'schema')
                   for m in res['messages'])

    def test_tier3_failures_become_messages(self, svc, minimal):
        res = svc.validate(minimal)
        # minimal_soc has no bootrom/debug/uart -> non-error checklist gaps
        failed = {c['id'] for c in res['checklist'] if not c['passed']}
        assert 'bootrom-role' in failed
        assert any(m['tier'] == 3 and m.get('rule') == 'bootrom-role'
                   for m in res['messages'])
        # but they are warnings/info, so still ok
        assert res['ok'] is True


# ---------------------------------------------------------------------------
# diagram
# ---------------------------------------------------------------------------
class TestDiagram:
    def test_minimal_layout(self, svc, minimal):
        d = svc.diagram(minimal)
        assert d['ok'] and d['module'] == 'minimal_soc'
        spine = d['spines'][0]
        assert spine['name'] == 'minimal_ahb_interconnect'
        assert spine['initiators'][0]['name'] == 'cpu_m'
        assert spine['initiators'][0]['kind'] == 'cpu'
        mem = spine['groups']['memory']
        assert mem[0]['name'] == 'sram_0'
        assert mem[0]['base_hex'] == '0x0'
        # unmet checklist items surface as ghost placeholders
        assert any(g['id'] == 'bootrom-role' for g in d['ghosts'])

    def test_badges_mark_broken_blocks(self, svc, minimal):
        bad = copy.deepcopy(minimal)
        ic = bad['module']['interconnects'][0]
        t2 = dict(ic['targets'][0])
        t2['name'] = 'sram_1'
        ic['targets'].append(t2)
        ic['initiators'][0]['targets'].append({'name': 'sram_1'})
        d = svc.diagram(bad)
        assert d['counts']['errors'] >= 1


# ---------------------------------------------------------------------------
# catalogs
# ---------------------------------------------------------------------------
class TestCatalogs:
    def test_catalogs_enumerate(self, svc):
        cat = svc.catalogs(template_dirs=[str(FIXTURES)])
        assert 'ahb_target_region' in cat['presets']
        assert 'generic' in cat['pad_techs']
        assert 'nanosoc_multicore' in cat['chip_boundaries']
        assert 'tiny_region' in cat['modules']
        assert any(t.endswith('minimal_soc.yaml') for t in cat['templates'])
        assert 'cpu-initiator' in cat['rules']


# ---------------------------------------------------------------------------
# YAML I/O
# ---------------------------------------------------------------------------
class TestYamlIO:
    def test_round_trip(self, svc, minimal):
        text = svc.to_yaml(minimal)
        assert yaml.safe_load(text) == minimal

    def test_load_inside_root(self, svc):
        data = svc.load_yaml('minimal_soc.yaml', FIXTURES)
        assert data['module']['name'] == 'minimal_soc'

    def test_path_guard_rejects_escape(self, svc, minimal, tmp_path):
        with pytest.raises(PermissionError):
            svc.save_yaml(minimal, '../escape.yaml', tmp_path / 'root')
        with pytest.raises(PermissionError):
            svc.load_yaml('/etc/hostname', FIXTURES)

    def test_save_and_reload(self, svc, minimal, tmp_path):
        p = svc.save_yaml(minimal, 'drafts/a.yaml', tmp_path)
        assert Path(p).is_file()
        assert yaml.safe_load(Path(p).read_text()) == minimal


# ---------------------------------------------------------------------------
# doc-05 expand preview
# ---------------------------------------------------------------------------
class TestExpandPreview:
    def test_identity_when_sugar_free(self, svc, minimal):
        out = svc.expand_preview(minimal)
        assert out == minimal

    def test_desugars_auto_base(self, svc, minimal):
        sweet = copy.deepcopy(minimal)
        ic = sweet['module']['interconnects'][0]
        ic['auto_base'] = {'from': 0x20000000, 'align': '16M'}
        del ic['targets'][0]['base']
        out = svc.expand_preview(sweet)
        oic = out['module']['interconnects'][0]
        assert 'auto_base' not in oic
        assert oic['targets'][0]['base'] == 0x20000000
