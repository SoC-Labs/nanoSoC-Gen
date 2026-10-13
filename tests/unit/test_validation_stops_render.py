# Copyright 2026, SoC Labs (www.soclabs.org)
"""Validation errors stop the render (Train 7 G8).

The validator's errors (an undriven top-level output, a cross-instance
reference that resolves nowhere, an unknown module, an address overlap, ...)
were printed and generation went on, exit 0: only interconnect errors were
blocking. Now any error stops the render before anything is written, unless
--validation-waive names that one error with a reason. A waiver naming no
error is refused, so waivers cannot go stale.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from tests.helpers import arm_free_env

pytestmark = pytest.mark.unit

PKG_ROOT = Path(__file__).resolve().parents[2]


def _bad(fixtures_dir, tmp_path):
    d = yaml.safe_load((fixtures_dir / "minimal_soc.yaml").read_text())
    d["module"]["interfaces"].append({"name": "dangling_out", "type": "wire", "direction": "out",
                                      "params": {"WIDTH": 1}, "desc": "undriven output"})
    p = tmp_path / "yaml" / "minimal_soc.yaml"
    p.parent.mkdir()
    p.write_text(yaml.safe_dump(d, sort_keys=False))
    return p


def _cli(y, build, fixtures_dir, *extra):
    env = arm_free_env()
    env.update(PYTHONPATH=str(PKG_ROOT), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-m", "soc_model", str(y), "--build-dir", str(build),
                           "--lib-dir", str(fixtures_dir), *extra], cwd=str(PKG_ROOT), env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=300)


KEY = "connection:dangling_out"


def test_an_error_stops_the_render(fixtures_dir, tmp_path):
    build = tmp_path / "build"
    r = _cli(_bad(fixtures_dir, tmp_path), build, fixtures_dir)
    assert r.returncode == 1, r.stdout[-2000:]
    assert "refusing to generate: 1 validation error(s)" in r.stderr
    assert "Top-level output 'dangling_out' has no driver" in r.stderr
    assert f"(e.g. {KEY}=<reason>)" in r.stderr and "Nothing was generated." in r.stderr
    assert not build.exists()


def test_a_named_waiver_lets_that_error_through(fixtures_dir, tmp_path):
    build = tmp_path / "build"
    r = _cli(_bad(fixtures_dir, tmp_path), build, fixtures_dir,
             "--validation-waive", f"{KEY}=driven in the hand-written wrapper")
    assert r.returncode == 0, r.stderr[-2000:]
    assert f"WAIVED validation error {KEY} (1): driven in the hand-written wrapper" in r.stdout
    assert (build / "rtl" / "minimal_soc.sv").is_file()


def test_a_waiver_naming_no_error_fails(fixtures_dir, tmp_path):
    build = tmp_path / "build"
    r = _cli(fixtures_dir / "minimal_soc.yaml", build, fixtures_dir,
             "--validation-waive", f"{KEY}=stale")
    assert r.returncode == 1
    assert f"--validation-waive {KEY}: no validation error of that name" in r.stderr
    assert "refusing to generate: 0 validation error(s) and 1 --validation-waive naming no error" in r.stderr
    assert not build.exists()


@pytest.mark.parametrize("arg", ["connection:dangling_out", "connection:dangling_out=",
                                 "dangling_out=why", "Connection dangling=why"])
def test_a_malformed_waiver_is_a_usage_error(fixtures_dir, tmp_path, arg):
    r = _cli(_bad(fixtures_dir, tmp_path), tmp_path / "build", fixtures_dir, "--validation-waive", arg)
    assert r.returncode == 2 and "expected <category>:<location>=<reason>" in r.stderr


def test_validate_only_and_no_validate(fixtures_dir, tmp_path):
    y = _bad(fixtures_dir, tmp_path)
    assert _cli(y, tmp_path / "b", fixtures_dir, "--validate-only").returncode == 1
    assert _cli(y, tmp_path / "b", fixtures_dir, "--validate-only",
                "--validation-waive", f"{KEY}=why").returncode == 0
    r = _cli(y, tmp_path / "b", fixtures_dir, "--no-validate", "--validation-waive", f"{KEY}=why")
    assert r.returncode == 2 and "nothing is validated" in r.stderr
