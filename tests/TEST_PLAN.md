# Copyright 2026, SoC Labs (www.soclabs.org)
# nanosoc_gen Master Test Coverage Matrix

> Single contract for the `nanosoc_gen` (import name `soc_model`) pytest suite.
> Every row is a real, implementable test grounded against the code at
> `soc_model/` on branch `feature/nanosoc-gen-roadmap`. Synthesised + de-duplicated
> from six exhaustive brainstorm enumerations (parser, builder+model, utils,
> validator, backends-codegen, backends-data).

## 1. Scope & tiers

The suite tests the generator **at the model seam** (`SoCParser → SoCBuilder → Module`)
and **snapshots at the artifact seam** (one backend → emitted text/file). It asserts on
emitted *text* only — no simulator, no synthesis, no functional RTL verification.

**Tiers** (pytest markers, registered in `tests/conftest.py`):

| Marker | Meaning | Tools required | Default run? |
|---|---|---|---|
| `unit` | Pure-Python, in-memory or `tmp_path`, sub-ms each | none | yes |
| `golden` | Snapshot of a rendered artifact (`__snapshots__/<fixture>/...`) | jinja2 (installed) | yes |
| `tooling` | perl/BuildBusMatrix or peakrdl-regblock RTL emission | external; **SKIP if absent** | no (`-m tooling`) |
| `slow` | drives the real multicore SoC YAML / submodules | submodules | no (`-m slow`) |

Default `addopts` = `-m 'not tooling and not slow'` → `unit + golden`.

## 2. How to run

```bash
cd "$SOCLABS_NANOSOC_GEN_DIR"        # your nanosoc_gen checkout
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider           # unit + golden
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -m unit   # fast tier only
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider --snapshot-update   # regen goldens
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -m tooling          # EDA host (else SKIPs)
```

## 3. Coverage targets

| Module group | Target line+branch coverage |
|---|---|
| Core: `parser.py`, `builder.py`, `model.py`, `utils.py`, `validator.py` | **≥ 90 %** |
| Mainstream backends: `toplevel.py`, `subsystem.py`, `ahb.py`, `protocol_utils.py`, `soc_config_pkg.py`, `firmware.py`, `discovery.py`, `text.py`, `rdl.py`, `sram.py`, `python.py`, `build_info.py`, `system.py`, `chip.py`, `lint.py` | **≥ 75 %** |
| `html.py` | best-effort smoke only (no golden — 110 KB f-string) |

## 4. Shared scaffold (READ-ONLY — consume, do not edit)

- `tests/conftest.py` — sys.path shim, markers, `--snapshot-update`, fixtures `fixtures_dir` / `snapshots_dir`; module constants `FIXTURES`, `SNAPSHOTS`.
- `tests/helpers.py` — `make_parser(base, lib_dirs=None)`, `build_module_from_yaml(base, top_yaml, lib_dirs=None)`, `build_module_inline(module, lib_dirs=None)` (runs `_resolve_instances` in place), `assert_matches_snapshot(actual, snap_path, request)` (reuses `soc_model.utils._strip_timestamps`).
- `tests/fixtures/`: `minimal_soc.yaml`, `two_region_soc.yaml`, `apb_bridge_soc.yaml`, `passthrough_soc.yaml`, `firmware_soc.yaml`; `regions/tiny_region/`, `regions/tiny_apb_periph/`; `register_maps/tiny_regs.yaml`.
- Confirmed missing fixtures implementers may need (request from scaffold agent, do **not** hand-roll in a way that diverges): a `multicore_soc.yaml` with two `gen:True` subsystems (one cpu_ss-like `EXCLUDE:[hready]`, one eth_ss-like) + a system passthrough target name-colliding with an initiator (`cpu_0`); a `system_soc.yaml` (`*_system` module with wire/swd/gpio ifaces + `region_exp` instance) for system/chip goldens. Where a fixture is absent, build the model inline via dataclasses.

---

## 5. Per-file coverage matrices

Priority key: **H** = must-have (core behaviour / foot-gun / coverage-critical branch), **M** = should-have, **L** = nice-to-have edge.

---

### 5.1 `tests/unit/test_utils.py` — `soc_model/utils.py`

Pure Python; only `write_if_changed` touches the filesystem (`tmp_path`). No tools.
Imports: `resolve_param_ref, _safe_eval, parse_bit_slice, bit_slice_width, parse_conn_ref, get_param_value, flatten_params, resolve_params_in_dict, write_if_changed, _strip_timestamps, _TIMESTAMP_LINE_RE`.

#### `resolve_param_ref` (utils.py:8) — 7 branches: not-str(15), fullmatch+in(22), fullmatch+miss(23), expr eval-ok(33), expr eval-raise(35), expr still-`$`(36), no-`$`(38)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| U-RP-01 | non-str passes through | `resolve_param_ref(5,{})` / `(None,{})` / `([1,2],{})` | identity | unit | H |
| U-RP-02 | pure `$X` in params → native-typed value | `("$SYS_ADDR_W",{"SYS_ADDR_W":32})` | `32` (int) | unit | H |
| U-RP-03 | pure `$X` value 0 still returned (`in` not truthiness) | `("$Z",{"Z":0})` | `0` | unit | H |
| U-RP-04 | pure `$X` non-int verbatim | `("$F",{"F":True})` / `("$N",{"N":"foo"})` | `True` / `"foo"` | unit | M |
| U-RP-05 | pure `$Y` unresolved → passthrough | `("$MISSING",{})` | `"$MISSING"` | unit | H |
| U-RP-06 | arithmetic `2 ** $N` → evaluated | `("2 ** $N",{"N":3})` | `8` | unit | H |
| U-RP-07 | nested parens `4 * (2 ** $N)` | `{"N":3}` | `32` | unit | H |
| U-RP-08 | expr with unresolved `$` left → returns substituted str | `("$N + $X",{"N":3})` | `"3 + $X"` | unit | H |
| U-RP-09 | expr substitutes valid-looking but eval rejects (`%`) → str | `("$N % 2",{"N":5})` | `"5 % 2"` | unit | H |
| U-RP-10 | division → float | `("8 / $N",{"N":3})` | `pytest.approx(2.666..)` | unit | M |
| U-RP-11 | FOOT-GUN: str param substring corruption | `("$NAME + 1",{"NAME":"foo","N":3})` (ordered dict) | `"3AME + 1"` (pin current) | unit | H |
| U-RP-12 | plain str no `$` unchanged; empty `""` | `("hello",{})` / `("",{})` | `"hello"` / `""` | unit | H |
| U-RP-13 | `$` non-word (`"$ "`) fails fullmatch → expr loop, no match → str | `("$ ",{})` | `"$ "` | unit | L |

#### `_safe_eval` (utils.py:41) — 2 branches: regex-match→`eval`, regex-fail→return str. NO try/except (whitelist-pass+eval-fail PROPAGATES)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| U-SE-01 | add / power / nested | `"2 + 3"` / `"2 ** 8"` / `"4 * (2 ** 3)"` | `5` / `256` / `32` | unit | H |
| U-SE-02 | true division → float; whitespace ok | `"8 / 2"` / `"  7  "` | `4.0` / `7` | unit | M |
| U-SE-03 | right-assoc power | `"2**3**2"` | `512` | unit | L |
| U-SE-04 | unary minus inside | `"-5 + 10"` | `5` | unit | M |
| U-SE-05 | REJECT identifier → str unchanged | `"abc"` / `"2 + a"` | input | unit | H |
| U-SE-06 | SECURITY: `__import__`/`print` NOT executed | `"__import__('os')"` / `"print(1)"` | str unchanged, no side effect | unit | H |
| U-SE-07 | REJECT `%` (not in whitelist) | `"2 % 3"` | `"2 % 3"` | unit | H |
| U-SE-08 | REJECT hex/shift/sci/list/comma | `"0x10"` / `"1<<2"` / `"1e3"` / `"[1]"` / `"1,2"` | input | unit | M |
| U-SE-09 | whitelist-pass but eval raises → PROPAGATES | `_safe_eval("-")` | `pytest.raises(SyntaxError)` | unit | H |
| U-SE-10 | div-by-zero propagates | `_safe_eval("1/0")` | `pytest.raises(ZeroDivisionError)` | unit | M |
| U-SE-11 | `abs(-1)` rejected by regex (letters) before eval | `"abs(-1)"` | `"abs(-1)"` | unit | M |

#### `parse_bit_slice` (utils.py:62) — 3 branches (range:73 / single:77 / fallthrough:81)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| U-BS-01 | range high:low | `"cpu_0_irq[31:16]"` | `("cpu_0_irq",31,16)` | unit | H |
| U-BS-02 | single bit → high==low | `"p1_in[7]"` / `"a[0]"` | `("p1_in",7,7)` / `("a",0,0)` | unit | H |
| U-BS-03 | no slice → (name,None,None) | `"sys_hclk"` | `("sys_hclk",None,None)` | unit | H |
| U-BS-04 | dotted name retained in base | `"u_ss_cpu.sys_hclk"` | `("u_ss_cpu.sys_hclk",None,None)` | unit | H |
| U-BS-05 | dotted + range | `"u_ss.P1_OUT_MUX[6:4]"` | `("u_ss.P1_OUT_MUX",6,4)` | unit | H |
| U-BS-06 | low>high NOT validated | `"a[3:7]"` | `("a",3,7)` (pin) | unit | M |
| U-BS-07 | 3-part `[1:2:3]` no match → whole base | `"weird[1:2:3]"` | `("weird[1:2:3]",None,None)` | unit | M |
| U-BS-08 | trailing chars after `]` (`$` anchor) → no match | `"a[3]x"` / `"name[]"` | base whole | unit | M |
| U-BS-09 | non-greedy `a[1][2]` → base `a[1]`, 2,2 | `"a[1][2]"` | `("a[1]",2,2)` (pin) | unit | M |

#### `bit_slice_width` (utils.py:84)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| U-BW-01 | width = high-low+1; single=1 | `(31,16)` / `(7,7)` | `16` / `1` | unit | H |
| U-BW-02 | any None → None | `(None,None)` / `(5,None)` / `(None,2)` | `None` | unit | H |
| U-BW-03 | low=0 (falsy not None) → 1 | `(0,0)` | `1` | unit | M |

#### `parse_conn_ref` (utils.py:91) — 2 branches (len==2 :107 / else :108)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| U-CR-01 | instance.port no slice | `"u_ss_cpu.sys_hclk"` | `("u_ss_cpu","sys_hclk",None,None)` | unit | H |
| U-CR-02 | bare signal → instance None | `"sys_sysresetn"` | `(None,"sys_sysresetn",None,None)` | unit | H |
| U-CR-03 | bare + slice | `"p1_in[6:0]"` | `(None,"p1_in",6,0)` | unit | H |
| U-CR-04 | instance.port + slice | `"u_ss.P1_OUT_MUX[6:4]"` | `("u_ss","P1_OUT_MUX",6,4)` | unit | H |
| U-CR-05 | split on FIRST dot only | `"a.b.c"` | `("a","b.c",None,None)` | unit | M |
| U-CR-06 | slice stripped before dot-split | `"u.p[5]"` | `("u","p",5,5)` | unit | M |
| U-CR-07 | empty / leading-dot edges | `""` / `".x"` | `(None,"",None,None)` / `("","x",None,None)` | unit | L |

