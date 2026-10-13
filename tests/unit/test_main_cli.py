# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for the ``soc_model.__main__`` CLI entry point.

``soc_model/__main__.py`` is a flat procedural ``main()`` that:
  1. parses argparse args,
  2. folds ``--config-override KEY=VALUE`` into a dict (int via ``int(v, 0)``
     with a str fallback; lines without '=' silently dropped),
  3. parses + builds the YAML via ``SoCParser`` -> ``SoCBuilder``,
  4. validates (unless ``--no-validate``; with ``--validate-only`` it exits
     1-on-error / 0-on-clean and stops), and
  5. otherwise runs ~16 backends, writing RTL / flists / reports / docs into the
     build directory.

Strategy: drive ``main()`` IN-PROCESS (so ``--cov`` measures it) by setting
``sys.argv`` via monkeypatch and catching the ``SystemExit`` the CLI raises.
Every backend runs for real against a hermetic fixture
(``tests/fixtures/minimal_soc.yaml`` and friends), with
``--arm-ip-library-path`` left at its default ``None`` so the perl /
BuildBusMatrix path is skipped (the AHB backend's own generator still emits, so
the run is fully self-contained and never touches the vendor IP tree).

Each test asserts real behaviour — exit codes, written-file existence, and the
content of generated artifacts (e.g. a ``--config-override`` landing in the
emitted config package) — never no-assert coverage padding.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# In-process runner: set sys.argv, call main(), capture the SystemExit code.
# ---------------------------------------------------------------------------

def _run_main(monkeypatch, argv):
    """Invoke ``main()`` with ``argv`` (sans program name) set on ``sys.argv``.

    Returns the integer exit code: ``main()`` only ever leaves via
    ``sys.exit(...)`` (build error, ``--validate-only``) or by falling off the
    end (implicit clean exit 0). ``SystemExit(None)`` / a normal return both map
    to 0; argparse's ``SystemExit(2)`` (usage error) propagates as 2.

    ``main`` is imported lazily here (not at module top level) so the
    ``soc_model.__main__`` module body is tracked *after* the coverage source
    filter is installed — otherwise a ``--cov=soc_model/__main__.py`` path-form
    run sees the package's ``__main__`` as already-imported and reports no data.
    """
    from soc_model.__main__ import main
    monkeypatch.setattr(sys, "argv", ["soc_model", *[str(a) for a in argv]])
    try:
        main()
    except SystemExit as exc:
        code = exc.code
        return 0 if code is None else int(code)
    return 0


# A representative slice of the artifacts a clean generate run must emit. These
# are the load-bearing outputs (top-level SV + the three flists) that downstream
# build flows consume.
def _expected_artifacts(build_dir: Path, top_name: str):
    return [
        build_dir / "rtl" / f"{top_name}.sv",
        build_dir / "flist" / f"{top_name}_toplevel.flist",
        build_dir / "flist" / f"{top_name}_soc_config_pkg.flist",
        build_dir / "reports" / f"{top_name}_connectivity.html",
        build_dir / "reports" / f"{top_name}_memory_map.txt",
    ]


# ===========================================================================
# Config-override parsing (lines 64-71)
# ===========================================================================

class TestConfigOverrideParsing:
    """The KEY=VALUE fold near the top of main(): int(v, 0) -> int, ValueError
    -> str, and a token with no '=' is dropped. Driven end-to-end so the parsed
    values are observed in the generated soc_config artifacts."""

    def test_overrides_int_str_and_malformed(self, fixtures_dir, tmp_path, monkeypatch):
        build_dir = tmp_path / "build"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "minimal_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
            # int via int(v, 0): decimal and 0x-prefixed hex both parse to int.
            "--config-override", "FAST_LOAD=1",
            "--config-override", "SYS_DATA_W=0x40",
            # non-numeric -> ValueError -> kept verbatim as a str.
            "--config-override", "NANOSOC_PARTNUMBER=mychip",
            # no '=' -> silently ignored (must NOT crash or appear anywhere).
            "--config-override", "MALFORMED_NO_EQUALS",
        ])
        assert rc == 0

        vh = (build_dir / "rtl" / "minimal_soc_soc_config.vh").read_text()
        # int branch: 0x40 parsed via int(v, 0) -> decimal 64 in the emitted vh.
        assert "`define SYS_DATA_W 64" in vh
        # int branch: plain decimal override survives.
        assert "`define FAST_LOAD 1" in vh
        # str fallback: non-numeric value emitted verbatim.
        assert "`define NANOSOC_PARTNUMBER mychip" in vh
        # malformed (no '=') token was dropped, never turned into a define.
        assert "MALFORMED_NO_EQUALS" not in vh


