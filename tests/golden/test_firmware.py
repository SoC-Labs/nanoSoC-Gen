# Copyright 2026, SoC Labs (www.soclabs.org)
"""Golden + structural tests for ``soc_model/backends/firmware.py``.

The firmware backend (:class:`SoCFirmwareBackend`) renders, from the
``firmware:`` section of a SoC YAML:

  * ``<top>_<profile>_memory.ld``  — one linker MEMORY{} fragment per
    ``linker_profiles`` entry,
  * ``<top>_memmap.h``             — C header with ``<TOP_UPPER>_<REGION>_BASE``
    /``_SIZE`` defines plus firmware config macros,
  * ``<top>_memmap.mk`` / ``.cmake`` — Make / CMake includes with region bases,
    per-profile RO/RW link bases and the ADP upload address,
  * ``<top>_adp.vh`` / ``<top>_adp.py`` — ADP upload-address constants.

These are downstream-critical artifacts (a silent change in a linker MEMORY
base or the macro prefix is a firmware boot/build break — cf. the
``alias -> 0x80000000`` boot bug in project memory), so we both byte-match a
committed golden (timestamp/copyright stripped) AND assert the load-bearing
addresses/lengths explicitly so the test has semantic teeth.

The macro prefix is derived from ``top.name.upper()`` (``firmware.py`` builds
it as ``self.top.name.upper()``), NOT a hard-coded ``NANOSOC_`` — for the
``firmware_soc`` fixture that prefix is ``FIRMWARE_SOC``.
"""

import re
from pathlib import Path

import pytest

from soc_model.backends.firmware import SoCFirmwareBackend

from conftest import FIXTURES, SNAPSHOTS
from helpers import build_module_from_yaml, assert_matches_snapshot

pytestmark = pytest.mark.golden


# Files the firmware backend emits for the firmware_soc fixture, keyed by the
# suffix appended to ``<top.name>``. The two .ld fragments are per-profile.
_PROFILE_LD = ("app_imem", "stage0_bootrom")
_SINGLE_FILES = (
    "memmap.h",
    "memmap.mk",
    "memmap.cmake",
    "adp.vh",
    "adp.py",
)

# Expected linker MEMORY blocks, per profile, derived from the fixture:
#   BOOTROM_RAM_ADDR_W=12 -> 2**12 = 0x1000 (4 KB)
#   IMEM_RAM_ADDR_W=14    -> 2**14 = 0x4000 (16 KB)
#   DMEM_RAM_ADDR_W=14    -> 2**14 = 0x4000, size_adjust -0x400 -> 0x3C00 (15 KB)
# Bases are the canonical interconnect-target bases (address_select: default).
_EXPECTED_MEMORY = {
    "app_imem": {
        "IMEM_0": {"origin": "0x10000000", "length": "0x4000", "attrs": "rwx"},
        "DMEM_0": {"origin": "0x18000000", "length": "0x3C00", "attrs": "rwx"},
    },
    "stage0_bootrom": {
        "BOOTROM_0": {"origin": "0x00000000", "length": "0x1000", "attrs": "rx"},
        "DMEM_0": {"origin": "0x18000000", "length": "0x3C00", "attrs": "rwx"},
    },
}


# ---------------------------------------------------------------------------
# Fixtures: build the firmware_soc model once and render all files to tmp.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def firmware_top():
    """Resolved Module for the firmware_soc fixture (module-scoped, immutable).

    Uses the conftest FIXTURES constant directly (not the function-scoped
    ``fixtures_dir`` fixture) so it can be module-scoped.
    """
    return build_module_from_yaml(FIXTURES, "firmware_soc.yaml")


@pytest.fixture(scope="module")
def firmware_outputs(firmware_top, tmp_path_factory):
    """Render every firmware artifact once; return (top_name, {filename: text})."""
    out_dir = tmp_path_factory.mktemp("firmware_out")
    SoCFirmwareBackend(firmware_top).generate(out_dir)
    name = firmware_top.name
    files = {p.name: p.read_text() for p in sorted(out_dir.iterdir())}
    return name, files


# ---------------------------------------------------------------------------
# Backend smoke: all seven files are emitted and named off top.name.
# ---------------------------------------------------------------------------

