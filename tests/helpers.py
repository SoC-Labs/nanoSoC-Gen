# Copyright 2026, SoC Labs (www.soclabs.org)
"""Test helpers shared across the nanosoc_gen suite.

These are deliberately thin wrappers over the generator's real seam
(``SoCParser`` -> ``SoCBuilder`` -> ``Module``) plus a snapshot helper that
reuses the generator's OWN timestamp/copyright normalisation
(``soc_model.utils._strip_timestamps``) so a golden that passes here is
byte-stable in a real ``write_if_changed`` build.
"""

from pathlib import Path
from typing import Iterable, Optional, Union

from soc_model.parser import SoCParser
from soc_model.builder import SoCBuilder
from soc_model.model import Module
# Reuse the generator's own normalisation — identical semantics to
# write_if_changed(), so golden comparison matches the real build.
from soc_model.utils import _strip_timestamps

PathLike = Union[str, Path]


def make_parser(base_dir: PathLike,
                lib_dirs: Optional[Iterable[PathLike]] = None) -> SoCParser:
    """Construct a :class:`SoCParser` for a hermetic fixture directory.

    ``base_dir`` is where the top-level SoC YAML lives. ``lib_dirs`` is the
    list of directories scanned for component (region / register_map) YAMLs;
    it defaults to ``[base_dir]`` so a single self-contained fixture
    directory resolves everything with no external libraries.
    """
    if lib_dirs is None:
        lib_list = [str(base_dir)]
    else:
        lib_list = [str(d) for d in lib_dirs]
    return SoCParser(str(base_dir), lib_list)


def build_module_from_yaml(base_dir: PathLike,
                           top_yaml: str,
                           lib_dirs: Optional[Iterable[PathLike]] = None) -> Module:
    """Parse + build a fixture SoC YAML into a resolved :class:`Module`.

    Drives the full ``SoCParser`` -> ``SoCBuilder.build_system`` path, so
    instance ``resolved_module`` references are attached. ``top_yaml`` is the
    *filename* (e.g. ``"minimal_soc.yaml"``) resolved against ``base_dir``.
    """
    parser = make_parser(base_dir, lib_dirs)
    return SoCBuilder(parser).build_system(top_yaml)


def build_module_inline(module: Module, lib_dirs=None) -> Module:
    """Resolve instance references for an in-memory :class:`Module`.

    Convenience for validator/builder unit tests that hand-construct a
    ``Module`` from dataclasses (rather than from YAML) and then want the
    builder's ``_resolve_instances`` pass applied. With no ``lib_dirs`` the
    parser scans nothing, so only ``rtl_module`` (inline-interface) instances
    get a stub ``resolved_module``; YAML-module instances stay unresolved
    (which is exactly the malformed shape several validator rules target).

    Returns the same ``module`` object, mutated in place.
    """
    # base_dir is irrelevant for in-memory modules; use cwd. lib_dirs default
    # to an empty scan so nothing external is pulled in.
    parser = SoCParser(".", [str(d) for d in (lib_dirs or [])] or ["."])
    SoCBuilder(parser)._resolve_instances(module)
    return module


def assert_matches_snapshot(actual: str, snap_path: PathLike, request) -> None:
    """Compare ``actual`` against the committed golden at ``snap_path``.

    Timestamp / copyright lines are stripped from BOTH sides using the
    generator's own ``_strip_timestamps`` before comparison.

    With ``--snapshot-update`` the golden is (re)written and the assertion
    passes — use that to regenerate goldens, then hand-review the diff
    before committing.
    """
    snap_path = Path(snap_path)
    if request.config.getoption("--snapshot-update"):
        snap_path.parent.mkdir(parents=True, exist_ok=True)
        snap_path.write_text(actual)
        return
    assert snap_path.exists(), (
        f"missing golden {snap_path}; run pytest --snapshot-update to create it"
    )
    expected = snap_path.read_text()
    assert _strip_timestamps(actual) == _strip_timestamps(expected), (
        f"output drift vs {snap_path}; review the diff or run "
        f"pytest --snapshot-update"
    )


def arm_free_env() -> dict:
    """``os.environ`` for a generator SUBPROCESS, with the Arm IP library taken out.

    Drops ARM_IP_LIBRARY_PATH and SOCLABS_DMA250_DIR, and every variable whose
    value lies under the Arm tree: ``$ARM_AAA_ROOT`` when set, and the
    directory that holds ``$ARM_IP_LIBRARY_PATH`` when that is set. So no Arm
    script can run with its working directory inside the shared, read-only IP
    tree, whatever the host calls that tree.
    """
    import os
    roots = []
    if os.environ.get("ARM_AAA_ROOT"):
        roots.append(os.environ["ARM_AAA_ROOT"].rstrip("/"))
    if os.environ.get("ARM_IP_LIBRARY_PATH"):
        roots.append(str(Path(os.environ["ARM_IP_LIBRARY_PATH"].rstrip("/")).parent))
    roots = [r for r in roots if r not in ("", "/")]
    return {k: v for k, v in os.environ.items()
            if k not in ("ARM_IP_LIBRARY_PATH", "SOCLABS_DMA250_DIR")
            and not any(r in v for r in roots)}
