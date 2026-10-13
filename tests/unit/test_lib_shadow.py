# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for the lib-dir shadowing auditor (doc 06a idea #17):
SoCParser.module_shadows + GuiService.lib_shadow_report flag module name /
filename-stem keys claimed by more than one lib file (silent last-writer-wins).
"""

from pathlib import Path

import pytest

from soc_model.gui_service import GuiService
from soc_model.parser import SoCParser

pytestmark = pytest.mark.unit


def _mod(path: Path, name: str):
    path.write_text(f"module:\n  name: {name}\n  gen: True\n")


@pytest.fixture()
def libs(tmp_path):
    a, b = tmp_path / 'liba', tmp_path / 'libb'
    a.mkdir()
    b.mkdir()
    _mod(a / 'dup_mod.yaml', 'dup_mod')        # name+stem collide across a,b
    _mod(b / 'dup_mod.yaml', 'dup_mod')
    _mod(a / 'shared.yaml', 'shared_a')        # stem 'shared' collides only
    _mod(b / 'shared.yaml', 'shared_b')
    _mod(a / 'unique.yaml', 'unique')          # no collision
    return tmp_path, a, b


class TestParserShadows:
    def test_name_and_stem_collisions_reported(self, libs):
        root, a, b = libs
        p = SoCParser(base_dir=str(root), lib_dir=[str(a), str(b)])
        sh = p.module_shadows()
        assert 'dup_mod' in sh and len(sh['dup_mod']) == 2
        assert 'shared' in sh          # stem collision
        assert 'unique' not in sh      # singletons excluded
        assert 'shared_a' not in sh    # distinct names don't collide

    def test_winner_is_last_in_scan_order(self, libs):
        root, a, b = libs
        p = SoCParser(base_dir=str(root), lib_dir=[str(a), str(b)])
        files = p.module_shadows()['dup_mod']
        assert 'libb' in files[-1]                 # last lib_dir wins
        assert any('liba' in f for f in files[:-1])

    def test_overlapping_lib_dirs_not_a_false_collision(self, tmp_path):
        # the same dir listed twice must NOT report a self-shadow
        d = tmp_path / 'lib'
        d.mkdir()
        _mod(d / 'only.yaml', 'only')
        p = SoCParser(base_dir=str(tmp_path), lib_dir=[str(d), str(d)])
        assert 'only' not in p.module_shadows()


class TestServiceReport:
    def test_report_shape_and_relative_paths(self, libs):
        root, a, b = libs
        svc = GuiService(base_dir=str(root), lib_dirs=[str(a), str(b)])
        rep = svc.lib_shadow_report()
        assert rep['count'] == 2
        dm = next(s for s in rep['shadows'] if s['key'] == 'dup_mod')
        # winner last, shadowed = the rest, paths relative to base_dir
        assert dm['winner'].startswith('libb/')
        assert dm['shadowed'] == ['liba/dup_mod.yaml']
        assert dm['files'][-1] == dm['winner']

    def test_clean_libs_report_empty(self, tmp_path):
        d = tmp_path / 'lib'
        d.mkdir()
        _mod(d / 'a.yaml', 'a')
        _mod(d / 'b.yaml', 'b')
        svc = GuiService(base_dir=str(tmp_path), lib_dirs=[str(d)])
        assert svc.lib_shadow_report() == {'count': 0, 'shadows': []}

    def test_catalogs_includes_shadows(self, libs):
        root, a, b = libs
        svc = GuiService(base_dir=str(root), lib_dirs=[str(a), str(b)])
        cat = svc.catalogs()
        assert 'shadows' in cat
        assert {s['key'] for s in cat['shadows']} >= {'dup_mod', 'shared'}