def test_emits_expected_file_set(firmware_outputs):
    """All 7 firmware artifacts are produced, each prefixed with <top.name>."""
    name, files = firmware_outputs
    expected = {f"{name}_{p}_memory.ld" for p in _PROFILE_LD}
    expected |= {f"{name}_{s}" for s in _SINGLE_FILES}
    assert set(files) == expected
    # None captured an empty string (an empty golden is worthless).
    for fname, text in files.items():
        assert text.strip(), f"{fname} rendered empty"


# ---------------------------------------------------------------------------
# Golden snapshots (timestamp/copyright stripped by the helper).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("profile", _PROFILE_LD)
def test_linker_ld_golden(firmware_outputs, profile, request):
    name, files = firmware_outputs
    fname = f"{name}_{profile}_memory.ld"
    assert_matches_snapshot(
        files[fname], SNAPSHOTS / "firmware_soc" / fname, request,
    )


@pytest.mark.parametrize("suffix", _SINGLE_FILES)
def test_single_file_golden(firmware_outputs, suffix, request):
    name, files = firmware_outputs
    fname = f"{name}_{suffix}"
    assert_matches_snapshot(
        files[fname], SNAPSHOTS / "firmware_soc" / fname, request,
    )


# ---------------------------------------------------------------------------
# Structural assertions: linker MEMORY base/length (the load-bearing values).
# ---------------------------------------------------------------------------

_MEMORY_LINE_RE = re.compile(
    r"^\s*(?P<name>\w+)\s+\((?P<attrs>[rwx]+)\)\s*:\s*"
    r"ORIGIN\s*=\s*(?P<origin>0x[0-9A-Fa-f]+)\s*,\s*"
    r"LENGTH\s*=\s*(?P<length>0x[0-9A-Fa-f]+)",
    re.MULTILINE,
)


def _parse_memory_block(ld_text):
    """Extract {region_name: {attrs, origin, length}} from a linker .ld."""
    out = {}
    for m in _MEMORY_LINE_RE.finditer(ld_text):
        out[m.group("name")] = {
            "attrs": m.group("attrs"),
            # Normalise to lower-case 0x for comparison stability.
            "origin": "0x" + m.group("origin")[2:].upper(),
            "length": "0x" + m.group("length")[2:].upper(),
        }
    return out


@pytest.mark.parametrize("profile", _PROFILE_LD)
def test_linker_memory_base_and_length(firmware_outputs, profile):
    """Assert each profile's MEMORY{} region has the exact base + length.

    These are the numbers a firmware build links against: a regression here
    silently breaks the boot image, so we pin them independent of the byte
    snapshot.
    """
    name, files = firmware_outputs
    ld = files[f"{name}_{profile}_memory.ld"]
    assert "MEMORY" in ld, "no MEMORY{} block emitted"
    parsed = _parse_memory_block(ld)
    expected = _EXPECTED_MEMORY[profile]
    assert set(parsed) == set(expected), (
        f"{profile}: regions {sorted(parsed)} != expected {sorted(expected)}"
    )
    for region, want in expected.items():
        got = parsed[region]
        assert got["origin"] == want["origin"], f"{profile}/{region} ORIGIN"
        assert got["length"] == want["length"], f"{profile}/{region} LENGTH"
        assert got["attrs"] == want["attrs"], f"{profile}/{region} attrs"


def test_linker_region_aliases_present(firmware_outputs):
    """Each .ld exposes the generic REGION_ALIAS names the section files need."""
    name, files = firmware_outputs
    for profile in _PROFILE_LD:
        ld = files[f"{name}_{profile}_memory.ld"]
        assert 'REGION_ALIAS("FLASH"' in ld
        assert 'REGION_ALIAS("RAM"' in ld
    # FLASH for the bootrom profile must alias the BOOTROM region; RAM -> DMEM.
    boot_ld = files[f"{name}_stage0_bootrom_memory.ld"]
    assert 'REGION_ALIAS("FLASH",   BOOTROM_0)' in boot_ld
    assert 'REGION_ALIAS("RAM",     DMEM_0)' in boot_ld
    # app_imem profile: no read-only region, so FLASH falls back to IMEM_0.
    app_ld = files[f"{name}_app_imem_memory.ld"]
    assert 'REGION_ALIAS("FLASH",   IMEM_0)' in app_ld


