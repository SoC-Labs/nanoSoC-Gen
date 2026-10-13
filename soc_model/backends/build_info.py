"""Build info backend — generates a register map exposing build metadata.

Produces a read-only RegisterMap that encodes SoC build metadata (version,
git hashes, generation timestamp, developer info, etc.) into registers
accessible by firmware at runtime.

Generated register layout (all 32-bit, read-only):

  Header:
    0x000  BUILD_INFO_ID       — magic 0x42494E46 ("BINF")
    0x004  BUILD_INFO_VERSION  — format version (1)
    0x008  BUILD_INFO_COUNT    — number of user-defined fields

  Per-field (variable number of registers depending on type):
    int32      — 1 register
    timestamp  — 1 register (Unix epoch, auto-captured)
    git_hash   — 1 register (truncated 32-bit SHA)
    string     — ceil(max_length/4) registers (packed ASCII, little-endian)

Output files:
  - build_soc/build_info/nanosoc_build_info.yaml   — YAML register map
  - build_soc/firmware/nanosoc_build_info.h         — C header with offsets
"""

import datetime
import math
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ..model import BuildInfo, BuildInfoField, Module, Register, RegisterMap
from ..utils import write_if_changed


# Magic number: ASCII "BINF" in little-endian
_BUILD_INFO_MAGIC = 0x42494E46

# Current format version
_BUILD_INFO_VERSION = 1


def _encode_string_registers(value: str, max_length: int) -> List[int]:
    """Encode a string into a list of 32-bit register values.

    Packs 4 ASCII bytes per register, little-endian, null-padded.
    """
    padded = (value[:max_length]).ljust(max_length, '\x00')
    regs = []
    for i in range(0, max_length, 4):
        chunk = padded[i:i + 4]
        val = sum(ord(c) << (8 * j) for j, c in enumerate(chunk))
        regs.append(val)
    return regs


def _capture_git_hash(repo_dir: str) -> int:
    """Get truncated 32-bit git hash from a repository directory.

    Returns the first 4 bytes of the HEAD commit SHA as a 32-bit integer.
    Returns 0 on failure (detached HEAD, shallow clone, not a git repo, etc.).
    """
    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=repo_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=5,
        )
        if result.returncode == 0:
            return int(result.stdout.strip()[:8], 16)
    except Exception:
        pass
    return 0


