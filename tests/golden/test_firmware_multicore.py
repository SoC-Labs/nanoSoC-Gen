# Copyright 2026, SoC Labs (www.soclabs.org)
"""Coverage gap-fill for ``soc_model/backends/firmware.py``.

The single-CPU ``firmware_soc.yaml`` (exercised by ``test_firmware.py``) drives
the happy ``address_select: default`` path only, so the harder branches of the
firmware backend stay uncovered:

  * ``_stitch_module`` hierarchical recursion + the bridge-merge that folds a
    subsystem's internal IMEM/BOOTROM alias + remap windows into a parent
    bridge initiator (firmware.py 261-264, 281-343), plus the same-name
    initiator merge across two top-level interconnects (226-230) and the
    remap-region base-offset rewrite (217-223),
  * ``_resolve_address`` alias / remapped resolution (411,416,423,425-441),
  * the conditional makefile vars: ``hex_adjust`` loop + ADP/region resolve
    fallbacks (594-595, 616-623, 633-634),
  * the linker + memmap alias/remap define branches (465-492, 538-556),
  * ``_derive_memory_regions`` periph -> peripheral normalisation (369-373)
    and ``_find_memory_region`` miss (389).

These are driven by a hierarchical fixture (``firmware_multicore_soc.yaml``,
whose CPU initiator is the *bridge* initiator ``eth_ss_m`` into the
``eth_subsystem`` child) plus a few synthetic-``init_data`` unit probes for the
pure address-resolution fallback branches that no realistic SoC reaches.

The bridge-merge is the path behind the documented ``alias -> 0x80000000``
boot break, so these assertions pin the *resolved addresses*, not just bytes.
"""

import re

import pytest

from soc_model.model import Firmware
from soc_model.backends.firmware import SoCFirmwareBackend

from conftest import FIXTURES, SNAPSHOTS
from helpers import build_module_from_yaml, assert_matches_snapshot

pytestmark = pytest.mark.golden

_FIXTURE = "firmware_multicore_soc.yaml"

# Files the backend emits for the multicore fixture. Two linker profiles:
#   app_alias  -> SS_IMEM_0 at its ALIAS window + SS_BOOTROM_0 default
#   boot_remap -> SS_BOOTROM_0 at its REMAPPED window
_PROFILE_LD = ("app_alias", "boot_remap")
_SINGLE_FILES = ("memmap.h", "memmap.mk", "memmap.cmake", "adp.vh", "adp.py")


# ---------------------------------------------------------------------------
# Module-scoped build + render of the hierarchical fixture.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mc_top():
    return build_module_from_yaml(FIXTURES, _FIXTURE)


@pytest.fixture(scope="module")
def mc_backend(mc_top):
    return SoCFirmwareBackend(mc_top)


@pytest.fixture(scope="module")
def mc_stitched(mc_backend):
    """The stitched address map (drives _stitch_module recursion + merge)."""
    return mc_backend._build_stitched_map()