#### `get_param_value` (utils.py:111) / `flatten_params` (utils.py:122)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| U-GP-01 | flat value | `({"A":5},"A")` | `5` | unit | H |
| U-GP-02 | nested `{default}` → default (incl 0) | `({"D":{"default":0}},"D")` | `0` | unit | H |
| U-GP-03 | dict without `default` → whole dict | `({"C":{"nope":1}},"C")` | `{"nope":1}` | unit | M |
| U-GP-04 | missing → None | `({},"X")` | `None` | unit | M |
| U-FP-01 | mixes flat + nested | `{"A":5,"B":{"type":"int","default":7}}` | `{"A":5,"B":7}` | unit | H |
| U-FP-02 | dict without default kept whole | `{"C":{"nope":1}}` | `{"C":{"nope":1}}` | unit | H |
| U-FP-03 | nested falsy default 0 | `{"D":{"default":0}}` | `{"D":0}` | unit | M |
| U-FP-04 | does NOT mutate input; new dict | call + assert input unchanged | identity check | unit | M |

#### `resolve_params_in_dict` (utils.py:49)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| U-RD-01 | flat scalar | `({"w":"$N"},{"N":8})` | `{"w":8}` | unit | H |
| U-RD-02 | nested dict recursion | `({"sub":{"w":"$N"}},{"N":8})` | `{"sub":{"w":8}}` | unit | H |
| U-RD-03 | list scalars element-wise | `({"l":["$N",5,"x"]},{"N":8})` | `{"l":[8,5,"x"]}` | unit | H |
| U-RD-04 | FOOT-GUN: dict/list IN list NOT resolved | `({"l":[{"w":"$N"},[1]]},{"N":8})` | unchanged | unit | M |
| U-RD-05 | list arithmetic element | `({"l":["2 ** $N"]},{"N":3})` | `{"l":[8]}` | unit | M |
| U-RD-06 | no input mutation | assert input dict unchanged | new dict | unit | M |

#### `write_if_changed` (utils.py:156) — needs `tmp_path`

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| U-WC-01 | absent file → writes, True | path not exist | True; exact content | unit | H |
| U-WC-02 | identical → False, no write | write then same | False | unit | H |
| U-WC-03 | ONLY `// Generated:` line differs → False, bytes unchanged | seed old ts, call new ts | False; old ts still in file | unit | H |
| U-WC-04 | ONLY `// Copyright YYYY,` differs → False | change copyright only | False; unchanged | unit | H |
| U-WC-05 | real body change → True, rewritten | `"module a;"` → `"module b;"` | True; new body | unit | H |
| U-WC-06 | real change WHILE ts differs → True | body+ts change | True; new body | unit | M |
| U-WC-07 | parent dirs auto-created | `tmp/n/deep/x.flist` | True; parents exist | unit | H |
| U-WC-08 | accepts str and Path | `str(path)` / `Path` | True | unit | M |
| U-WC-09 | FOOT-GUN: trailing-newline-only diff → False (splitlines) | `"line\n"` vs `"line"` | False | unit | M |
| U-WC-10 | interior blank line ADDED → True | `"a\nb\n"` vs `"a\n\nb\n"` | True | unit | M |
| U-WC-11 | non-4-digit copyright year NOT stripped → True | `"// Copyright abcd, X"` change | True | unit | M |
| U-WC-12 | return type is bool | assert `isinstance(...,bool)` | True | unit | L |

#### `_strip_timestamps` (utils.py:148) + `_TIMESTAMP_LINE_RE` (utils.py:137)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| U-ST-01 | strips `//` / `*` / `#` Generated forms | each prefix + body | ts line gone, body kept | unit | H |
| U-ST-02 | strips `> Auto-generated ... on <ts> ...` incl trailing | `"> Auto-generated x on 2026-06-09 12:34:56 y"` | stripped | unit | H |
| U-ST-03 | strips `// Copyright YYYY, ...` | `"// Copyright 2026, SoC Labs"` | stripped | unit | H |
| U-ST-04 | normal code line kept | `"module foo;"` | kept | unit | H |
| U-ST-05 | ASYMMETRY: `//` Generated with trailing text NOT stripped | `"// Generated: 2026-06-09 12:34:56 extra"` | KEPT | unit | M |
| U-ST-06 | FOOT-GUN: `/* Generated: ... */` block-comment NOT stripped (BI-13 root) | `"/* Generated: 2026-06-09 12:34:56 */"` | KEPT | unit | H |
| U-ST-07 | malformed/single-digit date NOT stripped | `"// Generated: 2026-6-9 1:2:3"` | KEPT | unit | M |
| U-ST-08 | output drops trailing newline (join+splitlines) | `"a\n"` | `"a"` | unit | M |
| U-ST-09 | import smoke: `_strip_timestamps` + `_TIMESTAMP_LINE_RE` exist (regression guard for helpers.py) | `from soc_model.utils import _strip_timestamps, _TIMESTAMP_LINE_RE` | `isinstance(_TIMESTAMP_LINE_RE, re.Pattern)` | unit | H |

---

### 5.2 `tests/unit/test_parser.py` — `soc_model/parser.py`

Pure; `tmp_path`-only; `_BUNDLED_INTERFACES_DIR` resolves to the in-repo `lib/interfaces/` (READ ONLY; never write). Collision tests MUST use code-fixed dir ordering (`modules/` scanned before `lib_dir` root; lib_dirs scanned in list order) — never rely on same-dir `glob`/`iterdir` order (filesystem-dependent).

#### `__init__` (parser.py:33)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| P-INIT-01 | lib_dir=None → lib_dirs==[base]; lib_dir==base | `SoCParser(str(tmp),None)` | `[Path(tmp)]`; `Path(tmp)` | unit | H |
| P-INIT-02 | lib_dir str → single-element list | `SoCParser(b,"/some/lib")` | `[Path("/some/lib")]` | unit | H |
| P-INIT-03 | lib_dir list/tuple → each coerced to Path; lib_dir==first | `SoCParser(b,[a,b])` / `(a,b)` | `[Path(a),Path(b)]`; `Path(a)` | unit | H |
| P-INIT-04 | base_dir coerced to Path; caches empty/unscanned | `SoCParser("rel")` | `isinstance(base_dir,Path)`; `_module_cache=={}`; `_modules_scanned is False` | unit | M |
| P-INIT-05 | FOOT-GUN: empty list lib_dir → `lib_dir=lib_dirs[0]` IndexError | `SoCParser(b,[])` | `pytest.raises(IndexError)` | unit | L |

#### `parse_top_level` (parser.py:50) + `_load_yaml` (parser.py:170)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| P-TOP-01 | valid top YAML → dict | write base/top.yaml | parsed dict with `module` | unit | H |
| P-TOP-02 | MISSING file → `{}` + prints `Warning: File not found:` (no raise) | call missing; capsys | `{}`; warning in `capsys.out` | unit | H |
| P-TOP-03 | malformed YAML → `{}` + `Warning: YAML parse error` | `"a: [1, 2"` | `{}`; warning text | unit | H |
| P-TOP-04 | empty file → `{}` (NO warning) | `""` | `{}`; `capsys.out==""` | unit | H |
| P-TOP-05 | top present but no `module:` key → dict without module | `"foo: bar"` | `{"foo":"bar"}`; `"module" not in` | unit | M |
| P-LY-01 | `null` / `{}` / `0` / `[]` (falsy) → `{}` | write `"null"`/`"0"`/`"[]"` | `{}` each (FOOT-GUN: `[]`→`{}`) | unit | M |
| P-LY-02 | truthy non-dict (list/int) returned unchanged | `"[1,2]"` / `"42"` | `[1,2]` / `42` | unit | M |
| P-LY-03 | duplicate YAML keys → YAMLError → `{}` | `"a: 1\na: 2"` | `{}` + warning | unit | L |
| P-LY-04 | PermissionError/IsADirectoryError NOT caught | open a directory Path | `pytest.raises(IsADirectoryError)` | unit | L |
| P-LY-05 | `!include` not supported → ConstructorError(YAMLError) → `{}` | `"!include x"` | `{}` + warning | unit | M |

#### `parse_module` + scan + cache (parser.py:55/67/109)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| P-MOD-01 | found by `module.name` AND by filename stem (same dict) | file myfile.yaml name=foo | `parse_module("foo") is parse_module("myfile")` | unit | H |
| P-MOD-02 | unknown name → None (no warning) | `parse_module("missing")`; capsys | `None`; `capsys.out==""` | unit | H |
| P-MOD-03 | lazy scan latch: scan once; file added later not seen | call, write new, call new | `_modules_scanned True`; new→None | unit | M |
| P-MOD-04 | module with no/`''`/null name → stem-only cache | `module:{ports:..}` no name | cached by stem only | unit | H |
| P-MOD-05 | legacy `modules/` scanned | base/modules/m.yaml | discoverable | unit | H |
| P-MOD-06 | hierarchical `subsystems/*` and `regions/*` scanned | base/subsystems/cpu/m.yaml; base/regions/r/m.yaml | discoverable | unit | H |
| P-MOD-07 | IP-local `../rtl/*/sys_desc/` scanned | base sibling rtl/ip/sys_desc/m.yaml | discoverable | unit | M |
| P-MOD-08 | lib_dir root scanned | base/m.yaml | discoverable | unit | H |
| P-MOD-09 | hidden `.file.yaml` skipped (:98) | base/.secret.yaml | `parse_module(stem)`→None | unit | M |
| P-MOD-10 | non-.yaml ignored (`.yml`/`.json`) | base/m.yml, m.json | not discovered | unit | M |
| P-MOD-11 | no-`module:`-key file skipped silently | `"foo: 1"` | None | unit | H |
| P-MOD-12 | empty yaml in scan dir skipped (`if data`) | base/empty.yaml `""` | no crash; absent | unit | M |
| P-MOD-13 | missing modules/subsystems/regions/rtl dirs → no crash (is_dir guards) | minimal base | works | unit | M |
| P-MOD-14 | multiple lib_dirs all scanned | lib_dirs=[a,b] module in each | both found | unit | H |
| P-MOD-COLLIDE-01 | name collision across lib_dirs → last-writer-wins (b last) | lib_dirs=[a,b] both name=foo | b's payload | unit | H |
| P-MOD-COLLIDE-02 | name collision modules/ vs root → root wins (root scanned last) | modules/x name=foo; root y name=foo | root payload | unit | M |
| P-MOD-FRAGILE-01 | FOOT-GUN: `module: 5` (non-dict) → AttributeError during scan | base/bad.yaml `"module: 5"` | `pytest.raises(AttributeError)` | unit | M |

