# Copyright 2026, SoC Labs (www.soclabs.org)
"""Shared pytest configuration for the nanosoc_gen test suite.

Provides:
  * a sys.path shim so ``import soc_model`` works without an install
    (the worktree root — parents[1] of this file — is prepended to
    sys.path, mirroring python/tests/conftest.py in the superproject),
  * the test-tier markers (unit / golden / tooling / slow),
  * a ``--snapshot-update`` option for the golden tier, and
  * the shared FIXTURES / SNAPSHOTS paths plus a ``fixtures_dir`` fixture.
"""

import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import soc_model without an install: put the package root (the worktree
# root, which contains the soc_model/ package) on sys.path.
# ---------------------------------------------------------------------------
_PKG_ROOT = Path(__file__).resolve().parents[1]   # nanosoc-gen-tests/
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

# Shared paths consumed by helpers and golden tests.
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SNAPSHOTS = Path(__file__).resolve().parent / "golden" / "__snapshots__"


def pytest_addoption(parser):
    parser.addoption(
        "--snapshot-update",
        action="store_true",
        default=False,
        help="Rewrite golden snapshot files instead of asserting against them.",
    )


def pytest_configure(config):
    for marker, blurb in (
        ("unit", "fast pure-Python tests (no EDA tools)"),
        ("golden", "snapshot tests of generated artifacts"),
        ("tooling", "tests requiring perl/BuildBusMatrix or peakrdl-regblock"),
        ("slow", "tests driving the real multicore SoC YAML / submodules"),
    ):
        config.addinivalue_line("markers", f"{marker}: {blurb}")


@pytest.fixture
def fixtures_dir():
    """Directory holding the hermetic fixture SoC YAMLs."""
    return FIXTURES


@pytest.fixture
def snapshots_dir():
    """Directory holding committed golden artifacts."""
    return SNAPSHOTS


@pytest.fixture(autouse=True)
def _no_arm_ip_library_outside_tooling(request, monkeypatch):
    """Keep the default suite away from the Arm IP library.

    The AHB and DMA-250 backends fall back to $ARM_IP_LIBRARY_PATH when no
    path is passed, and then run Arm's BuildBusMatrix.pl / DMA-250 `generate`
    with their working directory INSIDE the read-only IP tree. On a lab host
    that exports the variable, the default suite did exactly that: measured
    with a stand-in BuildBusMatrix.pl, 26 invocations per run, all with the
    IP tree as cwd. Only the opt-in `tooling` tier may see the variable.
    """
    if request.node.get_closest_marker("tooling") is None:
        monkeypatch.delenv("ARM_IP_LIBRARY_PATH", raising=False)

