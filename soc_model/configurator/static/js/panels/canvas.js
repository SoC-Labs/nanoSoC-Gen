// Copyright 2026, SoC Labs (www.soclabs.org)
// Canvas panel (doc 06a) — the drag-and-drop "shape a SoC fast" surface.
//
// LAYOUT (the bus-in-the-middle topology):
//
//     ┌──── top zones (grey drop boxes) ────┐   ＋ / －  add/remove lanes
//     │  [ CPU sub ] [ DMA ]   [   ...   ]   │   ← bus MASTERS / initiators
//     ╞════════════ AHB system bus ═════════╡   ← the spine, centred
//     │ ░░ address ribbon (to scale) ░░░░░  │   ← live window map + overlaps
//     │  [ SRAM ]   [ UART ]   [   ...   ]   │   ← memory & PERIPHERAL targets
//     └──── bottom zones (grey drop boxes) ─┘   ＋ / －
//
// Features (all client-side unless noted):
//   * centred bus, grey droppable zones above (masters) / below (targets),
//     ＋/－ to add/remove lanes, drag placed blocks between zones to re-file.
//   * CONNECTOR LINES: an SVG overlay draws each block down to the bus, dotted
//     at the bus with its base address.
//   * INLINE SIZE KNOB: memory blocks carry a size <select> (only >= the hard
//     minimum) -> PUT /component/{inst} (server enforces the floor, resizes the
//     window). The one model-mutating new endpoint.
//   * ADDRESS RIBBON: /memmap rendered to scale under the bus; overlaps flagged.
//   * SUGGESTED-NEXT chips from the completeness checklist (click to drop a
//     sensible default for the missing piece).
//   * DRAG GHOST: a placeholder shows which zone a drop will land in.
//   * RIGHT-CLICK menu: Duplicate / Select / Fine-tune / Remove.
//   * GROUP INTO SUBSYSTEM (visual): multi-select blocks -> a collapsible
//     container. Purely a canvas grouping (does NOT restructure the RTL).
//   * TEMPLATES: starting layouts that seed a scratch SoC with a recipe of
//     components resolved to whatever this project actually ships.
//
// Server stays the only truth for the model: after every change we re-read
// /dict + /validate + /diagram + /memmap. Zone layout, group membership and
// per-instance zone assignment are a presentation layer persisted per-draft in
// localStorage (soccfg.canvas.{layout,assign,groups}.<draft>).
//
// Node v10 parser: no optional chaining / no nullish coalescing.
'use strict';

