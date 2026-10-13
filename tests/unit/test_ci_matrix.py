# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for the CI system-validity matrix runner (roadmap doc 03).

Exercises the importable, in-process core of ``ci/matrix.py`` —
:func:`ci.matrix.run_matrix` at the cheapest (``validate``) tier — WITHOUT any
subprocess. The in-process tier wraps the same
:class:`~soc_model.validator.SoCValidator` that ``--validate-only`` runs, via
:class:`~soc_model.gui_service.GuiService`, so a green here == green in CI.

Coverage:
  * a clean config validates (pass detection),
  * a param override is applied to params[*].default and validates,
  * a config that violates the address-overlap rule reports failed,
  * an ``expect_fail`` config that fails as declared is reported green and one
    that unexpectedly passes is reported red,
  * axis (cartesian) expansion,
  * JUnit emission parses + carries the right counts.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

import ci.matrix as M
from tests.conftest import FIXTURES

pytestmark = pytest.mark.unit

BASE_YAML = FIXTURES / "two_region_soc.yaml"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _run(configs, **kw):
    """run_matrix over an explicit config list, in-process validate tier."""
    matrix = {"configs": configs}
    return M.run_matrix(matrix, base_yaml=BASE_YAML, base_dir=FIXTURES,
                        lib_dirs=[FIXTURES], tier="validate",
                        in_process=True, **kw)


def _by_name(results):
    return {r.config: r for r in results}


def _overlap_yaml(tmp_path):
    """Write a copy of the two-region fixture with sram_1 collided into
    sram_0's window (a Tier-1 address overlap), return its path."""
    data = yaml.safe_load(BASE_YAML.read_text())
    for ic in data["module"]["interconnects"]:
        for t in ic["targets"]:
            if t["name"] == "sram_1":
                t["base"] = 0x00000800  # inside sram_0's 0x0..0xFFF window
    out = tmp_path / "overlap_soc.yaml"
    out.write_text(yaml.safe_dump(data))
    return out


# ---------------------------------------------------------------------------
# applying overrides to the dict
# ---------------------------------------------------------------------------
class TestApplyOverrides:
    def test_dict_form_sets_default(self):
        data = yaml.safe_load(BASE_YAML.read_text())
        out = M.apply_overrides_to_dict(data, {"SRAM_RAM_ADDR_W": 16})
        assert out["module"]["params"]["SRAM_RAM_ADDR_W"]["default"] == 16
        # original untouched (deep copy)
        assert data["module"]["params"]["SRAM_RAM_ADDR_W"]["default"] == 12

    def test_unknown_key_added(self):
        data = yaml.safe_load(BASE_YAML.read_text())
        out = M.apply_overrides_to_dict(data, {"BRAND_NEW": 7})
        assert out["module"]["params"]["BRAND_NEW"]["default"] == 7

    def test_list_form_params(self):
        data = {"module": {"name": "x", "params": [
            {"name": "W", "default": 1}]}}
        out = M.apply_overrides_to_dict(data, {"W": 9, "Z": 3})
        idx = {p["name"]: p for p in out["module"]["params"]}
        assert idx["W"]["default"] == 9
        assert idx["Z"]["default"] == 3


# ---------------------------------------------------------------------------
# pass detection
# ---------------------------------------------------------------------------
class TestPassDetection:
    def test_clean_config_passes(self):
        results = _run([{"name": "default", "overrides": {}}])
        r = _by_name(results)["default"]
        assert r.passed is True
        assert r.tier == "validate"
        assert r.skipped is False

    def test_override_config_passes(self):
        results = _run([{"name": "bigger", "overrides": {"SRAM_RAM_ADDR_W": 16}}])
        assert _by_name(results)["bigger"].passed is True


# ---------------------------------------------------------------------------
# fail detection — a config that violates a validator rule
# ---------------------------------------------------------------------------
class TestFailDetection:
    def test_overlap_config_reports_failed(self, tmp_path):
        ov = _overlap_yaml(tmp_path)
        results = _run([{"name": "overlap", "yaml": str(ov)}])
        r = _by_name(results)["overlap"]
        assert r.passed is False                  # positive config, real failure
        assert "overlap" in r.message.lower()
        assert "address" in r.message.lower()

    def test_run_matrix_overall_nonzero_on_failure(self, tmp_path):
        ov = _overlap_yaml(tmp_path)
        results = _run([
            {"name": "ok", "overrides": {}},
            {"name": "bad", "yaml": str(ov)},
        ])
        assert all(r.passed for r in results) is False


