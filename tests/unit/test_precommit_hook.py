# Copyright 2026, SoC Labs (www.soclabs.org)
"""Integration test for the configurator pre-commit hook (doc 06a #13):
scripts/hooks/pre-commit runs the headless `check` on staged top/lib YAML and
blocks the commit on error-level findings (skips when irrelevant/unwired)."""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parent.parent.parent          # nanosoc_gen root
HOOK = REPO / 'scripts' / 'hooks' / 'pre-commit'


def _git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args],
                          capture_output=True, text=True)


def _run_hook(repo):
    env = dict(os.environ)
    # make soc_model importable in the hook's subprocess + force this python
    env['PYTHONPATH'] = str(REPO) + os.pathsep + env.get('PYTHONPATH', '')
    env['SOC_MODEL_PYTHON'] = os.environ.get('PYTHON', 'python3')
    return subprocess.run(['bash', str(HOOK)], cwd=str(repo),
                          capture_output=True, text=True, env=env)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / 'proj'
    r.mkdir()
    _git(r, 'init', '-q')
    _git(r, 'config', 'user.email', 't@t')
    _git(r, 'config', 'user.name', 't')
    (r / 'configurator.yaml').write_text(
        'top_yaml: sys_desc/top.yaml\nlib_dirs:\n  - sys_desc\n')
    (r / 'sys_desc').mkdir()
    return r


class TestPreCommitHook:
    def test_skips_without_configurator_yaml(self, tmp_path):
        r = tmp_path / 'plain'
        r.mkdir()
        _git(r, 'init', '-q')
        (r / 'foo.txt').write_text('hi')
        _git(r, 'add', 'foo.txt')
        assert _run_hook(r).returncode == 0       # unwired repo -> skip

    def test_skips_when_no_relevant_file_staged(self, repo):
        (repo / 'README.md').write_text('x')
        _git(repo, 'add', 'README.md')            # not top, not under lib dir
        assert _run_hook(repo).returncode == 0

    def test_blocks_on_error_level_top(self, repo):
        # a module with no name -> tier-1 schema error -> check exits 1
        (repo / 'sys_desc' / 'top.yaml').write_text(
            'module:\n  gen: True\n')
        _git(repo, 'add', 'sys_desc/top.yaml')
        res = _run_hook(repo)
        assert res.returncode == 1
        assert 'BLOCKED' in (res.stdout + res.stderr)

    def test_passes_valid_top(self, repo):
        # a genuinely error-free SoC (clock+reset+cpu initiator + a memory
        # target) -> check exits 0 -> hook allows the commit
        from soc_model.gui_service import GuiService
        fixtures = Path(__file__).resolve().parent.parent / 'fixtures'
        svc = GuiService(base_dir=str(fixtures), lib_dirs=[str(fixtures)])
        data = svc.add_component(GuiService.new_soc('tiny'),
                                 'tiny_region', size=0x1000)['data']
        assert svc.validate(data)['errors'] == 0          # precondition
        (repo / 'sys_desc' / 'top.yaml').write_text(svc.to_yaml(data))
        # point the adapter's lib dirs at the fixtures so tiny_region resolves
        (repo / 'configurator.yaml').write_text(
            'top_yaml: sys_desc/top.yaml\nlib_dirs:\n'
            f'  - {fixtures}\n  - sys_desc\n')
        _git(repo, 'add', 'sys_desc/top.yaml')
        res = _run_hook(repo)
        assert res.returncode == 0, res.stdout + res.stderr