App.register('canvas', (function () {
  var root = function () { return document.getElementById('canvas-root'); };

  // ---- cached server data ----
  var components = null;     // /api/components -> [{id, module, category, label, knobs...}]
  var compById = {};
  var busy = false;
  var lastDict = null;
  var lastVal = null;
  var lastDiagram = null;
  var lastMemmap = null;
  var loadedFor = null;

  // ---- presentation state (client-side, persisted per draft) ----
  var layout = null;         // {top:[{id,label}], bottom:[{id,label}], _seq}
  var assign = {};           // instanceName -> zoneId
  var groups = null;         // {_seq, <gid>:{label, members:[name], collapsed}}
  var layoutFor = null;
  var groupsFor = null;
  var selected = {};         // instanceName -> true (transient, for grouping)

  var CAT = {
    subsystem:  { label: 'Subsystems',  color: 'var(--cat-subsystem, #8b5cf6)' },
    cpu:        { label: 'CPU',          color: 'var(--cat-cpu, #4da3ff)' },
    memory:     { label: 'Memory',       color: 'var(--cat-memory, #3ddbb4)' },
    peripheral: { label: 'Peripherals',  color: 'var(--cat-peripheral, #ffc24b)' },
    dma:        { label: 'DMA',          color: 'var(--cat-dma, #ff8c42)' },
    debug:      { label: 'Debug',        color: 'var(--cat-debug, #ff6b9d)' },
    other:      { label: 'Other',        color: 'var(--cat-other, #8a949e)' }
  };
  var CAT_ORDER = ['subsystem', 'cpu', 'memory', 'peripheral', 'dma', 'debug', 'other'];
  function catOf(c) { return (c && CAT[c.category]) ? c.category : 'other'; }

  function sideForCat(cat) {
    return (cat === 'cpu' || cat === 'dma' || cat === 'debug' ||
            cat === 'subsystem') ? 'top' : 'bottom';
  }

  // Starting layouts — recipes are resolved to whatever this project ships
  // (first available component of each category), so a template never refers to
  // IP the project doesn't have.
  var TEMPLATES = [
    { id: 't_min',  icon: '🧩', label: 'Minimal',   desc: '1 CPU + boot SRAM',
      recipe: ['cpu', 'memory'] },
    { id: 't_mcu',  icon: '🔌', label: 'MCU',       desc: 'CPU + 2 SRAM + a peripheral',
      recipe: ['cpu', 'memory', 'memory', 'peripheral'] },
    { id: 't_dual', icon: '🧠', label: 'Dual-core', desc: '2 CPUs + 2 SRAM',
      recipe: ['cpu', 'cpu', 'memory', 'memory'] },
    { id: 't_net',  icon: '🌐', label: 'Networked', desc: 'CPU + SRAM + a subsystem',
      recipe: ['cpu', 'memory', 'subsystem'] }
  ];

  // ------------------------------------------------------------- helpers
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function humanBytes(n) {
    if (typeof n !== 'number' || !isFinite(n) || n <= 0) return '';
    var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0;
    while (n >= 1024 && i < u.length - 1) { n = n / 1024; i++; }
    var v = (n >= 10 || n === Math.floor(n)) ? String(Math.round(n)) : n.toFixed(1);
    return v + ' ' + u[i];
  }
  function toHex(n) { return (typeof n === 'number') ? '0x' + Math.max(0, n).toString(16) : ''; }

  function tileSize(c) {
    var ks = c.knobs || [];
    for (var i = 0; i < ks.length; i++) {
      var k = ks[i];
      if (k.kind === 'bytes' && typeof k.default_bytes === 'number') return humanBytes(k.default_bytes);
      if (k.kind === 'addr_w' && typeof k.default === 'number') return humanBytes(Math.pow(2, k.default));
      if (k.kind === 'bytes' && typeof k.default === 'number') return humanBytes(k.default);
    }
    if (typeof c.window_size === 'number') return humanBytes(c.window_size);
    return '';
  }
  // The primary "size" knob (addr_w with choices) for the inline resize control.
  function sizableKnob(comp) {
    if (!comp || !comp.knobs) return null;
    for (var i = 0; i < comp.knobs.length; i++) {
      var k = comp.knobs[i];
      if (k.kind === 'addr_w' && k.choices && k.choices.length) return k;
    }
    return null;
  }

  function toast(msg, cls, extra) {
    var box = document.getElementById('builder-toasts');
    if (!box) {
      box = document.getElementById('canvas-toasts');
      if (!box) { box = document.createElement('div'); box.id = 'canvas-toasts'; document.body.appendChild(box); }
    }
    var t = document.createElement('div');
    t.className = 'toast ' + (cls || '');
    t.innerHTML = esc(msg) + (extra ? '<span class="t2">' + esc(extra) + '</span>' : '');
    box.appendChild(t);
    setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 6000);
  }

  // ------------------------------------------------------------- self-inject
  function ensureSection() {
    if (document.getElementById('canvas-root')) return true;
    var main = document.querySelector('main');
    if (!main) return false;
    var sec = document.getElementById('panel-canvas');
    if (!sec) {
      sec = document.createElement('section');
      sec.id = 'panel-canvas'; sec.className = 'panel'; sec.hidden = true;
      main.appendChild(sec);
    }
    if (!document.getElementById('canvas-root')) {
      var r = document.createElement('div'); r.id = 'canvas-root'; sec.appendChild(r);
    }
    return true;
  }

  // ------------------------------------------------------------- zone + group state
  function lkey(kind) { return 'soccfg.canvas.' + kind + '.' + (App.currentDraft || '_'); }
  function saveLayout() { try { localStorage.setItem(lkey('layout'), JSON.stringify(layout)); } catch (e) {} }
  function saveAssign() { try { localStorage.setItem(lkey('assign'), JSON.stringify(assign)); } catch (e) {} }
  function saveGroups() { try { localStorage.setItem(lkey('groups'), JSON.stringify(groups)); } catch (e) {} }
  function newZoneId() { layout._seq = (layout._seq || 0) + 1; return 'z' + layout._seq; }
  function newGroupId() { groups._seq = (groups._seq || 0) + 1; return 'g' + groups._seq; }

  function defaultLayout() {
    return { _seq: 2,
      top: [{ id: 'z1', label: 'Bus masters' }],
      bottom: [{ id: 'z2', label: 'Memory & peripherals' }] };
  }
  function ensureLayout() {
    if (layout && layoutFor === App.currentDraft) return;
    layout = null; assign = {};
    try { layout = JSON.parse(localStorage.getItem(lkey('layout'))); } catch (e) {}
    if (!layout || !layout.top || !layout.bottom || !layout.top.length || !layout.bottom.length)
      layout = defaultLayout();
    try { assign = JSON.parse(localStorage.getItem(lkey('assign'))) || {}; } catch (e) { assign = {}; }
    layoutFor = App.currentDraft;
  }
  function ensureGroups() {
    if (groups && groupsFor === App.currentDraft) return;
    groups = null;
    try { groups = JSON.parse(localStorage.getItem(lkey('groups'))); } catch (e) {}
    if (!groups || typeof groups !== 'object') groups = { _seq: 0 };
    groupsFor = App.currentDraft;
  }
  function allZones() { return (layout.top || []).concat(layout.bottom || []); }
  function defaultZoneId(side) {
    var z = (layout[side] && layout[side][0]) || layout.top[0] || layout.bottom[0];
    return z ? z.id : null;
  }
  function groupIds() { return Object.keys(groups).filter(function (k) { return k !== '_seq'; }); }
  function memberGroup() {
    var m = {};
    groupIds().forEach(function (gid) {
      (groups[gid].members || []).forEach(function (n) { m[n] = gid; });
    });
    return m;
  }

  // ------------------------------------------------------------- data load
  function indexComponents() { compById = {}; (components || []).forEach(function (c) { compById[c.id] = c; }); }
  function loadComponents() {
    if (components) return Promise.resolve(components);
    return App.api('/api/components').then(function (d) {
      components = (d && d.components) || []; indexComponents(); return components;
    }, function () { components = []; return components; });
  }
  function loadDraftState() {
    if (!App.currentDraft) {
      lastDict = lastVal = lastDiagram = lastMemmap = null; loadedFor = null;
      return Promise.resolve();
    }
    var id = App.currentDraft;
    var get = function (p, body) {
      return App.api('/api/draft/' + id + p, body).then(function (x) { return x; }, function () { return null; });
    };
    return Promise.all([
      get('/dict'), get('/validate', { json: {} }), get('/diagram'), get('/memmap')
    ]).then(function (out) {
      lastDict = out[0]; lastVal = out[1]; lastDiagram = out[2]; lastMemmap = out[3];
      loadedFor = id;
    });
  }

  // ------------------------------------------------------------- model glue
  function instances() {
    var m = (lastDict && lastDict.module) || {};
    return (m.instances || []).filter(function (i) { return i && i.instance_name; });
  }
  function diagramByInstance() {
    var map = {}, spines = (lastDiagram && lastDiagram.spines) || [];
    spines.forEach(function (sp) {
      var g = sp.groups || {};
      Object.keys(g).forEach(function (cat) {
        (g[cat] || []).forEach(function (t) { if (t && t.instance) map[t.instance] = t; });
      });
    });
    return map;
  }
  function componentForInstance(inst) {
    var modName = inst.module || inst.rtl_module || '', hit = null;
    (components || []).forEach(function (c) { if (c.module === modName) hit = c; });
    return hit;
  }
  function catForInstance(inst, dt) {
    var comp = componentForInstance(inst);
    if (comp) return catOf(comp);
    return (dt && dt.region_type === 'memory') ? 'memory' : 'other';
  }

  // Group placed instances by zone; auto-file foreigners, keep group members
  // together in one zone, prune stale assignments / group members / selection.
  function instancesByZone() {
    ensureLayout(); ensureGroups();
    var insts = instances(), dByI = diagramByInstance();
    var zoneIds = {}; allZones().forEach(function (z) { zoneIds[z.id] = 1; });
    var valid = {};
    insts.forEach(function (i) {
      var name = i.instance_name; valid[name] = 1;
      var z = assign[name];
      if (!z || !zoneIds[z]) {
        z = defaultZoneId(sideForCat(catForInstance(i, dByI[name])));
        assign[name] = z;
      }
    });
    // prune
    Object.keys(assign).forEach(function (n) { if (!valid[n]) delete assign[n]; });
    Object.keys(selected).forEach(function (n) { if (!valid[n]) delete selected[n]; });
    var dirtyG = false;
    groupIds().forEach(function (gid) {
      var g = groups[gid];
      var kept = (g.members || []).filter(function (n) { return valid[n]; });
      if (kept.length !== (g.members || []).length) { g.members = kept; dirtyG = true; }
      if (!kept.length) { delete groups[gid]; dirtyG = true; return; }
      // keep a group cohesive in the first member's zone
      var zone = assign[kept[0]];
      kept.forEach(function (n) { if (assign[n] !== zone) { assign[n] = zone; dirtyG = true; } });
    });
    if (dirtyG) saveGroups();
    saveAssign();
    // bucket
    var byZone = {};
    insts.forEach(function (i) {
      var z = assign[i.instance_name];
      (byZone[z] = byZone[z] || []).push({ inst: i, dt: dByI[i.instance_name] });
    });
    return byZone;
  }

  // ------------------------------------------------------------- palette
  function paletteHtml(filter) {
    var f = (filter || '').toLowerCase(), grp = {};
    CAT_ORDER.forEach(function (k) { grp[k] = []; });
    (components || []).forEach(function (c) {
      var hay = (c.label + ' ' + c.id + ' ' + c.module + ' ' + (c.desc || '')).toLowerCase();
      if (f && hay.indexOf(f) < 0) return;
      grp[catOf(c)].push(c);
    });
    var html = '';
    CAT_ORDER.forEach(function (cat) {
      var list = grp[cat]; if (!list.length) return;
      html += '<div class="cv-grp" style="--c:' + CAT[cat].color + '">' +
        '<div class="cv-grp-h">' + esc(CAT[cat].label) + '<span class="cv-grp-n">' + list.length + '</span></div>';
      list.forEach(function (c) {
        var sz = tileSize(c);
        var pkg = c.packaged ? '<span class="cv-pkg" title="packaged drop-in IP">●</span>' : '';
        var avail = c.available === false;
        html += '<div class="cv-tile' + (avail ? ' cv-na' : '') + '"' + (avail ? '' : ' draggable="true"') +
          ' data-cid="' + esc(c.id) + '" title="' + esc(c.desc || c.label) + '">' +
          '<span class="cv-grip">⠿</span><span class="cv-tile-b">' +
          '<span class="cv-tile-l">' + esc(c.label) + ' ' + pkg + '</span>' +
          '<span class="cv-tile-s">' + esc(c.module) + (sz ? ' · ' + esc(sz) : '') +
          (avail ? ' · unavailable' : '') + '</span></span></div>';
      });
      html += '</div>';
    });
    if (!html) html = '<div class="cv-note">no components match the filter.</div>';
    return html;
  }

  // ------------------------------------------------------------- readiness + suggestions
  function readinessHtml() {
    if (!lastVal) return '<span class="cv-rd dim">readiness: —</span>';
    var list = lastVal.checklist || [];
    var passed = list.filter(function (c) { return c.passed; }).length, total = list.length;
    var errs = lastVal.errors || 0, warns = lastVal.warnings || 0;
    return '<span class="cv-rd">' +
      '<span class="cv-pill ' + (errs ? 'bad' : 'ok') + '">' + errs + ' error' + (errs === 1 ? '' : 's') + '</span>' +
      '<span class="cv-pill ' + (warns ? 'warn' : '') + '">' + warns + ' warning' + (warns === 1 ? '' : 's') + '</span>' +
      '<span class="cv-pill">' + passed + '/' + total + ' checks</span>' +
      (lastVal.ok ? '<span class="cv-pill ok">buildable ✓</span>' : '') + '</span>';
  }

  // map a failed completeness check -> a component category to suggest dropping
  var SUGGEST_CAT = {
    'cpu-initiator': 'cpu', 'memory-target': 'memory', 'reset-vector-memory': 'memory',
    'firmware-targets': 'memory', 'debug-access': 'debug', 'uart-present': 'peripheral'
  };
  function firstAvail(cat) {
    var pkg = null, bare = null;
    (components || []).forEach(function (c) {
      if (c.category !== cat || c.available === false) return;
      if (c.packaged && !pkg) pkg = c; else if (!bare) bare = c;
    });
    return pkg || bare;
  }
  function suggestionsHtml() {
    if (!lastVal || !lastVal.checklist) return '';
    var chips = '';
    lastVal.checklist.forEach(function (c) {
      if (c.passed) return;
      var cat = SUGGEST_CAT[c.id];
      var label = c.label || c.id;
      if (cat && firstAvail(cat)) {
        var comp = firstAvail(cat);
        chips += '<button class="cv-chip" data-add-cid="' + esc(comp.id) + '" title="' +
          esc((c.hint || c.message || label) + ' — adds ' + comp.label) + '">＋ ' + esc(comp.label) + '</button>';
      } else {
        chips += '<span class="cv-chip cv-chip-info" title="' + esc(c.hint || c.message || '') + '">' +
          esc(label) + '</span>';
      }
    });
    if (!chips) return '';
    return '<div class="cv-sugg"><span class="cv-sugg-h">to finish:</span>' + chips + '</div>';
  }

  // ------------------------------------------------------------- address ribbon
  function ribbonHtml() {
    if (!lastMemmap || !lastMemmap.ok) return '';
    var ic = (lastMemmap.interconnects || [])[0];
    if (!ic || !ic.targets || !ic.targets.length) return '';
    var lo = ic.span.lo, hi = ic.span.hi + 1, span = (hi - lo) || 1, bad = false;
    var segs = ic.targets.map(function (t) {
      var left = (t.base - lo) / span * 100;
      var w = Math.max(t.size / span * 100, 0.7);
      var over = t.overlaps && t.overlaps.length; if (over) bad = true;
      var cat = (t.region_type === 'memory') ? 'memory' : 'peripheral';
      return '<div class="cv-seg' + (over ? ' cv-seg-bad' : '') + '" style="left:' + left.toFixed(3) +
        '%;width:' + w.toFixed(3) + '%;--c:' + CAT[cat].color + '" title="' +
        esc(t.name + '  ' + t.base_hex + ' … ' + t.end_hex + (over ? '  ⚠ overlaps ' + t.overlaps.join(', ') : '')) +
        '"><span>' + esc(t.name) + '</span></div>';
    }).join('');
    return '<div class="cv-ribbon">' +
      '<div class="cv-ribbon-h">address map' + (bad ? ' <span class="cv-ribbon-bad">⚠ overlap</span>' : '') + '</div>' +
      '<div class="cv-ribbon-track">' + segs + '</div>' +
      '<div class="cv-ribbon-ax"><span>' + esc(toHex(lo)) + '</span><span>' + esc(toHex(hi)) + '</span></div></div>';
  }

  // ------------------------------------------------------------- placed cards + groups
  function placedCardHtml(inst, dt) {
    var comp = componentForInstance(inst), cat = catForInstance(inst, dt);
    var label = comp ? comp.label : (inst.module || inst.rtl_module || inst.instance_name);
    var colorVar = CAT[cat].color;
    var hier = !!(dt && dt.drill) || cat === 'subsystem' || cat === 'cpu';
    var sub = inst.instances && inst.instances.length ? inst.instances.length : 0;
    var size = (dt && typeof dt.size === 'number') ? humanBytes(dt.size) : (comp ? tileSize(comp) : '');
    var base = (dt && dt.base_hex) ? dt.base_hex : '';
    var sel = !!selected[inst.instance_name];
    var knob = sizableKnob(comp), sizeCtl = '';
    if (knob) {
      var curv = (inst.params && inst.params[knob.param] != null) ? inst.params[knob.param] : knob.default;
      var opts = (knob.choices || []).map(function (ch) {
        return '<option value="' + ch.value + '"' + (String(ch.value) === String(curv) ? ' selected' : '') + '>' +
          esc(ch.label) + '</option>';
      }).join('');
      if (opts) sizeCtl = '<select class="cv-size" data-inst="' + esc(inst.instance_name) +
        '" data-param="' + esc(knob.param) + '" title="resize — hard minimum enforced">' + opts + '</select>';
    }
    var badge = hier ? '<span class="cv-hier" title="hierarchical subsystem — brings sub-instances">▣ hierarchical' +
      (sub ? ' · ' + sub : '') + '</span>' : '';
    return '<div class="cv-card' + (hier ? ' cv-card-h' : '') + (sel ? ' cv-sel' : '') + '"' +
      ' draggable="true" style="--c:' + colorVar + '" data-inst="' + esc(inst.instance_name) + '"' +
      ' data-col="' + esc(colorVar) + '"' + (base ? ' data-base="' + esc(base) + '"' : '') +
      ' title="drag to a zone · right-click for actions · double-click = Compose">' +
      '<button class="cv-pick' + (sel ? ' on' : '') + '" title="select for grouping">' + (sel ? '✓' : '') + '</button>' +
      '<button class="cv-rm" title="remove ' + esc(inst.instance_name) + '">✕</button>' +
      '<div class="cv-card-l">' + esc(label) + '</div>' +
      '<div class="cv-card-i">' + esc(inst.instance_name) + '</div>' +
      '<div class="cv-card-m">' +
        (base ? '<span class="cv-card-base">' + esc(base) + '</span>' : '') +
        (size ? '<span class="cv-card-sz">' + esc(size) + '</span>' : '') +
      '</div>' + sizeCtl + badge + '</div>';
  }

  function groupHtml(gid, items) {
    var g = groups[gid];
    var body = g.collapsed
      ? '<div class="cv-group-collapsed">' + items.length + ' block' + (items.length === 1 ? '' : 's') + ' — expand</div>'
      : items.map(function (it) { return placedCardHtml(it.inst, it.dt); }).join('');
    return '<div class="cv-group' + (g.collapsed ? ' collapsed' : '') + '" data-group="' + esc(gid) + '">' +
      '<div class="cv-group-h">' +
        '<button class="cv-group-caret" data-act="toggle" title="collapse/expand">' + (g.collapsed ? '▸' : '▾') + '</button>' +
        '<span class="cv-group-name" title="double-click to rename">' + esc(g.label) + '</span>' +
        '<span class="cv-group-n">' + items.length + '</span>' +
        '<button class="cv-group-x" data-act="ungroup" title="ungroup (visual only)">⊟</button>' +
      '</div><div class="cv-group-b">' + body + '</div></div>';
  }

  function zoneInner(items) {
    if (!items.length) return '<div class="cv-zone-empty">drop here</div>';
    var memOf = memberGroup(), buckets = {}, order = [], loose = [];
    items.forEach(function (it) {
      var gid = memOf[it.inst.instance_name];
      if (gid) { if (!buckets[gid]) { buckets[gid] = []; order.push(gid); } buckets[gid].push(it); }
      else loose.push(it);
    });
    var html = '';
    order.forEach(function (gid) { html += groupHtml(gid, buckets[gid]); });
    loose.forEach(function (it) { html += placedCardHtml(it.inst, it.dt); });
    return html;
  }

  function zoneHtml(z, items) {
    return '<div class="cv-zone" data-zone="' + esc(z.id) + '">' +
      '<div class="cv-zone-h">' +
        '<span class="cv-zone-name" title="double-click to rename">' + esc(z.label) + '</span>' +
        '<span class="cv-zone-n">' + items.length + '</span></div>' +
      '<div class="cv-zone-body">' + zoneInner(items) + '</div></div>';
  }
  function sideCtlHtml(side) {
    return '<div class="cv-side-ctl">' +
      '<span class="cv-side-tag">' + (side === 'top' ? '▲ bus masters' : '▼ targets — memory & peripherals') + '</span>' +
      '<span class="cv-side-spacer"></span>' +
      '<button class="cv-zbtn" data-act="del" data-side="' + side + '" title="remove the last drop zone">－ zone</button>' +
      '<button class="cv-zbtn cv-zbtn-add" data-act="add" data-side="' + side + '" title="add a drop zone">＋ zone</button></div>';
  }
  function sideHtml(side, byZone) {
    var boxes = (layout[side] || []).map(function (z) { return zoneHtml(z, byZone[z.id] || []); }).join('');
    var zones = '<div class="cv-zones">' + boxes + '</div>';
    return '<div class="cv-side cv-' + side + '">' + (side === 'top' ? sideCtlHtml(side) + zones : zones + sideCtlHtml(side)) + '</div>';
  }
  function stageHtml() {
    var byZone = instancesByZone();
    return '<div class="cv-stage">' +
      sideHtml('top', byZone) +
      '<div class="cv-bus" id="cv-bus">' +
        '<div class="cv-bus-bar"><span class="cv-bus-lbl">AHB system bus</span></div>' + ribbonHtml() +
      '</div>' +
      sideHtml('bottom', byZone) + '</div>';
  }

  function selBarHtml() {
    var n = Object.keys(selected).length;
    if (!n) return '';
    return '<div class="cv-selbar">' +
      '<span>' + n + ' selected</span>' +
      '<button class="cv-selbtn" id="cv-group">▣ Group into subsystem</button>' +
      '<button class="cv-selbtn ghost" id="cv-clearsel">clear</button>' +
      '<span class="cv-selnote">visual grouping — does not change the RTL</span></div>';
  }

  // ------------------------------------------------------------- full render
  function render() {
    if (!ensureSection()) return;
    var r = root(); if (!r) return;
    if (!App.currentDraft) {
      var tpl = TEMPLATES.map(function (t) {
        return '<button class="cv-tpl" data-tpl="' + esc(t.id) + '">' +
          '<span class="cv-tpl-i">' + esc(t.icon) + '</span>' +
          '<span class="cv-tpl-l">' + esc(t.label) + '</span>' +
          '<span class="cv-tpl-d">' + esc(t.desc) + '</span></button>';
      }).join('');
      r.innerHTML =
        '<div class="cv-wrap">' + palettePaneHtml() +
          '<section class="cv-canvas-pane">' +
            '<div class="cv-canvas-h"><span class="cv-title">Canvas</span><span class="cv-rd dim">no draft open</span></div>' +
            '<div class="cv-canvas" id="cv-canvas"><div class="cv-empty">' +
              '<div class="cv-empty-i">◇</div><div class="cv-empty-t">Start a new SoC</div>' +
              '<div class="cv-empty-s">Pick a starting template, or begin blank and drag components onto the bus.</div>' +
              '<div class="cv-tpls">' + tpl + '</div>' +
              '<button class="cv-go" id="cv-scratch">＋ Blank SoC</button>' +
            '</div></div></section></div>';
      bind(r); return;
    }
    r.innerHTML =
      '<div class="cv-wrap">' + palettePaneHtml() +
        '<section class="cv-canvas-pane">' +
          '<div class="cv-canvas-h">' +
            '<span class="cv-title">' + esc((lastDict && lastDict.module && lastDict.module.name) || App.currentDraft) + '</span>' +
            readinessHtml() + '</div>' +
          suggestionsHtml() + selBarHtml() +
          '<div class="cv-canvas' + (busy ? ' cv-busy' : '') + '" id="cv-canvas">' + stageHtml() + '</div>' +
        '</section></div>';
    bind(r);
    scheduleConnectors();
  }
  function palettePaneHtml() {
    return '<aside class="cv-pal-pane">' +
      '<div class="cv-pal-h">Components</div>' +
      '<input class="cv-filter" id="cv-filter" placeholder="filter…" spellcheck="false">' +
      '<div class="cv-pal" id="cv-pal">' + paletteHtml('') + '</div></aside>';
  }

  // ------------------------------------------------------------- connector overlay
  function scheduleConnectors() {
    var raf = window.requestAnimationFrame || function (f) { return setTimeout(f, 16); };
    raf(function () { try { drawConnectors(); } catch (e) {} });
  }
  function drawConnectors() {
    var stage = document.querySelector('.cv-stage'), bus = document.getElementById('cv-bus');
    if (!stage || !bus || !stage.getBoundingClientRect || !document.createElementNS) return;
    var NS = 'http://www.w3.org/2000/svg';
    var svg = document.getElementById('cv-links');
    if (!svg) {
      svg = document.createElementNS(NS, 'svg'); svg.setAttribute('id', 'cv-links'); svg.setAttribute('class', 'cv-links');
      stage.insertBefore(svg, stage.firstChild);
    }
    var sr = stage.getBoundingClientRect();
    if (!sr.width) return;
    svg.setAttribute('width', sr.width); svg.setAttribute('height', sr.height);
    svg.setAttribute('viewBox', '0 0 ' + sr.width + ' ' + sr.height);
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var br = bus.getBoundingClientRect();
    var busTop = br.top - sr.top, busBot = br.bottom - sr.top, busMid = (br.top + br.height / 2) - sr.top;
    var cards = stage.querySelectorAll('.cv-card[data-inst]');
    for (var i = 0; i < cards.length; i++) {
      var card = cards[i], cr = card.getBoundingClientRect();
      if (!cr.height) continue;
      var cx = (cr.left + cr.width / 2) - sr.left;
      var topSide = ((cr.top - sr.top) < busMid);
      var cy = topSide ? (cr.bottom - sr.top) : (cr.top - sr.top);
      var anchor = topSide ? busTop : busBot;
      var col = card.getAttribute('data-col') || 'var(--accent, #4da3ff)';
      var line = document.createElementNS(NS, 'line');
      line.setAttribute('x1', cx); line.setAttribute('y1', cy);
      line.setAttribute('x2', cx); line.setAttribute('y2', anchor);
      line.setAttribute('class', 'cv-link'); line.style.stroke = col;
      svg.appendChild(line);
      var dot = document.createElementNS(NS, 'circle');
      dot.setAttribute('cx', cx); dot.setAttribute('cy', busMid); dot.setAttribute('r', 3);
      dot.setAttribute('class', 'cv-link-dot'); dot.style.fill = col;
      svg.appendChild(dot);
      var base = card.getAttribute('data-base');
      if (base) {
        var tx = document.createElementNS(NS, 'text');
        tx.setAttribute('x', cx); tx.setAttribute('y', topSide ? (anchor - 4) : (anchor + 11));
        tx.setAttribute('class', 'cv-link-lbl'); tx.setAttribute('text-anchor', 'middle');
        tx.textContent = base; svg.appendChild(tx);
      }
    }
  }

  // ------------------------------------------------------------- ghost preview
  function clearGhosts() {
    var gs = document.querySelectorAll('.cv-ghost');
    for (var i = 0; i < gs.length; i++) if (gs[i].parentNode) gs[i].parentNode.removeChild(gs[i]);
  }
  function showGhost(zoneEl) {
    if (zoneEl.querySelector('.cv-ghost')) return;
    clearGhosts();
    var body = zoneEl.querySelector('.cv-zone-body'); if (!body) return;
    var g = document.createElement('div'); g.className = 'cv-ghost'; g.textContent = '＋ drop to place';
    body.appendChild(g);
  }

  // ------------------------------------------------------------- DnD + actions
  function wireDrop(el, getZoneId, isZone) {
    el.addEventListener('dragover', function (e) {
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
      el.classList.add('cv-over');
      if (isZone) showGhost(el);
    });
    el.addEventListener('dragleave', function (e) {
      if (e.relatedTarget && el.contains(e.relatedTarget)) return;
      el.classList.remove('cv-over');
      if (isZone) clearGhosts();
    });
    el.addEventListener('drop', function (e) {
      e.preventDefault(); e.stopPropagation();
      el.classList.remove('cv-over'); clearGhosts();
      var dt = e.dataTransfer; if (!dt) return;
      var zid = getZoneId();
      var inst = dt.getData('application/x-soc-inst');
      if (inst) { if (zid && assign[inst] !== zid) { assign[inst] = zid; saveAssign(); render(); } return; }
      var cid = dt.getData('text/plain');
      if (cid) dropComponent(cid, zid);
    });
  }

  function bind(r) {
    var filt = r.querySelector('#cv-filter');
    if (filt) filt.addEventListener('input', App.debounce(function () {
      var pal = r.querySelector('#cv-pal');
      if (pal) { pal.innerHTML = paletteHtml(filt.value.trim()); bindTiles(r); }
    }, 120));
    bindTiles(r);

    r.querySelectorAll('.cv-zone').forEach(function (z) { wireDrop(z, function () { return z.dataset.zone; }, true); });
    var bus = r.querySelector('#cv-bus'); if (bus) wireDrop(bus, function () { return null; }, false);

    r.querySelectorAll('.cv-zbtn').forEach(function (b) {
      b.addEventListener('click', function () { (b.dataset.act === 'add' ? addZone : delZone)(b.dataset.side); });
    });
    r.querySelectorAll('.cv-zone-name').forEach(function (el) {
      el.addEventListener('dblclick', function (e) {
        e.stopPropagation();
        var box = el.parentNode && el.parentNode.parentNode, zid = box && box.dataset ? box.dataset.zone : null;
        var nn = prompt('Zone name:', el.textContent); if (nn == null) return;
        nn = nn.trim() || el.textContent;
        var zs = allZones(); for (var i = 0; i < zs.length; i++) if (zs[i].id === zid) { zs[i].label = nn; break; }
        saveLayout(); render();
      });
    });

    // templates / scratch (no-draft)
    var sc = r.querySelector('#cv-scratch'); if (sc) sc.addEventListener('click', function () { startScratch(); });
    r.querySelectorAll('.cv-tpl').forEach(function (b) {
      b.addEventListener('click', function () { loadTemplate(b.dataset.tpl); });
    });

    // suggestion chips
    r.querySelectorAll('.cv-chip[data-add-cid]').forEach(function (b) {
      b.addEventListener('click', function () { dropComponent(b.dataset.addCid, null); });
    });

    // selection bar
    var gb = r.querySelector('#cv-group'); if (gb) gb.addEventListener('click', groupSelected);
    var cb = r.querySelector('#cv-clearsel'); if (cb) cb.addEventListener('click', function () { selected = {}; render(); });

    // group header actions
    r.querySelectorAll('.cv-group').forEach(function (gEl) {
      var gid = gEl.dataset.group;
      var caret = gEl.querySelector('.cv-group-caret');
      if (caret) caret.addEventListener('click', function () {
        if (groups[gid]) { groups[gid].collapsed = !groups[gid].collapsed; saveGroups(); render(); }
      });
      var x = gEl.querySelector('.cv-group-x');
      if (x) x.addEventListener('click', function () { if (groups[gid]) { delete groups[gid]; saveGroups(); render(); } });
      var nm = gEl.querySelector('.cv-group-name');
      if (nm) nm.addEventListener('dblclick', function () {
        var nn = prompt('Subsystem name:', nm.textContent); if (nn == null) return;
        if (groups[gid]) { groups[gid].label = nn.trim() || groups[gid].label; saveGroups(); render(); }
      });
    });

    // inline size knob
    r.querySelectorAll('.cv-size').forEach(function (sel) {
      sel.addEventListener('change', function (e) { e.stopPropagation(); updateSize(sel.dataset.inst, sel.dataset.param, sel.value); });
      sel.addEventListener('dblclick', function (e) { e.stopPropagation(); });
    });

    // placed blocks: drag / pick / remove / dbl-click / context menu
    r.querySelectorAll('.cv-card').forEach(function (card) {
      card.addEventListener('dragstart', function (e) {
        if (e.dataTransfer) { e.dataTransfer.setData('application/x-soc-inst', card.dataset.inst); e.dataTransfer.effectAllowed = 'move'; }
        card.classList.add('cv-drag');
      });
      card.addEventListener('dragend', function () { card.classList.remove('cv-drag'); clearGhosts(); });
      var pick = card.querySelector('.cv-pick');
      if (pick) pick.addEventListener('click', function (e) { e.stopPropagation(); toggleSelect(card.dataset.inst); });
      var rm = card.querySelector('.cv-rm');
      if (rm) rm.addEventListener('click', function (e) { e.stopPropagation(); removeComponent(card.dataset.inst); });
      card.addEventListener('dblclick', function () { if (App.show) App.show('compose'); });
      card.addEventListener('contextmenu', function (e) { e.preventDefault(); showMenu(e, card.dataset.inst); });
    });
  }
  function bindTiles(r) {
    r.querySelectorAll('.cv-tile[draggable="true"]').forEach(function (tile) {
      tile.addEventListener('dragstart', function (e) {
        if (e.dataTransfer) { e.dataTransfer.setData('text/plain', tile.dataset.cid); e.dataTransfer.effectAllowed = 'copy'; }
        tile.classList.add('cv-drag');
      });
      tile.addEventListener('dragend', function () { tile.classList.remove('cv-drag'); clearGhosts(); });
      tile.addEventListener('dblclick', function () { dropComponent(tile.dataset.cid, null); });
    });
  }

  // ---- selection / grouping ----
  function toggleSelect(name) {
    if (selected[name]) delete selected[name]; else selected[name] = true;
    render();
  }
  function groupSelected() {
    ensureGroups();
    var names = Object.keys(selected);
    if (names.length < 2) { toast('Pick at least two blocks to group', 'warn'); return; }
    var gid = newGroupId();
    groups[gid] = { label: 'Subsystem ' + (groupIds().length), members: names.slice(), collapsed: false };
    selected = {}; saveGroups(); render();
    toast('Grouped ' + names.length + ' blocks (visual)', 'ok');
  }

  // ---- zone add/remove ----
  function addZone(side) {
    ensureLayout();
    var n = layout[side].length + 1;
    layout[side].push({ id: newZoneId(), label: (side === 'top' ? 'Masters ' : 'Targets ') + n });
    saveLayout(); render();
  }
  function delZone(side) {
    ensureLayout();
    if (layout[side].length <= 1) { toast('Keep at least one ' + side + ' zone', 'warn'); return; }
    var z = layout[side][layout[side].length - 1], keep = layout[side][0].id, moved = 0;
    Object.keys(assign).forEach(function (n) { if (assign[n] === z.id) { assign[n] = keep; moved++; } });
    layout[side].pop(); saveLayout(); saveAssign();
    if (moved) toast('Moved ' + moved + ' block' + (moved === 1 ? '' : 's') + ' to ' + layout[side][0].label, 'ok');
    render();
  }

  // ---- context menu ----
  var menuEl = null;
  function hideMenu() { if (menuEl) menuEl.style.display = 'none'; }
  function showMenu(e, name) {
    if (!menuEl) {
      menuEl = document.createElement('div'); menuEl.id = 'cv-menu'; menuEl.className = 'cv-menu';
      document.body.appendChild(menuEl);
      document.addEventListener('click', hideMenu);
      window.addEventListener('blur', hideMenu);
    }
    var picked = !!selected[name];
    menuEl.innerHTML =
      '<button data-mact="dup">⧉ Duplicate</button>' +
      '<button data-mact="sel">' + (picked ? '✓ Deselect' : '☐ Select') + '</button>' +
      '<button data-mact="compose">🎛 Fine-tune in Compose</button>' +
      '<button data-mact="rm" class="danger">✕ Remove</button>';
    menuEl.style.display = 'block';
    menuEl.style.left = (e.clientX + 2) + 'px';
    menuEl.style.top = (e.clientY + 2) + 'px';
    menuEl.querySelectorAll('button').forEach(function (b) {
      b.addEventListener('click', function (ev) {
        ev.stopPropagation(); hideMenu();
        var a = b.dataset.mact;
        if (a === 'dup') duplicateComponent(name);
        else if (a === 'sel') toggleSelect(name);
        else if (a === 'compose') { if (App.show) App.show('compose'); }
        else if (a === 'rm') removeComponent(name);
      });
    });
  }

  // ---- create / place / update / remove ----
  function startScratch(cb) {
    if (busy) return;
    busy = true;
    App.api('/api/drafts', { json: { scratch: { name: 'canvas_soc', clk_hz: 50000000, addr_w: 32 } } })
      .then(function (res) {
        busy = false;
        if (cb) { App.currentDraft = res.id; App.setDraft(res.id); cb(res.id); }
        else { toast('Blank SoC created — drag components onto the bus', 'ok'); App.setDraft(res.id); }
      }, function (e) { busy = false; toast('Could not start a draft: ' + e.message, 'err'); });
  }

  function dropComponent(cid, zid, params) {
    if (busy) return;
    if (!App.currentDraft) { startScratch(function () { dropComponent(cid, zid, params); }); return; }
    var c = compById[cid];
    if (!c) { toast('Unknown component: ' + cid, 'err'); return; }
    if (c.available === false) { toast(c.label + ' is unavailable in this project', 'warn'); return; }
    busy = true; render();
    var body = { module: c.module, component_id: c.id };
    if (params) body.params = params;
    return App.api('/api/draft/' + App.currentDraft + '/component', { json: body })
      .then(function (res) {
        if (res.instance) {
          ensureLayout();
          var z = (zid && zoneExists(zid)) ? zid : defaultZoneId(sideForCat(catOf(c)));
          if (z) { assign[res.instance] = z; saveAssign(); }
        }
        var wired = (res.wired || []).length, unwired = (res.unwired || []).length;
        toast('Placed ' + res.instance + (res.target ? ' @ ' + res.target : '') + ' — ' + wired + ' port' +
          (wired === 1 ? '' : 's') + ' auto-wired', unwired ? 'warn' : 'ok',
          unwired ? unwired + ' port' + (unwired === 1 ? '' : 's') + ' need wiring — fine-tune in Compose' : '');
        busy = false; return refresh();
      }, function (e) { busy = false; toast('Place ' + c.label + ' failed: ' + e.message, 'err'); render(); });
  }
  function zoneExists(zid) { var zs = allZones(); for (var i = 0; i < zs.length; i++) if (zs[i].id === zid) return true; return false; }

  function duplicateComponent(name) {
    var inst = null;
    instances().forEach(function (i) { if (i.instance_name === name) inst = i; });
    if (!inst) return;
    var comp = componentForInstance(inst);
    if (!comp) { toast('Cannot duplicate ' + name + ' (no package match)', 'warn'); return; }
    var zid = assign[name];
    dropComponent(comp.id, zid, inst.params ? JSON.parse(JSON.stringify(inst.params)) : null);
  }

  function updateSize(name, param, value) {
    if (busy || !App.currentDraft) return;
    busy = true; render();
    var body = { params: {} }; body.params[param] = Number(value);
    App.api('/api/draft/' + App.currentDraft + '/component/' + encodeURIComponent(name), { method: 'PUT', json: body })
      .then(function (res) {
        toast('Resized ' + name + (res.size ? ' → ' + humanBytes(res.size) : ''), 'ok');
        busy = false; return refresh();
      }, function (e) { busy = false; toast('Resize ' + name + ' failed: ' + e.message, 'err'); refresh(); });
  }

  function removeComponent(name) {
    if (busy || !App.currentDraft) return;
    if (!confirm('Remove "' + name + '" (and its address windows)?')) return;
    busy = true; render();
    App.api('/api/draft/' + App.currentDraft + '/component/' + encodeURIComponent(name), { method: 'DELETE' })
      .then(function (res) {
        var rt = (res.removed_targets || []).length;
        delete assign[name]; delete selected[name]; saveAssign();
        toast('Removed ' + name + (rt ? ' (+' + rt + ' window' + (rt === 1 ? '' : 's') + ')' : ''), 'ok');
        busy = false; return refresh();
      }, function (e) { busy = false; toast('Remove ' + name + ' failed: ' + e.message, 'err'); render(); });
  }

  function loadTemplate(tid) {
    if (busy) return;
    var t = null; TEMPLATES.forEach(function (x) { if (x.id === tid) t = x; });
    if (!t) return;
    var parts = [];
    t.recipe.forEach(function (cat) { var c = firstAvail(cat); if (c) parts.push(c.id); });
    if (!parts.length) { toast('No available components for this template in the project', 'warn'); return; }
    startScratch(function () {
      // drop parts one at a time (sequential — each needs the prior draft state)
      var i = 0;
      function next() {
        if (i >= parts.length) { toast('Loaded "' + t.label + '" — ' + parts.length + ' blocks', 'ok'); return; }
        var cid = parts[i++];
        var p = dropComponent(cid, null);
        if (p && p.then) p.then(next); else setTimeout(next, 50);
      }
      next();
    });
  }

  function refresh() { return loadDraftState().then(render, render); }

  // ------------------------------------------------------------- styles
  var CSS =
  '#canvas-root { height: 100%; }' +
  '.cv-wrap { display: grid; grid-template-columns: minmax(220px, 290px) 1fr; gap: 14px; padding: 10px; align-items: start; }' +
  '.cv-pal-pane { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px; position: sticky; top: 10px; }' +
  '.cv-pal-h { font-size: 12px; text-transform: uppercase; letter-spacing: .8px; color: var(--dim); margin-bottom: 8px; }' +
  '.cv-filter { width: 100%; box-sizing: border-box; background: var(--bg); color: var(--fg); border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px; font: 12px monospace; margin-bottom: 10px; }' +
  '.cv-filter:focus { outline: none; border-color: var(--accent, #4da3ff); box-shadow: 0 0 0 2px rgba(77,163,255,.22); }' +
  '.cv-pal { max-height: 74vh; overflow-y: auto; }' +
  '.cv-grp { margin-bottom: 12px; }' +
  '.cv-grp-h { font-size: 11px; font-weight: 700; letter-spacing: .5px; color: var(--c); text-transform: uppercase; margin: 0 2px 6px; display: flex; align-items: center; gap: 6px; }' +
  '.cv-grp-n { font-weight: 400; color: var(--dim); font-size: 10px; border: 1px solid var(--line); border-radius: 999px; padding: 0 6px; }' +
  '.cv-tile { display: flex; align-items: center; gap: 8px; cursor: grab; border: 1px solid var(--line); border-left: 3px solid var(--c); border-radius: 7px; padding: 7px 9px; margin-bottom: 6px; background: var(--panel); transition: transform 120ms ease, border-color 120ms ease, box-shadow 120ms ease; }' +
  '.cv-tile:hover { transform: translateY(-1px); border-color: var(--c); box-shadow: 0 3px 12px rgba(0,0,0,.35), inset 3px 0 0 -1px var(--c); }' +
  '.cv-tile.cv-drag { opacity: .45; }' +
  '.cv-tile.cv-na { opacity: .45; cursor: not-allowed; }' +
  '.cv-grip { color: var(--c); font-size: 13px; flex: 0 0 auto; letter-spacing: -2px; opacity: .8; }' +
  '.cv-tile-b { display: flex; flex-direction: column; min-width: 0; flex: 1; }' +
  '.cv-tile-l { font-size: 12.5px; color: var(--fg); font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }' +
  '.cv-tile-s { font: 10.5px monospace; color: var(--dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }' +
  '.cv-pkg { color: var(--ok); font-size: 9px; vertical-align: middle; }' +
  '.cv-canvas-pane { min-width: 0; }' +
  '.cv-canvas-h { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 4px 4px 10px; }' +
  '.cv-title { font-size: 18px; font-weight: 700; background: var(--accent-grad, linear-gradient(90deg,#4da3ff,#3ddbb4)); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; color: var(--accent, #4da3ff); }' +
  '.cv-rd { display: inline-flex; gap: 6px; align-items: center; margin-left: auto; flex-wrap: wrap; }' +
  '.cv-pill { font-size: 11px; border: 1px solid var(--line); border-radius: 999px; padding: 2px 10px; color: var(--dim); }' +
  '.cv-pill.ok { color: var(--ok); border-color: var(--ok); } .cv-pill.warn { color: var(--warn); border-color: var(--warn); } .cv-pill.bad { color: var(--err); border-color: var(--err); }' +
  /* suggestions */
  '.cv-sugg { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; padding: 0 4px 8px; }' +
  '.cv-sugg-h { font: 11px monospace; color: var(--dim); text-transform: uppercase; letter-spacing: .5px; }' +
  '.cv-chip { cursor: pointer; border: 1px solid var(--accent, #4da3ff); color: var(--accent, #4da3ff); background: rgba(77,163,255,.08); border-radius: 999px; padding: 3px 11px; font-size: 11.5px; transition: background 120ms ease; }' +
  '.cv-chip:hover { background: rgba(77,163,255,.2); }' +
  '.cv-chip-info { cursor: default; border-color: var(--warn); color: var(--warn); background: rgba(255,194,75,.07); }' +
  /* selection bar */
  '.cv-selbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 6px 10px; margin: 0 4px 8px; border: 1px solid var(--accent, #4da3ff); border-radius: 8px; background: rgba(77,163,255,.08); font-size: 12px; }' +
  '.cv-selbtn { cursor: pointer; border: 0; border-radius: 6px; padding: 4px 12px; font-weight: 600; color: #0c1014; background: var(--accent-grad, linear-gradient(90deg,#4da3ff,#3ddbb4)); }' +
  '.cv-selbtn.ghost { background: transparent; color: var(--dim); border: 1px solid var(--line); font-weight: 400; }' +
  '.cv-selnote { color: var(--dim); font-size: 10.5px; margin-left: auto; }' +
  '.cv-canvas { background: radial-gradient(circle at 18px 18px, rgba(127,127,127,.10) 1.5px, transparent 1.6px); background-size: 22px 22px; border: 1px solid var(--line); border-radius: 12px; min-height: 62vh; padding: 16px; transition: opacity 120ms ease; }' +
  '.cv-canvas.cv-busy { opacity: .6; pointer-events: none; }' +
  /* empty + templates */
  '.cv-empty { display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 10px; min-height: 46vh; border: 2px dashed var(--line); border-radius: 12px; padding: 24px; }' +
  '.cv-empty-i { font-size: 34px; color: var(--accent, #4da3ff); opacity: .8; }' +
  '.cv-empty-t { font-size: 15px; font-weight: 600; color: var(--fg); }' +
  '.cv-empty-s { font-size: 12px; color: var(--dim); max-width: 380px; }' +
  '.cv-tpls { display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; margin-top: 4px; }' +
  '.cv-tpl { cursor: pointer; width: 150px; text-align: left; border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; background: var(--panel); display: flex; flex-direction: column; gap: 3px; transition: transform 120ms ease, border-color 120ms ease, box-shadow 120ms ease; }' +
  '.cv-tpl:hover { transform: translateY(-2px); border-color: var(--accent, #4da3ff); box-shadow: 0 6px 18px rgba(0,0,0,.4); }' +
  '.cv-tpl-i { font-size: 20px; } .cv-tpl-l { font-weight: 700; font-size: 13px; color: var(--fg); } .cv-tpl-d { font-size: 11px; color: var(--dim); }' +
  '.cv-go { margin-top: 6px; cursor: pointer; border: 0; border-radius: 6px; padding: 8px 16px; font-weight: 600; color: #0c1014; background: var(--accent-grad, linear-gradient(90deg,#4da3ff,#3ddbb4)); }' +
  '.cv-go:hover { filter: brightness(1.12); }' +
  /* stage */
  '.cv-stage { position: relative; display: flex; flex-direction: column; gap: 6px; }' +
  '.cv-links { position: absolute; top: 0; left: 0; pointer-events: none; z-index: 4; overflow: visible; }' +
  '.cv-link { stroke-width: 1.4; opacity: .42; }' +
  '.cv-link-dot { opacity: .9; }' +
  '.cv-link-lbl { fill: var(--dim); font: 9px monospace; opacity: .85; }' +
  '.cv-side { display: flex; flex-direction: column; gap: 6px; }' +
  '.cv-side-ctl { display: flex; align-items: center; gap: 8px; padding: 0 4px; }' +
  '.cv-side-tag { font: 10px monospace; text-transform: uppercase; letter-spacing: .6px; color: var(--dim); opacity: .85; }' +
  '.cv-side-spacer { flex: 1; }' +
  '.cv-zbtn { cursor: pointer; border: 1px solid var(--line); background: var(--panel); color: var(--dim); border-radius: 6px; padding: 2px 10px; font-size: 11px; transition: color 120ms ease, border-color 120ms ease; }' +
  '.cv-zbtn:hover { color: var(--accent, #4da3ff); border-color: var(--accent, #4da3ff); } .cv-zbtn-add:hover { color: var(--ok); border-color: var(--ok); }' +
  '.cv-zones { display: flex; gap: 12px; align-items: stretch; overflow-x: auto; padding: 2px; }' +
  '.cv-zone { flex: 1 1 0; min-width: 210px; background: rgba(127,127,127,.06); border: 1.5px dashed var(--line); border-radius: 10px; padding: 8px; transition: border-color 120ms ease, background 120ms ease; }' +
  '.cv-zone.cv-over { border-color: var(--accent, #4da3ff); background: rgba(77,163,255,.12); }' +
  '.cv-zone-h { display: flex; align-items: center; gap: 6px; margin: 2px 4px 8px; }' +
  '.cv-zone-name { font-size: 11px; text-transform: uppercase; letter-spacing: .6px; color: var(--dim); cursor: text; } .cv-zone-name:hover { color: var(--fg); }' +
  '.cv-zone-n { margin-left: auto; font: 10px monospace; color: var(--dim); border: 1px solid var(--line); border-radius: 999px; padding: 0 6px; }' +
  '.cv-zone-body { display: flex; flex-wrap: wrap; gap: 10px; min-height: 96px; align-content: flex-start; }' +
  '.cv-zone-empty { width: 100%; align-self: center; text-align: center; color: var(--dim); font-size: 11px; padding: 22px 0; opacity: .6; }' +
  '.cv-ghost { width: 168px; min-height: 84px; border: 2px dashed var(--accent, #4da3ff); border-radius: 9px; display: flex; align-items: center; justify-content: center; color: var(--accent, #4da3ff); font-size: 11.5px; background: rgba(77,163,255,.06); animation: cvpulse 1s ease-in-out infinite; }' +
  '@keyframes cvpulse { 0%,100% { opacity: .5; } 50% { opacity: 1; } }' +
  /* bus + ribbon */
  '.cv-bus { margin: 8px 0; border-radius: 10px; transition: background 120ms ease; padding: 4px 0; position: relative; z-index: 5; }' +
  '.cv-bus.cv-over { background: rgba(77,163,255,.06); outline: 2px dashed var(--accent, #4da3ff); outline-offset: 4px; }' +
  '.cv-bus-bar { display: flex; align-items: center; justify-content: center; height: 22px; border-radius: 999px; background: linear-gradient(90deg, var(--accent, #4da3ff), var(--accent2, #3ddbb4)); box-shadow: 0 0 18px rgba(77,163,255,.40); }' +
  '.cv-bus-lbl { font: 11px monospace; color: #0c1014; font-weight: 700; letter-spacing: .6px; }' +
  '.cv-ribbon { margin: 8px 6px 2px; }' +
  '.cv-ribbon-h { font: 10px monospace; text-transform: uppercase; letter-spacing: .5px; color: var(--dim); margin-bottom: 3px; }' +
  '.cv-ribbon-bad { color: var(--err); }' +
  '.cv-ribbon-track { position: relative; height: 18px; background: rgba(127,127,127,.10); border: 1px solid var(--line); border-radius: 5px; overflow: hidden; }' +
  '.cv-seg { position: absolute; top: 0; bottom: 0; background: color-mix(in srgb, var(--c) 38%, transparent); border-left: 1px solid var(--c); border-right: 1px solid var(--c); overflow: hidden; }' +
  '.cv-seg span { font: 9px monospace; color: var(--fg); padding: 0 3px; line-height: 18px; white-space: nowrap; }' +
  '.cv-seg.cv-seg-bad { background: rgba(255,107,107,.35); border-color: var(--err); }' +
  '.cv-ribbon-ax { display: flex; justify-content: space-between; font: 9px monospace; color: var(--dim); margin-top: 2px; }' +
  /* placed cards */
  '.cv-card { position: relative; width: 168px; min-height: 84px; background: var(--panel); border: 1px solid var(--line); border-top: 3px solid var(--c); border-radius: 9px; padding: 10px 12px; box-sizing: border-box; cursor: grab; box-shadow: 0 2px 10px rgba(0,0,0,.30); transition: transform 120ms ease, box-shadow 120ms ease; }' +
  '.cv-card:hover { transform: translateY(-2px); box-shadow: 0 8px 22px rgba(0,0,0,.45); }' +
  '.cv-card.cv-drag { opacity: .45; }' +
  '.cv-card.cv-sel { outline: 2px solid var(--accent, #4da3ff); outline-offset: 1px; }' +
  '.cv-card-h { width: 200px; min-height: 100px; background: linear-gradient(160deg, color-mix(in srgb, var(--c) 14%, var(--panel)), var(--panel)); border-width: 1px; border-top-width: 3px; }' +
  '.cv-card-l { font-size: 13px; font-weight: 700; color: var(--fg); padding-right: 26px; line-height: 1.25; }' +
  '.cv-card-i { font: 10.5px monospace; color: var(--dim); margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }' +
  '.cv-card-m { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }' +
  '.cv-card-base, .cv-card-sz { font: 10px monospace; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--line); color: var(--dim); }' +
  '.cv-card-sz { color: var(--c); border-color: var(--c); }' +
  '.cv-size { margin-top: 8px; width: 100%; box-sizing: border-box; background: var(--bg); color: var(--fg); border: 1px solid var(--line); border-radius: 5px; padding: 2px 4px; font: 10.5px monospace; cursor: pointer; }' +
  '.cv-size:hover { border-color: var(--c); }' +
  '.cv-hier { display: inline-block; margin-top: 8px; font-size: 10px; color: var(--c); border: 1px solid var(--c); border-radius: 999px; padding: 1px 8px; opacity: .9; }' +
  '.cv-pick { position: absolute; top: 6px; left: 6px; width: 16px; height: 16px; border: 1px solid var(--line); background: var(--bg); color: var(--accent, #4da3ff); cursor: pointer; font-size: 10px; line-height: 1; border-radius: 4px; padding: 0; }' +
  '.cv-pick.on { background: var(--accent, #4da3ff); color: #0c1014; border-color: var(--accent, #4da3ff); }' +
  '.cv-rm { position: absolute; top: 6px; right: 6px; border: 0; background: transparent; color: var(--dim); cursor: pointer; font-size: 12px; line-height: 1; padding: 2px 4px; border-radius: 4px; }' +
  '.cv-rm:hover { color: var(--err); background: rgba(255,107,107,.12); }' +
  '.cv-card-l { padding-left: 16px; }' +
  /* group container */
  '.cv-group { border: 1.5px solid var(--cat-subsystem, #8b5cf6); border-radius: 10px; padding: 6px; background: rgba(139,92,246,.06); }' +
  '.cv-group-h { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }' +
  '.cv-group-caret { border: 0; background: transparent; color: var(--cat-subsystem, #8b5cf6); cursor: pointer; font-size: 11px; padding: 0 2px; }' +
  '.cv-group-name { font-size: 11px; font-weight: 700; color: var(--cat-subsystem, #8b5cf6); text-transform: uppercase; letter-spacing: .5px; cursor: text; }' +
  '.cv-group-n { font: 10px monospace; color: var(--dim); border: 1px solid var(--line); border-radius: 999px; padding: 0 6px; }' +
  '.cv-group-x { margin-left: auto; border: 0; background: transparent; color: var(--dim); cursor: pointer; font-size: 12px; }' +
  '.cv-group-x:hover { color: var(--err); }' +
  '.cv-group-b { display: flex; flex-wrap: wrap; gap: 10px; }' +
  '.cv-group-collapsed { color: var(--dim); font-size: 11px; padding: 6px 4px; }' +
  /* context menu */
  '.cv-menu { position: fixed; z-index: 2000; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 10px 30px rgba(0,0,0,.6); padding: 4px; display: none; min-width: 180px; }' +
  '.cv-menu button { display: block; width: 100%; text-align: left; border: 0; background: transparent; color: var(--fg); cursor: pointer; font-size: 12.5px; padding: 7px 10px; border-radius: 5px; }' +
  '.cv-menu button:hover { background: rgba(127,127,127,.14); }' +
  '.cv-menu button.danger:hover { background: rgba(255,107,107,.15); color: var(--err); }' +
  '.cv-note { color: var(--dim); font-size: 12px; padding: 10px 2px; }' +
  '#canvas-toasts { position: fixed; right: 18px; bottom: 18px; z-index: 1000; display: flex; flex-direction: column; gap: 8px; max-width: 380px; }' +
  '#canvas-toasts .toast { background: var(--panel); border: 1px solid var(--line); border-left: 3px solid var(--accent, #4da3ff); border-radius: 6px; padding: 9px 12px; font-size: 12.5px; box-shadow: 0 8px 22px rgba(0,0,0,.5); }' +
  '#canvas-toasts .toast.warn { border-left-color: var(--warn); } #canvas-toasts .toast.err { border-left-color: var(--err); } #canvas-toasts .toast.ok { border-left-color: var(--ok); }' +
  '#canvas-toasts .toast .t2 { color: var(--warn); display: block; margin-top: 3px; font-size: 11.5px; }';

  // ------------------------------------------------------------- lifecycle
  var inited = false, resizeWired = false;
  function boot() {
    if (inited) return;
    inited = true;
    var st = document.createElement('style'); st.textContent = CSS; document.head.appendChild(st);
    ensureSection();
    if (!resizeWired && window.addEventListener) {
      resizeWired = true;
      window.addEventListener('resize', App.debounce(function () { scheduleConnectors(); }, 150));
    }
    loadComponents().then(function () {
      if (App.currentDraft) loadDraftState().then(render, render); else render();
    });
  }

  return {
    init: function () { boot(); },
    onDraft: function () {
      layoutFor = null; groupsFor = null; selected = {};
      if (!components) { loadComponents().then(function () { refresh(); }); return; }
      refresh();
    },
    onShow: function () {
      boot();
      if (App.currentDraft !== loadedFor) refresh(); else { render(); }
    }
  };
})());