# ===========================================================================
# --validate-only (lines 104-116) and --no-validate (line 105)
# ===========================================================================

class TestValidateModes:

    def test_validate_only_clean_exits_zero(self, fixtures_dir, tmp_path, monkeypatch):
        """A fixture with zero validation errors -> --validate-only exits 0 and
        generates NO artifacts (the run stops at sys.exit before any backend)."""
        build_dir = tmp_path / "build"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "minimal_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
            "--validate-only",
        ])
        assert rc == 0
        # validate-only short-circuits before the build-dir is created.
        assert not build_dir.exists()

    def test_validate_only_with_errors_exits_one(self, tmp_path, monkeypatch):
        """A top module declaring an undriven wire output trips the validator's
        driver-coverage (C1) error -> --validate-only exits 1.

        The bad SoC is written into tmp_path (NOT the shared fixtures dir) so it
        is fully hermetic and self-contained."""
        bad_yaml = tmp_path / "bad_soc.yaml"
        bad_yaml.write_text(
            "# Copyright 2026, SoC Labs (www.soclabs.org)\n"
            "module:\n"
            "  name: bad_soc\n"
            "  gen: True\n"
            "  desc: top with an undriven wire output -> validator C1 error\n"
            "  interfaces:\n"
            "    - { name: dangling_out, type: wire, direction: out,"
            " params: { WIDTH: 1 }, desc: \"undriven output\" }\n"
        )
        rc = _run_main(monkeypatch, [
            bad_yaml,
            "--build-dir", tmp_path / "build",
            "--lib-dir", tmp_path,
            "--validate-only",
        ])
        assert rc == 1

    def test_no_validate_skips_validation_and_generates(
            self, fixtures_dir, tmp_path, monkeypatch):
        """--no-validate skips the validator block entirely yet still runs the
        full generate path (so the top-level SV is emitted)."""
        build_dir = tmp_path / "build"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "minimal_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
            "--no-validate",
        ])
        assert rc == 0
        assert (build_dir / "rtl" / "minimal_soc.sv").exists()


# ===========================================================================
# Build error path (lines 85-89)
# ===========================================================================

class TestBuildError:

    def test_unbuildable_yaml_exits_one(self, tmp_path, monkeypatch, capsys):
        """build_system() raising (here: a non-existent top YAML) is caught and
        turned into 'Error building model: ...' on stderr + sys.exit(1)."""
        rc = _run_main(monkeypatch, [
            tmp_path / "does_not_exist.yaml",
            "--build-dir", tmp_path / "build",
            "--lib-dir", tmp_path,
        ])
        assert rc == 1
        assert "Error building model" in capsys.readouterr().err


# ===========================================================================
# Full generate run + build-dir / lib-dir handling
# ===========================================================================

