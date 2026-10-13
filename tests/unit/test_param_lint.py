# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for the unresolved-$PARAM linter (doc 06a idea #16):
GuiService.param_lint flags $NAME refs that resolve against no known param,
the auto_base literals-only rule, and surfaces through validate() as tier-1."""

import copy
from pathlib import Path

import pytest

from soc_model.gui_service import GuiService

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'


@pytest.fixture()
def svc():
    return GuiService(base_dir=str(FIXTURES), lib_dirs=[str(FIXTURES)])


def _msgs(svc, data):
    return svc.param_lint(data)


class TestParamLint:
    def test_clean_seed_has_no_findings(self, svc):
        # the from-scratch seed references $SYS_ADDR_W etc — all real params
        assert _msgs(svc, GuiService.new_soc('demo')) == []

    def test_typo_ref_is_flagged_with_coords(self, svc):
        d = GuiService.new_soc('demo')
        d['module']['interfaces'][2]['params']['ADDR_WIDTH'] = '$SYS_ADDR_WIDTH'
        m = _msgs(svc, d)
        assert len(m) == 1
        assert m[0]['level'] == 'warning' and m[0]['category'] == 'reference'
        assert '$SYS_ADDR_WIDTH' in m[0]['message']
        assert m[0]['port'] == 'ADDR_WIDTH'
        assert m[0]['tier'] == 1

    def test_known_ref_not_flagged(self, svc):
        d = GuiService.new_soc('demo')
        d['module']['interfaces'].append(
            {'name': 'x', 'type': 'wire', 'direction': 'in',
             'params': {'W': '$SYS_DATA_W'}})       # a real param
        assert not any('SYS_DATA_W' in x['message'] for x in _msgs(svc, d))

    def test_expression_with_known_param_is_clean(self, svc):
        d = GuiService.new_soc('demo')
        d['module']['interfaces'][2]['params']['SZ'] = '4 * (2 ** $SYS_ADDR_W)'
        assert _msgs(svc, d) == []

    def test_expression_with_typo_is_flagged(self, svc):
        d = GuiService.new_soc('demo')
        d['module']['interfaces'][2]['params']['SZ'] = '2 ** $NOPE'
        m = _msgs(svc, d)
        assert any('$NOPE' in x['message'] for x in m)

    def test_auto_base_dollar_is_hard_error(self, svc):
        d = GuiService.new_soc('demo')
        d['module'].setdefault('interconnects', [{'name': 'ic'}])
        d['module']['interconnects'][0]['auto_base'] = {
            'from': '$BASE', 'align': '0x1000'}
        m = _msgs(svc, d)
        ab = [x for x in m if 'auto_base' in x['message']]
        assert ab and ab[0]['level'] == 'error'

    def test_param_defined_only_on_an_instance_is_not_flagged(self, svc):
        # permissive scope: a $ref to a param declared on ANY params: block
        # (here a nested instance) must not false-positive.
        d = GuiService.new_soc('demo')
        d['module'].setdefault('instances', []).append({
            'instance_name': 'u_x', 'rtl_module': 'foo',
            'params': {'LOCAL_W': 8},
            'connections': [{'port': 'p', 'conn': '$LOCAL_W'}]})
        assert not any('LOCAL_W' in x['message'] for x in _msgs(svc, d))

    def test_non_dict_input_is_empty(self, svc):
        assert svc.param_lint(None) == []
        assert svc.param_lint({'module': 'notadict'}) == []

    def test_dedups_same_ref_at_same_path(self, svc):
        d = GuiService.new_soc('demo')
        # one bad ref appearing once -> exactly one finding
        d['module']['interfaces'][2]['params']['A'] = '$GHOST'
        assert len([x for x in _msgs(svc, d) if '$GHOST' in x['message']]) == 1

    def test_validate_surfaces_param_lint(self, svc):
        d = GuiService.new_soc('demo')
        d['module']['interfaces'][2]['params']['ADDR_WIDTH'] = '$TYPO_REF'
        v = svc.validate(d)
        assert any(m['category'] == 'reference' and '$TYPO_REF' in m['message']
                   for m in v['messages'])
        assert v['warnings'] >= 1
