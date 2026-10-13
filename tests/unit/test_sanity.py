# Copyright 2026, SoC Labs (www.soclabs.org)
"""Sanity tests for the test scaffold itself.

Verifies that (a) the core soc_model modules import via the conftest sys.path
shim, (b) every hermetic fixture parses + builds into a Module with the
expected top name and fully-resolved instances, and (c) the snapshot helper
round-trips (write with --snapshot-update semantics, then match).

This is the foundation the parallel implementers inherit — if this is green,
their build_module_from_yaml / assert_matches_snapshot calls work.
"""

import importlib

import pytest

from tests.helpers import (
    build_module_from_yaml,
    build_module_inline,
    assert_matches_snapshot,
)

pytestmark = pytest.mark.unit


# Every fixture and the top-module name it must build to.
FIXTURE_NAMES = {
    "minimal_soc": "minimal_soc",
    "two_region_soc": "two_region_soc",
    "apb_bridge_soc": "apb_bridge_soc",
    "passthrough_soc": "passthrough_soc",
    "firmware_soc": "firmware_soc",
}


def test_core_modules_import():
    """The five core generator modules import cleanly via the shim."""
    for name in ("parser", "builder", "model", "validator", "utils"):
        mod = importlib.import_module(f"soc_model.{name}")
        assert mod is not None


def test_helpers_export_api():
    """The helper API implementers depend on is importable and callable."""
    assert callable(build_module_from_yaml)
    assert callable(build_module_inline)
    assert callable(assert_matches_snapshot)


@pytest.mark.parametrize("fixture,expected_name", sorted(FIXTURE_NAMES.items()))
def test_fixture_builds(fixture, expected_name, fixtures_dir):
    """Each fixture parses + builds into a Module with the expected name."""
    top = build_module_from_yaml(fixtures_dir, f"{fixture}.yaml")
    assert top.name == expected_name
    assert top.gen is True
    # Every non-rtl instance must have resolved its module from the hermetic
    # regions/ directory — the whole point of a self-contained fixture.
    unresolved = [
        inst.instance_name
        for inst in top.instances
        if inst.resolved_module is None and not inst.is_rtl_module
    ]
    assert unresolved == [], f"unresolved instances in {fixture}: {unresolved}"
    # Each fixture has at least one gen:True interconnect.
    assert any(ic.gen for ic in top.interconnects)


def test_snapshot_roundtrip(tmp_path, request):
    """assert_matches_snapshot writes-then-matches and detects drift.

    --snapshot-update is not set in the default run, so we first create the
    golden by writing it directly, then assert it matches, then assert a
    changed value fails.
    """
    snap = tmp_path / "sub" / "snippet.txt"
    payload = "// Copyright 2026, SoC Labs\nmodule m; endmodule\n"

    # Create the golden the way --snapshot-update would.
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text(payload)

    # Identical content matches.
    assert_matches_snapshot(payload, snap, request)

    # A copyright-only difference still matches (timestamp/copyright stripped).
    altered_header = "// Copyright 2099, SoC Labs\nmodule m; endmodule\n"
    assert_matches_snapshot(altered_header, snap, request)

    # A meaningful difference fails.
    with pytest.raises(AssertionError):
        assert_matches_snapshot("module DIFFERENT; endmodule\n", snap, request)