class TestFullGenerate:

    def test_full_generate_writes_artifacts(self, fixtures_dir, tmp_path, monkeypatch):
        """A complete (non-validate-only) run over minimal_soc into an explicit
        --build-dir exits 0 and writes the expected RTL / flist / report set.
        arm_ip_library_path stays None, so nothing touches the vendor IP tree."""
        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "minimal_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
        ])
        assert rc == 0
        for artifact in _expected_artifacts(build_dir, "minimal_soc"):
            assert artifact.exists(), f"missing expected artifact {artifact}"
        # the top-level SV actually declares the module (real content, not stub).
        assert "module minimal_soc" in (build_dir / "rtl" / "minimal_soc.sv").read_text()

    def test_full_generate_firmware_fixture(self, fixtures_dir, tmp_path, monkeypatch):
        """The firmware fixture drives the firmware + build-info backends in the
        same run (extra branches over minimal_soc). Exits 0 and writes the
        firmware config directory."""
        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "firmware_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
        ])
        assert rc == 0
        assert (build_dir / "rtl" / "firmware_soc.sv").exists()
        assert (build_dir / "firmware").is_dir()

    def test_repeated_lib_dir_accumulates(self, fixtures_dir, tmp_path, monkeypatch):
        """--lib-dir is action='append': passing it twice (the fixtures dir and
        its regions/ subdir) accumulates both into the parser lib list and the
        run still resolves + generates cleanly."""
        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "minimal_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
            "--lib-dir", fixtures_dir / "regions",
        ])
        assert rc == 0
        assert (build_dir / "rtl" / "minimal_soc.sv").exists()

    def test_register_map_fixture_drives_rdl_loop(
            self, fixtures_dir, tmp_path, monkeypatch):
        """The apb_bridge fixture attaches a register map to an APB peripheral,
        so the RDL backend reports 'Generated N register map(s)' — exercising the
        register-map result loop (vs the 'No register maps' else minimal_soc
        takes). The .rdl artifact lands."""
        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "apb_bridge_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
        ])
        assert rc == 0
        assert (build_dir / "rtl" / "apb_bridge_soc.sv").exists()
        # the register-map backend emitted RDL for tiny_regs (loop body ran).
        assert (build_dir / "rdl").is_dir()
        assert any((build_dir / "rdl").glob("*.rdl"))

    def test_subsystem_fixture_drives_subsystem_loop(
            self, fixtures_dir, tmp_path, monkeypatch):
        """The nested-subsystem fixture has a gen:True subsystem, so the
        subsystem backend reports 'Generated N subsystem(s)' — exercising the
        subsystem result loop (vs the 'No subsystems' else). The subsystem SV
        lands."""
        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "nested_subsystem_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
        ])
        assert rc == 0
        assert (build_dir / "rtl" / "nested_subsystem_soc.sv").exists()
        # the subsystem backend emitted the tiny_cpu_ss wrapper (loop body ran).
        assert (build_dir / "rtl" / "tiny_cpu_ss.sv").exists()

    def test_soc_without_interconnect_takes_no_ic_branches(
            self, fixtures_dir, tmp_path, monkeypatch):
        """A top with a region instance but NO gen:True interconnect drives the
        'No interconnects with gen: True found' else arms in both the AHB and the
        discovery sections, while the rest of the generate run still completes
        and emits the top-level SV. The bad-for-coverage YAML is hand-written
        into tmp_path (regions still resolve from the shared fixtures lib)."""
        no_ic = tmp_path / "no_ic_soc.yaml"
        no_ic.write_text(
            "# Copyright 2026, SoC Labs (www.soclabs.org)\n"
            "module:\n"
            "  name: no_ic_soc\n"
            "  gen: True\n"
            "  desc: top with a region but no gen:True interconnect\n"
            "  params:\n"
            "    SYS_ADDR_W: { type: int, default: 32 }\n"
            "    SYS_DATA_W: { type: int, default: 32 }\n"
            "  clocks:\n"
            "    - { name: sys_hclk, source: external }\n"
            "  resets:\n"
            "    - { name: sys_hresetn, active: low, source: external }\n"
            "  interfaces:\n"
            "    - { name: sys_hclk, type: wire, direction: in, params: { WIDTH: 1 } }\n"
            "    - { name: sys_hresetn, type: wire, direction: in, params: { WIDTH: 1 } }\n"
            "  instances:\n"
            "    - instance_name: u_sram_0\n"
            "      module: tiny_region\n"
            "      params: { SYS_ADDR_W: $SYS_ADDR_W, SYS_DATA_W: $SYS_DATA_W, RAM_ADDR_W: 12 }\n"
            "      connections:\n"
            "        - { port: HCLK, conn: sys_hclk }\n"
            "        - { port: HRESETn, conn: sys_hresetn }\n"
        )
        build_dir = tmp_path / "out"
        # --no-validate: the lone region's clock/reset ports are unreferenced at
        # top level, which the validator would flag; we only care about the
        # no-interconnect generate branches here.
        rc = _run_main(monkeypatch, [
            no_ic,
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
            "--no-validate",
        ])
        assert rc == 0
        assert (build_dir / "rtl" / "no_ic_soc.sv").exists()
        # no gen:True interconnect -> no interconnect RTL directory was emitted.
        assert not (build_dir / "interconnect").exists()

    def test_default_build_dir_is_yaml_sibling(self, fixtures_dir, tmp_path, monkeypatch):
        """With no --build-dir, the default is <yaml-parent>/build_soc. The
        fixture is copied into tmp_path first so the default build_soc/ lands in
        tmp_path (never polluting the shared fixtures dir)."""
        local_yaml = tmp_path / "minimal_soc.yaml"
        shutil.copy(fixtures_dir / "minimal_soc.yaml", local_yaml)
        rc = _run_main(monkeypatch, [
            local_yaml,
            "--lib-dir", fixtures_dir,
        ])
        assert rc == 0
        default_build = tmp_path / "build_soc"
        assert default_build.is_dir()
        assert (default_build / "rtl" / "minimal_soc.sv").exists()


