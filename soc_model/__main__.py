"""CLI entry point for the SoC Model tool.

Usage:
    python -m soc_model system.yaml --lib-dir <lib_dir>
    python -m soc_model system.yaml --lib-dir <lib_dir> --build-dir build_soc
    python -m soc_model system.yaml --lib-dir <lib_dir1> --lib-dir <lib_dir2>
    python -m soc_model system.yaml --validate-only
    python -m soc_model system.yaml --lib-dir <lib_dir> --system-yaml system_level.yaml
"""

import argparse
import os
import sys
from pathlib import Path

from .parser import SoCParser
from . import pad_packs
from .builder import SoCBuilder
from .validator import SoCValidator, parse_validation_waivers, waiver_key
from .expand import PresetLibrary, make_expander
from .backends.html import SoCVisualizer
from .backends.text import SoCTextBackend
from .backends.python import SoCPythonBackend
from .backends.ahb import SoCAhbBackend
from .backends import cmsdk_mux
from .backends.dma250 import SoCDma250Backend
from .backends.firmware import FirmwareError, SoCFirmwareBackend
from .backends.perf_probe import SoCPerfProbeBackend, inject_perf_probes
from .backends.rdl import SoCRdlBackend
from .backends.discovery import SoCDiscoveryBackend
from .backends.build_info import SoCBuildInfoBackend
from .backends.soc_config_pkg import SoCConfigPkgBackend
from .backends.toplevel import SoCTopLevelBackend, check_mux2_selects
from .backends.system import SoCSystemBackend
from .backends.docs import SoCDocsBackend
from .backends.lint import SoCLintBackend
from .backends.hsel_lint import lint_rtl_dir as lint_hsel_readers, FIX_ADVICE as HSEL_FIX_ADVICE
from .backends.mem_size_guard import (guard_render as guard_mem_sizes, FIX_ADVICE as MEM_FIX_ADVICE,
                                      parse_waivers as parse_mem_waivers)
from .backends.xref_check import (check_rtl as check_xref_wires,
                                  parse_unparsed_waivers as parse_xref_waivers)
from .backends.chip import ChipParamError, SoCChipBackend
from .backends.chip_wrapper import SoCChipWrapperBackend
from .backends.fpga_wrapper import SoCFpgaWrapperBackend
from .backends.pad_ring import SoCPadRingBackend
from .backends.subsystem import SoCSubsystemBackend
from .backends.sram import SoCSramBackend
from .backends.upf import SoCUpfBackend
from .backends.base import GenContext
from .backends.registry import all_backends, ordered, run_pipeline
import soc_model.backends  # noqa: F401  (fires @register_backend decorators)
from .backends.constraints import SoCConstraintsBackend
from .backends.tb import SoCTbBackend
from .backends.schema_doc import SoCSchemaDocBackend
from .backends.vivado_bd import SoCVivadoBdBackend


def _mem_size_guard(top_module, build_dir, args, config_overrides, mem_waivers) -> bool:
    """Run and print the discovery size guard on a rendered build directory;
    True if it fails the run."""
    print("\n--- Discovery size guard ---")
    mem_checks, mem_notes, mem_errors = guard_mem_sizes(
        top_module, build_dir, args.rtl_flist, config_overrides, mem_waivers)
    for n in mem_notes:
        print(f"  NOTE: {n}")
    for c in mem_checks:
        print(f"  {c.line()}", file=sys.stderr if c.fails else sys.stdout)
    for e in mem_errors:
        print(f"  ERROR: {e}", file=sys.stderr)
    mem_bad = [c for c in mem_checks if c.fails]
    count = lambda v: sum(c.verdict == v for c in mem_checks)
    waived = [c for c in mem_checks if c.verdict == 'WAIVED']
    print(f"  {len(mem_checks)} memory target(s): {count('OK')} match, {count('MISMATCH')} "
          f"mismatch, {count('UNRESOLVED')} unresolved, {count('WINDOW')} subsystem window(s), "
          f"{len(waived)} waived"
          + (' [' + '; '.join(f"{c.key}: {c.waiver} (found {c.found})" for c in waived) + ']'
             if waived else '')
          + (f" (--rtl-flist: {len(args.rtl_flist)})" if args.rtl_flist else ''))
    if mem_bad or mem_errors:
        print(f"\nERROR: {count('MISMATCH')} discovery-table size(s) differ from the memory "
              f"compiled behind them and {count('UNRESOLVED')} cannot be established"
              f"{'; ' + str(len(mem_errors)) + ' other error(s)' if mem_errors else ''}. "
              f"{MEM_FIX_ADVICE} A size that cannot be read is an error, never a pass: "
              f"pass the build's file list (--rtl-flist) or waive the one target "
              f"(--mem-size-waive <ic>.<target>=<reason>).", file=sys.stderr)
    return bool(mem_bad or mem_errors)


def _resolve_pad_spec(parser, args):
    """(pad-tech raw dict, pin-map raw dict) for --pad-tech/--pin-count, or
    exit 1 naming what is missing and where it was looked for.

    numeric --pin-count -> '<tech>_<N>pin'; a name (e.g. 'multicore') ->
    '<tech>_<name>' verbatim."""
    pt_raw = parser.parse_pad_tech(args.pad_tech)
    if not pt_raw:
        print(f"ERROR: {pad_packs.missing('pad tech', args.pad_tech, args.pad_tech_dir)}",
              file=sys.stderr)
        sys.exit(1)
    pin_count = args.pin_count
    if not pin_count:
        print(f"ERROR: --pad-tech {args.pad_tech} needs --pin-count (the pin map "
              f"is pin_map/{args.pad_tech}_<N>pin.yaml or {args.pad_tech}_<name>.yaml)",
              file=sys.stderr)
        sys.exit(1)
    pm_name = (f'{args.pad_tech}_{pin_count}pin' if str(pin_count).isdigit()
               else f'{args.pad_tech}_{pin_count}')
    pm_raw = parser.parse_pin_map(pm_name)
    if not pm_raw:
        print(f"ERROR: {pad_packs.missing('pin map', pm_name, args.pad_tech_dir)}",
              file=sys.stderr)
        sys.exit(1)
    return pt_raw, pm_raw


