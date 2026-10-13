"""YAML file parser for SoC system descriptions.

Supports a multi-directory layout:
  - base_dir:  directory containing the top-level SoC YAML file
  - lib_dirs:  one or more directories containing reusable component
               descriptions (modules, register_maps) — typically the
               arch_tech's sys_desc/ directory and any IP-specific
               sys_desc/ directories

Interface protocol definitions (ahb_master, apb_slave, etc.) are
bundled with the nanosoc_gen tool under lib/interfaces/ and are
discovered automatically.

Modules are discovered by recursively scanning each lib_dir for YAML
files containing a 'module:' key. This allows the YAML hierarchy to
mirror the RTL source hierarchy (e.g. subsystems/cpu/, regions/bootrom_0/).
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from .yaml_ints import load_yaml_checked
from . import pad_packs

# The nanosoc_gen lib/interfaces/ directory, relative to this package
_PACKAGE_DIR = Path(__file__).resolve().parent.parent  # nanosoc_gen/
_BUNDLED_INTERFACES_DIR = _PACKAGE_DIR / 'lib' / 'interfaces'
_BUNDLED_PAD_TECH_DIR = _PACKAGE_DIR / 'lib' / 'pad_tech'
_BUNDLED_PIN_MAP_DIR = _PACKAGE_DIR / 'lib' / 'pin_map'
_BUNDLED_CHIP_BOUNDARY_DIR = _PACKAGE_DIR / 'lib' / 'chip_boundary'
_BUNDLED_FPGA_PROFILE_DIR = _PACKAGE_DIR / 'lib' / 'fpga_profile'


class SoCParser:
    """Parses YAML system description files into raw dictionaries."""

    def __init__(self, base_dir: str, lib_dir: Union[str, List[str], None] = None,
                 expander=None, pad_tech_dirs: Optional[List[str]] = None):
        self.base_dir = Path(base_dir)
        # Pad packs named on the command line (--pad-tech-dir); the
        # $NANOSOC_PAD_TECH_DIR ones are read at lookup time (pad_packs.py).
        self.pad_tech_dirs = list(pad_tech_dirs or [])

        # Optional desugar pre-pass (roadmap doc 05). A 1-arg callable applied
        # to every raw module dict before it is cached/returned. Default None
        # -> exactly today's behaviour. When set, it is a strict identity on
        # any module that uses no sugar key, so existing YAML is unchanged.
        self.expander = expander

        # Normalise lib_dir(s) into a list of Paths
        if lib_dir is None:
            self.lib_dirs = [self.base_dir]
        elif isinstance(lib_dir, (list, tuple)):
            self.lib_dirs = [Path(d) for d in lib_dir]
        else:
            self.lib_dirs = [Path(lib_dir)]

        # Keep first lib_dir as primary for backwards compatibility
        self.lib_dir = self.lib_dirs[0]

        self._module_cache: Dict[str, Dict] = {}
        # key -> ordered list of distinct source files claiming it (scan order;
        # the LAST is the last-writer-wins entry in _module_cache). Feeds the
        # lib-dir shadowing auditor (doc 06a #17).
        self._module_sources: Dict[str, List[str]] = {}
        self._modules_scanned = False

    def parse_top_level(self, filename: str) -> Dict[str, Any]:
        """Parse the top-level system YAML file."""
        filepath = self.base_dir / filename
        return self._expand(self._load_yaml(filepath))

    def _expand(self, data):
        """Apply the optional desugar pre-pass (no-op when no expander set)."""
        if self.expander is None or not data:
            return data
        return self.expander(data)

    def parse_module(self, module_name: str) -> Optional[Dict[str, Any]]:
        """Parse a module YAML file by module name.

        Scans all lib_dirs recursively on first call, caching all
        discovered modules by both their module.name and filename stem.
        """
        if not self._modules_scanned:
            self._scan_modules()
            self._modules_scanned = True

        return self._module_cache.get(module_name)

    def _scan_lib_dir(self, lib_dir: Path):
        """Scan a single lib_dir for module YAML files and cache them."""
        search_dirs = []

        # Legacy flat layout: lib_dir/modules/
        modules_dir = lib_dir / 'modules'
        if modules_dir.is_dir():
            search_dirs.append(modules_dir)

        # Hierarchical layout: lib_dir/subsystems/*/ and lib_dir/regions/*/
        for subdir_name in ('subsystems', 'regions'):
            subdir = lib_dir / subdir_name
            if subdir.is_dir():
                for child in subdir.iterdir():
                    if child.is_dir():
                        search_dirs.append(child)

        # IP-local sys_desc directories: lib_dir/../rtl/*/sys_desc/
        rtl_dir = lib_dir.parent / 'rtl'
        if rtl_dir.is_dir():
            for ip_dir in rtl_dir.iterdir():
                if ip_dir.is_dir():
                    ip_sys_desc = ip_dir / 'sys_desc'
                    if ip_sys_desc.is_dir():
                        search_dirs.append(ip_sys_desc)

        # Also scan the lib_dir root itself for YAML files
        search_dirs.append(lib_dir)

        for search_dir in search_dirs:
            for yaml_file in search_dir.glob('*.yaml'):
                if yaml_file.name.startswith('.'):
                    continue
                data = self._load_yaml(yaml_file)
                if data and 'module' in data:
                    data = self._expand(data)   # desugar at discovery (no-op if no expander)
                    mod_data = data['module']
                    name = mod_data.get('name', '')
                    if name:
                        self._module_cache[name] = data
                    # Also cache by filename stem
                    self._module_cache[yaml_file.stem] = data
                    # Record every distinct source file per key for the
                    # shadowing auditor (overlapping lib_dirs can re-scan the
                    # same file — dedupe on resolved path so that isn't a
                    # false collision).
                    rp = str(yaml_file.resolve())
                    for key in ({name} if name else set()) | {yaml_file.stem}:
                        srcs = self._module_sources.setdefault(key, [])
                        if rp not in srcs:
                            srcs.append(rp)

    def _scan_modules(self):
        """Recursively scan all lib_dirs for module YAML files and cache them."""
        for lib_dir in self.lib_dirs:
            self._scan_lib_dir(lib_dir)

    def parse_register_map(self, filename: str) -> Optional[Dict[str, Any]]:
        """Parse a register map YAML file.

        Searches in base_dir and all lib_dirs for the file.
        Accepts paths like 'register_maps/cmsdk_apb_timer.yaml'
        or just 'cmsdk_apb_timer.yaml'.
        """
        # Try base_dir first (for SoC-specific register maps)
        filepath = self.base_dir / filename
        if filepath.exists():
            return self._load_yaml(filepath)

        # Try each lib_dir
        for lib_dir in self.lib_dirs:
            filepath = lib_dir / filename
            if filepath.exists():
                return self._load_yaml(filepath)

            # Try just the filename in lib_dir/register_maps/
            basename = Path(filename).name
            filepath = lib_dir / 'register_maps' / basename
            if filepath.exists():
                return self._load_yaml(filepath)

        print(f"Warning: Register map not found: {filename}")
        return None

    def parse_interface_definition(self, filename: str) -> Optional[Dict[str, Any]]:
        """Parse an interface definition YAML file.

        Searches in base_dir, all lib_dirs, and the bundled interfaces
        directory (nanosoc_gen/lib/interfaces/).
        """
        for search_base in [self.base_dir] + self.lib_dirs:
            filepath = search_base / filename
            if filepath.exists():
                return self._load_yaml(filepath)
            filepath = search_base / 'interfaces' / filename
            if filepath.exists():
                return self._load_yaml(filepath)

        # Fall back to bundled interfaces in nanosoc_gen/lib/interfaces/
        if _BUNDLED_INTERFACES_DIR.is_dir():
            filepath = _BUNDLED_INTERFACES_DIR / filename
            if filepath.exists():
                return self._load_yaml(filepath)

        return None

    def parse_pad_tech(self, name: str) -> Optional[Dict[str, Any]]:
        """Load a pad-tech descriptor by name (e.g. 'generic').

        Searches base_dir and all lib_dirs (both <base>/<name>.yaml and
        <base>/pad_tech/<name>.yaml), then the pad packs (--pad-tech-dir,
        $NANOSOC_PAD_TECH_DIR; pad_packs.py), then the bundled
        nanosoc_gen/lib/pad_tech/ directory.  Returns the raw dict
        (top-level 'pad_tech:' key preserved, mirroring parse_register_map).
        """
        fname = name if name.endswith('.yaml') else f'{name}.yaml'
        packs = pad_packs.pack_dirs(self.pad_tech_dirs)
        for base in [self.base_dir] + self.lib_dirs + packs:
            for cand in (base / fname, base / 'pad_tech' / fname):
                if cand.exists():
                    return self._load_yaml(cand)
        cand = _BUNDLED_PAD_TECH_DIR / fname
        if cand.is_file():
            return self._load_yaml(cand)
        print(f"Warning: pad_tech not found: {name}")
        return None

    def parse_pin_map(self, name: str) -> Optional[Dict[str, Any]]:
        """Load a pin-map by name (e.g. 'generic_multicore').

        Searches base_dir and all lib_dirs (both <base>/<name>.yaml and
        <base>/pin_map/<name>.yaml), then the pad packs (--pad-tech-dir,
        $NANOSOC_PAD_TECH_DIR), then the bundled nanosoc_gen/lib/pin_map/
        directory.  Returns the raw dict (top-level 'pin_map:' key preserved,
        mirroring parse_register_map).
        """
        fname = name if name.endswith('.yaml') else f'{name}.yaml'
        packs = pad_packs.pack_dirs(self.pad_tech_dirs)
        for base in [self.base_dir] + self.lib_dirs + packs:
            for cand in (base / fname, base / 'pin_map' / fname):
                if cand.exists():
                    return self._load_yaml(cand)
        cand = _BUNDLED_PIN_MAP_DIR / fname
        if cand.is_file():
            return self._load_yaml(cand)
        print(f"Warning: pin_map not found: {name}")
        return None

    def parse_chip_boundary(self, name: str) -> Optional[Dict[str, Any]]:
        """Load a chip-boundary spec by name (e.g. 'nanosoc_multicore').

        Searches base_dir and all lib_dirs (both <base>/<name>.yaml and
        <base>/chip_boundary/<name>.yaml), then the bundled
        nanosoc_gen/lib/chip_boundary/ directory.  Returns the raw dict
        (top-level 'chip_boundary:' key preserved, mirroring parse_pin_map).
        """
        fname = name if name.endswith('.yaml') else f'{name}.yaml'
        for base in [self.base_dir] + self.lib_dirs:
            for cand in (base / fname, base / 'chip_boundary' / fname):
                if cand.exists():
                    return self._load_yaml(cand)
        cand = _BUNDLED_CHIP_BOUNDARY_DIR / fname
        if cand.is_file():
            return self._load_yaml(cand)
        print(f"Warning: chip_boundary not found: {name}")
        return None

    def parse_fpga_profile(self, name: str) -> Optional[Dict[str, Any]]:
        """Load an FPGA-profile descriptor by name (e.g. 'nanosoc_multicore').

        Searches base_dir and all lib_dirs (both <base>/<name>.yaml and
        <base>/fpga_profile/<name>.yaml), then the bundled
        nanosoc_gen/lib/fpga_profile/ directory.  Returns the raw dict
        (top-level 'fpga_profile:' key preserved, mirroring parse_pin_map).
        """
        fname = name if name.endswith('.yaml') else f'{name}.yaml'
        for base in [self.base_dir] + self.lib_dirs:
            for cand in (base / fname, base / 'fpga_profile' / fname):
                if cand.exists():
                    return self._load_yaml(cand)
        cand = _BUNDLED_FPGA_PROFILE_DIR / fname
        if cand.is_file():
            return self._load_yaml(cand)
        print(f"Warning: fpga_profile not found: {name}")
        return None

    def list_module_files(self) -> List[str]:
        """List all discovered module names."""
        if not self._modules_scanned:
            self._scan_modules()
            self._modules_scanned = True
        return list(self._module_cache.keys())

    def module_shadows(self) -> Dict[str, List[str]]:
        """Keys (module name / filename stem) claimed by more than one distinct
        file across the lib dirs, mapped to the source files in scan order.
        Discovery is last-writer-wins with no warning, so the LAST file wins;
        the earlier ones are silently shadowed. Feeds the auditor (#17)."""
        if not self._modules_scanned:
            self._scan_modules()
            self._modules_scanned = True
        return {k: list(v) for k, v in self._module_sources.items()
                if len(v) > 1}

    def _load_yaml(self, filepath: Path) -> Dict[str, Any]:
        """Load a YAML file, handling errors gracefully.

        A plain scalar under an integer-typed key that YAML 1.1 reads as
        something else (a leading zero: octal; a colon: base 60) raises
        YamlIntegerError (a ValueError), never a warning: see yaml_ints.py.
        """
        try:
            with open(filepath) as f:
                data = load_yaml_checked(f, str(filepath))
            return data if data else {}
        except FileNotFoundError:
            print(f"Warning: File not found: {filepath}")
            return {}
        except yaml.YAMLError as e:
            print(f"Warning: YAML parse error in {filepath}: {e}")
            return {}
