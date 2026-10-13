# Copyright 2026, SoC Labs (www.soclabs.org)
"""Tooling-tier tests for ``soc_model/backends/ahb.py`` (perl path + helpers).

Per TEST_PLAN.md §5.13. This whole module is the ``tooling`` tier
(``pytest -m tooling``); it must SKIP — never FAIL — when external
prerequisites (perl / ``BuildBusMatrix.pl`` under ``ARM_IP_LIBRARY_PATH``)
are absent.

CRITICAL SAFETY: ``ARM_IP_LIBRARY_PATH`` may point at the shared, read-only
vendor IP tree (``$ARM_IP_LIBRARY_PATH``). These tests NEVER write under
it and NEVER export it. The only test that would invoke the *real*
``BuildBusMatrix.pl`` (AT-08) is deliberately SKIPPED, because the backend
runs perl with ``cwd`` set INSIDE that IP tree (``ahb.py:299``) and could
create scratch files there. Every other perl-path assertion uses a
``monkeypatch``-ed ``subprocess.run`` or a bogus ``tmp_path`` IP root, so no
real perl ever touches the vendor tree.

The pure AHB helper coverage (AH-01..AH-11) needs no tooling and asserts on
the deterministic non-perl artifacts (XML / address-maps / SV / config-pkg),
which the backend emits regardless of whether perl is available
(``ahb.py:268`` warns-and-continues).
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from soc_model.backends.ahb import (
    SoCAhbBackend,
    _build_apb_slot_list,
    _build_apb_target_context,
    _mem_hi,
    _to_hex,
    _transform_name,
)
from soc_model.model import InterconnectTarget

from tests.helpers import assert_matches_snapshot, build_module_from_yaml

pytestmark = pytest.mark.tooling


# ---------------------------------------------------------------------------
# Prerequisite detection (skip-not-fail)
# ---------------------------------------------------------------------------

def _build_script_path():
    """Resolve BuildBusMatrix.pl under ARM_IP_LIBRARY_PATH, or None.

    Mirrors the path the backend computes in ``_run_build_bus_matrix``
    (ahb.py:272-273). Returns the Path only if it actually exists; this is a
    read-only existence probe — nothing is written.
    """
    arm_ip = os.environ.get("ARM_IP_LIBRARY_PATH")
    if not arm_ip:
        return None
    script = (Path(arm_ip) / "latest" / "Corstone-101" / "logical"
              / "cmsdk_ahb_busmatrix" / "bin" / "BuildBusMatrix.pl")
    return script if script.exists() else None


_HAVE_PERL = shutil.which("perl") is not None
_BUILD_SCRIPT = _build_script_path()
_HAVE_PERL_TOOLCHAIN = _HAVE_PERL and _BUILD_SCRIPT is not None


# ===========================================================================
# AH-01..AH-02  pure address helpers
# ===========================================================================

class TestHexHelpers:
    """ahb._to_hex / _mem_hi / _transform_name (pure)."""

    def test_to_hex_int_8digit_lowercase(self):  # AH-01
        assert _to_hex(0x1000) == "00001000"

    def test_to_hex_hex_str_normalised(self):  # AH-01
        assert _to_hex(" 0x1000 ") == "00001000"

    def test_to_hex_decimal_str_is_base16_footgun(self):  # AH-01
        # FOOT-GUN pinned: a decimal-looking string is parsed base-16.
        assert _to_hex("20") == "00000020"

    def test_mem_hi_inclusive_end(self):  # AH-02
        assert _mem_hi("00001000", "00001000") == "00001fff"

    def test_transform_name_lower_to_underscore_upper(self):  # AH-02
        assert _transform_name("cpu_0") == "_CPU_0"


# ===========================================================================
# AH-03  _build_apb_slot_list  (gaps for unlisted slots)
# ===========================================================================

class TestApbSlotList:
    def test_unlisted_slots_become_gaps(self):  # AH-03
        slots = _build_apb_slot_list({"slaves": [{"slot": 2, "name": "tmr"}]})
        assert len(slots) == 16
        # slot 0 / 1 are auto-gaps
        assert slots[0]["is_gap"] is True
        assert slots[0]["enabled"] is False
        assert slots[0]["param_name"] == "GAP_0"
        assert slots[1]["is_gap"] is True
        # slot 2 is the declared one, enabled by default
        assert slots[2]["name"] == "tmr"
        assert slots[2]["is_gap"] is False
        assert slots[2]["enabled"] is True
        assert slots[2]["param_name"] == "TMR"

    def test_explicit_disable_honoured(self):  # AH-03
        slots = _build_apb_slot_list(
            {"slaves": [{"slot": 0, "name": "tmr", "enabled": False}]}
        )
        assert slots[0]["enabled"] is False
        assert slots[0]["is_gap"] is False  # declared, just disabled


# ===========================================================================
# AH-04  _build_apb_target_context  (has_mux / decode / n_slots / or {})
# ===========================================================================

class TestApbTargetContext:
    def test_with_slaves_has_mux(self):  # AH-04
        tgt = InterconnectTarget(
            name="periph", base=0x40000000, protocol="apb",
            apb_config={"slaves": [{"slot": 0, "name": "tmr"}]},
        )
        ctx = _build_apb_target_context(tgt)
        assert ctx["has_mux"] is True
        assert ctx["decode_msb"] == 15
        assert ctx["decode_lsb"] == 12
        assert ctx["n_slots"] == 16
        assert len(ctx["slots"]) == 16

    def test_without_slaves_no_mux(self):  # AH-04
        tgt = InterconnectTarget(name="periph", base=0x40000000,
                                 protocol="apb", apb_config={})
        ctx = _build_apb_target_context(tgt)
        assert ctx["has_mux"] is False
        assert ctx["decode_msb"] == 11
        assert ctx["decode_lsb"] == 0
        assert ctx["slots"] == []

    def test_apb_config_none_or_default(self):  # AH-04
        # apb_config None -> `or {}` branch; must not crash.
        tgt = InterconnectTarget(name="periph", base=0x40000000,
                                 protocol="apb", apb_config=None)
        ctx = _build_apb_target_context(tgt)
        assert ctx["has_mux"] is False
        assert ctx["region_base"] == "0x40000000"


# ===========================================================================
# AH-05  _build_initiator_data  (no visibility -> single 'none' region;
#                                unknown target skipped)
# ===========================================================================

class TestInitiatorData:
    def _backend(self, fixtures_dir):
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        return SoCAhbBackend(top, arm_ip_library_path=None), top

    def test_no_visibility_single_none_region(self, fixtures_dir):  # AH-05
        be, top = self._backend(fixtures_dir)
        ic = top.interconnects[0]
        data = be._build_initiator_data(ic)
        assert "cpu_m" in data
        regions = data["cpu_m"]["address_regions"]
        assert len(regions) == 1
        r = regions[0]
        assert r["interface"] == "sram_0"
        assert r["remapping"] == "none"
        # base 0x0 size 0x1000 -> inclusive hi 0x00000fff
        assert r["mem_lo"] == "00000000"
        assert r["mem_hi"] == "00000fff"

    def test_unknown_target_skipped(self, fixtures_dir):  # AH-05
        be, top = self._backend(fixtures_dir)
        ic = top.interconnects[0]
        # Inject an initiator-target referencing a non-existent target.
        from soc_model.model import InterconnectInitiatorTarget
        ic.initiators[0].targets.append(
            InterconnectInitiatorTarget(name="ghost_target")
        )
        data = be._build_initiator_data(ic)
        names = [r["interface"] for r in data["cpu_m"]["address_regions"]]
        assert "ghost_target" not in names  # continue-skipped


# ===========================================================================
# AH-06  _generate_remap_configurations
# ===========================================================================

class TestRemapConfigurations:
    def test_no_remap_bits_single_empty(self, fixtures_dir):  # AH-06
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        be = SoCAhbBackend(top)
        data = be._build_initiator_data(top.interconnects[0])
        assert be._generate_remap_configurations(data) == [{}]

    def test_two_bits_gives_four_configs(self, fixtures_dir):  # AH-06
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        be = SoCAhbBackend(top)
        data = {
            "cpu_m": {
                "remap_regions": [
                    {"bit": 0, "interface": "a", "mem_lo": "0", "mem_hi": "1"},
                    {"bit": 1, "interface": "b", "mem_lo": "2", "mem_hi": "3"},
                ],
            }
        }
        configs = be._generate_remap_configurations(data)
        assert len(configs) == 4
        assert {0: False, 1: False} in configs
        assert {0: True, 1: True} in configs


# ===========================================================================
# AH-07  _compute_sv_columns default
# ===========================================================================

class TestSvColumns:
    def test_empty_returns_default_tuple(self, fixtures_dir):  # AH-07
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        be = SoCAhbBackend(top)
        assert be._compute_sv_columns([], []) == (24, 24, 26)


# ===========================================================================
# AT-01  perl skipped when ARM_IP_LIBRARY_PATH unset (no subprocess, no log)
# ===========================================================================

class TestPerlSkip:
    def test_unset_env_no_subprocess(self, fixtures_dir, tmp_path, monkeypatch):  # AT-01
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        be = SoCAhbBackend(top, arm_ip_library_path=None)
        monkeypatch.delenv("ARM_IP_LIBRARY_PATH", raising=False)

        called = {"run": False}

        def _boom(*a, **k):
            called["run"] = True
            raise AssertionError("subprocess.run must NOT be called when "
                                 "ARM_IP_LIBRARY_PATH is unset")

        monkeypatch.setattr(subprocess, "run", _boom)
        # Should warn-and-skip, not raise; no perl invocation.
        be._run_build_bus_matrix(
            top.interconnects[0], tmp_path, tmp_path, tmp_path, tmp_path
        )
        assert called["run"] is False

    def test_bogus_ip_path_script_missing(self, fixtures_dir, tmp_path,
                                          monkeypatch):  # AT-02
        # arm_ip_library_path set to a harmless tmp dir (NOT /research): the
        # BuildBusMatrix.pl resolves under it and does NOT exist -> warn+return.
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        be = SoCAhbBackend(top, arm_ip_library_path=str(tmp_path))

        called = {"run": False}
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: called.__setitem__("run", True),
        )
        be._run_build_bus_matrix(
            top.interconnects[0], tmp_path, tmp_path, tmp_path, tmp_path
        )
        # script missing -> returns before any subprocess call
        assert called["run"] is False


# ===========================================================================
# AT-06 / AT-07  perl-invocation error paths caught (monkeypatched perl)
# ===========================================================================

class TestPerlErrorPaths:
    """Drive the try/except in _run_build_bus_matrix with a FAKE script.

    A real ``BuildBusMatrix.pl`` is faked inside ``tmp_path`` (never the IP
    tree) so ``build_script.exists()`` is True and execution reaches the
    ``subprocess.run`` call — which is monkeypatched to raise. No real perl
    runs; no vendor file is touched.
    """

    def _backend_with_fake_script(self, fixtures_dir, tmp_path):
        ip_root = tmp_path / "ip"
        script = (ip_root / "latest" / "Corstone-101" / "logical"
                  / "cmsdk_ahb_busmatrix" / "bin" / "BuildBusMatrix.pl")
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("# fake perl script\n")
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        be = SoCAhbBackend(top, arm_ip_library_path=str(ip_root))
        return be, top, ip_root

    def test_filenotfound_caught(self, fixtures_dir, tmp_path, monkeypatch):  # AT-06
        be, top, _ = self._backend_with_fake_script(fixtures_dir, tmp_path)
        logs = tmp_path / "logs"
        logs.mkdir()

        def _raise_fnf(*a, **k):
            raise FileNotFoundError("perl: not found")

        monkeypatch.setattr(subprocess, "run", _raise_fnf)
        # Must be caught (ahb.py:320) -> no exception propagates.
        be._run_build_bus_matrix(
            top.interconnects[0], tmp_path, tmp_path, tmp_path, logs
        )

    def test_timeout_caught(self, fixtures_dir, tmp_path, monkeypatch):  # AT-07
        be, top, _ = self._backend_with_fake_script(fixtures_dir, tmp_path)
        logs = tmp_path / "logs"
        logs.mkdir()

        def _raise_timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd="perl", timeout=120)

        monkeypatch.setattr(subprocess, "run", _raise_timeout)
        # Must be caught (ahb.py:322) -> no exception propagates.
        be._run_build_bus_matrix(
            top.interconnects[0], tmp_path, tmp_path, tmp_path, logs
        )


# ===========================================================================
# AT-03  generate_all still produces non-perl artifacts when perl skipped
# AT-04  XML byte-match golden
# AT-05  address-maps text byte-match golden
# AH-08  SV wrapper + config_pkg byte-match golden
# AH-09  generate_all returns [(ic.name, rtl_dir)]; gen:False skipped
# ===========================================================================

class TestNonPerlArtifacts:
    """With perl skipped (arm_ip_library_path=None) the XML / address-maps /
    SV / config-pkg artifacts are all still produced and deterministic."""

    def _run(self, fixtures_dir, tmp_path, fixture, monkeypatch):
        # Hard-guarantee no real perl is invoked even if the env var is set.
        monkeypatch.delenv("ARM_IP_LIBRARY_PATH", raising=False)

        def _no_perl(*a, **k):
            raise AssertionError("perl must not run in the non-perl artifact "
                                 "tests")

        monkeypatch.setattr(subprocess, "run", _no_perl)
        top = build_module_from_yaml(fixtures_dir, f"{fixture}.yaml")
        be = SoCAhbBackend(top, arm_ip_library_path=None)
        generated = be.generate_all(tmp_path)
        return top, generated

    def test_generate_all_emits_all_artifacts(self, fixtures_dir, tmp_path,
                                              monkeypatch):  # AT-03 / AH-09
        top, generated = self._run(fixtures_dir, tmp_path, "minimal_soc",
                                   monkeypatch)
        ic = top.interconnects[0]
        # AH-09: returns [(ic.name, rtl_dir)]
        assert generated == [(ic.name, tmp_path / "rtl" / ic.name)]

        prefix = "minimal"  # ic name minimal_ahb_interconnect -> minimal
        xml = tmp_path / "interconnect" / ic.name / "xml" / f"{ic.name}.xml"
        maps = (tmp_path / "interconnect" / ic.name / "address_maps"
                / f"{ic.name}_address_maps.txt")
        sv = tmp_path / "rtl" / ic.name / f"{prefix}_interconnect.sv"
        flist = tmp_path / "flist" / f"{prefix}_interconnect.flist"
        pkg = tmp_path / "rtl" / ic.name / f"{prefix}_config_pkg.sv"

        for p in (xml, maps, sv, flist, pkg):
            assert p.exists(), f"missing artifact {p}"
            assert p.read_text().strip(), f"empty artifact {p}"

        # Structural teeth (not just existence).
        assert "<cfgfile>" in xml.read_text()
        assert "<bus_matrix_name>" in xml.read_text()
        assert f"{ic.name} — Address Maps" in maps.read_text()
        assert "module minimal_interconnect" in sv.read_text()
        assert "package minimal_config_pkg" in pkg.read_text()

    def test_gen_false_interconnect_skipped(self, fixtures_dir, tmp_path,
                                            monkeypatch):  # AH-09
        monkeypatch.delenv("ARM_IP_LIBRARY_PATH", raising=False)
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        top.interconnects[0].gen = False
        be = SoCAhbBackend(top, arm_ip_library_path=None)
        generated = be.generate_all(tmp_path)
        assert generated == []
        assert not (tmp_path / "rtl").exists()

    def test_xml_byte_match(self, fixtures_dir, tmp_path, snapshots_dir,
                            request, monkeypatch):  # AT-04
        top, _ = self._run(fixtures_dir, tmp_path, "minimal_soc", monkeypatch)
        ic = top.interconnects[0]
        xml = (tmp_path / "interconnect" / ic.name / "xml"
               / f"{ic.name}.xml").read_text()
        # The XML carries NO timestamp -> byte-stable golden.
        assert_matches_snapshot(
            xml, snapshots_dir / "minimal_soc" / f"{ic.name}.xml", request
        )

    def test_address_maps_byte_match(self, fixtures_dir, tmp_path,
                                     snapshots_dir, request,
                                     monkeypatch):  # AT-05
        top, _ = self._run(fixtures_dir, tmp_path, "minimal_soc", monkeypatch)
        ic = top.interconnects[0]
        maps = (tmp_path / "interconnect" / ic.name / "address_maps"
                / f"{ic.name}_address_maps.txt").read_text()
        assert_matches_snapshot(
            maps, snapshots_dir / "minimal_soc" / f"{ic.name}_address_maps.txt",
            request,
        )

    def test_sv_and_pkg_byte_match(self, fixtures_dir, tmp_path,
                                   snapshots_dir, request,
                                   monkeypatch):  # AH-08
        top, _ = self._run(fixtures_dir, tmp_path, "minimal_soc", monkeypatch)
        ic = top.interconnects[0]
        sv = (tmp_path / "rtl" / ic.name
              / "minimal_interconnect.sv").read_text()
        pkg = (tmp_path / "rtl" / ic.name
               / "minimal_config_pkg.sv").read_text()
        assert "module minimal_interconnect" in sv  # structural teeth
        assert "package minimal_config_pkg" in pkg
        assert_matches_snapshot(
            sv, snapshots_dir / "minimal_soc" / "minimal_interconnect.sv",
            request,
        )
        assert_matches_snapshot(
            pkg, snapshots_dir / "minimal_soc" / "minimal_config_pkg.sv",
            request,
        )

    def test_apb_bridge_xml_byte_match(self, fixtures_dir, tmp_path,
                                       snapshots_dir, request,
                                       monkeypatch):  # AT-04 (apb variant)
        top, _ = self._run(fixtures_dir, tmp_path, "apb_bridge_soc",
                           monkeypatch)
        ic = top.interconnects[0]
        xml = (tmp_path / "interconnect" / ic.name / "xml"
               / f"{ic.name}.xml").read_text()
        assert "_APB_PERIPH_0" in xml  # APB target present in XML
        assert_matches_snapshot(
            xml, snapshots_dir / "apb_bridge_soc" / f"{ic.name}.xml", request
        )


# ===========================================================================
# AH-11  Environment None -> XML+maps emitted, SV absent (returns before SV)
# ===========================================================================

class TestEnvironmentNone:
    def test_jinja_none_emits_xml_not_sv(self, fixtures_dir, tmp_path,
                                         monkeypatch):  # AH-11
        import soc_model.backends.ahb as ahb_mod
        monkeypatch.delenv("ARM_IP_LIBRARY_PATH", raising=False)
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("perl must not run")))
        monkeypatch.setattr(ahb_mod, "Environment", None)
        top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
        be = SoCAhbBackend(top, arm_ip_library_path=None)
        be.generate_all(tmp_path)
        ic = top.interconnects[0]
        xml = tmp_path / "interconnect" / ic.name / "xml" / f"{ic.name}.xml"
        sv = tmp_path / "rtl" / ic.name / "minimal_interconnect.sv"
        assert xml.exists()           # produced before the Environment check
        assert not sv.exists()        # SV generation skipped


# ===========================================================================
# AT-08  real perl + valid IP path -> SKIPPED (never write to the IP tree)
# ===========================================================================

class TestRealPerl:
    @pytest.mark.skip(
        reason="Refusing to invoke the real BuildBusMatrix.pl: the backend "
               "sets cwd INSIDE the read-only vendor IP tree "
               "($ARM_IP_LIBRARY_PATH), which these tests must never "
               "write to. Real bus-matrix RTL emission is validated on a "
               "dedicated EDA host with a writable IP mirror, not here."
    )
    def test_real_bus_matrix_emission(self):  # AT-08
        # Intentionally never executed. Documented as a skip so `pytest -m
        # tooling -rs` surfaces the reason and the coverage gap is explicit.
        assert _HAVE_PERL_TOOLCHAIN  # pragma: no cover