class SoCBuildInfoBackend:
    """Generates build info register maps from the SoC model."""

    def __init__(self, top_module: Module):
        self.top = top_module

    def generate(self, build_dir: Path,
                 project_dir: Optional[str] = None,
                 tool_dir: Optional[str] = None,
                 ) -> Optional[Tuple[RegisterMap, Path]]:
        """Generate build info register map.

        Args:
            build_dir: Build output directory.
            project_dir: Project repo directory for git hash capture.
                         Defaults to current working directory.
            tool_dir: nanosoc_gen tool directory for tool git hash capture.
                      Defaults to the directory containing this file.

        Returns:
            (register_map, yaml_path) tuple, or None if no build_info section.
        """
        if self.top.build_info is None:
            return None

        bi = self.top.build_info

        if project_dir is None:
            project_dir = str(Path.cwd())
        if tool_dir is None:
            tool_dir = str(Path(__file__).resolve().parent.parent)

        registers: List[Register] = []
        offset = 0x000

        # --- Header registers ---
        registers.append(Register(
            name='BUILD_INFO_ID',
            offset=offset,
            width=32,
            access='RO',
            reset_value=_BUILD_INFO_MAGIC,
            desc='Magic number 0x42494E46 ("BINF") identifying a build info block',
        ))
        offset += 4

        registers.append(Register(
            name='BUILD_INFO_VERSION',
            offset=offset,
            width=32,
            access='RO',
            reset_value=_BUILD_INFO_VERSION,
            desc='Build info format version',
        ))
        offset += 4

        registers.append(Register(
            name='BUILD_INFO_FIELD_COUNT',
            offset=offset,
            width=32,
            access='RO',
            reset_value=len(bi.fields),
            desc=f'Number of build info fields ({len(bi.fields)})',
        ))
        offset += 4

        # --- User-defined fields ---
        for fld in bi.fields:
            if fld.type == 'timestamp':
                val = int(time.time())
                registers.append(Register(
                    name=fld.name,
                    offset=offset,
                    width=32,
                    access='RO',
                    reset_value=val,
                    desc=fld.desc or f'Generation timestamp (Unix epoch: {val})',
                ))
                offset += 4

            elif fld.type == 'git_hash':
                if fld.source == 'tool':
                    val = _capture_git_hash(tool_dir)
                else:
                    val = _capture_git_hash(project_dir)
                registers.append(Register(
                    name=fld.name,
                    offset=offset,
                    width=32,
                    access='RO',
                    reset_value=val,
                    desc=fld.desc or f'Git hash (truncated 32-bit): 0x{val:08X}',
                ))
                offset += 4

            elif fld.type == 'int32':
                val = fld.value if isinstance(fld.value, int) else 0
                registers.append(Register(
                    name=fld.name,
                    offset=offset,
                    width=32,
                    access='RO',
                    reset_value=val,
                    desc=fld.desc or fld.name,
                ))
                offset += 4

            elif fld.type == 'string':
                str_val = str(fld.value) if fld.value is not None else ''
                max_len = fld.max_length
                # Ensure max_length is a multiple of 4
                max_len = int(math.ceil(max_len / 4) * 4)
                reg_values = _encode_string_registers(str_val, max_len)
                num_regs = len(reg_values)

                for i, rv in enumerate(reg_values):
                    registers.append(Register(
                        name=f'{fld.name}_{i}',
                        offset=offset,
                        width=32,
                        access='RO',
                        reset_value=rv,
                        desc=(f'{fld.desc} (bytes {i*4}-{i*4+3})'
                              if fld.desc else f'{fld.name} bytes {i*4}-{i*4+3}'),
                    ))
                    offset += 4

        rm_name = f'{self.top.name}_build_info'
        rm = RegisterMap(
            name=rm_name,
            module=f'{self.top.name}_build_info',
            gen=bi.gen,
            desc=(f'Auto-generated build info registers for {self.top.name}. '
                  f'Read-only registers encoding {len(bi.fields)} build metadata fields.'),
            address_width=12,
            data_width=32,
            registers=registers,
            source_file='(generated by soc_model build_info backend)',
        )

        yaml_path = self._write_yaml(rm, build_dir)
        self._write_c_header(rm, build_dir)

        return rm, yaml_path

    def _write_yaml(self, rm: RegisterMap, build_dir: Path) -> Path:
        """Write the register map as a YAML file."""
        bi_dir = build_dir / 'build_info'
        bi_dir.mkdir(parents=True, exist_ok=True)

        yaml_path = bi_dir / f'{rm.name}.yaml'

        data: Dict[str, Any] = {
            'register_map': {
                'name': rm.name,
                'module': rm.module,
                'gen': rm.gen,
                'description': rm.desc,
                'address_width': rm.address_width,
                'data_width': rm.data_width,
                'registers': [],
            }
        }

        for reg in rm.registers:
            reg_dict: Dict[str, Any] = {
                'name': reg.name,
                'offset': f'0x{reg.offset:03X}',
                'width': reg.width,
                'access': reg.access,
                'description': reg.desc,
            }
            if reg.reset_value is not None:
                reg_dict['reset_value'] = f'0x{reg.reset_value:08X}'
            data['register_map']['registers'].append(reg_dict)

        header = (
            f"# Auto-generated build info register map for {self.top.name}\n"
            f"# Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"# DO NOT EDIT — regenerate with soc_model\n\n"
        )
        content = header + yaml.dump(data, default_flow_style=False,
                                     sort_keys=False, width=120)
        write_if_changed(yaml_path, content)
        return yaml_path

    def _write_c_header(self, rm: RegisterMap, build_dir: Path):
        """Write a C header with register offset defines."""
        fw_dir = build_dir / 'firmware'
        fw_dir.mkdir(parents=True, exist_ok=True)

        header_path = fw_dir / f'{rm.name}.h'
        guard = rm.name.upper().replace('-', '_') + '_H'

        lines = [
            f'/* Auto-generated build info register offsets for {self.top.name} */',
            f'/* Generated: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")} */',
            f'/* DO NOT EDIT — regenerate with soc_model */',
            '',
            f'#ifndef {guard}',
            f'#define {guard}',
            '',
        ]

        for reg in rm.registers:
            define_name = f'{rm.name}_{reg.name}'.upper()
            lines.append(f'#define {define_name}_OFFSET  0x{reg.offset:03X}U')
            if reg.reset_value is not None:
                lines.append(f'#define {define_name}_VALUE   0x{reg.reset_value:08X}U')

        lines.append('')
        lines.append(f'#endif /* {guard} */')
        lines.append('')

        write_if_changed(header_path, '\n'.join(lines))
