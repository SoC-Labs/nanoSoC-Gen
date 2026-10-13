# Copyright 2026, SoC Labs (www.soclabs.org)
"""Cross-instance wire check: every cross-instance wire the generator declares
is driven by the instance it names, and is as wide as that port and as the
ports that read it.

A connection `conn: u_x.PORT` makes the generator declare a flat wire
`u_x_PORT` (`// cross-instance wire: u_x.PORT`) and bind it on u_x. Three
defects have reached a render that way, each one silent until simulation or
silicon: the wire bound on the consumer but never on u_x (UNBOUND: no
driver; the PYNQ-Z2 APB PREADY hang), u_x binding PORT to another net
(the GPIO0 class, 2026-10-02: the referenced output went somewhere else), and
a wire declared 1 bit wide because the reference did not resolve (a typo or
an undeclared member: the collapsed TDATA). The validator refuses the
references it can see; this check reads the RENDERED RTL, so it judges what
was emitted.

For every `wire [..] W;  // cross-instance wire: u_x.PORT` in a rendered
module M:
  FAIL  u_x is not an instance of M;
  FAIL  u_x does not bind .PORT, or binds it to anything but W;
  FAIL  u_x's module declares no port PORT (its source is rendered or in the
        --rtl-flist files), or, when its source is not readable, the model
        cannot resolve PORT on it (a typo or an undeclared port);
  FAIL  W's width differs from PORT's width on u_x;
  FAIL  W (or a constant part-select of it) is bound to a port of another
        instance whose width differs.
A width that cannot be read or evaluated (a module whose source is not
readable, a non-constant range) is not a finding, but it is never a pass in
disguise: the summary counts the widths compared AND the ones that were not.

The wires are found by their `// cross-instance wire:` declaration. So that a
render without the comment (or a generator that stopped writing it) cannot
pass by having nothing to check, the check also asks the model which
cross-instance wires each rendered module must declare (the top-level and
subsystem backends' own list) and FAILS on any it does not find. A rendered
module that declares cross-instance wires but cannot be parsed FAILS too,
unless --xref-unparsed-waive <module>=<reason> names it.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .mem_size_guard import RtlIndex, RtlModule, read_flist, try_const

_DECL_RE = re.compile(r'^[ \t]*wire\s*(\[[^\]\n]*\])?\s*([A-Za-z_]\w*)\s*;[ \t]*//[ \t]*'
                      r'cross-instance wire:\s*([A-Za-z_]\w*)\.([A-Za-z_]\w*)(\S*)', re.M)
_MOD_START_RE = re.compile(r'^[ \t]*module\s+([A-Za-z_]\w*)', re.M)


@dataclass
class XrefReport:
    wires: int = 0
    modules: int = 0
    widths_compared: int = 0
    findings: List[str] = None
    notes: List[str] = None
    unreadable: Dict[str, int] = None    # module -> widths not compared (source not readable)
    unevaluated: int = 0                 # widths not compared: a range that is not constant
    expected: int = 0                    # cross-instance wires the model says the render declares

    def __post_init__(self):
        self.findings = self.findings or []
        self.notes = self.notes or []
        self.unreadable = self.unreadable or {}

    @property
    def not_compared(self) -> int:
        return sum(self.unreadable.values()) + self.unevaluated

    def summary(self) -> str:
        return (f"{self.wires} cross-instance wire(s) in {self.modules} module(s) "
                f"({self.expected} expected from the model), {self.widths_compared} width(s) "
                f"compared, {self.not_compared} not compared: {len(self.findings)} finding(s)")

    def all_notes(self) -> List[str]:
        out = list(self.notes)
        if self.unreadable:
            out.append(f"{sum(self.unreadable.values())} width(s) not compared: no readable "
                       f"source (render or --rtl-flist) for " + ', '.join(
                           f"{m} ({n})" for m, n in sorted(self.unreadable.items())))
        return out


def _width(rng: Optional[str], env) -> Optional[int]:
    """Width of a `[hi:lo]` / `hi:lo` range text ('' or None: 1 bit)."""
    if not rng:
        return 1
    t = rng.strip().lstrip('[').rstrip(']')
    if ':' not in t:
        return 1 if try_const(t, env) is not None else None
    hi, lo = t.rsplit(':', 1) if t.count(':') == 1 else (None, None)
    hi, lo = try_const(hi, env), try_const(lo, env)
    if hi is None or lo is None:
        return None
    return abs(hi - lo) + 1


def _inst_env(sub: RtlModule, inst, parent_env) -> Dict[str, Optional[int]]:
    over = {}
    for pn, pe in inst.params:
        v = try_const(pe, parent_env)
        if pn and v is not None:
            over[pn] = v
    return sub.env(over)


def _model_modules(top) -> Dict[str, object]:
    out = {}

    def walk(m):
        if m is None or m.name in out:
            return
        out[m.name] = m
        for i in m.instances:
            walk(i.resolved_module)
    walk(top)
    return out


def _model_resolves(model_mods, module_name: str, inst_name: str, port: str) -> Optional[bool]:
    """Does the model resolve `inst_name.port` in `module_name`? None: unknown."""
    from ..adapters.base import resolve_boundary_port
    m = model_mods.get(module_name)
    if m is None:
        return None
    inst = m.get_instance(inst_name)
    if inst is None:
        return None
    ifaces = list(inst.resolved_module.interfaces) if inst.resolved_module else []
    ifaces += list(inst.inline_interfaces)
    if not ifaces:
        return None
    return resolve_boundary_port(ifaces, port) is not None


def expected_wires(top) -> Dict[str, set]:
    """{rendered module: the cross-instance wire names its backend declares},
    for the top and every structural subsystem, as the render writes them."""
    from .toplevel import SoCTopLevelBackend
    from .subsystem import SoCSubsystemBackend
    out: Dict[str, set] = {}

    def names(backend):
        # the very context generate() renders the module's template from
        return {w['name'] for w in backend._build_context().get('xref_wires', [])}
    out[top.name] = names(SoCTopLevelBackend(top))

    def walk(m):
        for inst in m.instances:
            sub = inst.resolved_module
            if sub is not None and sub.gen:
                if (sub.interconnects or sub.instances) and sub.name not in out:
                    out[sub.name] = names(SoCSubsystemBackend(sub))
                walk(sub)
    walk(top)
    return {k: v for k, v in out.items() if v}


def parse_unparsed_waivers(items) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for it in items or ():
        key, eq, reason = str(it).partition('=')
        key, reason = key.strip(), reason.strip()
        if not eq or not reason or not re.fullmatch(r'[A-Za-z_]\w*', key):
            raise ValueError(f"--xref-unparsed-waive {it!r}: expected <module>=<reason>, with a reason")
        if key in out:
            raise ValueError(f"--xref-unparsed-waive {key}: given twice")
        out[key] = reason
    return out


def check_rtl(rtl_dir: Path, rtl_flists: Iterable[Path] = (), top=None,
              unparsed_waivers: Optional[Dict[str, str]] = None) -> XrefReport:
    rep = XrefReport()
    waivers = dict(unparsed_waivers or {})
    used_waivers = set()
    found: Dict[str, set] = {}                 # module -> wire names declared with the comment
    parsed_mods = set()
    index = RtlIndex()
    rendered = sorted(p for p in Path(rtl_dir).rglob('*') if p.is_file() and p.suffix in ('.sv', '.v'))
    index.add_files(rendered, rendered=True)
    files: List[Path] = []
    for f in rtl_flists or ():
        files += read_flist(Path(f), [])
    index.add_files(files)
    model_mods = _model_modules(top) if top is not None else {}

    for path in rendered:
        raw = path.read_text(errors='replace')
        if 'cross-instance wire:' not in raw:
            continue
        starts = list(_MOD_START_RE.finditer(raw))
        for n, ms in enumerate(starts):
            chunk = raw[ms.start():starts[n + 1].start() if n + 1 < len(starts) else len(raw)]
            decls = _DECL_RE.findall(chunk)
            if not decls:
                continue
            found.setdefault(ms.group(1), set()).update(d[1] for d in decls)
            mod, why = index.get(ms.group(1))
            if mod is None:
                msg = (f"{path.name}: module {ms.group(1)} declares {len(decls)} cross-instance "
                       f"wire(s) but could not be parsed ({why}): none of them is checked")
                if ms.group(1) in waivers:
                    used_waivers.add(ms.group(1))
                    rep.notes.append(f"{msg} [waived: {waivers[ms.group(1)]}]")
                else:
                    rep.findings.append(msg + ". Waive it by name only if that is known to be "
                                        "safe: --xref-unparsed-waive <module>=<reason>")
                continue
            parsed_mods.add(mod.name)
            rep.modules += 1
            env = mod.env({})
            insts = {i.name: i for i in mod.instances}
            for rng, wire, iname, port, sl in decls:
                rep.wires += 1
                where = f"{mod.name}: {wire} ({iname}.{port}{sl})"
                w = _width(rng, env)
                inst = insts.get(iname)
                if inst is None:
                    rep.findings.append(f"{where}: {iname} is not an instance of {mod.name} "
                                        f"(a misspelt or undeclared instance)")
                    continue
                bound = inst.conns.get(port)
                if bound is None:
                    rep.findings.append(f"{where}: UNBOUND: {iname} does not bind .{port}, so "
                                        f"nothing drives {wire}")
                elif bound.strip() != wire:
                    rep.findings.append(f"{where}: {iname} binds .{port}({bound.strip()}), not "
                                        f"{wire}: the wire is not driven by the port it names")
                pmod, pwhy = index.get(inst.module)
                if pmod is not None:
                    if port not in pmod.ports:
                        rep.findings.append(f"{where}: {inst.module} ({pmod.path.name}) declares "
                                            f"no port {port} (a misspelt or undeclared port)")
                        continue
                    pw = _width(pmod.ports[port], _inst_env(pmod, inst, env))
                    if pw is None or w is None:
                        rep.unevaluated += 1
                        rep.notes.append(f"{where}: width not evaluated ({inst.module}.{port} "
                                         f"[{pmod.ports[port]}], wire {rng or '1 bit'})")
                    else:
                        rep.widths_compared += 1
                        if pw != w:
                            rep.findings.append(f"{where}: the wire is {w} bit(s), {inst.module}."
                                                f"{port} is {pw}")
                else:
                    ok = _model_resolves(model_mods, mod.name, iname, port)
                    if ok is False:
                        rep.findings.append(f"{where}: {port} is not a port of {iname} "
                                            f"({inst.module}) in the model (a misspelt or "
                                            f"undeclared port)")
                    else:
                        rep.unreadable[inst.module] = rep.unreadable.get(inst.module, 0) + 1
                # consumers: W, or a constant part-select of it, bound to a port
                for c in mod.instances:
                    for cp, expr in c.conns.items():
                        if c is inst and cp == port:
                            continue
                        e = expr.strip()
                        m = re.fullmatch(re.escape(wire) + r'\s*(\[[^\]]*\])?', e)
                        if not m:
                            continue
                        need = w if m.group(1) is None else _width(m.group(1), env)
                        cmod, _ = index.get(c.module)
                        if cmod is None:
                            rep.unreadable[c.module] = rep.unreadable.get(c.module, 0) + 1
                            continue
                        if cp not in cmod.ports:
                            rep.findings.append(f"{where}: {c.name} binds .{cp}, a port its module "
                                                f"{c.module} ({cmod.path.name}) does not declare")
                            continue
                        cw = _width(cmod.ports[cp], _inst_env(cmod, c, env))
                        if cw is None or need is None:
                            rep.unevaluated += 1
                            rep.notes.append(f"{where}: consumer {c.name}.{cp} width not evaluated")
                            continue
                        rep.widths_compared += 1
                        if cw != need:
                            rep.findings.append(f"{where}: {c.name}.{cp} ({c.module}) is {cw} "
                                                f"bit(s), it is given {e} ({need} bit(s))")

    # the model's list: a wire the render does not declare with its comment is
    # a shortfall (an edited render, or a generator that stopped marking them)
    if top is not None:
        try:
            expected = expected_wires(top)
        except Exception as e:                      # never a silent pass
            expected = {}
            rep.findings.append(f"the model's cross-instance wire list could not be built "
                                f"({type(e).__name__}: {e}); the declared wires were not "
                                f"counted against it")
        for mname, names in sorted(expected.items()):
            rep.expected += len(names)
            missing = sorted(names - found.get(mname, set()))
            if missing:
                rep.findings.append(
                    f"{mname}: {len(missing)} of the {len(names)} cross-instance wire(s) the model "
                    f"declares have no `// cross-instance wire:` declaration in the render, so "
                    f"they were not checked: {', '.join(missing[:8])}"
                    f"{' ...' if len(missing) > 8 else ''}")
    for k in sorted(set(waivers) - used_waivers):
        rep.findings.append(f"--xref-unparsed-waive {k}: no unparsed module of that name (stale)")
    return rep


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('rtl_dir')
    ap.add_argument('--rtl-flist', action='append', default=[])
    a = ap.parse_args(argv)
    rep = check_rtl(Path(a.rtl_dir), a.rtl_flist)
    for n in rep.all_notes():
        print(f"NOTE: {n}")
    for f in rep.findings:
        print(f"ERROR: {f}")
    print(f"{rep.summary()}, {len(rep.all_notes())} note(s) (no model here: the expected "
          f"count is not checked)")
    return 1 if rep.findings else 0


if __name__ == '__main__':
    raise SystemExit(main())
