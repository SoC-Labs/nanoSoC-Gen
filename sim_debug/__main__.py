"""CLI entry point for the NanoSoC Simulation Debug Tool.

Usage:
    python -m sim_debug <sim_dir>
    python -m sim_debug simulate/sim/hello --verbose
    python -m sim_debug simulate/sim/hello --vcd waves.vcd --vcd-signals "*HADDR*,*lockup*"
"""

import argparse
import json
import sys

from .config import MemoryMap
from .sim_finder import SimArtifacts
from .report import Report
from .parsers.tarmac import parse_tarmac, TarmacAnalysisState
from .parsers.adp_log import parse_adp_log, parse_adp_from_capture
from .parsers.elf_symbols import SymbolTable
from .analyzers.fault_detector import FaultDetector, Finding
from .analyzers.hang_detector import HangDetector
from .analyzers.memory_checker import MemoryChecker, VectorTableChecker
from .analyzers.uart_checker import UartChecker


def analyze(sim_dir, tarmac=None, log=None, vcd=None, elf=None, memmap_path=None,
            context=20, verbose=False, quick=False, vcd_signals=None,
            use_colour=True, markdown=False):
    """Run the post-simulation analysis on ``sim_dir`` and return a Report.

    The keyword overrides mirror the CLI flags.  Nothing is printed.
    """
    from pathlib import Path

    # Discover simulation artifacts
    artifacts = SimArtifacts(sim_dir)
    artifacts.discover()

    # Apply overrides
    if tarmac:
        artifacts.tarmac = Path(tarmac)
    if log:
        artifacts.sim_log = Path(log)
    if vcd:
        artifacts.vcd = Path(vcd)
    if elf:
        artifacts.elf = Path(elf)
    if memmap_path:
        artifacts.memmap_h = Path(memmap_path)

    # Load memory map
    if artifacts.memmap_h:
        memmap = MemoryMap.from_header(artifacts.memmap_h)
    else:
        memmap = MemoryMap()

    # Load symbols
    symbols = None
    if artifacts.elf:
        symbols = SymbolTable.from_elf(artifacts.elf)

    # Set up report
    report = Report(
        testname=artifacts.testname,
        sim_dir=str(artifacts.sim_dir),
        use_colour=use_colour,
        markdown=markdown,
    )

    # Report discovered artifacts
    artifact_lines = []
    for name, path in [
        ('TARMAC', artifacts.tarmac),
        ('Sim log', artifacts.sim_log),
        ('ADP log', artifacts.adp_log),
        ('VCD', artifacts.vcd),
        ('ELF', artifacts.elf),
        ('Memmap', artifacts.memmap_h),
    ]:
        status = str(path) if path else '(not found)'
        artifact_lines.append(f'{name}: {status}')
    report.add_section('Artifacts', artifact_lines)

    # --- Parse ADP log ---
    adp_result = None
    if artifacts.sim_log:
        adp_result = parse_adp_log(artifacts.sim_log)
    elif artifacts.adp_log:
        adp_result = parse_adp_from_capture(artifacts.adp_log)

    if adp_result:
        outcome_lines = []
        if adp_result.banner:
            outcome_lines.append(f'Banner: {adp_result.banner}')
        if adp_result.remap:
            outcome_lines.append(f'Remap: {adp_result.remap}')
        if adp_result.sim_time_ns:
            outcome_lines.append(f'Sim time: {adp_result.sim_time_ns} ns')
        if adp_result.firmware_lines:
            outcome_lines.append(f'Firmware output:')
            for line in adp_result.firmware_lines:
                outcome_lines.append(f'  {line}')
        report.add_section('Simulation Outcome', outcome_lines)

    # --- Check vector table ---
    if symbols:
        vtc = VectorTableChecker(memmap, symbols)
        vt_findings = vtc.check(symbols)
        report.add_findings(vt_findings)

        if verbose:
            section_lines = []
            for name, (vma, lma, size) in symbols.get_sections().items():
                section_lines.append(f'{name}: VMA=0x{vma:08X} LMA=0x{lma:08X} Size=0x{size:X}')
            if section_lines:
                report.add_section('Firmware Sections', section_lines)

    # --- Analyse TARMAC ---
    if artifacts.tarmac:
        state = TarmacAnalysisState(context_size=max(context, 30))
        fault_det = FaultDetector(memmap, symbols, context)
        mem_check = MemoryChecker(memmap, symbols)
        uart_check = UartChecker(memmap)

        for evt in parse_tarmac(artifacts.tarmac):
            state.update(evt)
            fault_det.check_event(evt, state)
            mem_check.check_event(evt)
            uart_check.check_event(evt)

        # Post-analysis
        fault_det.analyse(state)
        mem_check.finalise(state)
        hang_det = HangDetector(symbols)
        hang_det.analyse(state, adp_result)
        uart_findings = uart_check.analyse(adp_result)

        report.add_findings(fault_det.findings)
        report.add_findings(mem_check.findings)
        report.add_findings(hang_det.findings)
        report.add_findings(uart_findings)

        if verbose:
            report.add_section('Execution Summary', [
                f'Instructions: {state.instruction_count}',
                f'Unique PCs: {len(state.unique_pcs)}',
                f'Final PC: 0x{state.last_pc:08X}' if state.last_pc else 'Final PC: unknown',
                f'Last timestamp: {state.last_timestamp} ns',
            ])
    else:
        report.add_section('TARMAC Analysis', ['No TARMAC trace file found'])

    # --- VCD analysis ---
    if artifacts.vcd and not quick:
        try:
            from .parsers.vcd import VcdParser
            filters = vcd_signals.split(',') if vcd_signals else [
                '*haddr*', '*htrans*', '*hresp*', '*lockup*', '*sleeping*',
            ]
            vcd_parser = VcdParser(artifacts.vcd, signal_filters=filters)
            vcd_parser.parse_header()
            tracked = vcd_parser.get_signal_names()

            if tracked:
                # Find bus errors (HRESP=1)
                bus_errors = []
                for change in vcd_parser.stream_changes():
                    if 'hresp' in change.signal_name.lower() and change.value == 1:
                        bus_errors.append(change)
                        if len(bus_errors) >= 10:
                            break

                vcd_lines = [f'Tracked signals: {len(tracked)}']
                if bus_errors:
                    vcd_lines.append(f'Bus errors (HRESP=1): {len(bus_errors)}')
                    for be in bus_errors[:5]:
                        vcd_lines.append(f'  {be.timestamp} ns: {be.signal_name} = 1')
                    report.add_findings([Finding(
                        Finding.ERROR,
                        f'AHB bus errors detected ({len(bus_errors)} occurrences)',
                        [f'First at {bus_errors[0].timestamp} ns'],
                    )])
                else:
                    vcd_lines.append('No bus errors detected')

                report.add_section('VCD Analysis', vcd_lines)
            else:
                report.add_section('VCD Analysis', ['No matching signals found'])
        except Exception as e:
            report.add_section('VCD Analysis', [f'Error: {e}'])

    report.adp_result = adp_result
    report.artifacts = artifacts
    return report


