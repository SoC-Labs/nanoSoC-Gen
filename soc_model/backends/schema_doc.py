# Copyright 2026, SoC Labs (www.soclabs.org)
"""Auto-generated schema & artifact reference backend (roadmap doc 09, §5.4-5.5).

The single worst maintenance hazard in the user guide is a YAML field table or
artifact inventory that drifts from the code.  This backend kills that hazard by
INTROSPECTING the live code — the :mod:`soc_model.model` dataclasses (whose
fields *are* the YAML schema), the completeness-rule predicate vocabulary
(:data:`soc_model.completeness._PREDICATES`), and the documented ``build_soc/``
artifact-kind tree — and emitting a Markdown (and optional JSON) reference that
``docs/USER_GUIDE.md`` §4b can transclude.

Nothing here is a hand-maintained list: add a field to ``model.Module`` (or any
schema dataclass), or a predicate to ``_PREDICATES``, and it shows up in the
output automatically.  This is what keeps the guide honest.

Generated files:
  - <module_name>_yaml_schema.md   — Markdown field + predicate + artifact ref
  - <module_name>_yaml_schema.json — machine-readable mirror (optional)
"""

import dataclasses
import json
import re
import typing
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import model
from .. import completeness
from ..utils import write_if_changed

DOC_SUMMARY = "Auto-generated YAML field + completeness-predicate + artifact reference"

_BACKEND_DIR = Path(__file__).resolve().parent
_PKG_DIR = _BACKEND_DIR.parent              # soc_model/
_MAIN_FILE = _PKG_DIR / '__main__.py'


# ---------------------------------------------------------------------------
# The schema groups.  Each entry maps a top-level (or nested) YAML key to the
# dataclass whose fields define its sub-keys.  The Module dataclass IS the
# top-level YAML schema (builder._build_module reads exactly its fields), so we
# enumerate Module first, then the nested element dataclasses keyed by the YAML
# list/section that holds them.
#
# This list is the ONE bit of structure that is hand-curated — it names which
# dataclasses are user-authored schema (vs internal/resolved state).  Every
# *field* within them is still introspected, so a new field can never be
# silently omitted.
# ---------------------------------------------------------------------------
_SCHEMA_GROUPS = [
    ('module',          model.Module),
    ('params',          model.Param),
    ('clocks',          model.Clock),
    ('resets',          model.Reset),
    ('interfaces',      model.Interface),
    ('instances',       model.Instance),
    ('  connections',   model.Connection),
    ('glue_logic',      model.GlueLogicEntry),
    ('assigns',         model.Assign),
    ('srams',           model.SramEntry),
    ('interconnects',   model.Interconnect),
    ('  targets',       model.InterconnectTarget),
    ('  initiators',    model.InterconnectInitiator),
    ('address_decode',  model.AddressDecode),
    ('firmware',        model.Firmware),
    ('linker_profiles', model.LinkerProfile),
    ('  regions',       model.LinkerRegion),
    ('build_info',      model.BuildInfo),
    ('constraints',     model.ConstraintSet),
]

# Fields on the schema dataclasses that are NOT user-authored YAML keys but
# builder-populated resolved state — flagged so the reference is honest about
# what a user can actually write.
_RESOLVED_FIELDS = {
    'source_file', 'resolved_module', 'resolved_register_map', 'inline_interfaces',
}


