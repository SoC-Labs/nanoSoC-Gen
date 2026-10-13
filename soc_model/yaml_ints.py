# Copyright 2026, SoC Labs (www.soclabs.org)
"""Integers YAML 1.1 does not read as they are written.

PyYAML implements YAML 1.1, where a plain (unquoted) scalar with a leading
zero is OCTAL and one with colons is BASE 60:

    origin: 020000000    ->  4194304  (0x0040_0000, not 0x0200_0000 or 20,000,000)
    base:   1:00         ->  60
    width:  08           ->  the string '08' (8 is not an octal digit)

Each loads without a message and lands in the address map, a linker script or
a parameter. The generator's own loader is NOT changed (other values, and
other tools reading the same files, keep YAML 1.1 semantics). Instead every
file the parser loads is checked, node by node:

  * a plain scalar YAML 1.1 reads as OCTAL (a leading 0 followed only by octal
    digits: 010, 0100, 0123) or as BASE 60 (an int or float with a colon:
    1:00) is refused under ANY key, and as a mapping key too -- a glue
    `value: 010` rendered 9'd8, a build-info `value: 0100` 0x40, and a
    string-typed parameter default `0123` loaded as the integer 83;
  * a plain scalar with a leading zero that YAML 1.1 keeps as a STRING (08, 09,
    0_8) is refused only under an INTEGER-TYPED key, where it would otherwise
    reach int() later; elsewhere it is the text it looks like.
0 itself, 0x.., 0o.. and 0b.. are never refused; quoting keeps text text.

Integer-typed keys (for the second rule) are taken from the model, so the
list cannot drift from the code: every dataclass field in soc_model.model
annotated with int (int, Optional[int], Dict[str, int], Dict[str, List[int]],
...), plus the fields typed Any that hold an integer or an expression
(phys_size, bit, reset_value), plus parameter values: every value directly
under a `params:` mapping, and the `default` of a `params:` entry whose type
is not a string.
"""

import dataclasses
import re
from typing import List, Optional

import yaml

from . import model

_INT_WORD = re.compile(r"(?<![A-Za-z_])int(?![A-Za-z_])")
_LEADING_ZERO = re.compile(r'^[-+]?0[0-9_]')
_OCTAL = re.compile(r'^[-+]?0[0-7_]*[0-7][0-7_]*$')   # YAML 1.1 int: 0 then octal digits
_ANY_INTEGER_FIELDS = frozenset({'phys_size', 'bit', 'reset_value'})
_STRING_TYPES = frozenset({'str', 'string', 'bool', 'boolean', 'path', 'file'})


def _integer_fields() -> frozenset:
    keys = set(_ANY_INTEGER_FIELDS)
    for obj in vars(model).values():
        if isinstance(obj, type) and dataclasses.is_dataclass(obj):
            for f in dataclasses.fields(obj):
                if _INT_WORD.search(str(f.type)):
                    keys.add(f.name)
    return frozenset(keys)


INTEGER_KEYS = _integer_fields()


class YamlIntegerError(ValueError):
    """A plain scalar under an integer-typed key that YAML 1.1 reinterprets."""


def _reading(raw: str, tag: str) -> str:
    """What YAML 1.1 made of `raw`, for the message."""
    t = raw.replace('_', '')
    try:
        if ':' in t:
            sign = -1 if t.startswith('-') else 1
            v = 0
            for part in t.lstrip('+-').split(':'):
                v = v * 60 + (float(part) if '.' in part else int(part))
            return f"base 60: {sign * v}"
        if tag.endswith(':int'):
            return f"octal: {int(t, 8)} (0x{int(t, 8):X})"
    except ValueError:
        pass
    return f"the {tag.rsplit(':', 1)[-1]} {raw!r}"


def find_misread_integers(node, filename: str = '<yaml>') -> List[str]:
    """Every plain scalar YAML 1.1 reads as octal or base 60 (any key), and
    every leading-zero string under an integer-typed key, one message each."""
    found: List[str] = []

    def check(n: yaml.ScalarNode, where: str, integer: bool):
        if n.style is not None:           # quoted / block: the author chose a string
            return
        raw = n.value.strip()
        octal = n.tag.endswith(':int') and bool(_OCTAL.match(raw))
        b60 = ':' in raw and (n.tag.endswith(':int') or n.tag.endswith(':float'))
        lz_text = (integer and bool(_LEADING_ZERO.match(raw))
                   and re.fullmatch(r'[-+]?[0-9_]+(\.[0-9_]*)?', raw) is not None)
        if octal or b60 or lz_text:
            m = n.start_mark
            found.append(
                f"{filename}:{m.line + 1}:{m.column + 1}: {where}: {raw} is not read as written "
                f"(YAML 1.1 {'colon' if b60 else 'leading zero'} -> {_reading(raw, n.tag)}). "
                f"Write the number as 0x..., 0o..., 0b... or decimal without a leading zero, "
                f"or quote it if it is text.")

    def walk(n, path: List[str], integer: bool):
        if isinstance(n, yaml.ScalarNode):
            check(n, '.'.join(path) or '<root>', integer)
        elif isinstance(n, yaml.SequenceNode):
            for i, v in enumerate(n.value):
                walk(v, path + [f'[{i}]'], integer)
        elif isinstance(n, yaml.MappingNode):
            in_params = bool(path) and path[-1] == 'params'
            # a dict-form parameter: {type: ..., default: ...}
            ptype = None
            if len(path) >= 2 and path[-2] == 'params':
                for k, v in n.value:
                    if isinstance(k, yaml.ScalarNode) and k.value == 'type' \
                            and isinstance(v, yaml.ScalarNode):
                        ptype = v.value.strip().lower()
            for k, v in n.value:
                key = k.value if isinstance(k, yaml.ScalarNode) else '?'
                if isinstance(k, yaml.ScalarNode):
                    check(k, '.'.join(path + [key]) + ' (as a key)', False)
                if in_params and not isinstance(v, yaml.MappingNode):
                    child = True                       # NAME: value
                elif len(path) >= 2 and path[-2] == 'params' and key == 'default':
                    child = ptype not in _STRING_TYPES
                else:
                    child = integer or key in INTEGER_KEYS
                walk(v, path + [key], child)

    walk(node, [], False)
    return found


def load_yaml_checked(stream, filename: str = '<yaml>'):
    """yaml.safe_load(), plus the misread-integer refusal: the document is
    composed once, checked, then constructed exactly as safe_load does."""
    loader = yaml.SafeLoader(stream)
    try:
        node = loader.get_single_node()
        if node is None:
            return None
        errors = find_misread_integers(node, filename)
        if errors:
            raise YamlIntegerError(
                f"{len(errors)} integer value(s) YAML 1.1 does not read as written:\n  "
                + '\n  '.join(errors))
        return loader.construct_document(node)
    finally:
        loader.dispose()