def main():
    parser = argparse.ArgumentParser(
        description='NanoSoC Simulation Debug Tool — automated post-simulation analysis',
    )
    parser.add_argument('sim_dir', help='Simulation directory (e.g., simulate/sim/hello)')
    parser.add_argument('--tarmac', default=None, help='Override TARMAC trace file path')
    parser.add_argument('--log', default=None, help='Override simulation log file path')
    parser.add_argument('--vcd', default=None, help='Override VCD waveform file path')
    parser.add_argument('--elf', default=None, help='Override firmware ELF file path')
    parser.add_argument('--memmap', default=None, help='Override memory map header (.h) path')
    parser.add_argument('--context', type=int, default=20, help='Instructions before faults (default: 20)')
    parser.add_argument('--no-color', action='store_true', help='Disable ANSI colour output')
    parser.add_argument('--markdown', action='store_true', help='Output as markdown')
    parser.add_argument('--verbose', action='store_true', help='Show all details')
    parser.add_argument('--vcd-signals', default=None,
                        help='Comma-separated VCD signal patterns (e.g., "*HADDR*,*lockup*")')
    parser.add_argument('--quick', action='store_true', help='Skip VCD analysis')
    parser.add_argument('--json', action='store_true', help='Output the report as JSON')
    args = parser.parse_args()


    report = analyze(
        args.sim_dir, tarmac=args.tarmac, log=args.log, vcd=args.vcd, elf=args.elf,
        memmap_path=args.memmap, context=args.context, verbose=args.verbose,
        quick=args.quick, vcd_signals=args.vcd_signals,
        use_colour=not args.no_color, markdown=args.markdown,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        # The JSON verdict is stricter than the text RESULT line (no PASS
        # marker is a FAIL), so the exit code follows the verdict here.
        sys.exit(1 if report.verdict()[0] == 'FAIL' else 0)
    print(report.render())

    # Exit code based on findings
    has_fatal_or_error = any(
        f.severity in (Finding.FATAL, Finding.ERROR)
        for f in report._findings
    )
    sys.exit(1 if has_fatal_or_error else 0)


if __name__ == '__main__':
    main()
