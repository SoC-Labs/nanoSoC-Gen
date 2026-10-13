# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for the read-only IRQ map (doc 06a idea #1): GuiService.irq_map
extracts each glue_logic concat as a bit->signal matrix + its NVIC consumer."""

from pathlib import Path

import pytest

from soc_model.gui_service import GuiService

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'


@pytest.fixture()
def svc():
    return GuiService(base_dir=str(FIXTURES), lib_dirs=[str(FIXTURES)])


def _soc(**glue_and_inst):
    return {'module': {'name': 'soc', **glue_and_inst}}


class TestIrqMap:
    def test_extracts_concat_bits_and_consumer(self, svc):
        data = _soc(
            glue_logic=[{
                'name': 'nvic', 'type': 'concat', 'output': 'cpu0_nvic_irq[31:0]',
                'pad_zero_above': 32,
                'inputs': [{'bit': 3, 'signal': 'phc_alarm_irq'},
                           {'bit': 8, 'signal': 'cc_periph_irq_w[0]'}]}],
            instances=[{'instance_name': 'u_eth_ss_0',
                        'connections': [{'port': 'NVIC_IRQ',
                                         'conn': 'cpu0_nvic_irq'}]}])
        m = svc.irq_map(data)
        assert m['count'] == 1
        c = m['concats'][0]
        assert c['width'] == 32 and c['mapped'] == 2
        assert c['bits'][3]['signal'] == 'phc_alarm_irq'
        assert c['bits'][0]['signal'] is None          # unmapped -> 1'b0
        assert c['consumer']['instance'] == 'u_eth_ss_0'
        assert c['consumer']['is_irq'] is True

    def test_non_concat_glue_ignored(self, svc):
        m = svc.irq_map(_soc(glue_logic=[
            {'name': 'r', 'type': 'or_reduce', 'output': 'x', 'input': 'y'},
            {'name': 'p', 'type': 'passthrough', 'output': 'a', 'input': 'b'}]))
        assert m['count'] == 0

    def test_unconsumed_output_has_no_consumer(self, svc):
        m = svc.irq_map(_soc(glue_logic=[{
            'name': 'orphan', 'type': 'concat', 'output': 'dangling_irq',
            'inputs': [{'bit': 0, 'signal': 'foo'}]}]))
        assert m['concats'][0]['consumer'] is None
        assert m['concats'][0]['width'] == 1

    def test_non_irq_consumer_flagged(self, svc):
        # output consumed by a non-IRQ-looking port -> is_irq False
        m = svc.irq_map(_soc(
            glue_logic=[{'name': 'c', 'type': 'concat', 'output': 'net',
                         'inputs': [{'bit': 0, 'signal': 's'}]}],
            instances=[{'instance_name': 'u_x',
                        'connections': [{'port': 'HSEL', 'conn': 'net'}]}]))
        assert m['concats'][0]['consumer']['is_irq'] is False

    def test_irq_concats_sorted_first(self, svc):
        m = svc.irq_map(_soc(
            glue_logic=[
                {'name': 'plain', 'type': 'concat', 'output': 'p',
                 'inputs': [{'bit': 0, 'signal': 's'}]},
                {'name': 'irq', 'type': 'concat', 'output': 'q',
                 'inputs': [{'bit': 0, 'signal': 's'}]}],
            instances=[{'instance_name': 'u', 'connections': [
                {'port': 'NVIC_IRQ', 'conn': 'q'}]}]))
        assert m['concats'][0]['name'] == 'irq'    # irq-feeding first

    def test_empty_and_non_dict(self, svc):
        assert svc.irq_map({'module': {'name': 'x'}}) == {'count': 0,
                                                          'concats': []}
        assert svc.irq_map(None)['count'] == 0


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
class TestIrqEndpoint:
    def test_irqmap_endpoint(self, tmp_path):
        from fastapi.testclient import TestClient
        from soc_model.configurator.app import create_app
        from soc_model.configurator.project import ProjectAdapter
        import yaml
        c = TestClient(create_app(ProjectAdapter(
            root=str(tmp_path), lib_dirs=[str(FIXTURES)],
            templates=[str(FIXTURES)])))
        did = c.post('/api/drafts', json={'scratch': {'name': 's'}}).json()['id']
        d = _soc(glue_logic=[{'name': 'n', 'type': 'concat',
                              'output': 'cpu_nvic_irq',
                              'inputs': [{'bit': 1, 'signal': 'timer_irq'}]}])
        c.put(f'/api/draft/{did}/dict', json=d)
        m = c.get(f'/api/draft/{did}/irqmap').json()
        assert m['count'] == 1
        assert m['concats'][0]['bits'][1]['signal'] == 'timer_irq'