def _type_str(tp: Any) -> str:
    """Render a (possibly typing-) type annotation as a short readable string."""
    if tp is None:
        return 'None'
    if isinstance(tp, str):
        # PEP 563 / forward-ref string annotation — strip quoting noise.
        return tp.replace("'", '').replace('"', '')
    # typing constructs (Optional[int], List[Clock], Dict[str, Any], ...)
    origin = typing.get_origin(tp)
    if origin is not None:
        args = typing.get_args(tp)
        # Optional[X] is Union[X, None]
        if origin is typing.Union:
            non_none = [a for a in args if a is not type(None)]
            inner = ', '.join(_type_str(a) for a in non_none)
            if len(args) != len(non_none):
                return f'Optional[{inner}]'
            return f'Union[{inner}]'
        name = getattr(origin, '__name__', str(origin))
        if args:
            return f"{name}[{', '.join(_type_str(a) for a in args)}]"
        return name
    # typing.Any has no __name__ before Python 3.10 (str() gives 'typing.Any'),
    # and one after: read its _name, and drop a 'typing.' prefix, so the
    # rendered reference is the same under every Python.
    name = getattr(tp, '__name__', None) or getattr(tp, '_name', None) or str(tp)
    return name[len('typing.'):] if name.startswith('typing.') else name


def _field_default(f: 'dataclasses.Field') -> str:
    """Human string for a dataclass field's default (or default_factory)."""
    if f.default is not dataclasses.MISSING:
        return repr(f.default)
    if f.default_factory is not dataclasses.MISSING:  # type: ignore[attr-defined]
        try:
            return repr(f.default_factory())           # type: ignore[misc]
        except Exception:
            return '<factory>'
    return '(required)'


