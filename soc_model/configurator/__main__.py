# Copyright 2026, SoC Labs (www.soclabs.org)
"""CLI for the SoC Configurator.

``serve`` — run the web app (needs the ``configurator`` extra)::

    python -m soc_model.configurator serve --project <repo> [--host 127.0.0.1] [--port 8090]

``check`` — headless tier-1..3 validation (NO web deps; CI-friendly)::

    python -m soc_model.configurator check <top.yaml> \
        [--lib-dir DIR ...] [--rules FILE] [--project DIR]

``check`` exits 1 on any error-level message (including failed error-level
completeness rules), 0 otherwise.
"""

import argparse
import sys
from pathlib import Path

import yaml


def _cmd_check(args) -> int:
    from ..gui_service import GuiService
    from .project import load_project

    lib_dirs = list(args.lib_dir or [])
    rules = args.rules
    base_dir = str(Path(args.yaml_file).resolve().parent)
    if args.project:
        adapter = load_project(args.project)
        lib_dirs = lib_dirs or adapter.abs_lib_dirs
        rules = rules or adapter.abs_rules
    svc = GuiService(base_dir=base_dir, lib_dirs=lib_dirs, rules_file=rules)
    data = yaml.safe_load(Path(args.yaml_file).read_text())
    res = svc.validate(data)

    for m in res['messages']:
        loc = f"{m['instance']}.{m['port']}".strip('.')
        rule = f" [{m['rule']}]" if m.get('rule') else ''
        print(f"[{m['level'].upper()}] (t{m['tier']}/{m['category']}){rule} "
              f"{loc + ': ' if loc else ''}{m['message']}")
        if m.get('hint'):
            print(f"         hint: {m['hint']}")
    passed = sum(1 for c in res['checklist'] if c['passed'])
    if res['checklist']:
        print(f"System completeness: {passed}/{len(res['checklist'])} checks pass")
    print(f"{res['errors']} error(s), {res['warnings']} warning(s)")
    return 0 if res['ok'] else 1


def _cmd_serve(args) -> int:
    from .project import load_project
    adapter = load_project(args.project)
    try:
        import uvicorn
        from .app import create_app
    except ImportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    app = create_app(adapter)
    print(f"nanoSoC Configurator: project={adapter.root}")
    print(f"  top_yaml={adapter.top_yaml}  lib_dirs={len(adapter.lib_dirs)}")
    uvicorn.run(app, host=args.host, port=args.port, log_level='info')
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='python -m soc_model.configurator',
                                 description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest='cmd', required=True)

    serve = sub.add_parser('serve', help='run the configurator web app')
    serve.add_argument('--project', default='.',
                       help='project root (with configurator.yaml)')
    serve.add_argument('--host', default='127.0.0.1',
                       help='bind address (default: localhost only)')
    serve.add_argument('--port', type=int, default=8090)
    serve.set_defaults(fn=_cmd_serve)

    check = sub.add_parser('check',
                           help='headless tier-1..3 validation (CI)')
    check.add_argument('yaml_file', help='top-level system YAML')
    check.add_argument('--lib-dir', action='append', metavar='DIR')
    check.add_argument('--rules', help='completeness rules YAML')
    check.add_argument('--project',
                       help='project root — supplies lib dirs/rules')
    check.set_defaults(fn=_cmd_check)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == '__main__':
    sys.exit(main())
