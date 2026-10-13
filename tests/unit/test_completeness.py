# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for the completeness rules engine (roadmap doc 06a, M2 /
validation tier 3)."""

import copy
from pathlib import Path

import pytest
import yaml

from soc_model.builder import SoCBuilder
from soc_model.completeness import (DEFAULT_RULES_FILE, Rule, evaluate,
                                    load_rules)
from soc_model.parser import SoCParser

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'


def _build(name_or_dict):
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    builder = SoCBuilder(parser)
    if isinstance(name_or_dict, str):
        return builder.build_system(name_or_dict)
    return builder.build_from_dict(name_or_dict)


def _rule(id, predicate, level='error', **kw):
    return Rule(id=id, level=level, require=dict(predicate, **kw))


def _result(module, rule):
    return evaluate(module, [rule])[0]


@pytest.fixture(scope='module')
def minimal():
    return _build('minimal_soc.yaml')


@pytest.fixture(scope='module')
def apb_soc():
    return _build('apb_bridge_soc.yaml')


# ---------------------------------------------------------------------------
# rules loading
# ---------------------------------------------------------------------------
class TestLoadRules:
    def test_bundled_default_rules_load(self):
        rules = load_rules(DEFAULT_RULES_FILE)
        ids = {r.id for r in rules}
        assert {'cpu-initiator', 'memory-target', 'clock-present',
                'reset-present', 'bootrom-role', 'debug-access',
                'firmware-targets', 'apb-config'} <= ids
        assert all(r.level in ('error', 'warning', 'info') for r in rules)

    def test_unknown_predicate_rejected(self, tmp_path):
        f = tmp_path / 'r.yaml'
        f.write_text("rules:\n  - id: x\n    require: { has_flux_capacitor: true }\n")
        with pytest.raises(ValueError, match='unknown predicate'):
            load_rules(f)

    def test_missing_id_rejected(self, tmp_path):
        f = tmp_path / 'r.yaml'
        f.write_text("rules:\n  - level: error\n    require: { target_role: bootrom }\n")
        with pytest.raises(ValueError, match='without an id'):
            load_rules(f)

    def test_bad_level_rejected(self, tmp_path):
        f = tmp_path / 'r.yaml'
        f.write_text("rules:\n  - id: x\n    level: fatal\n    require: { target_role: bootrom }\n")
        with pytest.raises(ValueError, match='level'):
            load_rules(f)


# ---------------------------------------------------------------------------
# predicates
# ---------------------------------------------------------------------------
class TestPredicates:
    def test_initiator_module_matches(self, minimal):
        assert _result(minimal, _rule('a', {'initiator_module_matches': 'cpu'})).passed
        assert not _result(minimal, _rule('b', {'initiator_module_matches': 'riscv'})).passed

    def test_instance_module_matches(self, minimal):
        assert _result(minimal, _rule('a', {'instance_module_matches': 'tiny_region'})).passed
        assert not _result(minimal, _rule('b', {'instance_module_matches': 'uart'})).passed

    def test_target_at_address(self, minimal):
        ok = _result(minimal, _rule('a', {'target_at_address': 0x0},
                                    region_type='memory'))
        assert ok.passed and 'sram_0' in ok.detail
        assert not _result(minimal, _rule('b', {'target_at_address': 0x2000})).passed
        # right address, wrong region type
        assert not _result(minimal, _rule('c', {'target_at_address': 0x0},
                                          region_type='periph')).passed

    def test_target_role(self, minimal):
        assert not _result(minimal, _rule('a', {'target_role': 'bootrom'})).passed

    def test_external_wires(self, minimal):
        assert _result(minimal, _rule(
            'a', {'external_wires': ['*hclk*', '*resetn*']})).passed
        missing = _result(minimal, _rule('b', {'external_wires': ['*pll_lock*']}))
        assert not missing.passed and 'pll_lock' in missing.detail

    def test_external_wires_alternation(self, minimal):
        assert _result(minimal, _rule(
            'a', {'external_wires': ['*fclk*|*hclk*']})).passed

    def test_firmware_regions_resolve_no_firmware(self, minimal):
        assert _result(minimal, _rule('a', {'firmware_regions_resolve': True})).passed

    def test_firmware_regions_resolve_real_and_broken(self):
        data = yaml.safe_load((FIXTURES / 'firmware_soc.yaml').read_text())
        assert _result(_build(copy.deepcopy(data)),
                       _rule('a', {'firmware_regions_resolve': True})).passed
        bad = copy.deepcopy(data)
        bad['module']['firmware']['linker_profiles'][0]['regions'][0]['target'] = 'ghost'
        bad['module']['name'] = 'firmware_soc_broken'  # builder caches by name
        res = _result(_build(bad), _rule('b', {'firmware_regions_resolve': True}))
        assert not res.passed and 'ghost' in res.detail

    def test_apb_targets_have_config(self, apb_soc):
        assert _result(apb_soc, _rule('a', {'apb_targets_have_config': True})).passed

    def test_apb_target_missing_config_fails(self):
        data = yaml.safe_load((FIXTURES / 'apb_bridge_soc.yaml').read_text())
        bad = copy.deepcopy(data)
        for t in bad['module']['interconnects'][0]['targets']:
            t.pop('apb_config', None)
        bad['module']['name'] = 'apb_soc_broken'
        res = _result(_build(bad), _rule('a', {'apb_targets_have_config': True}))
        assert not res.passed

    def test_min_targets(self, minimal):
        assert _result(minimal, _rule(
            'a', {'min_targets': {'region_type': 'memory', 'count': 1}})).passed
        assert not _result(minimal, _rule(
            'b', {'min_targets': {'region_type': 'memory', 'count': 2}})).passed


# ---------------------------------------------------------------------------
# checklist shape
# ---------------------------------------------------------------------------
class TestChecklist:
    def test_default_rules_on_minimal(self, minimal):
        results = evaluate(minimal, load_rules(DEFAULT_RULES_FILE))
        by_id = {r.rule.id: r for r in results}
        assert by_id['cpu-initiator'].passed
        assert by_id['memory-target'].passed
        assert by_id['clock-present'].passed
        assert by_id['reset-present'].passed
        assert not by_id['bootrom-role'].passed       # warning-level gap
        assert not by_id['debug-access'].passed       # warning-level gap
        # no ERROR-level rule fails on the clean fixture
        assert not [r for r in results
                    if not r.passed and r.rule.level == 'error']

    def test_to_dict_carries_hint_on_failure(self, minimal):
        results = evaluate(minimal, load_rules(DEFAULT_RULES_FILE))
        gap = next(r for r in results if r.rule.id == 'bootrom-role')
        d = gap.to_dict()
        assert d['passed'] is False and d['hint']
        ok = next(r for r in results if r.rule.id == 'cpu-initiator').to_dict()
        assert ok['passed'] is True and ok['hint'] == ''
