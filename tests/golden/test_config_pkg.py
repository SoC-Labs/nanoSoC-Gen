# Copyright 2026, SoC Labs (www.soclabs.org)
r"""Golden + unit tests for ``soc_model/backends/soc_config_pkg.py``.

The config-package backend (:class:`SoCConfigPkgBackend`) emits two files on
every successful ``generate``:

  * ``<top.name>_soc_config_pkg.sv`` — a SystemVerilog ``package`` of design +
    simulation localparams (jinja2-rendered, carries a ``// Generated:`` and a
    copyright line — both stripped by the shared snapshot helper);
  * ``<top.name>_soc_config.vh`` — a Verilog ``\`define`` include (the
    gen_defines.v replacement), ALWAYS written, also carrying a ``// Generated:``
    timestamp line.

A per-package ``.flist`` is written ONLY when ``flist_dir`` is supplied.

Tier split:
  * ``unit``  — design-param filter, override semantics, .vh / .flist
    conditionals, sim-param defaults, the ``Environment is None`` early-out.
  * ``golden`` — byte-stable snapshots of the rendered ``.sv`` + ``.vh`` for the
    hermetic ``minimal_soc`` fixture.

Covers TEST_PLAN section 5.8 (CP-01..CP-13) plus the config_pkg flist rows of
5.6 (FL-02 / FL-03).
"""

from pathlib import Path

import pytest

from soc_model.backends import soc_config_pkg as cfgpkg
from soc_model.backends.soc_config_pkg import (
    ConfigParam,
    SoCConfigPkgBackend,
    _DESIGN_PARAMS,
    _SIM_PARAM_DEFAULTS,
)
from soc_model.model import Module

from tests.conftest import SNAPSHOTS
from tests.helpers import assert_matches_snapshot, build_module_from_yaml


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    return Path(path).read_text()


def _design_localparam_names(sv_text: str):
    """Return the localparam names emitted in the Design Configuration block."""
    names = []
    for line in sv_text.splitlines():
        s = line.strip()
        if s.startswith("localparam") and "=" in s:
            # 'localparam int SYS_ADDR_W = 32;  // ...'  OR  'localparam NAME = "..."'
            lhs = s.split("=", 1)[0]
            tok = lhs.replace("localparam", "").replace("int", "").strip()
            names.append(tok)
    return names


# ===========================================================================
# unit tier
# ===========================================================================

