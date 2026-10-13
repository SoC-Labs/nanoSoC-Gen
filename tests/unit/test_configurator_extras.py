# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for the configurator memory-map / pack / dict / export /
artifacts surfaces (roadmap doc 06a follow-on endpoints).

GuiService-level tests (memory_map, pack) run on bare py3.8; the endpoint
tests need fastapi and are skipped where the optional extra is missing —
same conventions as test_configurator.py."""

import copy
import shutil
from pathlib import Path

import pytest
import yaml

from soc_model.gui_service import GuiService

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'

IC = 'two_region_ahb_interconnect'


def _load(name):
    return yaml.safe_load((FIXTURES / name).read_text())


@pytest.fixture()
def svc():
    return GuiService(base_dir=str(FIXTURES), lib_dirs=[str(FIXTURES)])


@pytest.fixture()
def two_region():
    return _load('two_region_soc.yaml')


def _targets(data):
    return data['module']['interconnects'][0]['targets']


# ---------------------------------------------------------------------------
# GuiService.memory_map
# ---------------------------------------------------------------------------
class TestMemoryMap:
    def test_windows_sorted_with_hex_fields(self, svc, two_region):
        # swap bases so YAML order != address order
        _targets(two_region)[0]['base'] = 0x10000000
        _targets(two_region)[1]['base'] = 0x00000000
        mm = svc.memory_map(two_region)
        assert mm['ok'] is True
        tgts = mm['interconnects'][0]['targets']
        assert [t['name'] for t in tgts] == ['sram_1', 'sram_0']
        assert [t['base'] for t in tgts] == sorted(t['base'] for t in tgts)
        assert tgts[0]['base_hex'] == '0x0'
        assert tgts[0]['end_hex'] == hex(0x1000 - 1)
        assert tgts[1]['base_hex'] == '0x10000000'
        assert tgts[1]['end'] == 0x10000fff

    def test_overlap_detection_is_mutual(self, svc, two_region):
        _targets(two_region)[1]['base'] = 0x800   # inside sram_0's 4 KB
        mm = svc.memory_map(two_region)
        assert mm['ok'] is True
        by_name = {t['name']: t for t in mm['interconnects'][0]['targets']}
        assert by_name['sram_0']['overlaps'] == ['sram_1']
        assert by_name['sram_1']['overlaps'] == ['sram_0']

    def test_no_overlaps_on_clean_map(self, svc, two_region):
        mm = svc.memory_map(two_region)
        assert all(t['overlaps'] == []
                   for t in mm['interconnects'][0]['targets'])

    def test_span_lo_hi(self, svc, two_region):
        mm = svc.memory_map(two_region)
        span = mm['interconnects'][0]['span']
        assert span == {'lo': 0x00000000, 'hi': 0x10000fff}

    def test_broken_dict_reports_not_ok(self, svc):
        mm = svc.memory_map({'module': {'name': 'x', 'instances': 'nope'}})
        assert mm['ok'] is False
        assert mm['interconnects'] == []
        assert any(m['level'] == 'error' for m in mm['messages'])


# ---------------------------------------------------------------------------
# GuiService.pack
# ---------------------------------------------------------------------------
class TestPack:
    def test_default_packs_only_missing_base(self, svc, two_region):
        del _targets(two_region)[1]['base']           # sram_1 unpinned
        res = svc.pack(two_region, IC, 0x20000000, '4K')
        rows = {r['name']: r for r in res['table']}
        assert rows['sram_0']['after'] == 0x00000000  # pinned, untouched
        assert rows['sram_1']['before'] is None
        assert rows['sram_1']['after'] == 0x20000000
        assert rows['sram_1']['after_hex'] == '0x20000000'

    def test_repack_all_from_start(self, svc, two_region):
        res = svc.pack(two_region, IC, 0x20000000, '16M', repack_all=True)
        rows = {r['name']: r for r in res['table']}
        assert rows['sram_0'] == {'name': 'sram_0', 'before': 0x00000000,
                                  'after': 0x20000000,
                                  'after_hex': '0x20000000'}
        assert rows['sram_1']['before'] == 0x10000000
        assert rows['sram_1']['after'] == 0x21000000

    def test_align_16m_shorthand(self, svc, two_region):
        res = svc.pack(two_region, IC, 0x20000000, '16M', repack_all=True)
        for row in res['table']:
            assert row['after'] % 0x1000000 == 0

    def test_unknown_interconnect_raises(self, svc, two_region):
        with pytest.raises(ValueError):
            svc.pack(two_region, 'no_such_ic', 0, 0)

    def test_table_has_before_and_after(self, svc, two_region):
        res = svc.pack(two_region, IC, 0x20000000, '4K')
        assert res['table']
        for row in res['table']:
            assert set(row) == {'name', 'before', 'after', 'after_hex'}

    def test_preview_does_not_mutate_input(self, svc, two_region):
        del _targets(two_region)[1]['base']
        snapshot = copy.deepcopy(two_region)
        res = svc.pack(two_region, IC, 0x20000000, '16M')
        assert two_region == snapshot                 # input untouched
        ic_out = res['data']['module']['interconnects'][0]
        assert 'auto_base' not in ic_out              # consumed in the output
        assert 'auto_base' not in two_region['module']['interconnects'][0]


# ---------------------------------------------------------------------------
# FastAPI endpoints (runs only where the optional extra is installed)
# ---------------------------------------------------------------------------
try:
    import fastapi  # noqa: F401
    _HAVE_FASTAPI = True
except ImportError:
    _HAVE_FASTAPI = False

needs_fastapi = pytest.mark.skipif(
    not _HAVE_FASTAPI, reason='configurator extra (fastapi) not installed')


def _make_adapter(tmp_path):
    from soc_model.configurator.project import ProjectAdapter
    top = tmp_path / 'sys_desc'
    top.mkdir(exist_ok=True)
    shutil.copy(FIXTURES / 'minimal_soc.yaml', top / 'minimal_soc.yaml')
    # ABSOLUTE lib dirs — relative ones break module resolution
    return ProjectAdapter(root=str(tmp_path),
                          top_yaml='sys_desc/minimal_soc.yaml',
                          lib_dirs=[str(FIXTURES)],
                          templates=[str(FIXTURES)])


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient
    from soc_model.configurator.app import create_app
    # raise_server_exceptions=False so unhandled server errors surface as
    # HTTP 500s (asserted on) instead of aborting the test.
    return TestClient(create_app(_make_adapter(tmp_path)),
                      raise_server_exceptions=False)


def _new_draft(client, text=None):
    draft_id = client.post('/api/drafts',
                           json={'name': 't', 'from_top': True}).json()['id']
    if text is not None:
        r = client.put(f'/api/draft/{draft_id}', json={'yaml': text})
        assert r.status_code == 200
    return draft_id


def _two_region_text(unpin_sram_1=False, auto_base=None):
    data = _load('two_region_soc.yaml')
    ic = data['module']['interconnects'][0]
    if unpin_sram_1:
        del ic['targets'][1]['base']
    if auto_base is not None:
        ic['auto_base'] = auto_base
    return yaml.dump(data, sort_keys=False)


@needs_fastapi
class TestDictEndpoints:
    def test_get_dict_returns_parsed_module(self, client):
        draft_id = _new_draft(client)
        d = client.get(f'/api/draft/{draft_id}/dict')
        assert d.status_code == 200
        assert d.json()['module']['name'] == 'minimal_soc'

    def test_put_dict_validates_and_rewrites_text(self, client):
        draft_id = _new_draft(client)
        data = client.get(f'/api/draft/{draft_id}/dict').json()
        data['module']['desc'] = 'edited-via-dict-MARKER'
        r = client.put(f'/api/draft/{draft_id}/dict', json=data)
        assert r.status_code == 200
        assert r.json()['ok'] is True
        text = client.get(f'/api/draft/{draft_id}').json()['yaml']
        assert 'edited-via-dict-MARKER' in text

    def test_put_dict_without_module_is_422(self, client):
        draft_id = _new_draft(client)
        r = client.put(f'/api/draft/{draft_id}/dict', json={'name': 'x'})
        assert r.status_code == 422


@needs_fastapi
class TestMemmapAndPackEndpoints:
    def test_memmap_matches_gui_service(self, client, tmp_path):
        draft_id = _new_draft(client, _two_region_text())
        got = client.get(f'/api/draft/{draft_id}/memmap')
        assert got.status_code == 200
        svc = GuiService(base_dir=str(tmp_path), lib_dirs=[str(FIXTURES)])
        assert got.json() == svc.memory_map(_load('two_region_soc.yaml'))

    def test_pack_preview_leaves_draft_unchanged(self, client):
        text = _two_region_text(unpin_sram_1=True)
        draft_id = _new_draft(client, text)
        r = client.post(f'/api/draft/{draft_id}/pack',
                        json={'interconnect': IC, 'from': 0x20000000,
                              'align': '4K'})
        assert r.status_code == 200
        body = r.json()
        assert body['applied'] is False
        assert {row['name']: row['after_hex'] for row in body['table']
                }['sram_1'] == '0x20000000'
        assert client.get(f'/api/draft/{draft_id}').json()['yaml'] == text

    def test_pack_apply_rewrites_draft(self, client):
        draft_id = _new_draft(client, _two_region_text(unpin_sram_1=True))
        r = client.post(f'/api/draft/{draft_id}/pack',
                        json={'interconnect': IC, 'from': 0x20000000,
                              'align': '4K', 'apply': True})
        assert r.status_code == 200 and r.json()['applied'] is True
        text = client.get(f'/api/draft/{draft_id}').json()['yaml']
        # yaml.dump writes ints in decimal, so the new base shows up as
        # 536870912 (== 0x20000000) in the text; assert both forms.
        assert str(0x20000000) in text
        new = yaml.safe_load(text)
        ic = new['module']['interconnects'][0]
        assert ic['targets'][1]['base'] == 0x20000000
        assert 'auto_base' not in ic

    def test_pack_unknown_interconnect_is_400(self, client):
        draft_id = _new_draft(client, _two_region_text())
        r = client.post(f'/api/draft/{draft_id}/pack',
                        json={'interconnect': 'nope', 'from': 0, 'align': 0})
        assert r.status_code == 400


@needs_fastapi
class TestExportEndpoint:
    def test_canonical_desugars_auto_base(self, client):
        text = _two_region_text(unpin_sram_1=True,
                                auto_base={'from': '0x20000000',
                                           'align': '16M'})
        draft_id = _new_draft(client, text)
        r = client.get(f'/api/draft/{draft_id}/export',
                       params={'mode': 'canonical'})
        assert r.status_code == 200
        assert 'auto_base' not in r.text
        out = yaml.safe_load(r.text)
        ic = out['module']['interconnects'][0]
        assert ic['targets'][1]['base'] == 0x20000000   # concrete integer

    def test_raw_mode_returns_verbatim_text(self, client):
        text = _two_region_text()
        draft_id = _new_draft(client, text)
        r = client.get(f'/api/draft/{draft_id}/export', params={'mode': 'raw'})
        assert r.status_code == 200 and r.text == text

    def test_bogus_mode_is_400(self, client):
        draft_id = _new_draft(client)
        r = client.get(f'/api/draft/{draft_id}/export',
                       params={'mode': 'bogus'})
        assert r.status_code == 400


@needs_fastapi
class TestArtifactsEndpoints:
    @pytest.fixture()
    def built_client(self, tmp_path):
        """Client over a project with a fake build_soc/reports/ tree."""
        from fastapi.testclient import TestClient
        from soc_model.configurator.app import create_app
        adapter = _make_adapter(tmp_path)
        reports = tmp_path / 'build_soc' / 'reports'
        reports.mkdir(parents=True)
        (reports / 'summary.txt').write_text('all good\n')
        (reports / 'blob.bin').write_bytes(b'\x00\x01\x02\x03')
        (tmp_path / 'secret.txt').write_text('outside-build-dir-SECRET\n')
        return TestClient(create_app(adapter), raise_server_exceptions=False)

    def test_missing_build_dir_reports_exists_false(self, client):
        r = client.get('/api/artifacts')
        assert r.status_code == 200
        body = r.json()
        assert body['exists'] is False and body['files'] == []

    def test_lists_files_with_text_flag(self, built_client):
        body = built_client.get('/api/artifacts').json()
        assert body['exists'] is True
        files = {f['path']: f for f in body['files']}
        assert files['reports/summary.txt']['text'] is True
        assert files['reports/blob.bin']['text'] is False

    def test_serves_text_artifact(self, built_client):
        r = built_client.get('/api/artifacts/file',
                             params={'path': 'reports/summary.txt'})
        assert r.status_code == 200 and 'all good' in r.text

    def test_missing_artifact_is_404(self, built_client):
        r = built_client.get('/api/artifacts/file',
                             params={'path': 'reports/nothere.txt'})
        assert r.status_code == 404

    def test_non_text_artifact_is_415(self, built_client):
        r = built_client.get('/api/artifacts/file',
                             params={'path': 'reports/blob.bin'})
        assert r.status_code == 415

    def test_relative_traversal_is_refused(self, built_client):
        r = built_client.get('/api/artifacts/file',
                             params={'path': '../secret.txt'})
        assert r.status_code >= 400          # PermissionError -> 500 today
        assert 'outside-build-dir-SECRET' not in r.text

    def test_absolute_path_is_refused(self, built_client, tmp_path):
        r = built_client.get('/api/artifacts/file',
                             params={'path': str(tmp_path / 'secret.txt')})
        assert r.status_code >= 400
        assert 'outside-build-dir-SECRET' not in r.text
        r2 = built_client.get('/api/artifacts/file',
                              params={'path': '/etc/hostname'})
        assert r2.status_code >= 400