@pytest.fixture(scope="module")
def mc_outputs(mc_top, tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("fw_multicore_out")
    SoCFirmwareBackend(mc_top).generate(out_dir)
    return mc_top.name, {p.name: p.read_text() for p in sorted(out_dir.iterdir())}


# ---------------------------------------------------------------------------
# _stitch_module: hierarchical recursion + bridge-merge (281-343).
# ---------------------------------------------------------------------------

def test_subsystem_targets_stitched_with_absolute_bases(mc_stitched):
    """Recursing into the eth_subsystem (261-264) rebases its child targets.

    The subsystem sits behind the parent target ``eth_ss_0`` @ 0x40000000, so
    the child IMEM (local 0x10000000) lands at 0x50000000 and the child BOOTROM
    (local 0x00000000) at 0x40000000 in the stitched map.
    """
    bases = {t["name"]: t["base"] for t in mc_stitched["targets"]}
    # Parent-level targets.
    assert bases["eth_ss_0"] == 0x40000000
    assert bases["shared_ram_0"] == 0x18000000
    # Child (subsystem) targets, rebased by the parent base offset.
    assert bases["ss_imem_0"] == 0x50000000     # 0x40000000 + 0x10000000
    assert bases["ss_bootrom_0"] == 0x40000000  # 0x40000000 + 0x00000000


def test_same_name_initiator_merge_across_interconnects(mc_stitched):
    """``cpu_m`` appears in two top-level interconnects -> regions merge (226-230).

    The aux interconnect re-uses the ``cpu_m`` initiator name with a
    shared_ram_0 view, so the same-name branch extends the existing entry
    rather than overwriting it: shared_ram_0 is therefore present more than
    once in cpu_m's merged address_regions.
    """
    cpu_m = mc_stitched["initiator_data"]["cpu_m"]
    shared_hits = [
        r for r in cpu_m["address_regions"] if r["interface"] == "shared_ram_0"
    ]
    assert len(shared_hits) >= 2, (
        "same-name merge did not extend cpu_m; the aux-interconnect cpu_m view "
        "was lost"
    )


def test_bridge_merge_folds_subsystem_alias_and_remap(mc_backend, mc_stitched):
    """The bridge initiator ``eth_ss_m`` receives the subsystem's internal
    alias + remap windows folded from the child CPU initiator (281-343).

    Without the bridge-merge, eth_ss_m would only see its own canonical
    windows; the subsystem's IMEM-alias and BOOTROM-remap metadata (carried by
    the internal ``ss_cpu_m`` initiator) would be invisible and a linker
    profile keyed on eth_ss_m could not resolve ``alias`` / ``remapped``.
    """
    eth = mc_stitched["initiator_data"]["eth_ss_m"]
    # The folded alias address-region for the subsystem IMEM (rebased to
    # 0x40000000) must be present and marked 'alias'.
    alias_imem = [
        r for r in eth["address_regions"]
        if r["interface"] == "ss_imem_0" and r["remapping"] == "alias"
    ]
    assert alias_imem, "IMEM alias window was not folded into the bridge initiator"
    assert alias_imem[0]["mem_lo"] == "40000000"
    # The folded BOOTROM remap-region (rebased, 217-223) must be present.
    remap_boot = [
        r for r in eth["remap_regions"] if r["interface"] == "ss_bootrom_0"
    ]
    assert remap_boot, "BOOTROM remap region was not folded into the bridge initiator"
    assert remap_boot[0]["mem_lo"] == "40000000"
    assert remap_boot[0]["bit"] == 0


def test_bridge_initiator_resolves_alias_and_remapped(mc_backend, mc_stitched):
    """End-to-end: eth_ss_m resolves alias + remapped for the folded regions.

    Exercises _resolve_address remapped (407-411) and explicit-alias (425-429)
    branches against the bridge-merged map.
    """
    fw = mc_backend._find_firmware()
    init_data = mc_backend._build_cpu_initiator_data(mc_stitched, fw.cpu_initiator)
    bases = {t["name"]: t["base"] for t in mc_stitched["targets"]}

    # default -> canonical rebased IMEM, alias -> 0x40000000 window.
    assert mc_backend._resolve_address("ss_imem_0", "default", init_data, bases) == 0x50000000
    assert mc_backend._resolve_address("ss_imem_0", "alias", init_data, bases) == 0x40000000
    # remapped IMEM resolves via the address-region fallback (414-415).
    assert mc_backend._resolve_address("ss_imem_0", "remapped", init_data, bases) == 0x40000000
    # BOOTROM remapped resolves via the remap_region branch (409-411).
    assert mc_backend._resolve_address("ss_bootrom_0", "remapped", init_data, bases) == 0x40000000


def test_bridge_initiator_alias_unresolvable_raises(mc_backend, mc_stitched):
    """A target with a single (non-alias) window cannot resolve 'alias' (433).

    shared_ram_0 has one canonical window in the bridge view, so requesting an
    alias address raises -- this is the ValueError that _build_memmap_context
    swallows (544-545) to omit the _ALIAS define.
    """
    fw = mc_backend._find_firmware()
    init_data = mc_backend._build_cpu_initiator_data(mc_stitched, fw.cpu_initiator)
    bases = {t["name"]: t["base"] for t in mc_stitched["targets"]}
    with pytest.raises(ValueError, match="alias"):
        mc_backend._resolve_address("shared_ram_0", "alias", init_data, bases)


# ---------------------------------------------------------------------------
# _derive_memory_regions periph normalisation (369-373) + _find_memory_region.
# ---------------------------------------------------------------------------

def test_periph_region_type_normalised_to_peripheral(mc_backend, mc_stitched):
    """A target with ``region_type: periph`` is normalised to 'peripheral'."""
    regions = mc_backend._derive_memory_regions(mc_stitched)
    periph = mc_backend._find_memory_region(regions, "periph_0")
    assert periph is not None
    assert periph["region_type"] == "peripheral"   # normalised from 'periph'
    # Non-memory/periph targets (e.g. the passthrough 'sys_exit') are excluded.
    assert mc_backend._find_memory_region(regions, "sys_exit") is None


def test_find_memory_region_miss_returns_none(mc_backend, mc_stitched):
    """_find_memory_region returns None for an unknown target name (389)."""
    regions = mc_backend._derive_memory_regions(mc_stitched)
    assert mc_backend._find_memory_region(regions, "does_not_exist") is None


# ---------------------------------------------------------------------------
# Golden snapshots for the hierarchical fixture (timestamp/copyright stripped).
# ---------------------------------------------------------------------------

def test_emits_expected_file_set(mc_outputs):
    name, files = mc_outputs
    expected = {f"{name}_{p}_memory.ld" for p in _PROFILE_LD}
    expected |= {f"{name}_{s}" for s in _SINGLE_FILES}
    assert set(files) == expected
    for fname, text in files.items():
        assert text.strip(), f"{fname} rendered empty"


@pytest.mark.parametrize("profile", _PROFILE_LD)
def test_linker_ld_golden(mc_outputs, profile, request):
    name, files = mc_outputs
    fname = f"{name}_{profile}_memory.ld"
    assert_matches_snapshot(
        files[fname], SNAPSHOTS / "firmware_multicore_soc" / fname, request,
    )


@pytest.mark.parametrize("suffix", _SINGLE_FILES)
def test_single_file_golden(mc_outputs, suffix, request):
    name, files = mc_outputs
    fname = f"{name}_{suffix}"
    assert_matches_snapshot(
        files[fname], SNAPSHOTS / "firmware_multicore_soc" / fname, request,
    )


# ---------------------------------------------------------------------------
# Linker context: alias-selected origin + linker_name/software_access overrides.
# ---------------------------------------------------------------------------

_MEMORY_LINE_RE = re.compile(
    r"^\s*(?P<name>\w+)\s+\((?P<attrs>[rwx]+)\)\s*:\s*"
    r"ORIGIN\s*=\s*(?P<origin>0x[0-9A-Fa-f]+)\s*,\s*"
    r"LENGTH\s*=\s*(?P<length>0x[0-9A-Fa-f]+)",
    re.MULTILINE,
)


def _parse_memory_block(ld_text):
    out = {}
    for m in _MEMORY_LINE_RE.finditer(ld_text):
        out[m.group("name")] = {
            "attrs": m.group("attrs"),
            "origin": "0x" + m.group("origin")[2:].upper(),
            "length": "0x" + m.group("length")[2:].upper(),
        }
    return out


def test_app_alias_profile_links_against_alias_window(mc_outputs):
    """The app_alias profile links IMEM at its ALIAS origin with the overrides.

    The ``linker_name: APP_IMEM`` + ``software_access: rwx`` overrides (483-484)
    rename the region and the ``address_select: alias`` resolves to 0x40000000
    (the folded subsystem alias window), NOT the canonical 0x50000000.
    """
    name, files = mc_outputs
    ld = files[f"{name}_app_alias_memory.ld"]
    parsed = _parse_memory_block(ld)
    assert "APP_IMEM" in parsed, "linker_name override APP_IMEM not applied"
    assert parsed["APP_IMEM"]["origin"] == "0x40000000"   # alias, not 0x50000000
    assert parsed["APP_IMEM"]["attrs"] == "rwx"
    # 64 KB physical IMEM (SS_IMEM_RAM_ADDR_W=16 -> 0x10000).
    assert parsed["APP_IMEM"]["length"] == "0x10000"


def test_boot_remap_profile_links_against_remap_window(mc_outputs):
    """The boot_remap profile resolves BOOTROM via its REMAPPED window."""
    name, files = mc_outputs
    ld = files[f"{name}_boot_remap_memory.ld"]
    parsed = _parse_memory_block(ld)
    assert "SS_BOOTROM_0" in parsed
    assert parsed["SS_BOOTROM_0"]["origin"] == "0x40000000"
    assert parsed["SS_BOOTROM_0"]["length"] == "0x800"     # 2 KB


# ---------------------------------------------------------------------------
# memmap.h alias + remap defines (538-556) and peripheral BASE-only define.
# ---------------------------------------------------------------------------

def test_memmap_h_emits_alias_and_remap_defines(mc_top, mc_outputs):
    """IMEM gets _ALIAS and _REMAP defines distinct from _BASE (538-556)."""
    name, files = mc_outputs
    prefix = mc_top.name.upper()
    assert prefix == "FIRMWARE_MULTICORE_SOC"
    h = files[f"{name}_memmap.h"]

    def _def(macro):
        m = re.search(rf"#define\s+{re.escape(macro)}\s+\((0x[0-9A-Fa-f]+UL)\)", h)
        return m.group(1) if m else None

    assert _def(f"{prefix}_SS_IMEM_0_BASE") == "0x50000000UL"
    # alias + remap windows were folded by the bridge-merge -> both emitted.
    assert _def(f"{prefix}_SS_IMEM_0_ALIAS") == "0x40000000UL"
    assert _def(f"{prefix}_SS_IMEM_0_REMAP") == "0x40000000UL"


def test_memmap_h_peripheral_has_base_but_no_size(mc_top, mc_outputs):
    """A peripheral region emits a _BASE define but no _SIZE/_ALIAS/_REMAP.

    Only ``region_type == 'memory'`` regions get _SIZE/_ALIAS/_REMAP
    (firmware.py 527), so the peripheral exercises the false arm of that
    conditional.
    """
    name, files = mc_outputs
    prefix = mc_top.name.upper()
    h = files[f"{name}_memmap.h"]
    assert f"{prefix}_PERIPH_0_BASE" in h
    assert f"{prefix}_PERIPH_0_SIZE" not in h
    assert f"{prefix}_PERIPH_0_ALIAS" not in h


# ---------------------------------------------------------------------------
# makefile context: hex_adjust loop + cmake CPU + ADP via alias select.
# ---------------------------------------------------------------------------

def test_memmap_mk_hex_adjust_and_cpu(mc_outputs):
    """The fixture's hex_adjust block produces ADJUST_VMA vars (616-623)."""
    name, files = mc_outputs
    mk = files[f"{name}_memmap.mk"]
    assert "NANOSOC_DEFAULT_CPU := cortex-m0plus" in mk
    # hex_adjust 'imem_alias' resolves IMEM alias 0x40000000 -> -0x40000000.
    assert re.search(r"^IMEM_ALIAS_ADJUST_VMA\s*=\s*-0x40000000\s*$", mk, re.MULTILINE)
    assert re.search(r"^BOOT_DEFAULT_ADJUST_VMA\s*=\s*-0x40000000\s*$", mk, re.MULTILINE)
    # ADP upload via address_select: alias -> 0x40000000.
    assert re.search(r"^ADP_UPLOAD_ADDRESS\s*=\s*0x40000000\s*$", mk, re.MULTILINE)


def test_adp_via_alias_select(mc_outputs):
    """ADP upload_target ss_imem_0 with address_select: alias -> 0x40000000."""
    name, files = mc_outputs
    vh = files[f"{name}_adp.vh"]
    py = files[f"{name}_adp.py"]
    assert "`define ADP_UPLOAD_ADDRESS 32'h40000000" in vh
    assert "ADP_UPLOAD_ADDRESS = 0x40000000" in py


# ---------------------------------------------------------------------------
# Pure-function fallback branches that no realistic SoC reaches: drive
# _resolve_address / _build_makefile_context with synthetic init_data.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def two_window_init():
    """An initiator with two non-alias windows for one target + one single."""
    return {
        "name": "x",
        "connections": ["twowin", "single"],
        "address_regions": [
            {"interface": "twowin", "mem_lo": "00001000", "mem_hi": "00001fff", "remapping": "none"},
            {"interface": "twowin", "mem_lo": "80001000", "mem_hi": "80001fff", "remapping": "none"},
            {"interface": "single", "mem_lo": "00002000", "mem_hi": "00002fff", "remapping": "none"},
        ],
        "remap_regions": [],
    }


def test_resolve_alias_falls_back_to_second_window(mc_backend, two_window_init):
    """With no explicitly alias-marked region, alias picks the 2nd window (431-432)."""
    assert mc_backend._resolve_address("twowin", "alias", two_window_init) == 0x80001000


def test_resolve_default_without_target_bases_uses_lowest_canonical(mc_backend, two_window_init):
    """default with no target_bases hit falls through to the canonical scan (436-441)."""
    # No target_bases -> skips the fast path; scans for the lowest non-alias
    # address_region.
    assert mc_backend._resolve_address("twowin", "default", two_window_init) == 0x1000
    # An empty target_bases dict (target absent) takes the same fallback.
    assert mc_backend._resolve_address("twowin", "default", two_window_init, {}) == 0x1000


def test_resolve_remapped_unresolvable_raises(mc_backend, two_window_init):
    """remapped on a target with no matching region raises (416)."""
    with pytest.raises(ValueError, match="remapped"):
        mc_backend._resolve_address("ghost", "remapped", two_window_init)


def test_resolve_alias_missing_target_raises(mc_backend, two_window_init):
    """alias on an absent target raises 'not found in address map' (423)."""
    with pytest.raises(ValueError, match="not found in address map"):
        mc_backend._resolve_address("ghost", "alias", two_window_init)


def test_makefile_context_swallows_unresolvable_region_and_adp(mc_backend):
    """A region/ADP target absent from init_data is skipped, not crashed.

    Drives the ValueError->continue in the region-var loop (594-595) and the
    ValueError->fallback for the ADP address (633-634).
    """
    empty_init = {"name": "x", "connections": [], "address_regions": [], "remap_regions": []}
    mem_regions = [{
        "target": "ghost", "region_type": "memory", "linker_name": "GHOST",
        "software_access": "rwx", "phys_size": 0x100, "role": None,
    }]
    fw = Firmware(cpu="cortex-m0", adp={"upload_target": "ghost", "address_select": "default"})
    ctx = mc_backend._build_makefile_context(fw, empty_init, mem_regions, {}, target_bases={})
    # Unresolvable region produced no region var.
    assert ctx["region_vars"] == []
    # Unresolvable ADP target fell back to the zero default.
    assert ctx["adp_upload_address"] == "0x00000000"
    assert ctx["cpu"] == "cortex-m0"
