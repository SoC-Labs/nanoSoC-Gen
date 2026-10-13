# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for SoCSchemaDocBackend (roadmap doc 09 §5.4-5.5).

The backend auto-generates the user-guide §4b field table + the completeness
predicate vocabulary + the artifact-kind inventory by INTROSPECTING the code
(`model.py` dataclasses, `completeness._PREDICATES`, the `build_soc/` tree in
`__main__.py`).  The whole point is that it cannot drift from the code, so the
tests assert against the live code objects — NOT against hard-coded copies.
A field added to a dataclass, or a predicate added to `_PREDICATES`, must show
up in the output or these tests fail.
"""

import dataclasses

import pytest

from soc_model import model
from soc_model import completeness
from soc_model.backends.schema_doc import SoCSchemaDocBackend

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _backend(name="schema_demo"):
    return SoCSchemaDocBackend(model.Module(name=name))


# ===========================================================================
# field reference — introspected, not hard-coded
# ===========================================================================
class TestFieldReference:
    def test_module_fields_all_present_introspected(self):
        """EVERY field of model.Module appears in the rendered reference.

        This is the canary: add a field to Module and forget about it, and the
        loop below fails because it was introspected, not transcribed.
        """
        md = _backend().render_markdown()
        for f in dataclasses.fields(model.Module):
            assert f"`{f.name}`" in md, f"Module field {f.name} missing from output"

    def test_every_schema_group_dataclass_field_present(self):
        """Same canary, but across every schema group (Interface, Clock,
        InterconnectTarget, LinkerRegion, ...)."""
        be = _backend()
        md = be.render_markdown()
        for _yaml_key, cls in be.SCHEMA_GROUPS:
            for f in dataclasses.fields(cls):
                assert f"`{f.name}`" in md, (
                    f"{cls.__name__} field {f.name} missing from reference")

    def test_known_fields_with_types_and_defaults(self):
        """Spot-check load-bearing fields render type + default honestly."""
        groups = {g['dataclass']: g for g in _backend().schema_fields()}
        mod = {f['name']: f for f in groups['Module']['fields']}
        assert mod['gen']['type'] == 'bool'
        assert mod['gen']['default'] == 'False'

        tgt = {f['name']: f for f in groups['InterconnectTarget']['fields']}
        assert tgt['base']['type'] == 'int'
        assert tgt['base']['default'] == '0'
        assert tgt['protocol']['default'] == "'ahb'"

    def test_optional_and_collection_types_render(self):
        groups = {g['dataclass']: g for g in _backend().schema_fields()}
        tgt = {f['name']: f for f in groups['InterconnectTarget']['fields']}
        # Optional[str] should be rendered as Optional, not Union[..., NoneType]
        assert tgt['region_type']['type'].startswith('Optional[')
        ic = {f['name']: f for f in groups['Interconnect']['fields']}
        # list[InterconnectTarget] for the targets field (origin name is
        # 'list' on 3.9+; just assert the element type is carried through)
        assert ic['targets']['type'].lower().startswith('list[')
        assert 'InterconnectTarget' in ic['targets']['type']

    def test_resolved_state_flagged_not_yaml(self):
        """Builder-populated resolved state is flagged so the guide doesn't
        claim a user can author it."""
        groups = {g['dataclass']: g for g in _backend().schema_fields()}
        inst = {f['name']: f for f in groups['Instance']['fields']}
        assert inst['resolved_module']['resolved'] is True
        assert inst['module_name']['resolved'] is False


# ===========================================================================
# completeness predicate vocabulary — straight from the live dict
# ===========================================================================
class TestPredicateVocabulary:
    def test_predicate_list_matches_live_dict_exactly(self):
        be = _backend()
        assert be.predicates() == sorted(completeness._PREDICATES.keys())

    def test_every_predicate_appears_in_markdown(self):
        """Add a predicate to _PREDICATES and it must surface in the doc."""
        md = _backend().render_markdown()
        for p in completeness._PREDICATES:
            assert f"`{p}`" in md, f"predicate {p} missing from reference"

    def test_rule_fields_from_dataclass(self):
        be = _backend()
        expected = [f.name for f in dataclasses.fields(completeness.Rule)]
        assert be.rule_fields() == expected
        md = be.render_markdown()
        for f in expected:
            assert f"`{f}`" in md

    def test_rule_levels_match(self):
        be = _backend()
        assert be.rule_levels() == list(completeness._LEVELS)
        md = be.render_markdown()
        for lvl in completeness._LEVELS:
            assert f"`{lvl}`" in md


# ===========================================================================
# artifact-kind inventory — parsed from the build tree the code lays out
# ===========================================================================
class TestArtifactKinds:
    def test_known_artifact_kinds_listed(self):
        kinds = {a['kind'] for a in _backend().artifact_kinds()}
        # these subdirs are documented in __main__.py's build-tree block
        for expected in ('rtl', 'flist', 'firmware', 'rdl', 'discovery',
                         'interconnect', 'docs', 'reports'):
            assert expected in kinds, f"artifact kind {expected} not discovered"

    def test_artifact_kinds_have_descriptions(self):
        for a in _backend().artifact_kinds():
            assert a['desc'], f"artifact kind {a['kind']} has no description"

    def test_artifact_kinds_in_markdown(self):
        md = _backend().render_markdown()
        for a in _backend().artifact_kinds():
            assert f"`{a['kind']}/`" in md


# ===========================================================================
# file emission
# ===========================================================================
class TestGenerate:
    def test_generate_writes_md_and_json(self, tmp_path):
        out = tmp_path / "docs"
        md = _backend("widget_soc").generate(out)
        assert md.name == "widget_soc_yaml_schema.md"
        assert md.exists() and md.read_text()
        json_path = out / "widget_soc_yaml_schema.json"
        assert json_path.exists() and json_path.read_text()

    def test_generate_no_json_when_disabled(self, tmp_path):
        out = tmp_path / "docs"
        _backend("nojson_soc").generate(out, emit_json=False)
        assert (out / "nojson_soc_yaml_schema.md").exists()
        assert not (out / "nojson_soc_yaml_schema.json").exists()

    def test_markdown_content_matches_render(self, tmp_path):
        be = _backend("match_soc")
        be.generate(tmp_path, emit_json=False)
        on_disk = (tmp_path / "match_soc_yaml_schema.md").read_text()
        assert on_disk == be.render_markdown()

    def test_json_mirrors_introspection(self, tmp_path):
        import json
        be = _backend("json_soc")
        be.generate(tmp_path)
        data = json.loads((tmp_path / "json_soc_yaml_schema.json").read_text())
        assert data['completeness_predicates'] == be.predicates()
        assert data['top'] == "json_soc"
        # schema groups round-trip the dataclass names
        names = {g['dataclass'] for g in data['schema_groups']}
        assert 'Module' in names and 'InterconnectTarget' in names

    def test_idempotent_rerun_no_rewrite(self, tmp_path):
        """write_if_changed means a second identical run does not rewrite."""
        be = _backend("idem_soc")
        be.generate(tmp_path)
        md_path = tmp_path / "idem_soc_yaml_schema.md"
        mtime = md_path.stat().st_mtime_ns
        be.generate(tmp_path)
        assert md_path.stat().st_mtime_ns == mtime


# ===========================================================================
# DOC_SUMMARY contract (so __main__ / artifact table can introspect it)
# ===========================================================================
def test_doc_summary_present():
    assert SoCSchemaDocBackend.DOC_SUMMARY
    assert isinstance(SoCSchemaDocBackend.DOC_SUMMARY, str)