def main():
    argp = argparse.ArgumentParser(
        description='SoC Model — parse, validate, and visualize YAML system descriptions',
    )
    argp.add_argument('yaml_file', nargs='?', default=None,
                      help='Top-level YAML system description file')
    argp.add_argument('--only', default=None,
                      help='Comma-separated registry backend names to run '
                           '(default: all registered). e.g. --only sram,text')
    argp.add_argument('--list-backends', action='store_true',
                      help='Print registered backends in run order, then exit.')
    argp.add_argument('--build-dir', '-b', default=None, help='Build output directory (default: build_soc/)')
    argp.add_argument('--validate-only', action='store_true', help='Only validate, do not generate outputs')
    argp.add_argument('--no-validate', action='store_true', help='Skip validation')
    argp.add_argument('--lib-dir', '-l', action='append', default=None,
                      help='Library directory for component YAMLs (modules, interfaces, register_maps). Can be specified multiple times.')
    argp.add_argument('--arm-ip-library-path', default=None,
                      help='Path to ARM IP library (overrides ARM_IP_LIBRARY_PATH env var)')
    argp.add_argument('--lint', action='store_true', default=False,
                      help='Run slang linter on all design RTL files')
    # HSEL-reader check policy: the set_defaults() below is the ONE line to
    # change to make the check warn-only by default; both flags keep working.
    # Discovery size guard (soc_model/backends/mem_size_guard.py).
    argp.add_argument('--rtl-flist', dest='rtl_flist', action='append', default=[],
                      metavar='FLIST',
                      help='A Verilog file list the design is compiled from '
                           '(repeatable; $(VAR)/${VAR} from the environment). The '
                           'discovery size guard reads the region wrappers and ROMs '
                           'in it, so it can size a boot ROM from its wrapper\'s '
                           'actual HADDR slice. Without one, RAM regions are sized by '
                           'their RAM_ADDR_W and a boot ROM is UNRESOLVED, which '
                           'fails the run.')
    argp.add_argument('--mem-size-waive', dest='mem_size_waive', action='append', default=[],
                      metavar='IC.TARGET=REASON',
                      help='Waive the discovery size guard for ONE memory target '
                           '(repeatable), with the reason, e.g. '
                           'multicore_ahb_interconnect.qspi_flash_xip="off-chip flash". '
                           'The target is still checked and printed with what was '
                           'found; every waiver is listed in the summary. A waiver '
                           'naming no memory target fails the run.')
    argp.add_argument('--mem-size-check-only', dest='mem_size_check_only', action='store_true',
                      help='Render nothing: run the discovery size guard on an EXISTING '
                           'build directory (--build-dir) against the compile list '
                           '(--rtl-flist), then exit 0 or 1. For flows that build the boot '
                           'ROM after soc_model: render (waiving the ROM the firmware has '
                           'not built yet: --mem-size-waive <ic>.<rom>=<why>), build the '
                           'firmware and the ROM, then check with this flag and no waiver.')
    argp.add_argument('--validation-waive', dest='validation_waive', action='append', default=[],
                      metavar='CATEGORY:LOCATION=REASON',
                      help='Let ONE validation error through (repeatable), with the reason, '
                           'named as it prints: "[ERROR] (connection) u_x.PORT: ..." is '
                           'connection:u_x.PORT. Any other validation error stops the '
                           'render before anything is written. Every waiver is listed; a '
                           'waiver naming no error fails the run.')
    argp.add_argument('--xref-unparsed-waive', dest='xref_unparsed_waive', action='append',
                      default=[], metavar='MODULE=REASON',
                      help='Let the cross-instance wire check pass a rendered module it cannot '
                           'parse (its wires go unchecked), with the reason. Without it such '
                           'a module fails the run; a waiver naming no such module fails too.')
    argp.add_argument('--allow-unread-hsel', dest='allow_unread_hsel',
                      action='store_true',
                      help='Report, but do not fail on, a bus-matrix <tgt>_hsel '
                           'that nothing outside the matrix reads, or a module '
                           'input hsel its module never reads (the HSEL-reader '
                           'check; see soc_model/backends/hsel_lint.py). Only for '
                           'reproducing a frozen render.')
    argp.add_argument('--fail-on-unread-hsel', dest='allow_unread_hsel',
                      action='store_false',
                      help='Fail the run on an HSEL-reader finding (the default).')
    argp.set_defaults(allow_unread_hsel=False)   # False: an unread hsel fails the run
    argp.add_argument('--lint-flist', default=None,
                      help='Filelist (.flist) for design RTL sources to lint')
    argp.add_argument('--slang-bin', default=None,
                      help='Path to slang binary (default: auto-detect from PATH)')
    argp.add_argument('--slang-args', default=None,
                      help='Extra arguments to pass to slang (space-separated)')
    argp.add_argument('--system-yaml', default=None,
                      help='System-level YAML description (generates system wrapper with expansion region)')
    argp.add_argument('--config-override', action='append', default=[],
                      help='Config parameter override as KEY=VALUE (can be repeated)')
    argp.add_argument('--allow-param-mismatch', action='store_true', default=False,
                      help='Report, but do not fail on, a parameter the top YAML and the '
                           '--system-yaml disagree on that the chip does not pass to '
                           'u_system (the RTL elaborates the system value). Default: fail.')
    argp.add_argument('--pad-tech', default=os.environ.get('PAD_TECH'),
                      help='Pad technology for the pad-ring wrapper (e.g. generic; foundry techs come from a pad pack, --pad-tech-dir). '
                           'When given (with --system-yaml), emits a <chip>_pads_<tech>_<N>pin.v ring.')
    argp.add_argument('--pin-count', default=os.environ.get('PIN_COUNT'),
                      help='Pin-count variant for the pad ring (e.g. 44, or a name '
                           'such as multicore). Selects pin_map/<tech>_<N>pin.yaml '
                           '(or <tech>_<name>.yaml).')
    argp.add_argument('--pad-tech-dir', action='append', default=[],
                      help='A pad pack directory (pad_tech/, pin_map/, templates/) '
                           'to search for --pad-tech and its pin map; repeatable. '
                           'Also read from $NANOSOC_PAD_TECH_DIR (os.pathsep list). '
                           'Foundry pad data is not bundled; the example tech is '
                           '"generic". A --pad-tech that is not found is an error.')
    argp.add_argument('--chip-boundary', default=os.environ.get('CHIP_BOUNDARY'),
                      help='Chip-boundary spec name (e.g. nanosoc_multicore). When '
                           'given, emits <chip_module>.v wrapping the SoC top: bonded '
                           'pads re-exposed, tied inputs constant-driven, opens left '
                           'unconnected. Selects lib/chip_boundary/<name>.yaml.')
    argp.add_argument('--emit-upf', action='store_true',
                      default=bool(os.environ.get('EMIT_UPF')),
                      help='Emit the pad-ring UPF (power intent) alongside the '
                           'pad ring when --pad-tech carries a upf: block '
                           '(doc-07 M5). A upf: block in the pad-tech also '
                           'auto-enables this; the flag forces it.')
    argp.add_argument('--emit-constraints', action='store_true',
                      default=bool(os.environ.get('EMIT_CONSTRAINTS')),
                      help='Emit TIMING constraints: <top>_constraints.sdc '
                           '(Synopsys/ASIC) + <top>_constraints.xdc (Xilinx/'
                           'Vivado FPGA), aggregated from the model clocks + '
                           'each module constraints: block. Uses --chip-boundary '
                           '(if given) for pad-cell + boundary-classified '
                           'io-delays. Pin placement stays project-level.')
    argp.add_argument('--emit-testbench', action='store_true',
                      default=bool(os.environ.get('EMIT_TESTBENCH')),
                      help='Emit a generated cocotb verification env '
                           '(tb_top.sv + Makefile + model-derived default '
                           'boot/reg/mem/IRQ tests) into build_soc/verification/.')
    argp.add_argument('--emit-vivado-bd', action='store_true',
                      default=bool(os.environ.get('EMIT_VIVADO_BD')),
                      help='Emit Vivado IP-XACT packaging TCL + an optional '
                           'bare block design (clk/reset/uart + promoted buses '
                           'typed as IP-XACT interfaces) into build_soc/fpga/.')
    argp.add_argument('--fpga-wrapper', default=os.environ.get('FPGA_WRAPPER'),
                      nargs='?', const=True,
                      help='Emit the synthesizable FPGA Vivado IP wrapper for the '
                           'SoC top (doc-07 M4). Reuses the --chip-boundary spec for '
                           'the bonded/tied/open topology and the matching FPGA '
                           'profile (lib/fpga_profile/<name>.yaml) for the bespoke '
                           'board layer. Pass a name to override the profile/boundary '
                           'name; bare --fpga-wrapper reuses --chip-boundary.')
    args = argp.parse_args()

    # --list-backends: print the registered pipeline in run order and exit.
    if args.list_backends:
        for cls in ordered():
            after = f" after={','.join(cls.after)}" if cls.after else ""
            print(f"  {cls.name:<12s} phase={cls.phase.name}{after}")
        sys.exit(0)

    if not args.yaml_file:
        argp.error('yaml_file is required (unless --list-backends)')
    if args.mem_size_check_only and (args.validate_only or args.only):
        argp.error('--mem-size-check-only renders nothing: it takes neither --validate-only nor --only')
    try:
        mem_waivers = parse_mem_waivers(args.mem_size_waive)
        val_waivers = parse_validation_waivers(args.validation_waive)
        xref_waivers = parse_xref_waivers(args.xref_unparsed_waive)
    except ValueError as e:
        argp.error(str(e))

    # Selection for the registry-driven report backends.
    only_selection = (set(s for s in args.only.split(',') if s)
                      if args.only else None)

    # Parse config overrides into a dict
    config_overrides = {}
    for ov in args.config_override:
        if '=' in ov:
            k, v = ov.split('=', 1)
            try:
                config_overrides[k] = int(v, 0)
            except ValueError:
                config_overrides[k] = v

    yaml_path = Path(args.yaml_file).resolve()
    base_dir = yaml_path.parent
    lib_dirs = [Path(d).resolve() for d in args.lib_dir] if args.lib_dir else None

    # --- Parse ---
    print(f"Parsing {yaml_path.name}...")
    if lib_dirs:
        for ld in lib_dirs:
            print(f"  Library: {ld}")
    # Optional YAML desugar pre-pass (roadmap doc 05). Presets are discovered
    # from the bundled lib/presets/ plus any <lib-dir>/presets/. The expander
    # is a strict identity on sugar-free YAML, so default output is unchanged.
    _presets_dir = Path(__file__).resolve().parent.parent / 'lib' / 'presets'
    preset_dirs = [_presets_dir] + ([Path(d) / 'presets' for d in lib_dirs] if lib_dirs else [])
    presets = PresetLibrary.from_dirs(preset_dirs)
    if len(presets):
        print(f"  Presets: {len(presets)} ({', '.join(presets.names())})")
    parser = SoCParser(
        str(base_dir),
        [str(d) for d in lib_dirs] if lib_dirs else None,
        expander=make_expander(presets),
        pad_tech_dirs=args.pad_tech_dir,
    )
    builder = SoCBuilder(parser)

    # A pad ring that was asked for must be found BEFORE anything is written:
    # a missing pad tech or pin map used to print "Skipped" and exit 0, so a
    # build without its pad data looked complete.
    pad_spec = None
    if (args.pad_tech or '').strip().lower() in ('', 'none'):
        args.pad_tech = None          # PAD_TECH=none: RTL only (as the flows use it)
    if args.pad_tech:
        pad_spec = _resolve_pad_spec(parser, args)

    try:
        top_module = builder.build_system(yaml_path.name)
    except Exception as e:
        print(f"Error building model: {e}", file=sys.stderr)
        sys.exit(1)

    # Inject performance-probe harnesses (no-op unless an interconnect declares
    # perf_probes: + probed ports). Must run before the AHB/system/top backends
    # so the injected read-out target + harness instance are rendered.
    n_probe_harness = inject_perf_probes(top_module)
    if n_probe_harness:
        print(f"  Perf-probe harnesses injected: {n_probe_harness}")

    print(f"  Module: {top_module.name}")
    print(f"  Params: {len(top_module.params)}")
    print(f"  Interfaces: {len(top_module.interfaces)}")
    print(f"  Instances: {len(top_module.instances)}")
    print(f"  Assigns: {len(top_module.assigns)}")
    print(f"  Glue logic: {len(top_module.glue_logic)}")
    print(f"  Internal wires: {len(top_module.internal_wires)}")
    print(f"  Interconnects: {len(top_module.interconnects)}")

    resolved = sum(1 for i in top_module.instances if i.resolved_module)
    print(f"  Resolved modules: {resolved}/{len(top_module.instances)}")

    if args.mem_size_check_only:
        # Post-firmware check: nothing is rendered or written. The rendered
        # tables and RTL are the ones a previous render left in --build-dir.
        build_dir = Path(args.build_dir) if args.build_dir else base_dir / 'build_soc'
        missing = [d for d in ('discovery', 'rtl') if not (build_dir / d).is_dir()]
        if missing:
            print(f"\nERROR: --mem-size-check-only: {build_dir} has no "
                  f"{' or '.join(d + '/' for d in missing)}: render it first "
                  f"(the same YAML, without this flag).", file=sys.stderr)
            sys.exit(1)
        print(f"\nBuild directory (read only): {build_dir}")
        sys.exit(1 if _mem_size_guard(top_module, build_dir, args, config_overrides,
                                      mem_waivers) else 0)

    # --- Validate ---
    messages = []
    if not args.no_validate:
        print("\nValidating...")
        validator = SoCValidator(top_module)
        messages = validator.validate_all()

        for msg in messages:
            print(f"  {msg}")

        print(f"\n  {len(validator.errors)} error(s), {len(validator.warnings)} warning(s)")

        # Validation errors stop the render (Train 7 G8; they used to be
        # printed and generation went on). One error passes only when a
        # --validation-waive names it, with a reason; a waiver naming no
        # error is refused (stale or misspelt).
        unwaived = [m for m in validator.errors if waiver_key(m) not in val_waivers]
        found_keys = {waiver_key(m) for m in validator.errors}
        stale = sorted(k for k in val_waivers if k not in found_keys)
        for k in sorted(val_waivers):
            if k in found_keys:
                n = sum(waiver_key(m) == k for m in validator.errors)
                print(f"  WAIVED validation error {k} ({n}): {val_waivers[k]}")
        for k in stale:
            print(f"  ERROR: --validation-waive {k}: no validation error of that name",
                  file=sys.stderr)

        # Unknown keys on a bus-matrix interconnect: noted here only, so the
        # generated reports (which embed validator messages) do not change.
        for _, ic in cmsdk_mux.iter_generated_interconnects(top_module):
            if cmsdk_mux.ic_impl(ic) != 'cmsdk_mux':
                for note in cmsdk_mux.check_interconnect_keys(ic)[1]:
                    print(f"  note: {note}", file=sys.stderr)

        if args.validate_only:
            sys.exit(1 if unwaived or stale else 0)

        # impl: cmsdk_mux refuses what it cannot build instead of writing a
        # wrong core: stop before anything is generated, and delete a core an
        # earlier run left behind. Errors that concern only bus-matrix
        # interconnects keep their old (non-blocking) behaviour.
        blocking = cmsdk_mux.blocking_errors(top_module, validator.errors)
        if blocking:
            build_dir = Path(args.build_dir) if args.build_dir else base_dir / 'build_soc'
            print(f"\nERROR: refusing to generate: {len(blocking)} interconnect "
                  f"error(s):", file=sys.stderr)
            for msg in blocking:
                print(f"  {msg}", file=sys.stderr)
            for ic_name in sorted({m.instance for m in blocking if m.instance}):
                for p in cmsdk_mux.remove_stale_outputs(ic_name, build_dir):
                    print(f"  Removed stale {p} (interconnect {ic_name} refused)",
                          file=sys.stderr)
            print("Nothing was generated.", file=sys.stderr)
            sys.exit(1)

        if unwaived or stale:
            print(f"\nERROR: refusing to generate: {len(unwaived)} validation error(s)"
                  + (f" and {len(stale)} --validation-waive naming no error" if stale else '')
                  + ":", file=sys.stderr)
            for msg in unwaived:
                print(f"  {msg}", file=sys.stderr)
            print("Fix the YAML, or, for an error known not to reach the RTL, waive it "
                  "by name with the reason: --validation-waive <category>:<location>=<reason>"
                  + (f" (e.g. {waiver_key(unwaived[0])}=<reason>)" if unwaived else '') + ".",
                  file=sys.stderr)
            print("Nothing was generated.", file=sys.stderr)
            sys.exit(1)
    elif val_waivers:
        argp.error('--validation-waive with --no-validate: nothing is validated, so nothing can be waived')

    # --- System model, and the chip/system parameter check ---
    # Built BEFORE any backend writes: a top/system parameter mismatch the chip
    # cannot resolve refuses the run with the build directory untouched, not
    # after the firmware and the config package have been rewritten beside an
    # old chip.
    system_module = None
    system_yaml_path = None
    if args.system_yaml:
        print("\n--- System Model ---")
        system_yaml_path = Path(args.system_yaml).resolve()
        system_base_dir = system_yaml_path.parent

        print(f"  Parsing {system_yaml_path.name}...")
        system_parser = SoCParser(str(system_base_dir), [str(d) for d in lib_dirs] if lib_dirs else None)
        system_builder = SoCBuilder(system_parser)

        try:
            system_module = system_builder.build_system(system_yaml_path.name)
        except Exception as e:
            print(f"  Error building system model: {e}", file=sys.stderr)
            system_module = None

        if system_module:
            print(f"  Module: {system_module.name}")
            print(f"  Instances: {len(system_module.instances)}")
            chip_backend = SoCChipBackend(
                system_module, top_module, config_overrides=config_overrides,
                allow_param_mismatch=args.allow_param_mismatch,
                yaml_paths={'core': str(yaml_path), 'system': str(system_yaml_path)})
            try:
                chip_backend.check_params()
            except ChipParamError as e:
                print(f"\nERROR: chip: {e}", file=sys.stderr)
                print("Nothing was generated.", file=sys.stderr)
                sys.exit(1)

    # --- Firmware linker profiles, checked before anything is written ---
    # A region with an invalid `origin:` (or one that runs past 4 GiB) refuses
    # the run with build_soc/ untouched. Only FirmwareError refuses here: any
    # other failure is left to the firmware stage itself, exactly as before.
    try:
        SoCFirmwareBackend(top_module, config_overrides=config_overrides).check_linker()
    except FirmwareError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        print("Nothing was generated.", file=sys.stderr)
        sys.exit(1)
    except Exception:
        pass

    # --- mux2 glue selects, checked before anything is written ---
    # soc_glue_mux2 selects at BUILD time (parameter SEL): a select that is not
    # a literal 0/1 or a declared parameter would be a runtime mux the cell
    # cannot build. Refused with build_soc/ untouched.
    mux2_errors = check_mux2_selects(top_module, config_overrides)
    if mux2_errors:
        print(f"\nERROR: {len(mux2_errors)} mux2 glue select(s) not a build-time "
              f"constant:", file=sys.stderr)
        for msg in mux2_errors:
            print(f"  {msg}", file=sys.stderr)
        print("Nothing was generated.", file=sys.stderr)
        sys.exit(1)

    # --- Build directory structure ---
    # Layout:
    #   build_soc/rtl/              — All generated RTL
    #   build_soc/flist/            — All file lists
    #   build_soc/firmware/         — Firmware configuration files
    #   build_soc/rdl/              — Register description files
    #   build_soc/discovery/        — Device discovery YAML tables
    #   build_soc/interconnect/     — Non-RTL interconnect artifacts (xml, ipxact, logs, address_maps)
    #   build_soc/docs/             — Design documentation
    #   build_soc/reports/          — Analysis reports (HTML, text, Python model)
    build_dir = Path(args.build_dir) if args.build_dir else base_dir / 'build_soc'
    rtl_dir = build_dir / 'rtl'
    flist_dir = build_dir / 'flist'
    reports_dir = build_dir / 'reports'

    rtl_dir.mkdir(parents=True, exist_ok=True)
    flist_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nBuild directory: {build_dir}")

    # --- Performance-probe harness generation ---
    # Renders <prefix>_perf_probe_harness.sv + its flist for each interconnect
    # that declared perf_probes (and had probed ports injected above). Strict
    # no-op otherwise. Runs after build_dir exists; harness is a standalone
    # module so generation order vs the RTL backends does not matter.
    perf_probe_backend = SoCPerfProbeBackend(top_module)
    perf_probe_generated = perf_probe_backend.generate_all(build_dir)
    if perf_probe_generated:
        print(f"  Generated {len(perf_probe_generated)} perf-probe harness(es):")
        for ic_name, harness_path in perf_probe_generated:
            print(f"    {ic_name}: {harness_path}")

    # --- Reports (HTML, text maps, Python model) ---
    # These four leaf backends now run through the registry (roadmap doc 02,
    # M3) at exactly this point in the flow. The print lines below preserve the
    # legacy console output; run_pipeline emits the identical files. The rest of
    # main() keeps its existing direct calls (mixed mode).
    print("\n--- Analysis Reports ---")

    html_path = reports_dir / f"{top_module.name}_connectivity.html"
    json_path = reports_dir / f"{top_module.name}_model_view.json"
    memmap_path = reports_dir / f"{top_module.name}_memory_map.txt"
    hierarchy_path = reports_dir / f"{top_module.name}_hierarchy.txt"
    py_path = reports_dir / f"{top_module.name}_model.py"
    sram_path = reports_dir / f"{top_module.name}_sram_report.txt"
    print(f"  HTML: {html_path}")
    print(f"  Model-view JSON: {json_path}")
    print(f"  Text memory map: {memmap_path}")
    print(f"  Text hierarchy:  {hierarchy_path}")
    print(f"  Python model: {py_path}")
    print(f"  SRAM report:  {sram_path}")

    report_ctx = GenContext(
        top=top_module, build_dir=build_dir, rtl_dir=rtl_dir,
        flist_dir=flist_dir, reports_dir=reports_dir, system=None,
        messages=messages,
        opts={
            'arm_ip_library_path': args.arm_ip_library_path,
            'config_overrides': config_overrides,
            'slang_bin': args.slang_bin,
            'lint': args.lint,
        },
    )
    report_selection = {'sram', 'text', 'python', 'html'}
    if only_selection is not None:
        report_selection &= only_selection
    run_pipeline(report_ctx, report_selection)

    # --- AHB interconnect generation ---
    print("\n--- AHB Interconnect Generation ---")
    ahb_backend = SoCAhbBackend(top_module, arm_ip_library_path=args.arm_ip_library_path)
    try:
        generated = ahb_backend.generate_all(build_dir)
    except cmsdk_mux.CmsdkMuxError as e:
        # reached with --no-validate; the backend has removed any stale core
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if generated:
        print(f"\n  Generated {len(generated)} interconnect(s):")
        for ic_name, ic_rtl_dir in generated:
            print(f"    {ic_name}: {ic_rtl_dir}")
    else:
        print("  No interconnects with gen: True found")

    # --- DMA-250 render generation ---
    print("\n--- DMA-250 Render Generation ---")
    dma250_backend = SoCDma250Backend(top_module, arm_ip_library_path=args.arm_ip_library_path)
    dma250_generated = dma250_backend.generate_all(build_dir)
    if dma250_generated:
        print(f"\n  Rendered {len(dma250_generated)} DMA-250 config(s):")
        for cfg_name, out_dir in dma250_generated:
            print(f"    {cfg_name}: {out_dir}")

    # --- Subsystem module generation ---
    print("\n--- Subsystem Module Generation ---")
    subsystem_results = SoCSubsystemBackend.generate_all(top_module, build_dir)
    if subsystem_results:
        print(f"\n  Generated {len(subsystem_results)} subsystem(s):")
        for sub_name, sub_path in subsystem_results:
            print(f"    {sub_name}: {sub_path}")
    else:
        print("  No subsystems with gen: True found")

    # --- RDL register description generation ---
    print("\n--- RDL Register Description Generation ---")
    rdl_backend = SoCRdlBackend(top_module)
    rdl_generated = rdl_backend.generate_all(build_dir)

    if rdl_generated:
        print(f"\n  Generated {len(rdl_generated)} register map(s):")
        for rm_name, rdl_path, rtl_gen in rdl_generated:
            status = " (RTL generated)" if rtl_gen else ""
            print(f"    {rm_name}: {rdl_path}{status}")
    else:
        print("  No register maps found in the design hierarchy")

    # --- Device Discovery Table ---
    print("\n--- Device Discovery Table ---")
    discovery_backend = SoCDiscoveryBackend(top_module, config_overrides=config_overrides)
    discovery_results = discovery_backend.generate(build_dir)

    if discovery_results:
        print(f"\n  Generated {len(discovery_results)} discovery table(s):")
        for ic_name, disc_rm, disc_yaml_path in discovery_results:
            print(f"    {ic_name}: {disc_yaml_path}")
            # Generate RDL (and optional RTL) for the discovery register map
            rdl_path, rtl_gen = rdl_backend.generate_single(disc_rm, build_dir)
            status = " (RTL generated)" if rtl_gen else ""
            print(f"    RDL: {rdl_path}{status}")
    else:
        print("  No interconnects with gen: True found")

    # --- Build Info Registers ---
    print("\n--- Build Info Registers ---")
    build_info_backend = SoCBuildInfoBackend(top_module)
    # Derive project directory from yaml path (project root)
    project_dir = str(yaml_path.parent.parent.parent)
    tool_dir = str(Path(__file__).resolve().parent.parent)
    build_info_result = build_info_backend.generate(
        build_dir, project_dir=project_dir, tool_dir=tool_dir,
    )

    if build_info_result:
        bi_rm, bi_yaml_path = build_info_result
        print(f"  Build info: {bi_yaml_path}")
        rdl_path, rtl_gen = rdl_backend.generate_single(bi_rm, build_dir)
        status = " (RTL generated)" if rtl_gen else ""
        print(f"  RDL: {rdl_path}{status}")
    else:
        print("  No build_info section found, skipping")

    # --- SoC Config Package ---
    print("\n--- SoC Config Package ---")
    config_pkg_backend = SoCConfigPkgBackend(top_module)
    config_pkg_path = config_pkg_backend.generate(
        rtl_dir, flist_dir, config_overrides=config_overrides,
    )
    if config_pkg_path:
        print(f"  Generated: {config_pkg_path}")
    else:
        print("  Skipped (jinja2 not available)")

    # --- Top-level module generation ---
    print("\n--- Top-Level Module Generation ---")
    toplevel_backend = SoCTopLevelBackend(top_module)
    toplevel_path = toplevel_backend.generate(rtl_dir, flist_dir)
    if toplevel_path:
        print(f"  Generated: {toplevel_path}")
    else:
        print("  Skipped (jinja2 not available)")

    # --- Firmware configuration ---
    print("\n--- Firmware Configuration ---")
    fw_backend = SoCFirmwareBackend(top_module, config_overrides=config_overrides)
    fw_dir = build_dir / 'firmware'
    fw_backend.generate(fw_dir)

    # --- Testbench generation (optional, --emit-testbench) ---
    if args.emit_testbench:
        print("\n--- Testbench Generation ---")
        tb_path = SoCTbBackend(top_module).generate(
            build_dir / 'verification', config_overrides=config_overrides)
        if tb_path:
            print(f"  Generated: {tb_path}")
        else:
            print("  Skipped (jinja2 not available)")

    # --- System wrapper generation (optional; the model was built above) ---
    if args.system_yaml:
        print("\n--- System Wrapper Generation ---")
        if system_module:
            system_backend = SoCSystemBackend(system_module, top_module)
            system_path = system_backend.generate(rtl_dir, flist_dir)
            if system_path:
                print(f"  Generated: {system_path}")
            else:
                print("  Skipped (jinja2 not available)")

    # --- Chip wrapper generation (requires system module) ---
    if system_module:
        print("\n--- Chip Wrapper Generation ---")
        # chip_backend: built, and its parameters checked, with the system model
        try:
            chip_path, pads_path = chip_backend.generate(rtl_dir, flist_dir)
        except ChipParamError as e:
            print(f"\nERROR: chip: {e}", file=sys.stderr)
            sys.exit(1)
        if chip_path:
            print(f"  Generated: {chip_path}")
        if pads_path:
            print(f"  Generated: {pads_path}")
        if not chip_path:
            print("  Skipped (jinja2 not available)")

    # The inner SoC's expanded boundary port list, handed to BOTH wrapper
    # backends so each can cross-check its spec against the module it claims to
    # wrap. Without this an unclassified SoC port was silently dropped from the
    # `u_soc` instantiation, leaving its inputs floating (Z in sim, 0 in synth).
    #
    # Widths are concretised here: a bus port carries its width as a param
    # reference ('$SYS_DATA_W'), but a wrapper does not instantiate the SoC's
    # parameters, so any wire it declares needs a literal width.
    _tl_backend = SoCTopLevelBackend(top_module)
    soc_boundary_ports = _tl_backend._build_ports()
    for _p in soc_boundary_ports:
        _w = _p.get('width')
        if isinstance(_w, int):
            continue
        _key = str(_w).lstrip('$')
        try:
            _p['width'] = int(_tl_backend.flat_params.get(_key, _key))
        except (TypeError, ValueError):
            _p['width'] = 1

    def _ports_for(boundary, top, ports):
        """Ports to cross-check `boundary` against — or None to skip.

        A spec can only be checked against the module it claims to wrap. When
        `inner_module` names a different top (a boundary spec reused across
        designs, or a test pairing) there is nothing meaningful to compare, so
        the check is skipped rather than failed. Say so out loud: a skipped
        check is how the bundled spec silently rotted for a month.
        """
        if boundary.inner_module == top.name:
            return ports
        print(f"  WARNING: chip_boundary wraps '{boundary.inner_module}' but the "
              f"generated top is '{top.name}' — port cross-check SKIPPED; "
              f"unclassified ports will be dropped from the wrapper silently.")
        return None

    # --- Chip-functional wrapper generation (optional, --chip-boundary) ---
    # The multicore top *is* the SoC (no separate *_system layer); wrap it
    # directly. Runs only when a boundary spec is named — default behaviour
    # is unchanged when absent.
    if args.chip_boundary:
        print("\n--- Chip-Functional Wrapper Generation ---")
        cb_raw = parser.parse_chip_boundary(args.chip_boundary)
        if cb_raw:
            boundary = builder._build_chip_boundary(cb_raw)
            chip_wrapper_path = SoCChipWrapperBackend(
                boundary, _ports_for(boundary, top_module, soc_boundary_ports)
            ).generate(rtl_dir, flist_dir)
            if chip_wrapper_path:
                print(f"  Generated: {chip_wrapper_path}")
            else:
                print("  Skipped (jinja2 not available)")
        else:
            print(f"  Skipped (chip_boundary not found: {args.chip_boundary})")

    # --- FPGA Vivado wrapper generation (optional, --fpga-wrapper) ---
    # Synthesizable IP wrapper for the SoC top: bonded boundary signals wired to
    # board ports (bidir -> Vivado _i/_o/_t tristate triple), bespoke FPGA layer
    # from the matching fpga_profile. Runs only when --fpga-wrapper is given —
    # default behaviour is unchanged when absent.
    if args.fpga_wrapper:
        print("\n--- FPGA Vivado Wrapper Generation ---")
        # bare flag (True) reuses --chip-boundary; a string overrides the name.
        fw_name = (args.fpga_wrapper if isinstance(args.fpga_wrapper, str)
                   else args.chip_boundary)
        if not fw_name:
            print("  Skipped (--fpga-wrapper needs a name or --chip-boundary)")
        else:
            cb_raw = parser.parse_chip_boundary(fw_name)
            if cb_raw:
                boundary = builder._build_chip_boundary(cb_raw)
                fp_raw = parser.parse_fpga_profile(fw_name)
                profile = (builder._build_fpga_profile(fp_raw, fw_name)
                           if fp_raw else None)
                fw_backend = SoCFpgaWrapperBackend(
                    boundary, profile,
                    _ports_for(boundary, top_module, soc_boundary_ports))
                fpga_wrapper_path = fw_backend.generate(rtl_dir, flist_dir)
                if fpga_wrapper_path:
                    print(f"  Generated: {fpga_wrapper_path}")
                    for r in fw_backend.residuals():
                        print(f"    residual (bespoke): {r}")
                else:
                    print("  Skipped (jinja2 not available)")
            else:
                print(f"  Skipped (chip_boundary not found: {fw_name})")

    # --- Timing constraints generation (optional, --emit-constraints) ---
    # Emits <top>_constraints.sdc (ASIC) + <top>_constraints.xdc (FPGA),
    # aggregated from the model clocks/freqs + every module constraints: block.
    # The --chip-boundary spec (when given) classifies boundary pads for the
    # pad-cell + io-delay + swd false-path constraints.
    if args.emit_constraints:
        print("\n--- Timing Constraints Generation ---")
        boundary = None
        if args.chip_boundary:
            cb_raw = parser.parse_chip_boundary(args.chip_boundary)
            if cb_raw:
                boundary = builder._build_chip_boundary(cb_raw)
        constraints_dir = build_dir / 'constraints'
        cb_backend = SoCConstraintsBackend(top_module, boundary)
        cons_paths = cb_backend.generate(constraints_dir, flist_dir)
        for p in cons_paths:
            print(f"  Generated: {p}")
        for r in cb_backend.residuals():
            print(f"    residual: {r}")

    # --- Vivado IP-XACT packaging + block-design (optional) ---
    if args.emit_vivado_bd:
        print("\n--- Vivado BD / IP-XACT Generation ---")
        bd_paths = SoCVivadoBdBackend(top_module).generate(build_dir / 'fpga')
        for p in bd_paths:
            print(f"  Generated: {p}")

    # --- Pad-ring generation (optional, requires --pad-tech) ---
    # GPIO-bus mode needs the system module (P0/P1 ifaces); MIXED-I/O mode
    # (e.g. --pin-count multicore) is driven entirely by the pin-map's pad list
    # and the chip top it names, so no system_module is required.
    if args.pad_tech:
        print("\n--- Pad-Ring Generation ---")
        pt_raw, pm_raw = pad_spec
        pin_count = args.pin_count
        if pt_raw and pm_raw:
            pad_tech = builder._build_pad_tech(pt_raw['pad_tech'])
            pin_map = builder._build_pin_map(pm_raw['pin_map'])
            if pin_map.mode == 'mixed' or pin_map.pads:
                # mixed ring wraps the pin-map's chip_module; system_module
                # (if any) only supplies a name fallback.
                pad_outs = SoCPadRingBackend(
                    system_module or top_module, top_module, pad_tech, pin_map,
                    pad_tech_dirs=args.pad_tech_dir,
                ).generate(rtl_dir, flist_dir)
            elif system_module:
                try:
                    pad_outs = SoCPadRingBackend(
                        system_module, top_module, pad_tech, pin_map,
                        pad_tech_dirs=args.pad_tech_dir,
                    ).generate(rtl_dir, flist_dir)
                except pad_packs.PadPackError as e:
                    print(f"ERROR: {e}", file=sys.stderr)
                    sys.exit(1)
            else:
                pad_outs = []
                print("  Skipped (GPIO-bus pad ring needs --system-yaml)")
            for p in pad_outs:
                print(f"  Generated: {p}")
            if not pad_outs and (pin_map.mode == 'mixed' or system_module):
                print("  Skipped (jinja2 not available)")

            # --- UPF (power-intent) emission (doc-07 M5) ---------------
            # Emit when the pad-tech carries a upf: block, or when forced
            # with --emit-upf.  Walks the inner SoC top for memories, so it
            # uses top_module (the real SoC), not the chip/system wrapper.
            if pad_tech.upf or args.emit_upf:
                if not pad_tech.upf:
                    print("  UPF skipped (--emit-upf set but pad-tech has no "
                          "upf: block)")
                else:
                    variant = (str(pin_count) if pin_count
                               and not str(pin_count).isdigit()
                               else (f'{pin_count}pin' if pin_count
                                     else 'multicore'))
                    upf_backend = SoCUpfBackend(
                        top_module, pad_tech.upf, tech=args.pad_tech,
                        variant=variant)
                    upf_path = upf_backend.generate(rtl_dir, flist_dir)
                    if upf_path:
                        print(f"  Generated: {upf_path}")
                        for r in upf_backend.residuals():
                            print(f"    residual (not derivable): {r}")

    # --- Design documentation ---
    print("\n--- Design Documentation ---")
    docs_backend = SoCDocsBackend(top_module)
    docs_dir = build_dir / 'docs'
    docs_path = docs_backend.generate(docs_dir)
    if docs_path:
        print(f"  Generated: {docs_path}")
    else:
        print("  Skipped (jinja2 not available)")

    # --- Schema & artifact reference (auto-generated, doc 09) ---
    # Introspects the model dataclasses + completeness predicates so the field
    # reference can't drift from the code.
    print("\n--- Schema & Artifact Reference ---")
    schema_path = SoCSchemaDocBackend(top_module).generate(docs_dir)
    print(f"  Generated: {schema_path}")

    # --- RTL `timescale sweep ---
    # The interconnect (BuildBusMatrix) and DMA-250 (Arm render) sub-files are
    # produced by external tools and do NOT pass through write_if_changed, so
    # they would otherwise carry no `timescale. Sweep rtl_dir and prepend one to
    # any .sv/.v that lacks it (generator-written files already have it from
    # write_if_changed; this is a no-op for them and for vendor IP that carries
    # its own). Keeps every generated RTL file self-sufficient for HAL/xmelab +
    # sim without a downstream patch script.
    print("\n--- RTL timescale sweep ---")
    _ts_added = 0
    for _f in rtl_dir.rglob('*'):
        if _f.is_file() and _f.suffix in ('.sv', '.v'):
            _txt = _f.read_text()
            if '`timescale' not in _txt[:400]:
                _f.write_text('`timescale 1ns/1ps\n' + _txt)
                _ts_added += 1
    print(f"  prepended `timescale to {_ts_added} file(s)")

    # --- Discovery size guard (always on) ---
    # Every memory target's TGT_i_SIZE against the memory compiled behind it,
    # sized from the RENDERED RTL's parameters and, with --rtl-flist, from the
    # region wrapper's own HADDR slice and the ROM's depth. A mismatch fails
    # the run after the HSEL check has reported too.
    mem_failed = _mem_size_guard(top_module, build_dir, args, config_overrides, mem_waivers)

    # --- HSEL-reader check (always on) ---
    # A bus-matrix target port may show HTRANS=NONSEQ with HSEL=0 for a cycle;
    # whatever sits behind it must qualify HTRANS by HSEL. Every missing gate
    # so far (cpu_ss_hsel declared and unused; eth_ss_slave_hsel read by
    # nobody) left the same trace in the render: a matrix-driven <tgt>_hsel
    # with no reader outside the matrix. Fail the generation on it.
    print("\n--- HSEL-reader check ---")
    hsel_roots = [top_module.name] + ([system_module.name] if system_module else [])
    hsel_findings, hsel_lint = lint_hsel_readers(rtl_dir, hsel_roots)
    for u in hsel_lint.unanalysable + hsel_lint.unused_inputs:
        print(f"  NOTE (not failing): {u}")
    for f in hsel_findings:
        print(f"  ERROR: {f}", file=sys.stderr)
    print(f"  {hsel_lint.checked} matrix-driven hsel(s) and "
          f"{hsel_lint.checked_inputs} hsel input(s) checked in "
          f"{len(hsel_lint.modules)} structural module(s): "
          f"{len(hsel_findings)} finding(s), "
          f"{len(hsel_lint.unanalysable) + len(hsel_lint.unused_inputs)} note(s)")
    if hsel_findings:
        print(f"\n{'ERROR' if not args.allow_unread_hsel else 'WARNING'}: "
              f"{len(hsel_findings)} bus-matrix hsel(s) never read outside the "
              f"matrix or by its module. {HSEL_FIX_ADVICE} To report without failing (only to "
              f"reproduce a frozen render): --allow-unread-hsel.", file=sys.stderr)
        if not args.allow_unread_hsel:
            sys.exit(1)

    # --- Cross-instance wire check (always on) ---
    # Every `conn: u_x.PORT` became a flat wire bound on u_x: read the render
    # back and check that u_x drives it, through a port u_x declares, at the
    # width of that port and of every port that reads it
    # (soc_model/backends/xref_check.py).
    print("\n--- Cross-instance wire check ---")
    xref = check_xref_wires(rtl_dir, args.rtl_flist, top_module, xref_waivers)
    for n in xref.all_notes():
        print(f"  NOTE: {n}")
    for f in xref.findings:
        print(f"  ERROR: {f}", file=sys.stderr)
    print(f"  {xref.summary()}")
    if xref.findings:
        print(f"\nERROR: {len(xref.findings)} cross-instance wire(s) not driven by the port "
              f"they name, or not as wide as it or as their readers. Fix the `conn: "
              f"<instance>.<port>` reference (the instance and port as the module declares "
              f"them) or the width in the module YAML.", file=sys.stderr)
        sys.exit(1)
    if mem_failed:
        sys.exit(1)

    # --- Lint generated RTL ---
    if args.lint:
        print("\n--- RTL Lint (slang) ---")
        lint_backend = SoCLintBackend(slang_bin=args.slang_bin)
        extra_args = args.slang_args.split() if args.slang_args else None

        # Derive directories: base_dir is the yaml dir (sys_desc/),
        # project_dir is two levels up from the yaml file
        soc_root = base_dir.parent
        project_dir = str(soc_root.parent)

        flist = args.lint_flist

        lint_passed = lint_backend.lint_and_report(
            build_dir,
            project_dir=project_dir,
            flist_path=flist,
            extra_args=extra_args,
        )
        if not lint_passed:
            print("\n  Lint did not pass — see above for details.")

    print("\nDone.")


if __name__ == '__main__':
    main()