class TestArtifacts:
    pytestmark = pytest.mark.unit

    def test_returns_sv_path(self, fixtures_dir, tmp_path):
        # CP-01 (path half): generate returns the .sv Path and it exists.
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        out = SoCConfigPkgBackend(top).generate(tmp_path / "rtl")
        assert out == (tmp_path / "rtl" / "minimal_soc_soc_config_pkg.sv")
        assert out.exists()
        text = _read(out)
        assert "package minimal_soc_soc_config_pkg;" in text
        assert "endpackage" in text

    def test_vh_always_written_without_flist(self, fixtures_dir, tmp_path):
        # CP-02: the .vh include is emitted even with flist_dir=None.
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        SoCConfigPkgBackend(top).generate(tmp_path / "rtl")
        vh = tmp_path / "rtl" / "minimal_soc_soc_config.vh"
        assert vh.exists()
        text = _read(vh)
        # gen_defines.v replacement: `define forms for the design params.
        assert "`define SYS_ADDR_W 32" in text
        assert "`define SYS_DATA_W 32" in text

    def test_flist_conditional_and_makevar_path(self, fixtures_dir, tmp_path):
        # CP-03 / FL-02 / FL-03: no flist when flist_dir is None; written with a
        # layout-agnostic make-variable .sv path + header when a flist_dir is given.
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")

        # No flist_dir -> no .flist anywhere.
        SoCConfigPkgBackend(top).generate(tmp_path / "rtl_noflist")
        assert list((tmp_path / "rtl_noflist").glob("*.flist")) == []

        # flist_dir given -> .flist present, header + $(SOCLABS_NANOSOC_SOC_DIR) path.
        out = SoCConfigPkgBackend(top).generate(tmp_path / "rtl", tmp_path / "flist")
        flist = tmp_path / "flist" / "minimal_soc_soc_config_pkg.flist"
        assert flist.exists()
        body = _read(flist)
        assert "// Auto-generated filelist for minimal_soc_soc_config_pkg" in body
        # Portable make-variable path, not the absolute path of the emitted .sv.
        assert ("$(SOCLABS_NANOSOC_SOC_DIR)/build_soc/rtl/"
                "minimal_soc_soc_config_pkg.sv") in body
        assert str(out.resolve()) not in body
        assert not Path(body.strip().splitlines()[-1]).is_absolute()

    def test_design_params_filtered_to_known_set(self, fixtures_dir, tmp_path):
        # CP-04: only params in (_DESIGN_PARAMS ∩ top.params) are emitted in the
        # design section. minimal_soc declares SYS_CLK_FREQ_HZ / SYS_ADDR_W /
        # SYS_DATA_W / SRAM_RAM_ADDR_W — only the SYS_ADDR_W & SYS_DATA_W ones are
        # in _DESIGN_PARAMS.
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        out = SoCConfigPkgBackend(top).generate(tmp_path / "rtl")
        names = _design_localparam_names(_read(out))

        assert "SYS_ADDR_W" in names
        assert "SYS_DATA_W" in names
        # Declared in YAML but NOT in _DESIGN_PARAMS -> filtered out.
        assert "SYS_CLK_FREQ_HZ" not in names
        assert "SRAM_RAM_ADDR_W" not in names
        # Defensive: the filter never emits a design param outside the union.
        for nm in names:
            if nm in ("FAST_LOAD", "VCD_ENABLE"):
                continue  # sim params share the localparam syntax
            assert nm in _DESIGN_PARAMS

    def test_override_replaces_yaml_default(self, fixtures_dir, tmp_path):
        # CP-05: an override for a YAML design param replaces its default value.
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        out = SoCConfigPkgBackend(top).generate(
            tmp_path / "rtl", config_overrides={"SYS_ADDR_W": 24}
        )
        text = _read(out)
        assert "SYS_ADDR_W" in text
        # rendered as 'localparam int SYS_ADDR_W           = 24;'
        line = next(ln for ln in text.splitlines()
                    if ln.strip().startswith("localparam") and "SYS_ADDR_W" in ln)
        assert "= 24;" in line
        assert "= 32;" not in line

    def test_override_not_in_yaml_still_emitted(self, fixtures_dir, tmp_path):
        # CP-06: a design param provided as an override but absent from the YAML
        # is still emitted (the `elif pname in overrides` branch). minimal_soc
        # does not declare NANOSOC_PARTNUMBER, which IS in _DESIGN_PARAMS.
        assert "NANOSOC_PARTNUMBER" in _DESIGN_PARAMS
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        assert "NANOSOC_PARTNUMBER" not in top.params
        out = SoCConfigPkgBackend(top).generate(
            tmp_path / "rtl", config_overrides={"NANOSOC_PARTNUMBER": 0xABCD}
        )
        names = _design_localparam_names(_read(out))
        assert "NANOSOC_PARTNUMBER" in names

    def test_sim_params_default_and_override(self, fixtures_dir, tmp_path):
        # CP-08: FAST_LOAD & VCD_ENABLE always present (default 0); an override
        # sets the value.
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")

        out_def = SoCConfigPkgBackend(top).generate(tmp_path / "rtl_def")
        text_def = _read(out_def)
        assert "FAST_LOAD" in text_def and "VCD_ENABLE" in text_def
        for nm in ("FAST_LOAD", "VCD_ENABLE"):
            line = next(ln for ln in text_def.splitlines()
                        if ln.strip().startswith("localparam") and nm in ln)
            assert "= 0;" in line

        out_ovr = SoCConfigPkgBackend(top).generate(
            tmp_path / "rtl_ovr", config_overrides={"VCD_ENABLE": 1}
        )
        text_ovr = _read(out_ovr)
        vcd = next(ln for ln in text_ovr.splitlines()
                   if ln.strip().startswith("localparam") and "VCD_ENABLE" in ln)
        assert "= 1;" in vcd
        # FAST_LOAD still at its default.
        fl = next(ln for ln in text_ovr.splitlines()
                  if ln.strip().startswith("localparam") and "FAST_LOAD" in ln)
        assert "= 0;" in fl

    def test_verilog_header_str_vs_int_define(self, tmp_path):
        # CP-09: _write_verilog_header emits `define NAME "val"` for str params
        # and `define NAME val` for int params.
        top = Module(name="hdr")
        be = SoCConfigPkgBackend(top)
        design = [
            ConfigParam(name="STR_P", value="hello", type="str"),
            ConfigParam(name="INT_P", value=7, type="int"),
        ]
        sim = [ConfigParam(name="FAST_LOAD", value=0, type="int")]
        vh = tmp_path / "hdr_soc_config.vh"
        be._write_verilog_header(design, sim, vh)
        text = _read(vh)
        assert '`define STR_P "hello"' in text
        assert "`define INT_P 7" in text
        # sim section header + define.
        assert "// Simulation configuration" in text
        assert "`define FAST_LOAD 0" in text

    def test_environment_none_returns_none(self, fixtures_dir, tmp_path, monkeypatch):
        # CP-11: jinja2 unavailable -> warns, returns None, writes nothing.
        monkeypatch.setattr(cfgpkg, "Environment", None)
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        rtl = tmp_path / "rtl"
        result = SoCConfigPkgBackend(top).generate(rtl, tmp_path / "flist")
        assert result is None
        # No .sv / .vh / .flist produced.
        assert not (rtl / "minimal_soc_soc_config_pkg.sv").exists()
        assert not (rtl / "minimal_soc_soc_config.vh").exists()
        assert not (tmp_path / "flist").exists()

    def test_empty_params_module_no_design_localparams(self, tmp_path):
        # CP-12: a Module with no params still emits a valid .sv (sim params
        # only) and a .vh with no design `define lines beyond the sim block.
        top = Module(name="bare")
        out = SoCConfigPkgBackend(top).generate(tmp_path / "rtl")
        assert out is not None and out.exists()
        text = _read(out)
        assert "package bare_soc_config_pkg;" in text
        assert "endpackage" in text
        names = _design_localparam_names(text)
        # Only the two sim params appear as localparams; no design params.
        assert set(names) == {"FAST_LOAD", "VCD_ENABLE"}

        vh = tmp_path / "rtl" / "bare_soc_config.vh"
        defines = [ln for ln in _read(vh).splitlines() if ln.startswith("`define")]
        assert defines == ["`define FAST_LOAD 0", "`define VCD_ENABLE 0"]

    def test_sim_param_defaults_contract(self):
        # Guard the module-level sim defaults the backend relies on.
        assert set(_SIM_PARAM_DEFAULTS) == {"FAST_LOAD", "VCD_ENABLE"}
        assert _SIM_PARAM_DEFAULTS["FAST_LOAD"][0] == 0
        assert _SIM_PARAM_DEFAULTS["VCD_ENABLE"][0] == 0