# ===========================================================================
# --system-yaml (lines 265-303) and --lint (lines 316-335)
# ===========================================================================

class TestSystemYaml:

    def test_system_yaml_drives_system_and_chip_wrappers(
            self, fixtures_dir, tmp_path, monkeypatch):
        """--system-yaml parses a second YAML and (on success) runs the system
        wrapper backend AND the chip-wrapper backend. Pointing it at a buildable
        fixture exercises both success branches; the system/chip flists land."""
        build_dir = tmp_path / "out"
        # The system YAML is minimal_soc renamed: with the SAME name the system
        # wrapper is written over the core's minimal_soc.sv, and the discovery
        # size guard (rightly) cannot find the core's SRAM in the render.
        system = tmp_path / "minimal_soc_system.yaml"
        system.write_text((fixtures_dir / "minimal_soc.yaml").read_text().replace(
            "  name: minimal_soc\n", "  name: minimal_soc_system\n", 1))
        rc = _run_main(monkeypatch, [
            fixtures_dir / "minimal_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
            "--system-yaml", system,
        ])
        assert rc == 0
        # system + chip wrapper backends both emitted their flists.
        assert (build_dir / "flist" / "minimal_soc_system_system.flist").exists()
        assert (build_dir / "flist" / "minimal_soc_chip_chip.flist").exists()

    def test_system_yaml_build_failure_skips_chip(
            self, fixtures_dir, tmp_path, monkeypatch, capsys):
        """If the system YAML fails to build, the except arm sets
        system_module=None: an error is printed and the chip-wrapper block is
        skipped, but the CLI still completes cleanly (the core SoC was fine)."""
        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "minimal_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
            "--system-yaml", tmp_path / "no_such_system.yaml",
        ])
        assert rc == 0
        assert "Error building system model" in capsys.readouterr().err
        # chip wrapper skipped -> no chip flist; the core run still emitted SV.
        assert not (build_dir / "flist" / "minimal_soc_chip.flist").exists()
        assert (build_dir / "rtl" / "minimal_soc.sv").exists()


class TestChipBoundary:

    def test_chip_boundary_absent_is_noop(self, fixtures_dir, tmp_path, monkeypatch):
        """No --chip-boundary => the chip-functional wrapper block never runs;
        default behaviour is unchanged and no <chip>.flist is emitted."""
        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "minimal_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
        ])
        assert rc == 0
        assert not (build_dir / "flist" / "multicore_chip.flist").exists()
        assert (build_dir / "rtl" / "minimal_soc.sv").exists()

    def test_chip_boundary_given_emits_wrapper(self, fixtures_dir, tmp_path, monkeypatch):
        """--chip-boundary <name> resolves the spec (lib-dir/chip_boundary/) and
        emits <chip_module>.v + .flist wrapping the inner SoC."""
        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "minimal_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
            "--chip-boundary", "multicore_chip",
        ])
        assert rc == 0
        v = build_dir / "rtl" / "multicore_chip.v"
        assert v.exists()
        assert "multicore_soc u_soc" in v.read_text()
        assert (build_dir / "flist" / "multicore_chip.flist").exists()

    def test_chip_boundary_missing_spec_skips_cleanly(
            self, fixtures_dir, tmp_path, monkeypatch, capsys):
        """An unresolvable --chip-boundary name prints a skip and exits clean."""
        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "minimal_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
            "--chip-boundary", "no_such_boundary",
        ])
        assert rc == 0
        assert not (build_dir / "rtl" / "no_such_boundary.v").exists()
        assert (build_dir / "rtl" / "minimal_soc.sv").exists()


