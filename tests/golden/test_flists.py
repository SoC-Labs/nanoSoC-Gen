# Copyright 2026, SoC Labs (www.soclabs.org)
"""Golden + structural tests for the generated ``*.flist`` artifacts.

Flists are emitted by several backends — each owning a differently-named list:

  * ``SoCTopLevelBackend``  -> ``<top>_toplevel.flist``        (FL-01)
  * ``SoCConfigPkgBackend`` -> ``<top>_soc_config_pkg.flist``  (FL-02 / FL-03)
  * ``SoCAhbBackend``       -> ``<prefix>_interconnect.flist`` (FL-04, perl-skip)
  * ``SoCSystemBackend``    -> ``<top>_system.flist``          (FL-05)
  * ``SoCChipBackend``      -> ``<top>_chip.flist``            (FL-06)

The toplevel / AHB / system / chip flists reference files by ``$(SOCLABS_*)``
make-variable paths (no tmp paths) and carry only a stripped ``// Generated:``
/ ``// Copyright`` header, so they are byte-stable goldens. The config-pkg
flist likewise references its ``.sv`` via ``$(SOCLABS_NANOSOC_SOC_DIR)`` (no
longer an absolute ``output_path.resolve()``), so it too is now deterministic;
it is still asserted structurally here.

The AHB backend warns-and-skips ``BuildBusMatrix.pl`` when ``ARM_IP_LIBRARY_PATH``
is unset; its Jinja flist is still produced and is what we golden. No test here
writes under / points at any read-only vendor IP tree.
"""

import pytest

from soc_model.backends.toplevel import SoCTopLevelBackend
from soc_model.backends.soc_config_pkg import SoCConfigPkgBackend
from soc_model.backends.ahb import SoCAhbBackend
from soc_model.backends.system import SoCSystemBackend
from soc_model.backends.chip import SoCChipBackend

from tests.conftest import SNAPSHOTS
from tests.helpers import build_module_from_yaml, assert_matches_snapshot

pytestmark = pytest.mark.golden


# ---------------------------------------------------------------------------
# FL-01 — toplevel <top>_toplevel.flist byte-match + structural
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fixture",
    ["minimal_soc", "two_region_soc", "apb_bridge_soc",
     "passthrough_soc", "firmware_soc"],
)
def test_toplevel_flist_golden(fixture, fixtures_dir, tmp_path, request):
    top = build_module_from_yaml(fixtures_dir, f"{fixture}.yaml")
    rtl_dir = tmp_path / "rtl"
    flist_dir = tmp_path / "flist"
    SoCTopLevelBackend(top).generate(rtl_dir, flist_dir)

    flist_path = flist_dir / f"{top.name}_toplevel.flist"
    assert flist_path.is_file()
    flist = flist_path.read_text()

    # Structural: references the generated top .sv and the glue helpers.
    assert flist.strip(), f"{fixture}: toplevel flist is empty"
    assert f"{top.name}.sv" in flist
    assert "+libext+" in flist
    assert "soc_glue_passthrough.sv" in flist
    assert "soc_glue_constant.sv" in flist

    assert_matches_snapshot(
        flist, SNAPSHOTS / fixture / f"{top.name}_toplevel.flist", request)


# ---------------------------------------------------------------------------
# FL-02 — config-pkg flist NOT written when flist_dir is None
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_config_pkg_no_flist_when_none(fixtures_dir, tmp_path):
    top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
    rtl_dir = tmp_path / "rtl"
    SoCConfigPkgBackend(top).generate(rtl_dir)  # no flist_dir
    # The .sv and .vh are written, but no flist anywhere.
    assert (rtl_dir / "minimal_soc_soc_config_pkg.sv").is_file()
    assert not list(tmp_path.rglob("*.flist"))


# ---------------------------------------------------------------------------
# FL-03 — config-pkg flist written when flist_dir given; contains the
#          resolved absolute .sv path + the auto-generated header (NOT a
#          byte golden — the path is environment-specific).
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_config_pkg_flist_structural(fixtures_dir, tmp_path):
    top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
    rtl_dir = tmp_path / "rtl"
    flist_dir = tmp_path / "flist"
    sv_path = SoCConfigPkgBackend(top).generate(rtl_dir, flist_dir)

    flist_path = flist_dir / "minimal_soc_soc_config_pkg.flist"
    assert flist_path.is_file()
    flist = flist_path.read_text()
    assert "// Auto-generated filelist for minimal_soc_soc_config_pkg" in flist
    # The flist references the generated .sv via a layout-agnostic make
    # variable (portable + byte-stable), not an absolute path.
    assert ("$(SOCLABS_NANOSOC_SOC_DIR)/build_soc/rtl/"
            "minimal_soc_soc_config_pkg.sv") in flist
    assert str(sv_path.resolve()) not in flist


