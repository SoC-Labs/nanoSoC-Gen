# Copyright 2026, SoC Labs (www.soclabs.org)
"""Unit tests for the *bundled* component-package library (doc 06a).

Pure-python (no fastapi): walks every ``lib/components/*.yaml`` that ships with
nanosoc_gen and asserts each is a well-formed, droppable IP package — valid
category, consistent knob limits (min <= default <= max), addr_w knobs that
produce selectable size choices, and a non-empty module name. Complements
``test_components.py`` (which exercises the loader/limit machinery on a couple
of fixtures) by guarding the whole shipped catalogue against drift."""

from pathlib import Path

import pytest
import yaml

from soc_model import components as C

pytestmark = pytest.mark.unit

# The bundled lib/components dir that components.py searches first.
BUNDLED = C.BUNDLED_COMPONENTS_DIR


def _bundled_yaml_files():
    """Every non-hidden package YAML shipped in lib/components/."""
    return sorted(f for f in BUNDLED.glob('*.yaml') if not f.name.startswith('.'))


def _bundled_specs():
    """The bundled packages only (load_components([]) searches just BUNDLED)."""
    return C.load_components([])


# ids for parametrize labelling -------------------------------------------------
_FILES = _bundled_yaml_files()
_SPECS = _bundled_specs()


# ---------------------------------------------------------------------------
# the catalogue exists and loads
# ---------------------------------------------------------------------------
class TestBundledLibrary:
    def test_bundled_dir_exists_and_has_packages(self):
        assert BUNDLED.is_dir()
        assert len(_FILES) >= 3, 'expected several bundled component packages'

    def test_every_yaml_file_loads_as_a_spec(self):
        """No bundled YAML is silently dropped by the loader (id+module set)."""
        loaded = _bundled_specs()
        for f in _bundled_yaml_files():
            raw = (yaml.safe_load(f.read_text()) or {}).get('component') or {}
            assert raw.get('id'), '%s: missing component.id' % f.name
            assert raw['id'] in loaded, (
                '%s declares id %r but it did not load' % (f.name, raw['id']))

    def test_ids_are_unique_across_files(self):
        ids = []
        for f in _bundled_yaml_files():
            raw = (yaml.safe_load(f.read_text()) or {}).get('component') or {}
            if raw.get('id'):
                ids.append(raw['id'])
        assert len(ids) == len(set(ids)), 'duplicate component id in lib/components'

    def test_expected_new_packages_present(self):
        """The packages this batch added are all in the bundled catalogue."""
        loaded = _bundled_specs()
        for pid in ('ipc_mailbox', 'dma_controller', 'ptp_hardware_clock',
                    'reset_controller', 'cpu_peripheral_subsystem'):
            assert pid in loaded, 'missing bundled package %r' % pid


# ---------------------------------------------------------------------------
# every spec is well-formed
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('spec', list(_SPECS.values()),
                         ids=list(_SPECS.keys()))
class TestSpecWellFormed:
    def test_category_is_valid(self, spec):
        assert spec.category in C.CATEGORIES, (
            '%s: category %r not in %r' % (spec.id, spec.category, C.CATEGORIES))

    def test_module_name_is_non_empty_string(self, spec):
        assert isinstance(spec.module, str) and spec.module.strip(), (
            '%s: module name must be a non-empty string' % spec.id)

    def test_packaged_flag_and_label(self, spec):
        # authored packages are flagged packaged and carry a friendly label
        assert spec.packaged is True
        assert isinstance(spec.label, str) and spec.label.strip()

    def test_knob_limits_consistent(self, spec):
        for k in spec.knobs:
            assert isinstance(k.param, str) and k.param.strip(), (
                '%s: a knob has an empty param' % spec.id)
            assert k.kind in ('addr_w', 'bytes', 'int', 'enum'), (
                '%s.%s: bad kind %r' % (spec.id, k.param, k.kind))
            lo = k.min
            hi = k.max
            dflt = k.default
            # numeric min/max must order; default must sit within them.
            if lo is not None and hi is not None:
                assert int(lo) <= int(hi), (
                    '%s.%s: min %s > max %s' % (spec.id, k.param, lo, hi))
            if isinstance(dflt, int):
                if lo is not None:
                    assert int(lo) <= dflt, (
                        '%s.%s: default %s below min %s'
                        % (spec.id, k.param, dflt, lo))
                if hi is not None:
                    assert dflt <= int(hi), (
                        '%s.%s: default %s above max %s'
                        % (spec.id, k.param, dflt, hi))

    def test_addr_w_knobs_produce_choices(self, spec):
        """addr_w knobs with limits must offer selectable size choices, each at
        or above the hard minimum (the whole point of the limit)."""
        for k in spec.knobs:
            if k.kind != 'addr_w':
                continue
            ch = k.size_choices()
            assert ch, ('%s.%s: addr_w knob produced no size choices'
                        % (spec.id, k.param))
            for c in ch:
                assert c['bytes'] == (1 << c['value'])
                if k.min is not None:
                    assert c['value'] >= int(k.min), (
                        '%s.%s: choice %s below min %s'
                        % (spec.id, k.param, c['value'], k.min))

    def test_below_min_knob_is_rejected(self, spec):
        """For any knob with a hard min, a value one below it is rejected."""
        for k in spec.knobs:
            if k.min is None:
                continue
            ok, msg = C.check_knob(k, int(k.min) - 1)
            assert not ok and 'minimum' in msg, (
                '%s.%s: value below min was not rejected' % (spec.id, k.param))

    def test_default_params_within_limits(self, spec):
        """A package dropped with its defaults never trips its own limits."""
        for param, val in spec.default_params().items():
            k = spec.knob(param)
            ok, msg = C.check_knob(k, val)
            assert ok, '%s.%s: default %s rejected: %s' % (
                spec.id, param, val, msg)


# ---------------------------------------------------------------------------
# parametrized module-name check (explicitly required by the task)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('spec', list(_SPECS.values()),
                         ids=list(_SPECS.keys()))
def test_package_module_is_non_empty(spec):
    assert spec.module and isinstance(spec.module, str)
