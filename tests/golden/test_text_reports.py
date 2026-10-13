# Copyright 2026, SoC Labs (www.soclabs.org)
"""Golden + unit tests for ``soc_model/backends/text.py``.

The text backend (:class:`SoCTextBackend`) emits two fully deterministic
report files — a memory map and a component hierarchy. Neither carries a
timestamp or copyright header, so the rendered text is a stable golden with
no normalisation needed beyond the shared snapshot helper.

Tier split:
  * ``unit``  — pure-Python helpers (``_fmt_size``) and structural assertions
    on small in-memory / fixture-built ``Module`` objects.
  * ``golden`` — byte-stable snapshots of the rendered ``memory_map.txt`` /
    ``hierarchy.txt`` for the hermetic fixtures.

Scope note: this module owns the ``text.py`` rows of TEST_PLAN 5.10
(``sram.py`` / ``python.py`` belong to other rows of that same section and are
out of this area's two-file remit).
"""

from pathlib import Path

import pytest

from soc_model.backends.text import SoCTextBackend, _fmt_size
from soc_model.model import (
    Instance,
    Interconnect,
    InterconnectInitiator,
    InterconnectInitiatorTarget,
    InterconnectTarget,
    Module,
)

from tests.conftest import SNAPSHOTS
from tests.helpers import assert_matches_snapshot, build_module_from_yaml


# ---------------------------------------------------------------------------
# unit tier — _fmt_size buckets (TX-01)
# ---------------------------------------------------------------------------

class TestFmtSize:
    pytestmark = pytest.mark.unit

    @pytest.mark.parametrize(
        "size, expected",
        [
            (0x40000000, "1 GB"),     # exactly 1 GiB
            (0x100000, "1 MB"),       # exactly 1 MiB
            (0x800, "2 KB"),          # exactly 2 KiB
            (0x10, "16 B"),           # below 1 KiB -> bytes
            (0x600, "1.5 KB"),        # fractional KiB -> one decimal
            (0x60000000, "1.5 GB"),   # fractional GiB
            (0, "0 B"),               # zero -> bytes
        ],
    )
    def test_buckets(self, size, expected):
        assert _fmt_size(size) == expected

    def test_kb_boundary_is_bytes_below_1024(self):
        # 1023 B stays in the byte bucket (>= 1024 guard).
        assert _fmt_size(1023) == "1023 B"
        # 1024 B is exactly 1 KiB.
        assert _fmt_size(1024) == "1 KB"


# ---------------------------------------------------------------------------
# unit tier — memory map structural behaviour
# ---------------------------------------------------------------------------

def _memmap_inline_top():
    """A hand-built top whose targets are intentionally out of base order and
    whose second target has ``region_type=None`` (blank Type column)."""
    top = Module(name="t_unit")
    ic = Interconnect(name="t_unit_ic", type="ahb_lite")
    ic.targets = [
        # deliberately out of order: 'b' (high base) before 'a' (low base)
        InterconnectTarget(name="b", base=0x10000000, size=0x1000, region_type=None),
        InterconnectTarget(name="a", base=0x00000000, size=0x800, sw_access="rwx",
                           region_type="memory"),
    ]
    ic.initiators = [
        InterconnectInitiator(
            name="m",
            targets=[InterconnectInitiatorTarget("a"),
                     InterconnectInitiatorTarget("b")],
        )
    ]
    top.interconnects = [ic]
    return top


@pytest.mark.unit
def test_memory_map_targets_sorted_by_base(tmp_path):
    # TX-02: targets are rendered sorted by base ascending regardless of the
    # order they appear in the model.
    top = _memmap_inline_top()
    out = tmp_path / "memory_map.txt"
    SoCTextBackend(top).generate_memory_map(str(out))
    text = out.read_text()

    a_idx = text.index("\n  a ")     # row for target 'a'
    b_idx = text.index("\n  b ")     # row for target 'b'
    assert a_idx < b_idx, "low-base target 'a' must render before high-base 'b'"
    # Bases formatted as 0x%08X.
    assert "0x00000000  0x000007FF" in text  # a: 2 KB window
    assert "0x10000000  0x10000FFF" in text  # b: 4 KB window


@pytest.mark.unit
def test_memory_map_region_type_none_blank_column(tmp_path):
    # TX-07: region_type None renders as '' (no crash, blank Type column).
    top = _memmap_inline_top()
    out = tmp_path / "memory_map.txt"
    SoCTextBackend(top).generate_memory_map(str(out))
    lines = out.read_text().splitlines()
    b_row = next(ln for ln in lines if ln.startswith("  b "))
    # 'a' has region_type 'memory'; 'b' has None and must not print 'None'.
    assert "memory" not in b_row
    assert "None" not in b_row


@pytest.mark.unit
def test_memory_map_initiator_connectivity(tmp_path):
    # TX-05: initiator connectivity lists each initiator's visible target_names.
    top = _memmap_inline_top()
    out = tmp_path / "memory_map.txt"
    SoCTextBackend(top).generate_memory_map(str(out))
    text = out.read_text()
    assert "Initiator Connectivity:" in text
    assert "m: a, b" in text