# ---------------------------------------------------------------------------
# FL-04 — AHB <prefix>_interconnect.flist (perl skipped) byte-match
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fixture,prefix",
    [
        ("minimal_soc", "minimal"),
        ("two_region_soc", "two_region"),
        ("apb_bridge_soc", "apb_bridge"),
    ],
)
def test_ahb_interconnect_flist_golden(fixture, prefix, fixtures_dir,
                                       tmp_path, request, monkeypatch):
    # Ensure the perl path is the warn-and-skip branch regardless of host env:
    # never point at or read a vendor IP tree.
    monkeypatch.delenv("ARM_IP_LIBRARY_PATH", raising=False)

    top = build_module_from_yaml(fixtures_dir, f"{fixture}.yaml")
    build_dir = tmp_path / "build"
    # arm_ip_library_path=None -> BuildBusMatrix.pl is warned-and-skipped.
    SoCAhbBackend(top, arm_ip_library_path=None).generate_all(build_dir)

    flist_path = build_dir / "flist" / f"{prefix}_interconnect.flist"
    assert flist_path.is_file(), (
        f"expected {flist_path}; AHB Jinja flist must emit even with perl skipped"
    )
    flist = flist_path.read_text()

    # Structural: wraps the generated interconnect wrapper + busmatrix IP.
    assert flist.strip()
    assert "AHB Interconnect IP Filelist" in flist
    assert f"{prefix}_interconnect.sv" in flist
    assert "+libext+" in flist

    assert_matches_snapshot(
        flist, SNAPSHOTS / fixture / f"{prefix}_interconnect.flist", request)


# ---------------------------------------------------------------------------
# FL-05 — system <top>_system.flist default dir + lists the .sv
# ---------------------------------------------------------------------------
def test_system_flist_golden(fixtures_dir, tmp_path, request):
    top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
    rtl_dir = tmp_path / "rtl"
    flist_dir = tmp_path / "flist"
    SoCSystemBackend(top).generate(rtl_dir, flist_dir)

    flist_path = flist_dir / "minimal_soc_system.flist"
    assert flist_path.is_file()
    flist = flist_path.read_text()
    assert flist.strip()
    # Lists the generated SoC .sv via a make-variable path (deterministic).
    assert "minimal_soc.sv" in flist
    assert "SOCLABS_NANOSOC_SOC_DIR" in flist

    assert_matches_snapshot(
        flist, SNAPSHOTS / "minimal_soc" / "minimal_soc_system.flist", request)


@pytest.mark.unit
def test_system_flist_default_dir(fixtures_dir, tmp_path):
    """FL-05: flist defaults to rtl_dir.parent/flist when flist_dir omitted."""
    top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
    rtl_dir = tmp_path / "build" / "rtl"
    SoCSystemBackend(top).generate(rtl_dir)  # no flist_dir
    assert (rtl_dir.parent / "flist" / "minimal_soc_system.flist").is_file()


# ---------------------------------------------------------------------------
# FL-06 — chip <top>_chip.flist lists both .v and _pads.v
# ---------------------------------------------------------------------------
def test_chip_flist_golden(fixtures_dir, tmp_path, request):
    top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
    rtl_dir = tmp_path / "rtl"
    flist_dir = tmp_path / "flist"
    SoCChipBackend(top).generate(rtl_dir, flist_dir)

    flist_path = flist_dir / "minimal_soc_chip.flist"
    assert flist_path.is_file()
    flist = flist_path.read_text()
    assert flist.strip()
    # Both the chip wrapper and the pad-ring wrapper must be listed.
    assert "minimal_soc.v" in flist
    assert "minimal_soc_pads.v" in flist

    assert_matches_snapshot(
        flist, SNAPSHOTS / "minimal_soc" / "minimal_soc_chip.flist", request)