class TestFpgaWrapper:

    def test_fpga_wrapper_absent_is_noop(self, fixtures_dir, tmp_path, monkeypatch):
        """No --fpga-wrapper => the FPGA-wrapper block never runs; default
        behaviour unchanged and no FPGA wrapper .v / .flist is emitted."""
        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "minimal_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
        ])
        assert rc == 0
        assert not (build_dir / "rtl"
                    / "nanosoc_multicore_vivado_wrapper.v").exists()
        assert (build_dir / "rtl" / "minimal_soc.sv").exists()

    def test_fpga_wrapper_named_emits_wrapper(
            self, fixtures_dir, tmp_path, monkeypatch):
        """--fpga-wrapper <name> resolves the boundary + matching fpga_profile
        and emits the synthesizable Vivado wrapper (instantiates the SoC top,
        no pad cells)."""
        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "minimal_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
            "--fpga-wrapper", "nanosoc_multicore",
        ])
        assert rc == 0
        v = build_dir / "rtl" / "nanosoc_multicore_vivado_wrapper.v"
        assert v.exists()
        text = v.read_text()
        assert "nanosoc_multicore_soc" in text and "u_soc (" in text
        assert "uPAD" not in text and "inout" not in text
        assert (build_dir / "flist"
                / "nanosoc_multicore_vivado_wrapper.flist").exists()

    def test_fpga_wrapper_bare_flag_reuses_chip_boundary(
            self, fixtures_dir, tmp_path, monkeypatch):
        """Bare --fpga-wrapper reuses the --chip-boundary name (emits BOTH the
        chip-functional wrapper and the FPGA wrapper)."""
        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "minimal_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
            "--chip-boundary", "nanosoc_multicore",
            "--fpga-wrapper",
        ])
        assert rc == 0
        assert (build_dir / "rtl" / "nanosoc_multicore_chip.v").exists()
        assert (build_dir / "rtl"
                / "nanosoc_multicore_vivado_wrapper.v").exists()

    def test_fpga_wrapper_missing_name_skips_cleanly(
            self, fixtures_dir, tmp_path, monkeypatch):
        """Bare --fpga-wrapper with no --chip-boundary prints a skip, exits 0."""
        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "minimal_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
            "--fpga-wrapper",
        ])
        assert rc == 0
        assert not (build_dir / "rtl"
                    / "nanosoc_multicore_vivado_wrapper.v").exists()
        assert (build_dir / "rtl" / "minimal_soc.sv").exists()


class TestPadRingMixed:

    def test_mixed_pad_ring_via_pin_count_name(
            self, fixtures_dir, tmp_path, monkeypatch):
        """--pad-tech generic --pin-count multicore selects the bundled mixed
        pin-map (generic_multicore) and emits the per-signal pad ring wrapping
        nanosoc_multicore_chip — no --system-yaml needed (the pin-map names its
        own chip top)."""
        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "minimal_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
            "--pad-tech", "generic", "--pin-count", "multicore",
        ])
        assert rc == 0
        v = build_dir / "rtl" / "nanosoc_chip_pads_generic_multicore.v"
        assert v.exists()
        text = v.read_text()
        assert "nanosoc_multicore_chip u_nanosoc_multicore_chip (" in text
        # 43 physical signal pad cells (30 logical pads, bus-expanded; +6 SPI bits)
        assert text.count("PAD_INOUT8MA_NOE uPAD_") == 43
        assert (build_dir / "flist"
                / "nanosoc_chip_pads_generic_multicore.flist").exists()

    def test_full_chain_chip_boundary_plus_mixed_pad_ring(
            self, fixtures_dir, tmp_path, monkeypatch):
        """--chip-boundary + --pad-tech/--pin-count in one run emit BOTH the
        chip-functional wrapper and the pad ring (the doc-07 full chain)."""
        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "minimal_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
            "--chip-boundary", "multicore_chip",
            "--pad-tech", "generic", "--pin-count", "multicore",
        ])
        assert rc == 0
        assert (build_dir / "rtl" / "multicore_chip.v").exists()
        assert (build_dir / "rtl"
                / "nanosoc_chip_pads_generic_multicore.v").exists()