@pytest.mark.unit
def test_memory_map_sub_regions_for_addressable_child(fixtures_dir, tmp_path):
    # TX-04: an interconnect target backed by an addressable child instance
    # whose resolved module carries an address_decode renders indented
    # sub-region rows (and the register lines beneath the slot).
    top = build_module_from_yaml(fixtures_dir, "apb_bridge_soc.yaml")
    out = tmp_path / "memory_map.txt"
    SoCTextBackend(top).generate_memory_map(str(out))
    text = out.read_text()
    # The APB peripheral target carries a register-map-bearing slot.
    assert "apb_periph_0" in text
    assert "└─ regs_0" in text                 # indented sub-region row
    assert "[tiny_regs]" in text               # register-map tag on the slot
    # Register detail rows are absolute-addressed at the slot base (0x40000000).
    assert "0x40000000  CTRL" in text
    assert "0x40000004  STATUS" in text


@pytest.mark.unit
def test_memory_map_empty_when_no_interconnects(tmp_path):
    # A top with no interconnects still emits the banner (no crash, no rows).
    top = Module(name="bare")
    out = tmp_path / "memory_map.txt"
    SoCTextBackend(top).generate_memory_map(str(out))
    text = out.read_text()
    assert "bare — Memory Map" in text
    assert "Interconnect:" not in text


# ---------------------------------------------------------------------------
# unit tier — hierarchy tree structural behaviour
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_hierarchy_tags_and_connectors(tmp_path):
    # TX-06 (structural teeth): the tree uses ├── / └── connectors and the
    # (RTL) / [addressable] / [if <cond>] tags, and recurses into resolved
    # children.
    grandchild_parent = Module(name="child")
    grandchild_parent.instances = [
        Instance(instance_name="u_grandchild", module_name="gc")
    ]
    top = Module(name="t_tree")
    top.instances = [
        Instance(instance_name="u_rtl", module_name="cmsdk_x", is_rtl_module=True),
        Instance(instance_name="u_cond", module_name="c", condition="ENABLE_FOO"),
        Instance(instance_name="u_parent", module_name="child",
                 addressable=True, resolved_module=grandchild_parent),
    ]
    out = tmp_path / "hierarchy.txt"
    SoCTextBackend(top).generate_hierarchy(str(out))
    text = out.read_text()

    assert "t_tree (top)" in text
    # non-last children use ├──, last uses └──
    assert "├── u_rtl : cmsdk_x (RTL)" in text
    assert "├── u_cond : c [if ENABLE_FOO]" in text
    assert "└── u_parent : child [addressable]" in text
    # recursion into the resolved child renders the grandchild one level deeper
    assert "└── u_grandchild : gc" in text
    # the deeper level is indented past the parent's connector column
    gc_line = next(ln for ln in text.splitlines() if "u_grandchild" in ln)
    assert gc_line.startswith("      ")


@pytest.mark.unit
def test_hierarchy_empty_top(tmp_path):
    top = Module(name="empty_top")
    out = tmp_path / "hierarchy.txt"
    SoCTextBackend(top).generate_hierarchy(str(out))
    text = out.read_text()
    assert "empty_top (top)" in text
    # No instances -> no connector lines.
    assert "──" not in text.split("(top)", 1)[1].replace("=", "")


# ---------------------------------------------------------------------------
# golden tier — full rendered report snapshots
# ---------------------------------------------------------------------------

@pytest.mark.golden
@pytest.mark.parametrize("fixture", ["minimal_soc", "two_region_soc", "apb_bridge_soc"])
def test_memory_map_golden(fixture, fixtures_dir, tmp_path, request):
    top = build_module_from_yaml(fixtures_dir, f"{fixture}.yaml")
    out = tmp_path / "memory_map.txt"
    SoCTextBackend(top).generate_memory_map(str(out))
    text = out.read_text()

    # Structural teeth so a vacuous/empty golden cannot pass.
    assert f"{top.name} — Memory Map" in text
    assert "Interconnect:" in text
    assert "Initiator Connectivity:" in text
    assert text.endswith("\n")

    assert_matches_snapshot(text, SNAPSHOTS / fixture / "memory_map.txt", request)


@pytest.mark.golden
@pytest.mark.parametrize("fixture", ["minimal_soc", "two_region_soc", "apb_bridge_soc"])
def test_hierarchy_golden(fixture, fixtures_dir, tmp_path, request):
    top = build_module_from_yaml(fixtures_dir, f"{fixture}.yaml")
    out = tmp_path / "hierarchy.txt"
    SoCTextBackend(top).generate_hierarchy(str(out))
    text = out.read_text()

    # Structural teeth.
    assert f"{top.name} — Component Hierarchy" in text
    assert f"{top.name} (top)" in text
    assert "└──" in text  # at least the last child uses the corner connector

    assert_matches_snapshot(text, SNAPSHOTS / fixture / "hierarchy.txt", request)
