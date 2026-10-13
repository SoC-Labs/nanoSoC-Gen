# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for the IRQ-map editing layer (doc 06a idea #1, phase 2):
GuiService.set_irq_bit sets/replaces/clears the source signal on one NVIC
concat bit and round-trips through irq_map; PUT /irq mirrors it."""

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


def _nvic(*inputs, output='cpu0_nvic_irq[31:0]', pad=32):
    return _soc(glue_logic=[{
        'name': 'nvic', 'type': 'concat', 'output': output,
        'pad_zero_above': pad, 'inputs': list(inputs)}])


class TestSetIrqBit:
    def test_set_new_bit(self, svc):
        data = _nvic({'bit': 3, 'signal': 'phc_alarm_irq'})
        res = svc.set_irq_bit(data, 'cpu0_nvic_irq', 5, 'timer_irq')
        assert res['bit'] == 5 and res['signal'] == 'timer_irq'
        ins = res['data']['module']['glue_logic'][0]['inputs']
        got = {it['bit']: it['signal'] for it in ins}
        assert got == {3: 'phc_alarm_irq', 5: 'timer_irq'}

    def test_replace_existing_bit(self, svc):
        data = _nvic({'bit': 3, 'signal': 'old_irq'})
        res = svc.set_irq_bit(data, 'cpu0_nvic_irq', 3, 'new_irq')
        ins = res['data']['module']['glue_logic'][0]['inputs']
        assert ins == [{'bit': 3, 'signal': 'new_irq'}]

    def test_clear_bit_with_none(self, svc):
        data = _nvic({'bit': 3, 'signal': 'a'}, {'bit': 8, 'signal': 'b'})
        res = svc.set_irq_bit(data, 'cpu0_nvic_irq', 3, None)
        ins = res['data']['module']['glue_logic'][0]['inputs']
        assert ins == [{'bit': 8, 'signal': 'b'}]
        assert res['signal'] is None

    def test_clear_bit_with_empty_string(self, svc):
        data = _nvic({'bit': 3, 'signal': 'a'})
        res = svc.set_irq_bit(data, 'cpu0_nvic_irq', 3, '')
        assert res['data']['module']['glue_logic'][0]['inputs'] == []
        assert res['signal'] is None

    def test_base_name_match(self, svc):
        # caller may pass the sliced output or just the base name
        data = _nvic({'bit': 0, 'signal': 's'})
        res = svc.set_irq_bit(data, 'cpu0_nvic_irq[31:0]', 1, 't')
        got = {it['bit'] for it in
               res['data']['module']['glue_logic'][0]['inputs']}
        assert got == {0, 1}

    def test_roundtrip_via_irq_map(self, svc):
        data = _nvic({'bit': 3, 'signal': 'phc_alarm_irq'})
        res = svc.set_irq_bit(data, 'cpu0_nvic_irq', 8, 'cc_irq')
        m = svc.irq_map(res['data'])
        c = m['concats'][0]
        assert c['bits'][3]['signal'] == 'phc_alarm_irq'
        assert c['bits'][8]['signal'] == 'cc_irq'
        assert c['bits'][0]['signal'] is None
        assert c['mapped'] == 2

    def test_unknown_output_raises(self, svc):
        data = _nvic({'bit': 0, 'signal': 's'})
        with pytest.raises(ValueError):
            svc.set_irq_bit(data, 'no_such_irq', 0, 'x')

    def test_negative_bit_raises(self, svc):
        with pytest.raises(ValueError):
            svc.set_irq_bit(_nvic(), 'cpu0_nvic_irq', -1, 'x')

    def test_out_of_range_bit_raises(self, svc):
        # pad_zero_above is the declared width -> bit >= pad is out of range
        with pytest.raises(ValueError):
            svc.set_irq_bit(_nvic(pad=8), 'cpu0_nvic_irq', 8, 'x')

    def test_input_not_mutated(self, svc):
        data = _nvic({'bit': 3, 'signal': 'orig'})
        svc.set_irq_bit(data, 'cpu0_nvic_irq', 3, 'changed')
        svc.set_irq_bit(data, 'cpu0_nvic_irq', 7, 'added')
        assert data['module']['glue_logic'][0]['inputs'] == [
            {'bit': 3, 'signal': 'orig'}]


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
class TestIrqEditEndpoint:
    def _client(self, tmp_path):
        from fastapi.testclient import TestClient
        from soc_model.configurator.app import create_app
        from soc_model.configurator.project import ProjectAdapter
        return TestClient(create_app(ProjectAdapter(
            root=str(tmp_path), lib_dirs=[str(FIXTURES)],
            templates=[str(FIXTURES)])))

    def test_put_irq_sets_and_returns_irqmap(self, tmp_path):
        c = self._client(tmp_path)
        did = c.post('/api/drafts',
                     json={'scratch': {'name': 's'}}).json()['id']
        d = _nvic({'bit': 1, 'signal': 'timer_irq'},
                  output='cpu_nvic_irq', pad=4)
        c.put(f'/api/draft/{did}/dict', json=d)
        r = c.put(f'/api/draft/{did}/irq',
                  json={'output': 'cpu_nvic_irq', 'bit': 2,
                        'signal': 'uart_irq'})
        assert r.status_code == 200
        body = r.json()
        assert body['bit'] == 2 and body['signal'] == 'uart_irq'
        assert 'validation' in body
        bits = body['irqmap']['concats'][0]['bits']
        assert bits[1]['signal'] == 'timer_irq'
        assert bits[2]['signal'] == 'uart_irq'
        # persisted: a fresh read sees it
        m = c.get(f'/api/draft/{did}/irqmap').json()
        assert m['concats'][0]['bits'][2]['signal'] == 'uart_irq'

    def test_put_irq_clear_bit(self, tmp_path):
        c = self._client(tmp_path)
        did = c.post('/api/drafts',
                     json={'scratch': {'name': 's'}}).json()['id']
        d = _nvic({'bit': 1, 'signal': 'timer_irq'},
                  output='cpu_nvic_irq', pad=4)
        c.put(f'/api/draft/{did}/dict', json=d)
        r = c.put(f'/api/draft/{did}/irq',
                  json={'output': 'cpu_nvic_irq', 'bit': 1, 'signal': ''})
        assert r.status_code == 200
        assert r.json()['signal'] is None
        m = c.get(f'/api/draft/{did}/irqmap').json()
        assert m['concats'][0]['bits'][1]['signal'] is None

    def test_put_irq_unknown_output_400(self, tmp_path):
        c = self._client(tmp_path)
        did = c.post('/api/drafts',
                     json={'scratch': {'name': 's'}}).json()['id']
        c.put(f'/api/draft/{did}/dict',
              json=_nvic(output='cpu_nvic_irq', pad=4))
        r = c.put(f'/api/draft/{did}/irq',
                  json={'output': 'nope', 'bit': 0, 'signal': 'x'})
        assert r.status_code == 400