# ---------------------------------------------------------------------------
# Structural assertions: C header memmap.h (prefix DERIVED, not hard-coded).
# ---------------------------------------------------------------------------

def test_memmap_h_prefix_and_defines(firmware_top, firmware_outputs):
    """The C header derives its macro prefix from top.name.upper()."""
    name, files = firmware_outputs
    prefix = firmware_top.name.upper()
    assert prefix == "FIRMWARE_SOC"          # sanity for this fixture
    assert prefix != "NANOSOC"               # must NOT be the hard-coded prefix
    h = files[f"{name}_memmap.h"]

    # Include guard derived from the prefix.
    guard = f"{prefix}_MEMMAP_GENERATED_H"
    assert f"#ifndef {guard}" in h
    assert f"#define {guard}" in h
    assert f"#endif /* {guard} */" in h

    # Region BASE/SIZE defines with the exact canonical addresses.
    expected_defines = {
        f"{prefix}_BOOTROM_0_BASE": "0x00000000UL",
        f"{prefix}_BOOTROM_0_SIZE": "0x00001000UL",
        f"{prefix}_IMEM_0_BASE": "0x10000000UL",
        f"{prefix}_IMEM_0_SIZE": "0x00004000UL",
        f"{prefix}_DMEM_0_BASE": "0x18000000UL",
        f"{prefix}_DMEM_0_SIZE": "0x00004000UL",
    }
    for macro, value in expected_defines.items():
        m = re.search(rf"#define\s+{re.escape(macro)}\s+\(({re.escape(value)})\)", h)
        assert m, f"missing/incorrect define {macro} == {value}"


def test_memmap_h_config_macro_uses_top_prefix(firmware_top, firmware_outputs):
    """SYS_CLK_FREQ_HZ is exported as <TOP_UPPER>_SYS_CLK_FREQ_HZ, not NANOSOC_."""
    name, files = firmware_outputs
    prefix = firmware_top.name.upper()
    h = files[f"{name}_memmap.h"]
    macro = f"{prefix}_SYS_CLK_FREQ_HZ"
    # 25 MHz from the fixture param, emitted as a UL literal.
    assert re.search(rf"#define\s+{re.escape(macro)}\s+\(25000000UL\)", h)
    assert "NANOSOC_SYS_CLK_FREQ_HZ" not in h


def test_memmap_h_clock_is_overridable_by_the_build(firmware_top, firmware_outputs, tmp_path):
    """The clock macro sits inside #ifndef, so -D<TOP>_SYS_CLK_FREQ_HZ wins.

    The YAML states the clock the SoC was designed for; an FPGA board runs the
    same SoC at whatever its clocking gives it, and every baud divisor the
    firmware computes comes from this macro. Unwrapped, a -D on the compiler
    command line is silently replaced by the header's value (GCC warns
    "redefined" and uses the later definition), so the console runs at the
    wrong rate. Checked by the preprocessor itself, not by reading text.
    """
    import shutil
    import subprocess
    name, files = firmware_outputs
    prefix = firmware_top.name.upper()
    macro = f"{prefix}_SYS_CLK_FREQ_HZ"
    h = files[f"{name}_memmap.h"]
    assert re.search(rf"#ifndef {re.escape(macro)}\n#define\s+{re.escape(macro)}\s+"
                     rf"\(25000000UL\)[^\n]*\n#endif", h), "clock define is not #ifndef-wrapped"

    cpp = shutil.which("cpp")
    if cpp is None:
        pytest.skip("no C preprocessor on PATH for the behavioural half")
    (tmp_path / "memmap.h").write_text(h)
    probe = tmp_path / "probe.c"
    probe.write_text(f'#include "memmap.h"\nCLK={macro}\n')

    def value(*defs):
        out = subprocess.run([cpp, "-P", "-I", str(tmp_path), *defs, str(probe)],
                             capture_output=True, text=True, check=True).stdout
        return re.search(r"CLK=(.*)", out).group(1).strip()

    assert value() == "(25000000UL)"                           # the YAML's value
    assert value(f"-D{macro}=50000000UL") == "50000000UL"      # the build's value


