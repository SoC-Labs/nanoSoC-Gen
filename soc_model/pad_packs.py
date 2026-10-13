# Copyright 2021-2026 SoC Labs (www.soclabs.org), University of Southampton
# SPDX-License-Identifier: Apache-2.0
"""Pad technology packs: where foundry pad data is found.

The public generator carries the pad-ring MECHANISM (pad_ring.py, the mixed-I/O
template, the .io plan and UPF emitters) and one invented example technology,
``generic`` (lib/pad_tech/generic.yaml, lib/pin_map/generic_*.yaml). Foundry
pad-cell names, port maps and pin maps are not part of it. They live in a pad
pack: a directory laid out as

    <pack>/pad_tech/<tech>.yaml          pad-cell descriptors
    <pack>/pin_map/<tech>_<variant>.yaml pin maps
    <pack>/templates/*.j2                extra templates (e.g. a fixed-pin ring)

and named with ``--pad-tech-dir <pack>`` (repeatable) or the environment
variable ``NANOSOC_PAD_TECH_DIR`` (a ``os.pathsep``-separated list). Command-line
directories come first, then the environment's. A pack's templates are searched
AFTER the generator's own, so a pack can add a template but never replace one.
"""

import os
from pathlib import Path
from typing import Iterable, List, Optional

ENV_VAR = 'NANOSOC_PAD_TECH_DIR'


class PadPackError(Exception):
    """A pad technology, pin map or pad template was asked for and not found."""


def pack_dirs(extra: Optional[Iterable] = None) -> List[Path]:
    """The pad-pack directories: ``extra`` (the command line) first, then
    ``$NANOSOC_PAD_TECH_DIR``. Order kept, duplicates dropped."""
    out: List[Path] = []
    env = os.environ.get(ENV_VAR, '')
    for d in list(extra or []) + [p for p in env.split(os.pathsep) if p]:
        p = Path(d).expanduser().resolve()
        if p not in out:
            out.append(p)
    return out


def template_dirs(extra: Optional[Iterable] = None) -> List[Path]:
    """``<pack>/templates`` of every pack that has one."""
    return [d / 'templates' for d in pack_dirs(extra) if (d / 'templates').is_dir()]


def missing(kind: str, name: str, extra: Optional[Iterable] = None) -> str:
    """The error text for a pad technology / pin map / template not found."""
    dirs = pack_dirs(extra)
    where = (', '.join(str(d) for d in dirs) if dirs
             else f'none (no --pad-tech-dir, ${ENV_VAR} unset)')
    return (f"{kind} '{name}' not found. Foundry pad data is not part of the "
            f"public generator: name a pad pack with --pad-tech-dir <dir> or "
            f"${ENV_VAR} (it holds pad_tech/, pin_map/ and templates/). "
            f"Pad packs searched: {where}. The bundled example technology is "
            f"'generic'.")