#### `parse_register_map` (parser.py:114)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| P-RM-01 | found in base_dir (wins first) | base/regs.yaml | dict | unit | H |
| P-RM-02 | base precedence over lib_dir | same name both, distinct payloads | base payload | unit | M |
| P-RM-03 | found directly in a lib_dir | lib/regs.yaml | dict | unit | H |
| P-RM-04 | found via `lib_dir/register_maps/<basename>` fallback (uses basename) | call `"register_maps/foo.yaml"` file at lib/register_maps/foo.yaml | dict | unit | H |
| P-RM-05 | NOT found → None + `Warning: Register map not found:` | `"ghost.yaml"`; capsys | None; warning | unit | H |
| P-RM-06 | empty found file → `{}` (distinguishes from not-found None) | base/regs.yaml `""` | `{}` | unit | M |
| P-RM-07 | found file YAML error → `{}` + YAML warning, NOT not-found warning | base/regs.yaml invalid | `{}`; not "Register map not found" | unit | M |

#### `parse_interface_definition` (parser.py:141) — ZERO call sites; test directly

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| P-IF-01 | found in base_dir directly / in base/interfaces/ | base/myif.yaml ; base/interfaces/myif.yaml | dict | unit | H |
| P-IF-02 | found in lib_dir / lib_dir/interfaces/ | lib variants | dict | unit | H |
| P-IF-03 | base precedence; direct beats interfaces/ within a base | conflicting names | base / direct wins | unit | M |
| P-IF-04 | FALLBACK to bundled when absent locally | `"ahb_master.yaml"` not local | real bundled dict (non-None) | unit | H |
| P-IF-05 | local copy shadows bundled | base/interfaces/ahb_master.yaml sentinel | local | unit | M |
| P-IF-06 | NOT found anywhere → None (NO warning — this fn never warns) | `"does_not_exist_anywhere.yaml"`; capsys | None; `capsys.out==""` | unit | H |
| P-IF-07 | bundled dir exists (branch reachable) | assert `_BUNDLED_INTERFACES_DIR.is_dir()` | True | unit | M |

#### `list_module_files` (parser.py:163)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| P-LIST-01 | returns all cache keys (names AND stems); name==stem dedup | foo.yaml name=foo; bar.yaml name=baz | `sorted == ["bar","baz","foo"]` | unit | H |
| P-LIST-02 | triggers scan when unscanned; latch set | fresh parser → call | non-empty; `_modules_scanned True` | unit | M |
| P-LIST-03 | empty lib → [] | base no modules | `[]` | unit | M |

#### Cross-cutting

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| P-X-01 | all 3 warnings to stdout via print (capsys.out, err empty) | trigger each | in out; err empty | unit | M |
| P-X-02 | repeated parse_module returns identical cached object | call twice | `r1 is r2` | unit | M |
| P-X-03 | base_dir NOT auto-scanned when lib_dir given | `SoCParser(base,[other])` module only in base | `parse_module`→None | unit | M |

---

### 5.3 `tests/unit/test_builder.py` — `soc_model/builder.py` + `soc_model/model.py`

Pure. Prefer calling private `_build_*` helpers directly with literal dicts for mapping tests (no FS); use `build_module_from_yaml` for the full `build_system`/`_resolve_instances` paths.

#### `build_system` / `_build_module` (builder.py:23/36)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| B-01 | no `module:` key → ValueError `"No 'module:' key in <f>"` | fixture yaml only `not_a_module:` | raises w/ message | unit | H |
| B-02 | missing top file → ValueError (parser returns `{}`) + stdout `Warning: File not found` | `build_system("nope.yaml")` | raises; warning | unit | H |
| B-03 | returns top Module name/gen/desc; source_file = filename | minimal top | fields match; `source_file=="top.yaml"` | unit | H |
| B-04 | `_resolve_instances` runs (top instance resolved_module set) | top w/ one module instance | `instances[0].resolved_module is not None` | unit | H |
| B-05 | default name `'unknown'`; .get defaults gen=False/desc=''/source_file='' | `module:{gen:true}` / `{'name':'x'}` | as stated | unit | M |
| B-06 | FOOT-GUN: cache short-circuit — same name returns first obj, ignores new fields | `_build_module({'name':'X'})` then `({'name':'X','desc':'d'})` | `a1 is a2`; `a2.desc==''` | unit | H |
| B-07 | params dict form → Param(type,default,desc) | `{P:{type:int,default:8,desc:w}}` | fields | unit | H |
| B-08 | params scalar form → Param(default=val,type='int') | `{PLAIN:42}` | default 42, type int | unit | H |
| B-09 | params dict missing keys → .get defaults | `{P:{}}` | type='int',default=None,desc='' | unit | M |
| B-10 | clocks/resets .get defaults; reset active default 'low' | `clocks:[{}]` `resets:[{name:r}]` | defaults | unit | M |
| B-11 | interfaces → `_build_interface` appended | `interfaces:[{name:clk}]` | name=clk,type=wire,dir=in | unit | H |
| B-12 | assigns target/expr str-coerced; missing→defaults | `[{target:1,expr:2,...}]` / `[{}]` | `'1'`/`'2'`; '' defaults | unit | M |
| B-13 | internal_wires direction='internal', type default wire | `[{name:w}]` | dir internal | unit | M |
| B-14 | glue non-concat → inputs+input str-coerced, concat_inputs=[] | `or_combine inputs:[1,a] input:9` | `['1','a']`,`'9'`,`[]` | unit | H |
| B-15 | glue CONCAT branch → concat_inputs preserved dicts, inputs cleared, pad set | `concat inputs:[{bit:0,signal:s}] pad_zero_above:4` | concat_inputs kept; inputs=[]; pad=4 | unit | H |
| B-16 | glue input None stays None (`if input is not None`) | `constant value:0` no input | `input is None` | unit | M |
| B-17 | srams RAW values, no resolution; defaults addr=14/data=32 | `[{name:r,addr_width:"$N",data_width:32}]` / `[{name:r2}]` | `'$N'` preserved; 14/32 | unit | H |
| B-18 | address_decode/firmware/build_info present→built / absent→None | with/without each key | set / None | unit | M |
| B-19 | minimal module → all optional lists empty | `{name:x}` | clocks/resets/.../srams == [] | unit | M |

#### `_build_interface` / `_build_instance` / `_build_interconnect` (builder.py:162/172/200)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| B-20 | interface .get defaults | `{name:i,type:ahb,direction:target,params:{A:1}}` / `{}` | full / name='',type='wire',dir='in' | unit | H |
| B-21 | instance `module:` form → is_rtl_module False | `{instance_name:u,module:child}` | False; module_name='child' | unit | H |
| B-22 | instance `rtl_module:` form → is_rtl_module True | `{instance_name:u,rtl_module:rtlx}` | True; module_name='rtlx' | unit | H |
| B-23 | neither module nor rtl_module → module_name '' | `{instance_name:u}` | `''`; False | unit | M |
| B-24 | addressable/condition/params .get defaults | `{instance_name:u}` vs full | False/None/{} vs set | unit | M |
| B-25 | connections port/conn str-coerced; desc default | `[{port:1,conn:2}]` / `[{}]` | `'1'/'2'` / `''/''` | unit | H |
| B-26 | inline interfaces parsed into inline_interfaces | instance `interfaces:[{name:clk}]` | `inline_interfaces[0].name=='clk'` | unit | M |
| B-27 | interconnect gen default True, type 'ahb_lite', params {} | `[{name:ic}]` | defaults | unit | M |
| B-28 | target dict → fields incl protocol lowercased | `{name:t,protocol:APB,base:16}` | protocol='apb',base=16 | unit | M |
| B-29 | target bare string → name=str(t), defaults | `targets:[foo]` | name='foo',base=0,protocol='ahb' | unit | M |
| B-30 | initiator targets dict (visibility) vs str | `targets:[{name:t,visibility:[..]},t2]` | first vis set; second None | unit | M |
| B-31 | initiator instance/passthrough .get defaults | `initiators:[{name:m}]` | None/None/targets [] | unit | L |

#### `_resolve_instances` (builder.py:368)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| B-32 | normal module ref → resolved_module attached; child source_file=`modules/<name>.yaml` | top w/ module:child, child yaml | name; `source_file=='modules/child.yaml'` | unit | H |
| B-33 | SHARED: two instances same module → SAME Module object | two module:child instances | `i0.resolved_module is i1.resolved_module` | unit | H |
| B-34 | missing module → resolved_module stays None (no raise) [parse_module→None half] | instance module:nosuch | `is None`; no exception | unit | H |
| B-35 | discovered dict lacking 'module' key → resolved_module None [`and 'module' in` half] | discovered yaml top key != module | None | unit | M |
| B-36 | rtl_module stub: Module(name,gen=False,desc='Standalone RTL module: <n>'), interfaces = inline copies | rtl_module:r + 1 inline iface | stub fields; `stub.interfaces==inline` | unit | H |
| B-37 | rtl_module empty-stub: no inline interfaces → stub.interfaces==[] | rtl_module:bare | `[]` | unit | H |
| B-38 | recursion into resolved child instances (grandchild resolved) | child instantiates grandchild | `child.instances[0].resolved_module is not None` | unit | M |
| B-39 | $PARAM passthrough verbatim on child instance params (NOT resolved by builder) | top instance params:{WIDTH:"$NWIDE"} | `inst.params=={'WIDTH':'$NWIDE'}` | unit | H |
| B-40 | rtl stub interfaces are SAME objects from inline (aliasing) | rtl inst inline iface | `stub.interfaces[0] is inst.inline_interfaces[0]` | unit | L |