class TestEmitUpf:
    """The UPF (power-intent) emission path in main() (doc-07 M5).

    The pad-ring stage auto-emits the UPF when the selected pad-tech carries a
    ``upf:`` block (the bundled generic does); ``--emit-upf`` forces it. The UPF backend
    itself is covered in test_pad_ring_generic.py (and in the pad packs) — here we lock the CLI WIRING: the file is
    emitted alongside the pad ring, its flist is written, and the structural
    teeth (supply set + AON PST + at least one derived connect line) are present.
    """

    def test_upf_auto_emitted_with_pad_tech_block(
            self, fixtures_dir, tmp_path, monkeypatch):
        """--pad-tech generic (which carries a upf: block) auto-emits the UPF
        next to the mixed pad ring; no --emit-upf needed. two_region_soc gives
        2 derived SRAM connect pairs so the model-walk path is exercised."""
        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "two_region_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
            "--pad-tech", "generic", "--pin-count", "multicore",
        ])
        assert rc == 0
        upf = build_dir / "rtl" / "nanosoc_chip_pads_generic_multicore.upf"
        assert upf.exists()
        text = upf.read_text()
        assert "create_supply_set VDD_VSS" in text
        assert "add_pst_state aon -pst all_pst -state {state1}" in text
        # model-DERIVED connect block: two_region_soc has 2 SRAM regions
        assert text.count("connect_supply_net VDD -ports u_") == 2
        assert (build_dir / "flist"
                / "nanosoc_chip_pads_generic_multicore.flist").exists()

    def test_emit_upf_flag_forces_emission_and_reports_residuals(
            self, fixtures_dir, tmp_path, monkeypatch, capsys):
        """--emit-upf forces the path and the residual lines are printed (the
        honest non-derivable disclosure)."""
        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "two_region_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
            "--pad-tech", "generic", "--pin-count", "multicore",
            "--emit-upf",
        ])
        assert rc == 0
        assert (build_dir / "rtl"
                / "nanosoc_chip_pads_generic_multicore.upf").exists()
        out = capsys.readouterr().out
        assert "residual (not derivable): top_prefix declared constant" in out

    def test_no_pad_tech_no_upf(
            self, fixtures_dir, tmp_path, monkeypatch):
        """Without --pad-tech the UPF path never runs (no pad ring, no UPF)."""
        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "two_region_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
        ])
        assert rc == 0
        assert not list((build_dir / "rtl").glob("*.upf"))


class TestLint:

    def test_lint_flag_runs_and_does_not_abort(
            self, fixtures_dir, tmp_path, monkeypatch, capsys):
        """--lint runs the lint backend over the generated RTL. With neither
        pyslang nor a real slang binary available, lint reports failure but the
        CLI still exits 0 (a lint miss does not abort generation). Exercises the
        --slang-bin and --slang-args branches too.

        If pyslang IS installed (so the CLI fallback path is not taken), SKIP —
        we never want a tool-presence difference to fail the unit tier."""
        from soc_model.backends.lint import _HAS_PYSLANG
        if _HAS_PYSLANG:
            pytest.skip("pyslang installed: slang-CLI fallback branch not taken")

        build_dir = tmp_path / "out"
        rc = _run_main(monkeypatch, [
            fixtures_dir / "minimal_soc.yaml",
            "--build-dir", build_dir,
            "--lib-dir", fixtures_dir,
            "--lint",
            "--slang-bin", str(tmp_path / "no_such_slang"),
            # '=' form so argparse does not treat the leading '--' as a flag.
            "--slang-args=--lint-only",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "RTL Lint" in out
        # the bogus slang binary path is echoed and the lint miss is reported.
        assert "no_such_slang" in out
        assert "Lint did not pass" in out


# ===========================================================================
# Subprocess smoke: prove the real `python3 -m soc_model` entry point works.
# (In-process tests above carry the coverage; this is end-to-end belt-and-braces.)
# ===========================================================================

def test_subprocess_module_entry_point(fixtures_dir, tmp_path):
    """`python3 -m soc_model <yaml> --build-dir ... --lib-dir ...` runs to
    completion (rc 0), prints 'Done.', and writes the top-level SV — proving the
    packaged __main__ guard wires main() to the real process entry point."""
    build_dir = tmp_path / "out"
    pkg_root = Path(__file__).resolve().parents[2]  # nanosoc-gen-tests/
    env = {
        **__import__("os").environ,
        "PYTHONPATH": str(pkg_root),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "soc_model",
         str(fixtures_dir / "minimal_soc.yaml"),
         "--build-dir", str(build_dir),
         "--lib-dir", str(fixtures_dir)],
        cwd=str(pkg_root), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout
    assert "Done." in proc.stdout
    assert (build_dir / "rtl" / "minimal_soc.sv").exists()
