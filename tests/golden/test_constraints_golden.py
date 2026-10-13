# Copyright 2026, SoC Labs (www.soclabs.org)
"""Golden snapshots for the timing-constraints backend (SDC + XDC).

Built from the hermetic tests/fixtures/constraints_soc.yaml (system clock + an
eth RMII IP + a QSPI IP, each with a constraints: block) + the matching
constraints_chip boundary.  Pins the FULL rendered SDC and XDC so future
template / aggregation / scope drift is caught byte-for-byte.

The load-bearing parts are the AGGREGATED per-IP timing (the RMII / QSPI
generated clocks + io-delays + async group, derived by walking
module.constraints across the resolved hierarchy) and the boundary-driven
ASIC pad-cell block (uPAD_SWDIO_IO / uPAD_HOSTIO4_*).  Changing the model or
an IP's constraints: block changes the snapshot.

Regenerate with:  pytest tests/golden/test_constraints_golden.py --snapshot-update
"""

import pytest

from soc_model.parser import SoCParser
from soc_model.builder import SoCBuilder
from soc_model.backends.constraints import SoCConstraintsBackend

from tests.conftest import FIXTURES, SNAPSHOTS
from tests.helpers import assert_matches_snapshot

pytestmark = pytest.mark.golden


def _backend():
    parser = SoCParser(str(FIXTURES), [str(FIXTURES)])
    builder = SoCBuilder(parser)
    top = builder.build_system("constraints_soc.yaml")
    boundary = builder._build_chip_boundary(
        parser.parse_chip_boundary("constraints_chip"))
    return SoCConstraintsBackend(top, boundary)


def test_sdc_golden(request):
    sdc = _backend().render_sdc()
    # structural teeth so a vacuous golden cannot pass
    assert "create_clock -name sys_fclk -period 10.00000" in sdc
    assert "create_clock -name swdclk -period 40.00000" in sdc
    assert "set_false_path -through uPAD_SWDIO_IO" in sdc
    assert "set_max_fanout 10 [all_inputs]" in sdc
    # scope: the FPGA-only IPs must NOT leak into the ASIC SDC
    assert "rmii_ref_clk" not in sdc
    assert_matches_snapshot(
        sdc, SNAPSHOTS / "constraints" / "constraints_soc_constraints.sdc",
        request)


def test_xdc_golden(request):
    xdc = _backend().render_xdc()
    assert ("create_clock -name rmii_ref_clk -period 20.00000 "
            "[get_ports rmii_ref_clk]") in xdc
    assert "create_generated_clock -name mii_rx_clk -divide_by 2" in xdc
    assert "create_generated_clock -name qspi_sclk -divide_by 1" in xdc
    assert "set_clock_groups -name async_sys_rmii -asynchronous" in xdc
    assert "set_false_path -from [get_ports swdio]" in xdc
    assert_matches_snapshot(
        xdc, SNAPSHOTS / "constraints" / "constraints_soc_constraints.xdc",
        request)