# ---------------------------------------------------------------------------
# expect_fail inversion (the matrix's own self-test)
# ---------------------------------------------------------------------------
class TestExpectFail:
    def test_expected_failure_is_green(self, tmp_path):
        ov = _overlap_yaml(tmp_path)
        results = _run([{"name": "neg", "yaml": str(ov),
                         "expect_fail": "validate"}])
        r = _by_name(results)["neg"]
        assert r.passed is True            # failed as declared -> green
        assert r.expected_fail is True

    def test_unexpected_pass_is_red(self):
        # a clean config that wrongly declares expect_fail must go RED
        results = _run([{"name": "wrongly_neg", "overrides": {},
                         "expect_fail": "validate"}])
        r = _by_name(results)["wrongly_neg"]
        assert r.passed is False
        assert "UNEXPECTED PASS" in r.message


# ---------------------------------------------------------------------------
# axis expansion + selection
# ---------------------------------------------------------------------------
class TestAxesAndSelect:
    def test_axis_expands_to_grid(self):
        matrix = {
            "defaults": {"tier": "validate"},
            "configs": [{"name": "default", "overrides": {}}],
            "axes": [{"name": "sweep", "base": "default",
                      "grid": {"SRAM_RAM_ADDR_W": [12, 14, 16]}}],
        }
        cfgs = M.expand_axes(matrix)
        names = [c["name"] for c in cfgs]
        assert "default" in names
        assert sum(1 for n in names if n.startswith("sweep__")) == 3

    def test_select_filters(self):
        results = M.run_matrix(
            {"configs": [{"name": "a", "overrides": {}},
                         {"name": "b", "overrides": {}}]},
            base_yaml=BASE_YAML, base_dir=FIXTURES, lib_dirs=[FIXTURES],
            tier="validate", in_process=True, select="name=a")
        assert [r.config for r in results] == ["a"]


# ---------------------------------------------------------------------------
# JUnit reporting
# ---------------------------------------------------------------------------
class TestJunit:
    def test_junit_parses_with_counts(self, tmp_path):
        results = [
            M.TierResult("ok", "validate", passed=True),
            M.TierResult("bad", "validate", passed=False, message="boom"),
            M.TierResult("eda", "elaborate", passed=True, skipped=True,
                         message="needs EDA"),
        ]
        out = tmp_path / "r.xml"
        M.write_junit(results, out)
        root = ET.parse(out).getroot()
        assert root.tag == "testsuite"
        assert root.get("tests") == "3"
        assert root.get("failures") == "1"
        assert root.get("skipped") == "1"
        cases = {tc.get("name"): tc for tc in root.findall("testcase")}
        assert cases["bad"].find("failure") is not None
        assert cases["eda"].find("skipped") is not None
        assert cases["ok"].find("failure") is None


# ---------------------------------------------------------------------------
# the shipped example matrix actually runs green (integration of the parts)
# ---------------------------------------------------------------------------
def test_example_matrix_runs_green():
    spec = M.load_matrix(_PKG_EXAMPLE)
    results = M.run_matrix(
        spec,
        base_yaml=_PKG_ROOT / spec["defaults"]["base_yaml"],
        base_dir=_PKG_ROOT / spec["defaults"]["base_dir"],
        lib_dirs=[_PKG_ROOT / d for d in spec["defaults"]["lib_dirs"]],
        tier="validate", in_process=True)
    assert results, "example matrix produced no results"
    assert all(r.passed for r in results), \
        "example matrix not all green: " + \
        "; ".join(f"{r.config}:{r.message[:60]}" for r in results if not r.passed)


_PKG_ROOT = Path(__file__).resolve().parents[2]
_PKG_EXAMPLE = _PKG_ROOT / "ci" / "example_matrix.yaml"
