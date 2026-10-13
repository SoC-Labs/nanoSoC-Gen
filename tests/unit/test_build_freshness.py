# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for the diff-vs-built staleness check (doc 06a idea #3):
GuiService.record_build_manifest / build_freshness detect when build_soc/ has
drifted from the current top + lib YAML."""

import os
from pathlib import Path

import pytest

from soc_model.gui_service import GuiService

pytestmark = pytest.mark.unit

T0 = 1_000_000.0          # a fixed "build time" for deterministic mtimes


@pytest.fixture()
def proj(tmp_path):
    top = tmp_path / 'sys_desc' / 'top.yaml'
    top.parent.mkdir()
    top.write_text('module:\n  name: tiny\n  gen: True\n')
    lib = tmp_path / 'sys_desc'                 # top lives under a lib dir
    (lib / 'regions').mkdir()
    region = lib / 'regions' / 'mem.yaml'
    region.write_text('module:\n  name: mem\n  gen: True\n')
    build = tmp_path / 'build_soc'
    build.mkdir()
    svc = GuiService(base_dir=str(tmp_path), lib_dirs=[str(lib)])
    # put every yaml comfortably BEFORE the build time
    for f in (top, region):
        os.utime(f, (T0 - 100, T0 - 100))
    return dict(svc=svc, top=top, lib=lib, region=region, build=build)


class TestRecord:
    def test_manifest_written(self, proj):
        man = proj['svc'].record_build_manifest(
            proj['top'], [str(proj['lib'])], str(proj['build']), now=T0)
        out = proj['build'] / 'build_info' / 'configurator_build.yaml'
        assert out.is_file()
        assert man['top_sha256'] and man['built_epoch'] == T0


class TestFreshness:
    def _fresh(self, proj):
        return proj['svc'].build_freshness(
            proj['top'], [str(proj['lib'])], str(proj['build']))

    def test_unknown_without_manifest(self, proj):
        assert self._fresh(proj)['state'] == 'unknown'

    def test_fresh_when_nothing_changed(self, proj):
        proj['svc'].record_build_manifest(
            proj['top'], [str(proj['lib'])], str(proj['build']), now=T0)
        assert self._fresh(proj)['state'] == 'fresh'

    def test_stale_when_top_edited(self, proj):
        proj['svc'].record_build_manifest(
            proj['top'], [str(proj['lib'])], str(proj['build']), now=T0)
        proj['top'].write_text('module:\n  name: tiny\n  gen: True\n  desc: x\n')
        os.utime(proj['top'], (T0 - 100, T0 - 100))   # same mtime, new content
        r = self._fresh(proj)
        assert r['state'] == 'stale' and 'top' in r['reason']

    def test_stale_when_lib_region_edited(self, proj):
        proj['svc'].record_build_manifest(
            proj['top'], [str(proj['lib'])], str(proj['build']), now=T0)
        # region edited AFTER the build (mtime in the future) — the case the
        # top-mtime auto-regen misses
        os.utime(proj['region'], (T0 + 50, T0 + 50))
        r = self._fresh(proj)
        assert r['state'] == 'stale' and 'lib' in r['reason']
        assert any('mem.yaml' in c for c in r['changed'])

    def test_top_mtime_bump_alone_is_not_stale(self, proj):
        # a touched-but-unchanged top (e.g. git checkout) must NOT read stale:
        # hash matches and the top file is excluded from the lib mtime walk
        proj['svc'].record_build_manifest(
            proj['top'], [str(proj['lib'])], str(proj['build']), now=T0)
        os.utime(proj['top'], (T0 + 999, T0 + 999))
        assert self._fresh(proj)['state'] == 'fresh'


# ---------------------------------------------------------------------------
# endpoint: /api/project surfaces build_fresh (skip without fastapi)
# ---------------------------------------------------------------------------
try:
    import fastapi  # noqa: F401
    _HAVE_FASTAPI = True
except ImportError:
    _HAVE_FASTAPI = False

needs_fastapi = pytest.mark.skipif(
    not _HAVE_FASTAPI, reason='configurator extra (fastapi) not installed')


@needs_fastapi
class TestProjectEndpoint:
    def test_project_reports_build_fresh(self, tmp_path):
        from fastapi.testclient import TestClient
        from soc_model.configurator.app import create_app
        from soc_model.configurator.project import ProjectAdapter
        (tmp_path / 'sys_desc').mkdir()
        top = tmp_path / 'sys_desc' / 'top.yaml'
        top.write_text('module:\n  name: tiny\n  gen: True\n')
        adapter = ProjectAdapter(
            root=str(tmp_path), top_yaml='sys_desc/top.yaml',
            lib_dirs=['sys_desc'], build_dir='build_soc')
        c = TestClient(create_app(adapter))
        body = c.get('/api/project').json()
        assert 'build_fresh' in body and 'lib_shadows' in body
        assert body['build_fresh']['state'] == 'unknown'   # no build yet
        # record a manifest, then it reads fresh
        GuiService(base_dir=str(tmp_path),
                   lib_dirs=[str(tmp_path / 'sys_desc')]).record_build_manifest(
            top, [str(tmp_path / 'sys_desc')],
            str(tmp_path / 'build_soc'), now=T0)
        os.utime(top, (T0 - 100, T0 - 100))
        assert c.get('/api/project').json()['build_fresh']['state'] == 'fresh'