# ===========================================================================
# golden tier
# ===========================================================================

class TestGolden:
    pytestmark = pytest.mark.golden

    def test_sv_and_vh_golden(self, fixtures_dir, tmp_path, request):
        # CP-01 / CP-10 / CP-13: full .sv + .vh byte-match (timestamps stripped).
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        out = SoCConfigPkgBackend(top).generate(tmp_path / "rtl")
        sv_text = _read(out)
        vh_text = _read(tmp_path / "rtl" / "minimal_soc_soc_config.vh")

        # Structural teeth so an empty/garbage golden cannot silently pass.
        assert "package minimal_soc_soc_config_pkg;" in sv_text
        assert "endpackage" in sv_text
        # CP-10: source_yaml line reflects top.source_file (filename).
        assert "// Source:    minimal_soc.yaml" in sv_text
        # Design + sim localparams present.
        assert "SYS_ADDR_W" in sv_text and "SYS_DATA_W" in sv_text
        assert "FAST_LOAD" in sv_text and "VCD_ENABLE" in sv_text

        assert "AUTO-GENERATED SoC Configuration" in vh_text
        assert "`define SYS_ADDR_W 32" in vh_text
        assert "`define FAST_LOAD 0" in vh_text

        assert_matches_snapshot(
            sv_text, SNAPSHOTS / "minimal_soc" / "minimal_soc_soc_config_pkg.sv",
            request,
        )
        assert_matches_snapshot(
            vh_text, SNAPSHOTS / "minimal_soc" / "minimal_soc_soc_config.vh",
            request,
        )
