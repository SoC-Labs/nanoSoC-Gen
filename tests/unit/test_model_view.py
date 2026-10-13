# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for the model-view JSON artifact + viewer-mode HTML
(``soc_model/backends/html.py`` build_payload / generate_json /
generate_viewer_html) and the configurator's Built-SoC surfaces
(``/api/generated`` + ``/generated``).

Contract under test (the html backend redesign):

  * ``build_payload()`` is the ONE producer of the report data — schema v1:
    ``{meta, graph, all_graphs, address_map, initiator_maps,
    interconnect_info, hierarchy, assigns, validation}``.
  * ``generate_html`` consumes that payload and stays BYTE-IDENTICAL to the
    pre-refactor self-contained page (no timestamps anywhere, so a straight
    byte compare is meaningful; proven here via determinism + the
    inline/viewer shared-template tail).
  * ``generate_json`` writes the payload as a standalone artifact.
  * ``generate_viewer_html`` emits the SAME page with a fetch() bootstrap
    instead of inlined data (one template, two data-injection strategies).

FastAPI endpoint tests reuse the ``needs_fastapi`` skip pattern from
``test_configurator.py``; lib dirs are always ABSOLUTE.
"""

import json
import shutil
from pathlib import Path

import pytest

from soc_model.backends.html import (
    SoCVisualizer,
    _inline_data_section,
    render_viewer_html,
)
from soc_model.utils import _strip_timestamps

from tests.helpers import build_module_from_yaml

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'

PAYLOAD_KEYS = ['meta', 'graph', 'all_graphs', 'address_map',
                'initiator_maps', 'interconnect_info', 'hierarchy',
                'assigns', 'validation']


@pytest.fixture()
def minimal_top():
    return build_module_from_yaml(FIXTURES, 'minimal_soc.yaml')


@pytest.fixture()
def two_region_top():
    return build_module_from_yaml(FIXTURES, 'two_region_soc.yaml')


# ---------------------------------------------------------------------------
# build_payload — completeness vs the fixture model
# ---------------------------------------------------------------------------
class TestBuildPayload:
    def test_payload_has_all_schema_v1_keys(self, minimal_top):
        payload = SoCVisualizer(minimal_top).build_payload()
        assert list(payload.keys()) == PAYLOAD_KEYS
        assert payload['meta'] == {
            'name': 'minimal_soc',
            'desc': minimal_top.desc,
            'generator': 'nanosoc_gen',
            'schema': 1,
        }

    def test_graph_nodes_match_instances_and_interconnects(self, two_region_top):
        payload = SoCVisualizer(two_region_top).build_payload()
        node_ids = {n['id'] for n in payload['graph']['nodes']}
        # every instance and every interconnect of the top is a node ...
        for inst in two_region_top.instances:
            assert inst.instance_name in node_ids
        for ic in two_region_top.interconnects:
            assert ic.name in node_ids
        # ... plus the top container itself.
        assert two_region_top.name in node_ids
        assert payload['graph']['links'], 'fixture has connections'

    def test_address_map_entries(self, two_region_top):
        payload = SoCVisualizer(two_region_top).build_payload()
        regions = {r['name']: r for r in payload['address_map']}
        assert {'sram_0', 'sram_1'} <= set(regions)
        r0 = regions['sram_0']
        assert r0['abs_base'] == 0x00000000 and r0['size'] == 0x1000
        # initiator maps cover the single cpu initiator with both targets.
        init_maps = payload['initiator_maps']
        (init_name, im), = init_maps.items()
        assert {r['name'] for r in im['regions']} == {'sram_0', 'sram_1'}
        # interconnect info carries the top-level IC with the initiator.
        ics = payload['interconnect_info']
        assert any(ic['top_level'] and ic['initiators'] for ic in ics)

    def test_validation_messages_stringified(self, minimal_top):
        class Msg:
            def __str__(self):
                return '[ERROR] boom'
        payload = SoCVisualizer(minimal_top).build_payload([Msg()])
        assert payload['validation'] == ['[ERROR] boom']
        assert SoCVisualizer(minimal_top).build_payload()['validation'] == []

    def test_payload_is_json_serializable(self, two_region_top):
        payload = SoCVisualizer(two_region_top).build_payload()
        round_trip = json.loads(json.dumps(payload))
        assert round_trip['meta']['schema'] == 1


# ---------------------------------------------------------------------------
# generate_json — the standalone artifact
# ---------------------------------------------------------------------------
class TestGenerateJson:
    def test_writes_valid_schema_v1_json(self, minimal_top, tmp_path):
        out = tmp_path / 'minimal_soc_model_view.json'
        SoCVisualizer(minimal_top).generate_json(
            str(out), validation_messages=['[WARNING] w'])
        data = json.loads(out.read_text())
        assert list(data.keys()) == PAYLOAD_KEYS
        assert data['meta']['schema'] == 1
        assert data['meta']['generator'] == 'nanosoc_gen'
        assert data['validation'] == ['[WARNING] w']
        assert out.read_text().endswith('\n')

    def test_json_equals_build_payload(self, two_region_top, tmp_path):
        viz = SoCVisualizer(two_region_top)
        out = tmp_path / 'mv.json'
        viz.generate_json(str(out))
        assert json.loads(out.read_text()) == viz.build_payload()


# ---------------------------------------------------------------------------
# inline html — byte-stability of the existing report
# ---------------------------------------------------------------------------
class TestInlineHtmlIdentity:
    def test_inline_html_is_payload_plus_template(self, minimal_top, tmp_path):
        """The self-contained page is exactly the shared template with the
        inline data strategy — the payload really is the single source."""
        viz = SoCVisualizer(minimal_top)
        out = tmp_path / 'c.html'
        viz.generate_html(str(out))
        html = out.read_text()
        assert _inline_data_section(viz.build_payload()) in html

    def test_inline_html_deterministic_and_timestamp_free(self, minimal_top,
                                                          tmp_path):
        """No timestamps in the page (so byte compares are meaningful) and
        two generations are byte-identical."""
        viz = SoCVisualizer(minimal_top)
        a, b = tmp_path / 'a.html', tmp_path / 'b.html'
        viz.generate_html(str(a))
        SoCVisualizer(minimal_top).generate_html(str(b))
        ta = a.read_text()
        assert ta == b.read_text()
        assert _strip_timestamps(ta) == '\n'.join(ta.splitlines())
        # structural teeth: the known consts + panels are all present.
        for marker in ('const graphData = {', 'const allGraphs = {',
                       'const icInfo = [', 'Connectivity Visualiser',
                       'minimal_soc'):
            assert marker in ta

    def test_inline_and_viewer_share_the_template_tail(self, minimal_top,
                                                       tmp_path):
        """Anti-drift: everything after the data-injection seam (the whole
        viewer JS + body) must be byte-identical between the two modes."""
        viz = SoCVisualizer(minimal_top)
        out = tmp_path / 'c.html'
        viz.generate_html(str(out))
        inline = out.read_text()
        viewer = render_viewer_html('minimal_soc', 'x.json')
        seam = '// Human-readable size formatting'
        assert inline.split(seam, 1)[1] == viewer.split(seam, 1)[1]
        # and the head/body before the <script> seam matches too.
        assert inline.split('<script', 1)[0] == viewer.split('<script', 1)[0]


# ---------------------------------------------------------------------------
# viewer html — fetch bootstrap instead of inline payload
# ---------------------------------------------------------------------------
class TestViewerHtml:
    def test_viewer_has_fetch_bootstrap_not_inline_data(self, minimal_top,
                                                        tmp_path):
        out = tmp_path / 'viewer.html'
        SoCVisualizer(minimal_top).generate_viewer_html(
            str(out), '/api/generated')
        html = out.read_text()
        assert '<script type="module">' in html
        assert 'await fetch(_payloadUrl)' in html
        assert 'const _payloadUrl = "/api/generated";' in html
        assert 'const graphData = _payload.graph;' in html
        # the clear failure body names the http(s) requirement.
        assert 'served over' in html and 'http' in html
        # NO inlined payload in viewer mode.
        assert 'const graphData = {' not in html
        assert 'const allGraphs = {' not in html

    def test_render_viewer_html_escapes_url(self):
        html = render_viewer_html('t', 'reports/a"b.json')
        assert json.dumps('reports/a"b.json') in html

    def test_msg_count_lands_in_nav(self):
        html = render_viewer_html('t', 'x.json', msg_count=7)
        assert 'Validation (7)' in html


# ---------------------------------------------------------------------------
# configurator endpoints (fastapi optional — same skip pattern as
# tests/unit/test_configurator.py)
# ---------------------------------------------------------------------------
try:
    import fastapi  # noqa: F401
    _HAVE_FASTAPI = True
except ImportError:
    _HAVE_FASTAPI = False

needs_fastapi = pytest.mark.skipif(
    not _HAVE_FASTAPI, reason='configurator extra (fastapi) not installed')


def _make_client(tmp_path, with_build=True):
    from fastapi.testclient import TestClient
    from soc_model.configurator.app import create_app
    from soc_model.configurator.project import ProjectAdapter

    top = tmp_path / 'sys_desc'
    top.mkdir(exist_ok=True)
    shutil.copy(FIXTURES / 'minimal_soc.yaml', top / 'minimal_soc.yaml')
    if with_build:
        reports = tmp_path / 'build_soc' / 'reports'
        reports.mkdir(parents=True)
        viz = SoCVisualizer(build_module_from_yaml(FIXTURES, 'minimal_soc.yaml'))
        viz.generate_json(str(reports / 'minimal_soc_model_view.json'),
                          validation_messages=['[WARNING] w'])
    adapter = ProjectAdapter(root=str(tmp_path),
                             top_yaml='sys_desc/minimal_soc.yaml',
                             lib_dirs=[str(FIXTURES)])
    return TestClient(create_app(adapter))


@needs_fastapi
class TestGeneratedEndpoints:
    def test_api_generated_returns_payload_with_provenance(self, tmp_path):
        client = _make_client(tmp_path)
        r = client.get('/api/generated')
        assert r.status_code == 200
        d = r.json()
        assert set(PAYLOAD_KEYS) <= set(d.keys())
        assert d['meta']['name'] == 'minimal_soc'
        assert d['meta']['source_file'].endswith(
            'reports/minimal_soc_model_view.json')
        assert isinstance(d['meta']['source_mtime'], float)
        assert d['validation'] == ['[WARNING] w']
        assert d['graph']['nodes']

    def test_api_generated_404_when_no_build(self, tmp_path):
        client = _make_client(tmp_path, with_build=False)
        r = client.get('/api/generated')
        assert r.status_code == 404
        assert 'make soc' in r.json()['detail']

    def test_api_generated_picks_newest(self, tmp_path):
        import os
        client = _make_client(tmp_path)
        reports = tmp_path / 'build_soc' / 'reports'
        old = reports / 'older_soc_model_view.json'
        old.write_text(json.dumps({'meta': {'name': 'older_soc'}}))
        os.utime(old, (1, 1))                       # ancient mtime
        d = client.get('/api/generated').json()
        assert d['meta']['name'] == 'minimal_soc'

    def test_api_generated_corrupt_json_is_500(self, tmp_path):
        client = _make_client(tmp_path, with_build=False)
        reports = tmp_path / 'build_soc' / 'reports'
        reports.mkdir(parents=True)
        (reports / 'bad_model_view.json').write_text('{nope')
        assert client.get('/api/generated').status_code == 500

    def test_generated_viewer_page(self, tmp_path):
        client = _make_client(tmp_path)
        r = client.get('/generated')
        assert r.status_code == 200
        assert 'text/html' in r.headers['content-type']
        html = r.text
        assert 'minimal_soc — Connectivity Visualiser' in html
        assert 'const _payloadUrl = "/api/generated";' in html
        assert 'await fetch(_payloadUrl)' in html
        assert 'Validation (1)' in html             # msg count from the JSON
        assert 'const graphData = {' not in html    # no inline payload

    def test_generated_viewer_404_when_no_build(self, tmp_path):
        client = _make_client(tmp_path, with_build=False)
        assert client.get('/generated').status_code == 404

    def test_index_has_built_soc_tab(self, tmp_path):
        client = _make_client(tmp_path, with_build=False)
        html = client.get('/').text
        # The grey top tabs were replaced by a JS-rendered left sidebar
        # (core.js App.NAV). The "Built SoC" view is now reachable via the
        # generated panel section + its script include; the data-panel
        # attribute is emitted client-side by core.renderNav().
        assert 'id="panel-generated"' in html
        assert 'panels/generated.js' in html
        assert 'panels/core.js' in html or '/static/js/core.js' in html
