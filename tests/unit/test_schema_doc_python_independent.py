"""The schema reference is the same under every Python.

Before Python 3.10, typing.Any has no __name__ and str() gives 'typing.Any';
from 3.10 it has one ('Any'). The schema reference (build_soc/docs/
*_yaml_schema.md/.json) printed `typing.Any` / `dict[str, typing.Any]` under
3.8 and `Any` / `dict[str, Any]` under 3.11, so the same render differed by
interpreter (and a committed build_soc failed a --check under the other one).
"""
import typing
from typing import Any, Dict, List, Optional

from soc_model.backends.schema_doc import SoCSchemaDocBackend, _type_str


def test_any_is_any():
    assert _type_str(Any) == "Any"
    assert _type_str(Dict[str, Any]) == "dict[str, Any]"
    assert _type_str(Optional[Any]) == "Optional[Any]"
    assert _type_str(List[Dict[str, Any]]) == "list[dict[str, Any]]"
    assert _type_str(int) == "int" and _type_str(None) == "None"


def test_no_typing_prefix_in_the_reference(tmp_path):
    from soc_model.model import Module
    SoCSchemaDocBackend(Module(name="t", gen=True)).generate(tmp_path)
    for f in tmp_path.iterdir():
        assert "typing." not in f.read_text(), f.name