# ---------------------------------------------------------------------------
# Structural assertions: Makefile / CMake includes.
# ---------------------------------------------------------------------------

def test_memmap_mk_region_and_profile_vars(firmware_outputs):
    name, files = firmware_outputs
    mk = files[f"{name}_memmap.mk"]
    assert "NANOSOC_DEFAULT_CPU := cortex-m0" in mk
    # Region bases/sizes (note: .mk uses raw 0x.. without UL).
    assert re.search(r"^BOOTROM_0_BASE\s*=\s*0x00000000\s*$", mk, re.MULTILINE)
    assert re.search(r"^IMEM_0_BASE\s*=\s*0x10000000\s*$", mk, re.MULTILINE)
    assert re.search(r"^DMEM_0_BASE\s*=\s*0x18000000\s*$", mk, re.MULTILINE)
    # Per-profile RO/RW link bases.
    assert re.search(r"^APP_IMEM_RO_BASE\s*=\s*0x00000000\s*$", mk, re.MULTILINE)
    assert re.search(r"^APP_IMEM_RW_BASE\s*=\s*0x18000000\s*$", mk, re.MULTILINE)
    assert re.search(r"^STAGE0_BOOTROM_RO_BASE\s*=\s*0x00000000\s*$", mk, re.MULTILINE)
    # ADP upload address = imem base.
    assert re.search(r"^ADP_UPLOAD_ADDRESS\s*=\s*0x10000000\s*$", mk, re.MULTILINE)


def test_memmap_cmake_region_and_profile_vars(firmware_outputs):
    name, files = firmware_outputs
    cm = files[f"{name}_memmap.cmake"]
    assert 'set(NanoSoC_DEFAULT_CPU "cortex-m0")' in cm
    assert "set(NanoSoC_REGION_IMEM_0_BASE 0x10000000)" in cm
    assert "set(NanoSoC_REGION_BOOTROM_0_BASE 0x00000000)" in cm
    assert "set(NanoSoC_REGION_DMEM_0_BASE 0x18000000)" in cm
    # Both linker profiles listed.
    assert "set(NanoSoC_LINKER_PROFILES" in cm
    assert "app_imem" in cm
    assert "stage0_bootrom" in cm
    # LD fragment path references the top-name-prefixed .ld files.
    assert f"{name}_app_imem_memory.ld" in cm
    assert f"{name}_stage0_bootrom_memory.ld" in cm
    assert "set(NanoSoC_ADP_UPLOAD_ADDRESS 0x10000000)" in cm


# ---------------------------------------------------------------------------
# Structural assertions: ADP outputs (verilog header + python module).
# ---------------------------------------------------------------------------

def test_adp_vh_upload_address(firmware_outputs):
    name, files = firmware_outputs
    vh = files[f"{name}_adp.vh"]
    # upload_target = imem_0 (default) -> 0x10000000.
    assert "`define ADP_UPLOAD_ADDRESS 32'h10000000" in vh
    # Eight adpbuf nibble comment lines, one per hex digit of the address.
    assert len(re.findall(r"adpbuf\[\d+\] = ", vh)) == 8


def test_adp_py_upload_address(firmware_outputs):
    name, files = firmware_outputs
    py = files[f"{name}_adp.py"]
    assert "ADP_UPLOAD_ADDRESS = 0x10000000" in py


# ---------------------------------------------------------------------------
# No-firmware path: a SoC without a firmware: section is a clean no-op.
# ---------------------------------------------------------------------------

def test_no_firmware_section_is_noop(fixtures_dir, tmp_path):
    """minimal_soc has no firmware: section -> generate() emits nothing."""
    top = build_module_from_yaml(fixtures_dir, "minimal_soc.yaml")
    assert top.firmware is None
    out_dir = tmp_path / "nofw"
    SoCFirmwareBackend(top).generate(out_dir)
    # Backend returns early before creating output_dir or any files.
    assert not out_dir.exists() or not any(out_dir.iterdir())