class SoCSchemaDocBackend:
    """Emit the YAML field / predicate / artifact reference by introspection.

    Follows the existing per-backend shape (``__init__(top_module)`` + a
    ``generate(dir) -> Path``) so it slots into ``__main__.py`` after the docs
    backend.
    """

    DOC_SUMMARY = DOC_SUMMARY

    # Exposed so tests can assert the reference covers exactly the live schema.
    SCHEMA_GROUPS = _SCHEMA_GROUPS

    def __init__(self, top_module: model.Module):
        self.top = top_module

    # -- public API ---------------------------------------------------------
    def generate(self, docs_dir, emit_json: bool = True) -> Path:
        """Render the Markdown reference (and optional JSON) into *docs_dir*."""
        docs_dir = Path(docs_dir)
        docs_dir.mkdir(parents=True, exist_ok=True)

        md_path = docs_dir / f'{self.top.name}_yaml_schema.md'
        write_if_changed(md_path, self.render_markdown())

        if emit_json:
            json_path = docs_dir / f'{self.top.name}_yaml_schema.json'
            write_if_changed(json_path, json.dumps(self.as_dict(), indent=2,
                                                   sort_keys=True) + '\n')
        return md_path

    # -- introspection (the source-of-truth queries) ------------------------
    def schema_fields(self) -> "List[Dict[str, Any]]":
        """One record per (group, dataclass) with its introspected fields."""
        groups = []
        for yaml_key, cls in self._SCHEMA_GROUPS():
            fields = []
            for f in dataclasses.fields(cls):
                fields.append({
                    'name': f.name,
                    'type': _type_str(f.type),
                    'default': _field_default(f),
                    'resolved': f.name in _RESOLVED_FIELDS,
                })
            groups.append({
                'yaml_key': yaml_key.strip(),
                'nested': yaml_key.startswith(' '),
                'dataclass': cls.__name__,
                'doc': (cls.__doc__ or '').strip().splitlines()[0]
                       if cls.__doc__ else '',
                'fields': fields,
            })
        return groups

    def predicates(self) -> List[str]:
        """The completeness-rule predicate vocabulary, straight from the dict."""
        return sorted(completeness._PREDICATES.keys())

    def rule_fields(self) -> List[str]:
        """The fields a completeness Rule may declare."""
        return [f.name for f in dataclasses.fields(completeness.Rule)]

    def rule_levels(self) -> List[str]:
        """The severity levels a rule may take."""
        return list(completeness._LEVELS)

    def artifact_kinds(self) -> "List[Dict[str, str]]":
        """The ``build_soc/<kind>/`` artifact-kind tree, parsed from __main__.

        Derived from the canonical build-tree comment block in ``__main__.py``
        (``build_soc/<kind>/  — <description>``) so it tracks the code that
        actually lays out the tree rather than a parallel hand-list.
        """
        kinds: List[Dict[str, str]] = []
        seen = set()
        try:
            text = _MAIN_FILE.read_text()
        except OSError:
            return kinds
        # Match: "#   build_soc/rtl/   — All generated RTL"  (em dash or hyphen)
        pat = re.compile(
            r'build_soc/(?P<kind>[A-Za-z0-9_]+)/\s*[—-]+\s*(?P<desc>.+?)\s*$')
        for line in text.splitlines():
            m = pat.search(line)
            if not m:
                continue
            kind = m.group('kind')
            if kind in seen:
                continue
            seen.add(kind)
            kinds.append({'kind': kind, 'desc': m.group('desc').strip()})
        return kinds

    # -- serialisation ------------------------------------------------------
    def as_dict(self) -> Dict[str, Any]:
        return {
            'top': self.top.name,
            'schema_groups': self.schema_fields(),
            'completeness_predicates': self.predicates(),
            'completeness_rule_fields': self.rule_fields(),
            'completeness_rule_levels': self.rule_levels(),
            'artifact_kinds': self.artifact_kinds(),
        }

    def render_markdown(self) -> str:
        lines: List[str] = []
        lines.append(f'# YAML Schema & Artifact Reference — `{self.top.name}`')
        lines.append('')
        lines.append('> Auto-generated from the code (`soc_model/model.py` '
                     'dataclasses, `soc_model/completeness.py` predicates, and '
                     'the `build_soc/` tree in `soc_model/__main__.py`). '
                     '**Do not edit by hand** — regenerate.')
        lines.append('')

        # --- Field reference -------------------------------------------------
        lines.append('## 1. YAML field reference')
        lines.append('')
        lines.append('The top-level `module:` mapping and every nested list '
                     'element below correspond 1:1 to a dataclass in '
                     '`model.py`; the table rows are its fields.')
        lines.append('')
        for g in self.schema_fields():
            indent = '  ' if g['nested'] else ''
            heading = '###' if not g['nested'] else '####'
            lines.append(f"{heading} `{g['yaml_key']}` "
                         f"(`{g['dataclass']}`)")
            if g['doc']:
                lines.append('')
                lines.append(g['doc'])
            lines.append('')
            lines.append('| field | type | default | kind |')
            lines.append('|---|---|---|---|')
            for fld in g['fields']:
                kind = 'resolved' if fld['resolved'] else 'yaml'
                lines.append(
                    f"| `{fld['name']}` | `{fld['type']}` | "
                    f"`{fld['default']}` | {kind} |")
            lines.append('')

        # --- Completeness predicates ----------------------------------------
        lines.append('## 2. Completeness-rule predicate vocabulary')
        lines.append('')
        lines.append('A `require:` block in a completeness rules YAML contains '
                     'exactly one of these predicate keywords '
                     '(`soc_model.completeness._PREDICATES`):')
        lines.append('')
        for p in self.predicates():
            lines.append(f'- `{p}`')
        lines.append('')
        lines.append(f"A rule may declare these fields: "
                     f"{', '.join('`%s`' % f for f in self.rule_fields())}.")
        lines.append('')
        lines.append(f"Rule severity `level:` is one of: "
                     f"{', '.join('`%s`' % l for l in self.rule_levels())}.")
        lines.append('')

        # --- Artifact kinds --------------------------------------------------
        lines.append('## 3. Generated artifact kinds')
        lines.append('')
        lines.append('Each generator run lays out `build_soc/` as:')
        lines.append('')
        lines.append('| `build_soc/` dir | contents |')
        lines.append('|---|---|')
        for a in self.artifact_kinds():
            lines.append(f"| `{a['kind']}/` | {a['desc']} |")
        lines.append('')

        return '\n'.join(lines) + '\n'

    # -- internal -----------------------------------------------------------
    def _SCHEMA_GROUPS(self):
        return type(self).SCHEMA_GROUPS