#### model.py dataclasses & properties

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| M-01 | Interface.width: wire no WIDTH→1; int WIDTH→int | `Interface('w')` / `params={'WIDTH':8}` | 1 / 8 | unit | H |
| M-02 | FOOT-GUN: wire str WIDTH→None | `params={'WIDTH':'8'}` | None | unit | H |
| M-03 | non-wire type→None regardless | `Interface('a','ahb',params={'WIDTH':8})` | None | unit | H |
| M-04 | addr_width: ADDR_WIDTH int / ADDR_W fallback | `{'ADDR_WIDTH':12}` / `{'ADDR_W':10}` | 12 / 10 | unit | H |
| M-05 | FOOT-GUN: addr_width value 0 → None (`or` falsy) | `{'ADDR_WIDTH':0}` | None | unit | H |
| M-06 | addr_width missing/str → None | `{}` / `{'ADDR_WIDTH':'12'}` | None | unit | M |
| M-07 | data_width: DATA_WIDTH/DATA_W; axis_byte default 8 | `{'DATA_WIDTH':32}` / `Interface('s','axis_byte')` | 32 / 8 | unit | H |
| M-08 | data_width axis_byte WITH DATA_WIDTH → that int; FOOT-GUN 0→None | `axis_byte {'DATA_WIDTH':16}` / `{'DATA_WIDTH':0}` | 16 / None | unit | M |
| M-09 | is_input True for in/target/receiver | each direction | True×3, False else | unit | H |
| M-10 | is_output True for out/initiator/sender; ASYMMETRY False for receiver/target | each direction | True×3; False receiver/target | unit | H |
| M-11 | is_bidirectional only for inout | inout vs others | True / False | unit | M |
| M-12 | Connection.is_unconnected iff conn=='unconnected' | both | True / False | unit | H |
| M-13 | Connection both args required positionally | `Connection('p')` | `pytest.raises(TypeError)` | unit | L |
| M-14 | InterconnectTarget defaults | `InterconnectTarget('a')` | base=0,size=0,sw_access='rw',protocol='ahb',subordinate_bus=False, others None | unit | H |
| M-15 | InterconnectInitiator.target_names flat list; empty default [] | 2 targets / none | `['a','b']` / `[]` | unit | H |
| M-16 | Interconnect defaults gen=True,type='ahb_lite', lists empty | `Interconnect('ic')` | as stated | unit | H |
| M-17 | Module defaults: all lists empty, params {}, gen False, address_decode/firmware/build_info None | `Module('t')` | as stated | unit | H |
| M-18 | Module.get_interface match / missing / empty-list → None | populated/absent/empty | Interface / None / None | unit | M |
| M-19 | Module.get_instance / get_interconnect found / not-found | populated | obj / None | unit | M |
| M-20 | Module.flat_params {name:default}; empty→{} | params w/ Param / none | mapping / {} | unit | M |
| M-21 | ResolvedSram.depth==2^aw; size_bytes==depth*(dw//8) | aw=10,dw=32 | 1024 / 4096 | unit | M |
| M-22 | Instance defaults | `Instance('u')` | module_name='',is_rtl False,addressable False,condition None,lists empty,resolved None | unit | M |
| M-23 | SramEntry / GlueLogicEntry defaults | `SramEntry('r')` / `GlueLogicEntry('g','passthrough','o')` | aw=14,dw=32 / inputs/concat [], input/value/width/pad None | unit | L |

---

### 5.4 `tests/unit/test_validator.py` — `soc_model/validator.py`

Pure. Build inline via dataclasses. **Isolation:** populate one `Module` list field; the other 5 checks emit nothing. **Double-emission trap:** an `Instance` with `resolved_module=None` fires BOTH a `_validate_connections` "Unresolved module" warning AND a `_validate_instance_references` "not found" error — prefer `category`/`level`/`any(...)` filters over exact counts, OR set `resolved_module=child` manually to isolate connection/driver checks.

#### ValidationMessage / properties / validate_all

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| V-VM-01 | `__str__` with instance → `[WARNING] (reference) u.p: msg` | `ValidationMessage('warning','reference','u','p','msg')` | exact string | unit | H |
| V-VM-02 | `__str__` empty instance → bare port loc | `('error','connection',port='o',message='m')` | `[ERROR] (connection) o: m` | unit | H |
| V-VM-03 | default fields ''; `.level.upper()` | `('info','x')` | instance/port/message ''; `[INFO]` | unit | M |
| V-PR-01 | `.errors` filters level=='error' only | model w/ 1 C1 error + 1 C2 warning | len 1, all error | unit | H |
| V-PR-02 | `.warnings` filters level=='warning' only | same | len 1, all warning | unit | H |
| V-PR-03 | both [] when no messages; validate_all==[] | `Module('t')` | [] / [] | unit | M |
| V-VA-01 | validate_all resets self.messages (idempotent) | call twice on 0-msg model | second len 0 | unit | M |

#### _validate_connections / width / direction / bus

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| V-C-01 | unresolved child → warning (reference) "Unresolved module 'nope'"; continues | Instance(module_name='nope') resolved None | 1 warning | unit | H |
| V-C-02 | port not on child → warning "Port 'x' not found on module '<c>'" | child iface real; conn port=ghost | warning reference | unit | H |
| V-C-03 | bus-member expanded port recognized → no warning | child iface dbg_ahb; conn `_slvaddr` member | [] | unit | H |
| V-C-04 | bit-sliced port name parsed for lookup | child iface p w8; conn `p[3:0]`→net w4 | resolves to p, slice 4 | unit | M |
| V-CW-01 | wire width mismatch → error (width) "port 'p' has width 8 ... conn ... width 4" | child p w8; parent net w4 | error width | unit | H |
| V-CW-02 | port width None (bus no slice) → skip width | bus iface, `_get_effective_width` None | no width msg | unit | M |
| V-CW-03 | conn width None (target non-wire / unknown / parent ahb) → skip width | conn to ahb iface or unknown | no width error (covers final `return None`) | unit | M |
| V-CW-04 | conn-side bit-slice gives width (match) | conn `bus[3:0]` (w32), port w4 | no error | unit | H |
| V-CW-05 | conn = other instance wire port width | child p w8; conn v.q (q wire w8) | no error; differ→error | unit | M |
| V-CD-01 | wire output↔output → error (direction) | both insts iface o out; a→b.o | error direction | unit | H |
| V-CD-02 | wire input↔input → error | both iface d in; a→b.d | error direction | unit | H |
| V-CD-03 | bidirectional (inout) → skip | iface io inout; conn→y.io | [] | unit | H |
| V-CD-04 | conn to parent port (no conn_inst) → no error | child clk in → parent sys_clk in | [] | unit | H |
| V-CD-05 | conn_inst names missing instance → no dir error | conn missing.o | no direction error | unit | M |
| V-CD-06 | bus incompatible (initiator↔initiator) → WARNING not error | both ahb initiator; a→b.m | warning direction | unit | H |
| V-CD-07 | bus compatible (initiator↔target / out↔in / sender↔receiver) → no msg | each pair | [] (parametrize 6) | unit | H |
| V-CD-08 | bus-type dispatch only for ahb/apb/axis/axis_byte (apb routes to bus check) | apb incompatible dirs | warning not wire error | unit | M |

#### _validate_interconnects / _validate_address_overlaps / _validate_instance_references

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| V-IC-01 | initiator → undefined target → error (reference) "references target 'ghost' which is not defined" | ic.targets=[t1]; init.targets=[t1,ghost] | error reference; instance=ic.name | unit | H |
| V-IC-02 | all defined → no error | init.targets ⊆ targets | [] | unit | H |
| V-IC-03 | multiple interconnects iterated; empty init targets no-op | 2 ics one bad | 1 error | unit | M |
| V-AO-01 | overlap → error (address); EXACT msg `"Address overlap: 'a' (0x00000000-0x00000FFF) overlaps with 'b' (0x00000800-0x000017FF)"` | a(0,0x1000),b(0x800,0x1000) | error; exact string | unit | H |
| V-AO-02 | BOUNDARY: exactly adjacent (t1_end==t2.base) → NO error (`>` not `>=`) | a(0,0x1000),b(0x1000,0x1000) | [] | unit | H |
| V-AO-03 | sorted by base before pairwise (out-of-order still detected) | b(0x1000),a(0,0x1800) | overlap; t1=a | unit | H |
| V-AO-04 | single / empty targets → no error | [one] / [] | [] | unit | M |
| V-AO-05 | end math `t1_end-1` in message | a(0x2000,0x100) overlap b | shows `0x00002000-0x000020FF` | unit | M |
| V-AO-06 | 3 targets, only adjacent pair overlaps → 1 error (pairwise) | a,b ok; b,c overlap | 1 error | unit | M |
| V-IR-01 | unresolved + not rtl → error (reference) "Module 'nope' not found" (AND the connections warning) | Instance(module_name='nope',resolved None,is_rtl False) | both present; assert error | unit | H |
| V-IR-02 | is_rtl_module True → NO "not found" error | Instance(is_rtl=True,resolved None) | no not-found error | unit | H |
| V-IR-03 | resolved_module set → no error | inst.resolved_module=child | [] | unit | H |

#### _validate_known_rtl_modules (_REQUIRED_RTL_PORTS)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| V-RTL-01 | cmsdk_ahb_to_apb missing PREADY/PSLVERR → error listing sorted missing | rtl inst, inline=[PCLKEN] | error connection; "missing required port declarations: PREADY, PSLVERR" | unit | H |
| V-RTL-02 | declared ports case-insensitive (.upper()) | inline lowercase pclken/pready/pslverr | NO missing error | unit | H |
| V-RTL-03 | all required declared → no error | inline all 3 | [] | unit | H |
| V-RTL-04 | non-rtl_module instance skipped | module_name=cmsdk..., is_rtl False | no missing error | unit | H |
| V-RTL-05 | module_name not in dict → skip | rtl cmsdk_apb_uart | [] | unit | H |
| V-RTL-06 | missing list SORTED alphabetical | declare only PSLVERR | "PCLKEN, PREADY" | unit | M |
| V-RTL-07 | empty inline → all 3 missing | inline=[] | "PCLKEN, PREADY, PSLVERR" | unit | M |
| V-RTL-08 | pin _REQUIRED_RTL_PORTS dict contract | introspect | `{'cmsdk_ahb_to_apb':{'PCLKEN','PREADY','PSLVERR'}}` | unit | M |

#### _validate_driver_coverage (C1/C2)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| V-DC-01 | undriven top wire output → C1 error (connection) "Top-level output 'o' has no driver" | top iface o wire out; nothing | error connection port=o | unit | H |
| V-DC-02 | instance output drives net → suppress C1 | child o out; conn o→topout | [] | unit | H |
| V-DC-03 | glue output / assign target drives net → suppress C1 | glue output=o / Assign(target=o) | [] each | unit | H |
| V-DC-04 | instance INPUT port does NOT drive | child i in; conn i→o(out) | C1 still fires | unit | H |
| V-DC-05 | `unconnected` conn skipped, doesn't drive/reference | child oo out→unconnected; o separately | C1 on o still fires | unit | H |
| V-DC-06 | hierarchical conn (`inst.port`) → `_flat_net` None → not flat net | conn oo→other.foo | neither drives nor references flat | unit | M |
| V-DC-07 | non-wire top output (ahb initiator) NOT C1-scoped (type guard) | top iface m ahb initiator | [] | unit | H |
| V-DC-08 | child None (unresolved inst) → child_ports {} → referenced but not driven | top out o; unresolved inst conn x→o | C1 fires | unit | M |
| V-DC-09 | declared top wire never referenced → C2 warning "declared but never referenced" | top din wire in; nothing refs | warning connection port=din | unit | H |
| V-DC-10 | C1-flagged port NOT double-reported by C2 | top o wire out undriven | exactly 1 (C1), no C2 for o | unit | H |
| V-DC-11 | interconnect conn references net → suppress C2 | top din; ic conn hclk→din | [] | unit | H |
| V-DC-12 | glue input/inputs/concat_inputs reference net → suppress C2 | glue input=din / concat_inputs=[{signal:din}] | [] | unit | M |
| V-DC-13 | instance conn (any direction) references net → suppress C2 | child i in; conn i→din | [] | unit | M |
| V-DC-14 | non-wire top port skipped by C2 | top iface m ahb unreferenced | no C2 | unit | M |
| V-DC-15 | MIX: C1+C2 coexist (feeds V-PR-01/02) | top o out undriven + din in unref | 1 error + 1 warning | unit | H |

---

### 5.5 `tests/golden/test_toplevel_sv.py` — `soc_model/toplevel.py` (+ `subsystem.py`)

`SoCTopLevelBackend(top).generate(rtl_dir, flist_dir)` → `<top.name>.sv`. Timestamp+copyright stripped by snapshot helper. Most `_build_*` context methods return Python lists/dicts and are unit-testable directly (no render). Reserve `golden` for the rendered `.sv`.

#### generate / params / ports (unit + golden)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| TL-01 | golden: full `.sv` byte-match (ts stripped) | minimal_soc, two_region_soc, passthrough_soc | == snapshot | golden | H |
| TL-02 | returns Path==rtl_dir/<name>.sv; file exists; has `module <name> #(` + `endmodule` | minimal | Path; substrings | golden | H |
| TL-03 | flist default = rtl_dir.parent/'flist'/<name>_toplevel.flist when None | generate(rtl) only | flist at parent/flist | unit | H |
| TL-04 | Environment is None → returns None, no .sv | monkeypatch toplevel.Environment=None | None | unit | M |
| TL-05 | _build_params: int >=2^31 → `32'hXXXXXXXX`; smaller as-is | default 0x80000000 / 32 | hex / 32 | unit | H |
| TL-06 | _build_ports wire → input/output, width from WIDTH (default 1) | wire in/out | dirs/widths | unit | H |
| TL-07 | swd iface `_swd` suffix → `<base>_swdi` etc.; width '' | name cpu_0_swd | cpu_0_swdi/swclk/swdo/swdoen | golden | H |
| TL-08 | dbg_ahb → 8 ports widths 32/2/1; gpio → in/out/outen default w16 | dbg_ahb / gpio ifaces | ports | golden | M |
| TL-09 | _expand_ahb_port target → AHB_TARGET dirs (hsel/hready in, hreadyout out) | iface direction:target | hsel input, hreadyout output | golden | H |
| TL-10 | _expand_ahb_port initiator → AHB_INITIATOR (no hsel/hreadyout) | direction:initiator | haddr output, hrdata input, no hsel | golden | H |
| TL-11 | EXCLUDE drops listed signals | EXCLUDE:[hburst] | no `_hburst` port | unit | H |
| TL-12 | passthrough-initiator (no hready excl) → AHB_INITIATOR INVERTED dirs (master out→module input) | ic init passthrough, no EXCLUDE hready | haddr INPUT, hrdata OUTPUT, no hsel | golden | H |
| TL-13 | passthrough-initiator WITH hready excl → target boundary, hready dropped | EXCLUDE:[hready] | hsel input, no hready port | golden | H |
| TL-14 | axis input + HAS_FLUSH → extra flush OUTPUT; output → no flush | axis in/out HAS_FLUSH | flush only on input | unit | M |

#### wire builders (passthrough heuristic core)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| TL-20 | _build_internal_wires skips name == top port; $PARAM width resolved | wire name collides / WIDTH '$N' | skipped / int | unit | H |
| TL-21 | passthrough TARGET → `<tgt.name>_<sig>` wires emitted | ic target passthrough | system_haddr etc. | golden | H |
| TL-22 | passthrough INITIATOR → `<init.passthrough>_<sig>` wires | ic init passthrough | eth_ss_0_haddr | golden | H |
| TL-23 | initiator name collides w/ pt-target prefix → `<prefix>_ic_<sig>` wires | cpu_0 init + system target passthrough=cpu_0 | cpu_0_ic_haddr | golden | H |
| TL-24 | bus wires already top ports NOT re-declared; `seen` dedup | port-named bus sig / two insts same ic port | absent / single bundle | unit | M |

#### xref resolution (recursion + cycle break)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| TL-30 | _resolve_conn_to_wire no dot → unchanged | `'sys_hclk'` | `'sys_hclk'` | unit | H |
| TL-31 | resolves to src inst's connected input/internal target directly | u_a.X conn'd to net | net name | unit | H |
| TL-32 | target is top OUTPUT → flat wire (multi-driver guard) | u_a.X → top output | `u_a_X` | unit | H |
| TL-33 | CYCLE broken via _visited → flat wire, no infinite loop | u_a.X→u_b.Y→u_a.X | flat | unit | H |
| TL-34 | bit-slice preserved `[h:l]`/`[h]` | conn w/ [7:0] / [3] | preserved | unit | M |
| TL-35 | _build_xref_wires hierarchical conn `u_a.PORT` → flat wire `u_a_PORT` | inst conn u_a.X | xref declared | golden | M |
| TL-36 | resolves-to-real-wire → NOT declared; ic ref not xref | conn to top input / ic.port | not declared | unit | M |

#### instances / connection expansion / coalescing / ahb naming

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| TL-40 | _build_instances: param `$`-prefix stripped; unconnected → signal None | params {P:'$X'} / conn unconnected | 'X' / None | unit | H |
| TL-41 | ic bus ref expands to per-signal connections | inst port→ic.initname | many .haddr() conns | golden | H |
| TL-42 | bare-name conn to ahb/axis/swd top iface → protocol expansion | conn = top iface name | expanded conns | golden | M |
| TL-43 | _expand_bus_connection initiator pt-collision → `_ic_` wires | cpu_0 init + cpu_0 pt target | _ic_ wires | golden | H |
| TL-44 | _expand_bus_connection target child-pt-init → AHB_INITIATOR, hready→hreadyout map | child w/ pt-init ic | hready→`_hreadyout` | golden | H |
| TL-45 | _ahb_inst_port_name: gen:False single-AHB → BARE UPPER (HADDR); multi → prefixed | resolved gen False 1 vs 2 ahb ifaces | HADDR / prefixed | unit | H |
| TL-46 | _ahb_inst_port_name port ahb_slave→bare; uppercase→PORT_HADDR; lowercase→port_sig | port variants | naming | unit | M |
| TL-47 | _coalesce_bit_select: 1-bit/full-width→`.port(sig)`; multi bits→`{msb,..,lsb}` desc | port[0] / port[1],port[0] | bare / concat | unit | H |
| TL-48 | _coalesce signal None passes through | unconnected | preserved | unit | M |

#### glue instances (every type)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| TL-50 | passthrough/or_reduce → input+width | each | keys | unit | H |
| TL-51 | or_combine → inputs[]+n_inputs+width | 3 inputs | n_inputs 3 | unit | M |
| TL-52 | and_gate → input_a/input_b (idx 0/1, '' if missing) | 2 / 1 inputs | input_b '' fallback | unit | M |
| TL-53 | constant → value+width (default 1) | width None | width 1 | unit | M |
| TL-54 | concat → bits MSB→LSB, `1'b0` pad, pad_zero_above honored; skips malformed | concat_inputs+pad | bits, fills | unit | H |
| TL-55 | _infer_width: bit-slice high-low+1; internal_wire WIDTH; iface match; default 1 | each source | widths / 1 | unit | H |

#### interconnect + passthrough tie-offs (HTRANS gate)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| TL-60 | _build_interconnect None when no interconnects | Module no ic | None | unit | H |
| TL-61 | module_name strips `_ahb_interconnect`/`_interconnect` + appends `_interconnect` | ic name nanosoc_ahb_interconnect | nanosoc_interconnect | unit | H |
| TL-62 | initiator pt-collision → `<prefix>_ic_<sig>` signals | cpu_0 init + cpu_0 pt tgt | _ic_ | golden | H |
| TL-63 | target hready → `<tgt>_hreadymux` fed by `<tgt>_hready` | any tgt | hreadymux mapping | golden | H |
| TL-64 | uses interconnects[0] only (extra ics ignored) | 2 ics | second absent | unit | M |
| TL-65 | _build_passthrough_tieoffs: HTRANS GATED by hsel `tgt_hsel ? tgt_htrans : 2'b00` | pt target w/ htrans top port | gated expr present | golden | H |
| TL-66 | hreadyout from port if exists, else tie `1'b1` | pt target w/ & w/o hreadyout port | port / 1'b1 | golden | H |
| TL-67 | non-passthrough target → NO tieoffs | normal tgt | empty | unit | M |
| TL-68 | golden structural: passthrough_soc .sv contains `hsel ?` + `: 2'b00` | passthrough fixture | substrings | golden | H |

---

### 5.6 `tests/golden/test_flists.py` — flist outputs across backends

Covers `_toplevel.flist` (toplevel), `<pkg>.flist` (config_pkg), `<ic>_interconnect.flist` (ahb), `<name>_system.flist` / `<chip>_chip.flist`. Timestamp stripped.

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| FL-01 | toplevel `_toplevel.flist` byte-match | minimal/two_region | == snapshot | golden | H |
| FL-02 | config_pkg flist NOT written when flist_dir None | generate w/o flist_dir | no .flist | unit | H |
| FL-03 | config_pkg flist written when given; contains `output_path.resolve()` + header | flist_dir set | exists, absolute path | golden | H |
| FL-04 | ahb `<prefix>_interconnect.flist` produced (perl-skip) byte-match | minimal | == snapshot | golden | H |
| FL-05 | system `<name>_system.flist` default dir = rtl_dir.parent/flist; lists .sv | system fixture | exists, path | golden | M |
| FL-06 | chip `<chip>_chip.flist` lists both `.v` + `_pads.v` | system fixture | both listed | golden | M |

---

### 5.7 `tests/golden/test_firmware.py` — `soc_model/firmware.py`

`SoCFirmwareBackend(top).generate(output_dir)`. Macro prefix = `top.name.upper()` (e.g. `firmware_soc` → `FIRMWARE_SOC_*`). Keep `git_hash`/`timestamp` out of goldens (non-deterministic). `generate` builds stitched-map + cpu-initiator contexts BEFORE the jinja2-None check.

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| FW-01 | no `firmware:` → prints "No firmware: section", writes nothing | top firmware=None, no child fw | output_dir empty | unit | H |
| FW-02 | _find_firmware: top.firmware; else first child resolved_module.firmware | top set / child set | correct fw | unit | H |
| FW-03 | jinja2 None → builds contexts then warns + returns, no files | monkeypatch firmware.Environment=None | no files | unit | M |
| FW-04 | golden: `<T>_<profile>_memory.ld` MEMORY block ORIGIN/LENGTH per region | firmware_soc | exact `ORIGIN`/`LENGTH` | golden | H |
| FW-05 | golden: `<T>_memmap.h` define `<TOP_UPPER>_<NAME>_BASE 0x...` | firmware_soc | `FIRMWARE_SOC_<NAME>_BASE` | golden | H |
| FW-06 | _FW_PARAMS: int SYS_CLK_FREQ_HZ → `<TOP_UPPER>_SYS_CLK_FREQ_HZ` w/ `UL`; non-int → str no UL; absent → none | param 25000000 / "x" / missing | `25000000UL` / "x" / absent | unit | H |
| FW-07 | _build_linker_context: target w/o region_type → WARNING + skip | linker→non-memory target | stdout warn; absent | unit | M |
| FW-08 | region no phys_size anywhere → WARNING + skip | no phys_size | warn; absent | unit | M |
| FW-09 | phys_size str `int(x,0)` parsed; size_adjust added | "0x1000" / size_adjust=16 | length 0x1000 / +16 | unit | M |
| FW-10 | size comment buckets MB/K/bytes | 0x100000/0x400/0x10 | "1MB"/"1K"/"16 bytes" | unit | M |
| FW-11 | _build_cpu_initiator_data: unknown cpu_initiator → ValueError listing available | fw.cpu_initiator="nope" | raises w/ "not found" + list | unit | H |
| FW-12 | cpu_initiator default 'cpu_0' when falsy | fw.cpu_initiator='' | uses 'cpu_0' | unit | M |
| FW-13 | _derive_memory_regions filter: memory/periph/peripheral only; `periph`→`peripheral` | targets memory/periph/debug/None | debug+None excluded; normalized | unit | H |
| FW-14 | _resolve_address 'default' w/ target_bases → canonical base; w/o → lowest non-alias | target_bases hit / miss | base / lowest | unit | H |
| FW-15 | _resolve_address alias → region remapping=='alias' else 2nd window else ValueError | 2 windows / 1 window | 2nd / raises | unit | M |
| FW-16 | _resolve_address remapped → remap_region match | remap target | remap mem_lo | unit | M |
| FW-17 | golden: `<T>_memmap.cmake` includes top_name + cpu (NanoSoC_DEFAULT_CPU) | firmware_soc fw.cpu | cpu line | golden | M |
| FW-18 | golden: `<T>_adp.vh` + `<T>_adp.py`; no adp → zero-defaults (adp_nibbles==[]) | with / without adp | content / defaults | golden | M |
| FW-19 | output filenames use `top.name` prefix | any fixture | `<top>_*` | unit | H |
| FW-20 | _stitch_module: child interconnect targets folded w/ base_offset; bridge-merge path | two-level fixture | parent gets child regions | golden | M |
| FW-21 | duplicate target name across ICs → first-wins | two ICs same name | first kept | unit | M |

---

### 5.8 `tests/golden/test_config_pkg.py` — `soc_model/soc_config_pkg.py`

`SoCConfigPkgBackend(top).generate(rtl_dir, flist_dir=None, config_overrides=None)`. Emits `<name>_soc_config_pkg.sv`, `<name>_soc_config.vh` (always), flist (only if flist_dir). Returns `.sv` Path.

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| CP-01 | returns Path to `<name>_soc_config_pkg.sv`; `package <name>_soc_config_pkg;` + `endpackage` present | minimal | Path; substrings | golden | H |
| CP-02 | `.vh` ALWAYS written even when flist_dir None | call w/o flist_dir | .vh exists | unit | H |
| CP-03 | flist NOT written when flist_dir None; written + absolute .sv path when given | with/without | absent / present | unit | H |
| CP-04 | design_params filter: only `_DESIGN_PARAMS ∩ top.params` emitted | fixture SYS_ADDR_W + FOO | SYS_* present, FOO absent | unit | H |
| CP-05 | override REPLACES YAML default value | YAML SYS_ADDR_W=32, override=24 | `= 24` | unit | H |
| CP-06 | param as override but NOT in YAML still emitted (elif) | overrides={'NANOSOC_PARTNUMBER':0xABCD} fixture lacking | localparam emitted | unit | M |
| CP-07 | str param → quoted localparam | str design param | `= "val"` | golden | M |
| CP-08 | sim params FAST_LOAD & VCD_ENABLE always present default 0; override sets | none / {'VCD_ENABLE':1} | both =0 / =1 | unit | H |
| CP-09 | _write_verilog_header: str → `define NAME "val"`; int → `define NAME val` | str + int param | both define forms in .vh | unit | H |
| CP-10 | source_yaml: top.source_file when set; fallback `'nanosoc_m0_soc.yaml'` when empty | fixture / inline source_file='' | Source line | golden | M |
| CP-11 | Environment None → returns None, no files | monkeypatch Environment=None | None | unit | M |
| CP-12 | empty-params Module → only sim_params + .vh empty design section, no crash | Module('t') | .sv exists, no design localparams | unit | M |
| CP-13 | golden: full `.sv` + `.vh` byte-match (ts stripped) | minimal | == snapshots | golden | H |

---

### 5.9 `tests/golden/test_discovery_yaml.py` — `soc_model/discovery.py`

`SoCDiscoveryBackend(top).generate(build_dir)` → writes `discovery/<ic>_discovery.yaml` (the contract `device_model.py` consumes). Magic `_TABLE_MAGIC=0x534F4344`.

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| DY-01 | only gen:True interconnects produce tables | ic gen True + gen False | only gen True | unit | H |
| DY-02 | recurses into child instances' resolved_module ICs | nested ic | nested table | unit | M |
| DY-03 | header TABLE_ID reset_value == 0x534F4344 | any ic | first reg reset==magic | unit | H |
| DY-04 | TABLE_SIZE packs num_targets[7:0]|num_initiators[15:8]|addr_width[31:16] | 2 tgt 1 init aw 32 | `2|(1<<8)|(32<<16)` | unit | H |
| DY-05 | addr_width from SYS_ADDR_W/ADDR_W/routing_address_width, default 32 | ic.params SYS_ADDR_W=24 | 24 | unit | M |
| DY-06 | _encode_name packs first 4 chars LE, null-pad short | "ab" / "abcdef" | LE int | unit | H |
| DY-07 | TGT_ATTR packs region_type[3:0]|sw_access[7:4]|protocol[15:8]|id[23:16] | known enums | packing | unit | H |
| DY-08 | enum maps unknown/None → 0; case-insensitive | "MEMORY"/"weird"/None | 1/0/0 | unit | H |
| DY-09 | _build_visibility_mask bit per reachable target index | init refs tgt0,tgt2 of [t0,t1,t2] | 0b101 | unit | H |
| DY-10 | initiator target not in target_names → ignored (no bit) | init refs nonexistent | bit absent | unit | M |
| DY-11 | register count == 4 header + 4*num_targets + 2*num_initiators | 2t 1i | 14 | unit | H |
| DY-12 | RegisterMap name `<ic>_discovery`, module `<ic>_discovery_table`, address_width 12 | any | exact | unit | H |
| DY-13 | offsets `0x%03X`; reset `0x%08X`; fields only when reg.fields present | TABLE_VERSION vs TABLE_ID | formats; key present/absent | unit | M |
| DY-14 | golden: `.yaml` structure (`register_map` top key, name/module/gen/desc/address_width 12/data_width 32/registers[]); `# Generated:` stripped | gen ic | == snapshot | golden | H |

---

### 5.10 `tests/golden/test_text_reports.py` — `soc_model/text.py`, `sram.py`, `python.py`

Three text-report backends with no timestamps (fully deterministic goldens). Plus `python.py` round-trip.

#### text.py — `SoCTextBackend(top)` `.generate_memory_map(path)` / `.generate_hierarchy(path)`

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| TX-01 | _fmt_size GB/MB/KB/B buckets | 0x40000000/0x100000/0x800/0x10 | "1 GB"/"1 MB"/"2 KB"/"16 B" | unit | H |
| TX-02 | targets sorted by base | out-of-order targets | sorted | unit | H |
| TX-03 | golden: memory_map.txt | minimal/two_region | == snapshot | golden | H |
| TX-04 | sub-regions rendered for addressable child instance | target w/ instance+ic | indented rows | golden | M |
| TX-05 | initiator connectivity lists target_names | ic w/ initiators | "init: t0, t1" | unit | M |
| TX-06 | golden: hierarchy.txt tree connectors `├──`/`└──`; tags (RTL)/[addressable]/[if X] | rtl/addressable/condition variants | == snapshot | golden | H |
| TX-07 | region_type None → '' (no crash) | target region_type None | blank type column | unit | L |

#### sram.py — `SoCSramBackend(top)` `.generate(output_path)`

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| SR-01 | no SRAMs → "No SRAMs declared" block, no table | top no srams | message | unit | H |
| SR-02 | _walk: addr/data_width $PARAM-resolved; non-int → defaults 14/32 | srams "$AW" / "$UNRESOLVED" | resolved / depth 2^14, w32 | unit | H |
| SR-03 | recursion: child effective params = child defaults + instance overrides | nested w/ override | overridden width | unit | M |
| SR-04 | total line sums size_bytes + count; hierarchy path dotted | 2 srams nested | "Total: 2 SRAM(s)"; path | unit | M |
| SR-05 | golden: full report | fixture w/ 2 srams | == snapshot | golden | M |

#### python.py — `SoCPythonBackend(top)` `.generate(output_path)` (importable .py)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| PY-01 | emitted .py round-trips: exec/compile → Module same name/fields | minimal | reconstructed Module matches | golden | H |
| PY-02 | _collect_modules dedup by name; children before parents | shared child / nested | emitted once; order | unit | H |
| PY-03 | _h: int>=0x100 → `0x%08X`; smaller → repr | 0x10000000 / 4 | hex / "4" | unit | H |
| PY-04 | _var_name: `mod_` + name with -/.→_ | "a-b.c" | "mod_a_b_c" | unit | M |
| PY-05 | _repr_assign optional kwargs conditional; bit==0 emitted (`is not None`) | assign bit=0 | `bit=0` present | unit | M |
| PY-06 | golden: full `.py` | minimal | == snapshot | golden | H |

---

### 5.11 `tests/golden/test_rdl.py` — `soc_model/rdl.py`

`SoCRdlBackend(top)` `.generate_all(build_dir)`/`.generate_single(rm, build_dir)`. The `.rdl` text needs NO toolchain (default golden); RTL emission via regblock → `tooling`. Build via inline Module with `address_decode`→`AddressDecodeSlot.resolved_register_map=RegisterMap(...)`.

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| RD-01 | no register maps → returns [], no rdl dir | top no AD/regmaps | [] | unit | H |
| RD-02 | _collect_register_maps dedup by name (`seen`); + nested AD recursion | two slots same name / nested | once / both | unit | H |
| RD-03 | _rdl_access: RW→rw, RO→r, WO→w, W1C→rw, RC→r, unknown→rw | each | mapped | unit | H |
| RD-04 | _needs_onwrite_woclr only W1C; _needs_onread_rclr only RC (case-insens) | access strings | bool | unit | H |
| RD-05 | _parse_bits "7:0"→(7,0); "3"→(3,3) | both | tuple | unit | H |
| RD-06 | _sanitize_name non-alnum→_; leading digit→_-prefixed | "a-b.c" / "3x" | "a_b_c" / "_3x" | unit | H |
| RD-07 | _format_reset_value None→"0"; int→`W'hX`; hex-str parsed; bad→"0" | each | literal | unit | H |
| RD-08 | _generate_register: fields → per-field blocks; no fields → single DATA[width] | both | structural | unit | H |
| RD-09 | W1C → `onwrite = woclr`; RC → `onread = rclr` | W1C/RC reg | line present | unit | M |
| RD-10 | addrmap sets `default accesswidth = data_width`; regblock `<name>_regs` | rm | line; sanitized name | unit | M |
| RD-11 | _generate_field width=high-low+1; sw mapped; reset/desc conditional | "7:4" | `[4]`, sw line | unit | H |
| RD-12 | golden: full `.rdl` (`// Auto-generated`/`// Source` NOT ts-stripped but deterministic — no date) | inline RegisterMap (header + fielded reg) | == snapshot | golden | H |
| RD-13 | gen:True regmap triggers RTL; gen:False does not | rm.gen toggled | True/False | tooling | M |
| RD-14 | _generate_rtl ImportError path → warn + return False | monkeypatch import fail | False, no crash | unit | M |
| RD-15 | regblock RTL emission produces .sv + apb_wrapper; PADDR[aw-1:0], pprot/pstrb tied | valid rm.gen=True, aw=8 | rtl dir; `[7:0] PADDR` | tooling | M |

---

### 5.12 `tests/unit/test_backends_misc.py` — `protocol_utils.py`, `build_info.py`, `system.py`, `chip.py`, `lint.py`, `html.py`

Pure helpers + small backends + jinja2-None branches + the negative lint paths. The `system`/`chip` rendered goldens are FLAGGED (datetime → verify the template's `Generated:` line is `_strip_timestamps`-matched before committing a golden; prefer structural substring asserts if not). `html.py` is smoke-only.

#### protocol_utils.py (pure — easiest 100 %)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| PU-01 | passthrough_initiator_uses_initiator_boundary: non-dict → True | None/"x"/5/[] | True | unit | H |
| PU-02 | dict w/o EXCLUDE → True; EXCLUDE w/o hready → True | {} / {'EXCLUDE':['hburst']} | True | unit | H |
| PU-03 | EXCLUDE w/ hready → False | {'EXCLUDE':['hready']} | False | unit | H |
| PU-04 | EXCLUDE None (`or []`) → True | {'EXCLUDE':None} | True | unit | H |
| PU-05 | width_str: int 1→''; N>1→`[N-1:0]` | 1 / 32 | '' / '[31:0]' | unit | H |
| PU-06 | width_str: numeric str / `$`-prefixed numeric | '8' / '$8' | '[7:0]' | unit | H |
| PU-07 | width_str: non-numeric str → `[<clean>-1:0]` `$` stripped | '$SYS_ADDR_W' | '[SYS_ADDR_W-1:0]' | unit | H |
| PU-08 | width_str: non-int/non-str (None) → '' | None | '' | unit | M |
| PU-09 | param_width_str identical to width_str (parametrize both fns) | mirror PU-05..08 | same | unit | M |
| PU-10 | bus_member_names ahb → sorted union TARGET+INITIATOR `<name>_<sig>` | Interface('b','ahb') | sorted, has b_hsel/b_haddr/b_hreadyout | unit | H |
| PU-11 | dbg_ahb → 8 DBGAHB names in table order | 'cpu_0_dbgahb','dbg_ahb' | slvaddr..slvresp | unit | H |
| PU-12 | swd `_swd` suffix strips for base; w/o suffix keeps | 'cpu_0_swd'/'dbg' | cpu_0_swdi / dbg_swdi | unit | H |
| PU-13 | axis/axis_stream/axis_byte/gpio member names | each type | per-table names | unit | M |
| PU-14 | wire/clock/unknown → [] | 'wire' | [] | unit | H |

#### build_info.py — `SoCBuildInfoBackend(top)` `.generate(build_dir, project_dir=None, tool_dir=None)`

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| BI-01 | no build_info → None, no files | top.build_info None | None | unit | H |
| BI-02 | header BUILD_INFO_ID==0x42494E46, VERSION==1, FIELD_COUNT==len(fields) | bi 2 fields | reset_values | unit | H |
| BI-03 | int32 field → 1 reg value (non-int→0) | 5 / "x" | 5 / 0 | unit | H |
| BI-04 | string → ceil(max_len/4) regs, 4-byte LE packed (max rounded up) | "ABCDEF" max 6 | 2 regs; names `_0/_1` | unit | H |
| BI-05 | _encode_string_registers truncate+ljust null-pad LE | "ABCD" max 4 | [0x44434241] | unit | H |
| BI-06 | timestamp field → reg `int(time.time())` (NON-DET — shape only, NOT golden) | type timestamp | int>0 | unit | M |
| BI-07 | git_hash field → `_capture_git_hash`; source 'tool'→tool_dir else project_dir (shells git; shape only) | type git_hash, project_dir=tmp | reg present, int | unit | M |
| BI-08 | _capture_git_hash non-git dir → 0 | tmp_path | 0 | unit | H |
| BI-09 | RegisterMap name/module `<top>_build_info`, gen=bi.gen, aw 12 | any | exact | unit | H |
| BI-10 | _write_c_header defines `<NAME>_<REG>_OFFSET 0x%03XU` + `_VALUE` when reset | bi int32 | defines | unit | H |
| BI-11 | golden: `.yaml` (int32+string only, `# Generated:` stripped) | bi int32+string | == snapshot | golden | M |
| BI-12 | FLAG: C-header `/* Generated */` NOT ts-stripped → assert non-timestamp lines only (NO full golden) | bi int32 | structural substrings | unit | M |

#### system.py / chip.py — jinja2-driven (monkeypatch Environment for None branch)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| SY-01 | Environment None → warn + return None | monkeypatch system.Environment=None | None | unit | M |
| SY-02 | _build_params int >=2^31 → `32'hXXXXXXXX` | 0x80000000 | hex | unit | H |
| SY-03 | _build_ports wire / swd (`_swd` strip) / dbg_ahb / gpio (default w16) expansion | each iface type | ports | unit | M |
| SY-04 | _build_internal_wires skip name==port; `$PARAM` width resolved | collide / "$W" | skipped / int | unit | M |
| SY-05 | is_expansion True when 'region_exp' in module_name; param `$`-strip; unconnected→None | variants | flags | unit | M |
| SY-06 | golden OR structural: `<name>_system.sv` (FLAG datetime — verify Generated line stripped, else substrings) | system fixture | == snapshot / substrings | golden | M |
| CH-01 | Environment None → (None, None) | monkeypatch chip.Environment=None | (None,None) | unit | M |
| CH-02 | chip name: system `_system`→`_chip`; core_name fallback | foo_system / core None | foo_chip | unit | H |
| CH-03 | writes `<chip>.v`, `<chip>_pads.v`; gpio_ports WIDTH-resolved; pads module name `<chip>_pads` | system fixture | files; ctx | golden | M |
| CH-04 | golden OR structural: `.v` + `_pads.v` (FLAG datetime/generated_year — verify stripped) | system fixture | == snapshot / substrings | golden | M |

#### lint.py — pure FlistParser + negative paths (pyslang ABSENT, slang binary ABSENT)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| LN-01 | _expand_vars `$(VAR)` from env; unknown left as-is | `$(FOO)/x.sv` env FOO=/a | `/a/x.sv` / unchanged | unit | H |
| LN-02 | parse already-visited flist → no re-parse (recursion guard) | parse twice | files once | unit | H |
| LN-03 | missing flist → WARNING print, return (no raise) | nonexistent | warn; empty | unit | H |
| LN-04 | skip blank + `//` comment lines | flist w/ comments | excluded | unit | M |
| LN-05 | `-f <path>` recursive include (relative→flist_dir) | nested flist | merged | unit | H |
| LN-06 | `+incdir+a+b` splits on `+`, relative→flist_dir-joined | `+incdir+inc1+inc2` | both incdirs | unit | H |
| LN-07 | other `+...` / `-y ` skipped | `+libext+.v`/`-y lib` | ignored | unit | M |
| LN-08 | abs vs rel source; missing → WARNING; dedup | mix | existing deduped; warn | unit | H |
| LN-09 | slang_bin default arg > which('slang') > 'slang' | ctor variants | resolved | unit | M |
| LN-10 | build_env constructs SOCLABS_* paths from project_dir tree | a project_dir | dict keys/paths | unit | H |
| LN-11 | collect_rtl_files rglob *.sv+*.v sorted | tmp tree | sorted | unit | M |
| LN-12 | lint_and_report no rtl files → "No RTL files" + True | empty build_dir | True | unit | H |
| LN-13 | _lint_cli slang absent → returncode -1 → WARNING + False; FileNotFoundError → install msg | no/bogus slang | False; tuple msg | unit | M |
| LN-14 | TOOLING: real pyslang/regblock lint paths SKIP unless tool present | mark.tooling | SKIP cleanly | tooling | L |

#### html.py — smoke only (110 KB f-string, NO datetime → deterministic)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| HT-01 | generate_html writes NON-EMPTY file, no exception | minimal fixture | exists, len>1000 | unit | H |
| HT-02 | contains `<!DOCTYPE html>` + `<title>{top.name}` | any | substrings | unit | H |
| HT-03 | validation_messages stringified into `messages_json`; None default safe | list / None | `str(m)` embedded / no crash | unit | M |
| HT-04 | embedded JSON valid (extract `const graphData`/`validationMessages`, json.loads) | any | parses | unit | M |
| HT-05 | two runs byte-identical (no datetime/random) | run twice | equal | unit | M |
| HT-06 | empty top (no instances/ICs) → valid HTML, empty graphs | bare Module | no crash, written | unit | M |
| HT-07 | _classify_link_type port/conn → ahb/apb/axis/wire/clock/reset | crafted strings | each branch | unit | L |

---

### 5.13 `tests/tooling/*` — opt-in EDA/regblock tier (SKIP if absent)

`@pytest.mark.tooling`. MUST skip cleanly (never fail) when env/tool missing. **NEVER write under the Arm IP library (`$ARM_IP_LIBRARY_PATH`, `$ARM_AAA_ROOT`) and NEVER export `ARM_IP_LIBRARY_PATH` to those paths.**

#### `tests/tooling/test_ahb_perl.py` — `ahb._run_build_bus_matrix` (perl, subprocess timeout=120)

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| AT-01 | ARM_IP_LIBRARY_PATH unset → warns, NO subprocess, NO log (skip-not-fail) | env unset; mock subprocess.run | run NOT called; returns | unit | H |
| AT-02 | build_script missing (bogus tmp path, NOT /research) → warn + return, no crash | arm_ip_library_path=tmp_path | returns | unit | H |
| AT-03 | generate_all STILL produces SV+flist+config_pkg+XML when perl skipped | default env | all non-perl artifacts exist | golden | H |
| AT-04 | XML byte-match (NO timestamp in XML) | minimal/apb_bridge | == snapshot | golden | H |
| AT-05 | address-maps text byte-match (deterministic) | same | == snapshot | golden | H |
| AT-06 | FileNotFoundError on perl caught → warn, no raise (monkeypatch subprocess.run) | mock raise FileNotFoundError, script exists | no exception | unit | M |
| AT-07 | TimeoutExpired caught → warn, no raise (monkeypatch) | mock raise TimeoutExpired | no exception | unit | L |
| AT-08 | TOOLING: real perl + valid IP path → bus-matrix RTL emitted OR SKIP | mark.tooling skip-unless resolvable | RTL dir / SKIP | tooling | M |

#### ahb.py pure helpers + context (default unit, no perl) — also home for AHB helper coverage

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| AH-01 | _to_hex int → 8-digit lowercase; hex-str normalized; FOOT-GUN decimal-str is base-16 | 0x1000 / ' 0x1000 ' / '20' | '00001000' / '00001000' / '00000020' | unit | H |
| AH-02 | _mem_hi base+size-1; _transform_name lower→`_UPPER` | ('00001000','00001000') / 'cpu_0' | '00001fff' / '_CPU_0' | unit | H |
| AH-03 | _build_apb_slot_list: unlisted slots → gaps (enabled False, is_gap True, GAP_n), len n_slots; listed default enabled True | slaves:[{slot:2,name:tmr}] | slot0/1 gaps, slot2 tmr | unit | H |
| AH-04 | _build_apb_target_context: slaves → has_mux True, decode 15/12, n_slots 16; none → has_mux False; apb_config None → `or {}` | variants | contexts | unit | H |
| AH-05 | _build_initiator_data: no visibility → single region remapping 'none'; visibility windows; unknown tgt skipped (continue) | init variants | regions | unit | H |
| AH-06 | _generate_remap_configurations: no bits → [{}]; N bits → 2^N | none / 2 bits | [{}] / len 4 | unit | M |
| AH-07 | _compute_sv_columns empty → (24,24,26) default | [],[] | (24,24,26) | unit | H |
| AH-08 | golden: SV wrapper + config_pkg byte-match (ts stripped) | minimal + apb_bridge | == snapshot | golden | H |
| AH-09 | generate_all returns [(ic.name, rtl_dir)]; creates dir tree; gen:False ic SKIPPED | minimal / ic gen False | list / not generated | golden | H |
| AH-10 | _walk_module recurses into nested gen:True ic | subsystem-with-ic fixture | nested generated | golden | M |
| AH-11 | Environment None → after XML+maps returns before SV | monkeypatch ahb.Environment=None | XML exists, SV absent | unit | M |

---

### 5.14 `tests/slow/*` — real-config smoke (SKIP if submodules absent)

`@pytest.mark.slow`. `tests/slow/test_real_soc_smoke.py`.

| ID | asserts | trigger | expected | tier | pri |
|---|---|---|---|---|---|
| SL-01 | real `nanosoc_multicore_soc.yaml` builds (if lib-dirs/submodules present, else SKIP) | real YAML + 8 lib-dirs | Module built / SKIP | slow | M |
| SL-02 | `--validate-only` parity: SoCValidator on real model returns expected error/warning shape | real model | matches CLI verdict / SKIP | slow | M |
| SL-03 | subsystem.generate_all cross-backend contract: child boundary (`_expand_ahb_port`) == parent instantiation (`_expand_bus_connection`) port set for same pt-init iface (no dangling hsel) | multicore_soc fixture | port sets equal | golden/slow | H |
| SL-04 | toplevel/ahb/firmware/discovery artifacts all emit for real config (existence only) | real YAML / SKIP | files exist / SKIP | slow | L |

---

## 6. Foot-guns & non-determinism

**Non-determinism (must neutralize for goldens):**
- `datetime.now()` injected into SV/flist/pkg: `toplevel.py:97`, `ahb.py:717/748`, `soc_config_pkg.py:169/204`, `firmware.py:84`, `discovery.py:392`, `build_info.py:259/277`, `system.py:88`, `chip.py:90`. The snapshot helper strips `// Generated:`/`* Generated:`/`# Generated:`/`> Auto-generated...on...`/`// Copyright YYYY,` via `_TIMESTAMP_LINE_RE`.
- **NOT stripped** (real foot-guns): build_info C-header `/* Generated: ... */` block comment (BI-12, U-ST-06) — assert non-timestamp lines only; the `//` Generated variant with **trailing text** (U-ST-05); chip `generated_year` line (verify before goldening). XML (`ahb`) and the address-maps/text/sram/python/rdl reports have **NO timestamp** → byte-stable, golden directly.
- `build_info`/`firmware` `git_hash` (subprocess `git`) and `timestamp` (`time.time()`) reset_values — keep OUT of goldens; assert value SHAPE only (BI-06/07).
- `html.py` has ZERO datetime/random (verified) → deterministic; still smoke-only by size policy.
- Dict insertion order (Py3.7+) is stable; `_DESIGN_PARAMS`/`_FW_PARAMS` are fixed lists; `sorted()` on address-overlap targets and RTL missing-ports is deterministic — assert exact order.
- `parser` `glob`/`iterdir` order is filesystem-dependent — collision tests MUST use code-fixed dir ordering (`modules/` before lib_dir root; lib_dirs in list order), never two files in one dir; assert cache keys as SETS.
- `resolve_param_ref` str.replace loop iterates `params.items()` — U-RP-11 outcome is insertion-order-dependent; pin with explicit dict order.

**Tool / shell-out isolation:**
- Only `ahb._run_build_bus_matrix` shells out (perl, `subprocess.run` timeout=120). Default tier asserts it SKIPS (no env var) and non-perl artifacts still emit. Tooling tier skip-unless `ARM_IP_LIBRARY_PATH` + `perl` + `BuildBusMatrix.pl` resolvable. For timeout/FileNotFound catches, monkeypatch `subprocess.run` (do not invoke real perl).
- `rdl._generate_rtl` uses systemrdl/regblock (importable here) — keep RTL emission in `tooling`; the `.rdl` text is default golden.
- `lint.py` pyslang NOT installed + no `slang` binary → real-lint paths are `tooling`/SKIP; the CLI-not-found negative path is unit-testable with a bogus binary.
- NEVER write under / export anything at the Arm IP library (`$ARM_AAA_ROOT`) or `ARM_IP_LIBRARY_PATH`.

**Behavioural foot-guns the suite PINS (current behaviour, not desired spec):**
- `_load_yaml` collapses falsy non-None (`[]`, `0`) to `{}` (P-LY-01); truthy non-dict returned despite `Dict` hint (P-LY-02).
- `parse_top_level`/`build_system` never raise on missing file — missing → `{}` → `ValueError("No 'module:' key")` (B-02 distinct from B-01 no-module-key file).
- `_build_module` cache short-circuits by name, silently ignoring a second dict's fields (B-06).
- `Interface.width` returns None for str WIDTH (M-02); `addr_width`/`data_width` `or` collapses value 0 → None (M-05/M-08).
- `is_output` excludes `receiver`/`target` (asymmetry vs `is_input`) (M-10).
- `_to_hex` parses decimal-looking strings as base-16 (AH-01).
- `_validate_address_overlaps` uses `>` not `>=` — exactly-adjacent is legal (V-AO-02).
- `module: 5` (non-dict module value) raises AttributeError during scan (P-MOD-FRAGILE-01).

## 7. Coverage-critical branches checklist

These lines are the ones coverage will flag if not explicitly hit:

- [ ] `utils.resolve_param_ref` all 7 branches: not-str(15), fullmatch-in(22)/miss(23), expr eval-ok(33)/raise(35)/still-`$`(36), no-`$`(38) — U-RP-01/02/05/06/08/09/12.
- [ ] `utils._safe_eval` both arms + whitelist-pass-eval-raises propagation — U-SE-05/09/10.
- [ ] `utils.write_if_changed` not-exists / identical / ts-only-diff / real-diff / mkdir-parents — U-WC-01/02/03/05/07.
- [ ] `utils._strip_timestamps` `//`/`*`/`#`/`>`/Copyright variants + `/* */` NOT matched — U-ST-01..06.
- [ ] `parser` `is_dir()` false branches (no modules/subsystems/regions/rtl) + true (P-MOD-13 vs P-MOD-05/06/07); hidden-file continue (P-MOD-09); `if data and 'module' in data` both false ways (P-MOD-11/12); `if name:` false (P-MOD-04); register_maps fallback + not-found (P-RM-04/05); interface direct vs interfaces/ vs bundled vs None (P-IF-01/04/06).
- [ ] `builder` cache short-circuit (B-06); concat vs non-concat glue (B-14/15); `input is not None` guard (B-16); BOTH resolved_module-None halves: parse_module→None (B-34) AND dict-without-module (B-35); rtl stub with vs without inline interfaces (B-36/37); $PARAM passthrough (B-39); target dict-vs-string (B-28/29).
- [ ] `model` M-02/M-03/M-05/M-08 falsy-or & non-wire early-outs; M-10 is_output asymmetry; M-18 get_interface empty-list path.
- [ ] `validator` AO `>` boundary (V-AO-02); width port-None(133)/conn-None(141)/final-None(283) skips (V-CW-02/03); direction wire out-out/in-in vs bus dispatch (V-CD-01/02/06/08); both resolved-None message sources (V-IR-01); RTL .upper() + sorted-missing + not-in-dict skip (V-RTL-02/05/06); C1 type-guard / input-not-driver / unconnected-skip / child-None (V-DC-04/05/07/08); C2 ic/glue/concat suppression + C1-no-double-report (V-DC-10/11/12).
- [ ] `protocol_utils` `or []` (PU-04), final `return ''` (PU-08), swd suffix (PU-12), default `[]` (PU-14).
- [ ] `soc_config_pkg` design-param filter (CP-04), elif-override (CP-06), vh-always (CP-02), flist-conditional (CP-03), Environment-None (CP-11).
- [ ] `ahb` perl-skip (AT-01), apb has_mux/no-mux/None-config (AH-04), initiator unknown-tgt continue (AH-05), Environment-None pre-SV (AH-11), _compute_sv_columns default (AH-07).
- [ ] `firmware` no-firmware skip (FW-01), region_type filter+normalize (FW-13), unknown-cpu ValueError (FW-11), the three `except ValueError` swallows in `_resolve_address`/adp (FW-14/15), bridge-merge stitch (FW-20).
- [ ] `discovery` gen-filter (DY-01), enum unknown→0 (DY-08), visibility mask + ignore-unknown (DY-09/10), packing (DY-04/07).
- [ ] `rdl` collect dedup + nested AD (RD-02), access map incl unknown→rw (RD-03), reset-value formats (RD-07), ImportError path (RD-14).
- [ ] `build_info` no-section (BI-01), string LE pack + max-len round-up (BI-04/05), non-git→0 (BI-08), C-header `/* */` gap (BI-12).
- [ ] `lint` recursion guard (LN-02), missing-file warn (LN-03), `-f` include (LN-05), `+incdir+` split (LN-06), slang-absent -1 (LN-13).
- [ ] `toplevel`/`subsystem` passthrough boundary forks (TL-12/13), `_ic_` collision (TL-23/43/62), xref output-port + cycle (TL-32/33), HTRANS gate (TL-65/68), glue concat + and_gate <2 inputs (TL-52/54), subsystem `i_` prefix divergence + cross-backend contract (SL-03).
