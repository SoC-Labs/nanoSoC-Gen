"""Shared Jinja2 environment helper for template backends.

Factors out the import-guard + Environment-construction pattern that was
duplicated across ~6 template backends (docs, toplevel, ahb, system, chip,
soc_config_pkg, ...). The Environment settings here MUST match the ones the
backends used inline (trim_blocks/lstrip_blocks/keep_trailing_newline,
default no-autoescape FileSystemLoader) so output stays byte-identical.
"""

from pathlib import Path
from typing import Optional

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:  # pragma: no cover - exercised only without jinja2
    Environment = None
    FileSystemLoader = None


def have_jinja() -> bool:
    """True if jinja2 is importable (template backends can run)."""
    return Environment is not None


def make_env(template_dir) -> Optional["Environment"]:
    """Build the shared Jinja2 Environment for a template directory.

    Returns None if jinja2 is not installed, so callers keep their existing
    skip-with-warning behaviour. The Environment settings are identical to the
    ones previously inlined in each backend.
    """
    if Environment is None:
        return None
    return Environment(
        loader=FileSystemLoader(str(Path(template_dir))),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
