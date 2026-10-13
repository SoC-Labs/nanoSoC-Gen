// Copyright 2026, SoC Labs (www.soclabs.org)
// Diagram panel (doc 06a §4): renders GET /api/draft/{id}/diagram into
// #diagram-root as an inline SVG block diagram in the visual language of the
// multicore demo dashboard (mk/txt/blk helpers + the fitText overflow guard
// ported from demo_gui dashboard.js — self-contained, nothing imported).
// Layout per spine: initiator row → bus bar → grouped target grid; ghost
// placeholders for failed completeness checks; click → details popover.
'use strict';

App.register('diagram', (() => {
  const SVGNS = 'http://www.w3.org/2000/svg';
  const W = 1120, MX = 14, PAD = 12, GAP = 12, COLS = 5;
  const CW = Math.floor((W - 2 * MX - 2 * PAD - (COLS - 1) * GAP) / COLS); // 204
  const CH = 46, IH = 48;
  const GROUP_ORDER = ['memory', 'periph', 'subordinate'];
  const GROUP_LABEL = { memory: 'memory', periph: 'peripherals',
                        subordinate: 'subsystems (subordinate buses)' };
  let pop = null;                       // the open popover element
  let seq = 0;                          // refresh epoch (drops stale renders)

  // ── SVG helpers (demo-dashboard style) ──────────────────────────────────
  function mk(tag, attrs, text) {
    const n = document.createElementNS(SVGNS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    return n;
  }
  function txt(parent, x, y, cls, content, anchor, maxW) {
    const t = mk('text', { x, y, 'class': cls });
    t.setAttribute('text-anchor', anchor || 'start');
    if (content != null) t.textContent = content;
    parent.appendChild(t);
    if (maxW != null) fitText(t, maxW);
    return t;
  }

  // ── text-fit guard: shrink the font (floor 0.85×), then ellipsize ───────
  const FONT_OF = {
    'dg-name': { px: 11, mono: false }, 'dg-sub':  { px: 10, mono: true },
    'dg-bus':  { px: 12, mono: false }, 'dg-cap':  { px: 10, mono: true },
    'dg-addr': { px: 10, mono: true },  'dg-ghost': { px: 10, mono: false },
  };
  function fontOf(el) {
    const cls = (el.getAttribute('class') || '').split(/\s+/).find(c => FONT_OF[c]);
    return FONT_OF[cls] || { px: 11, mono: true };
  }
  function estWidth(s, f, px) {
    let u = 0;
    for (const ch of String(s)) {
      if (f.mono) u += 1;
      else if ('—◉●↔→'.indexOf(ch) >= 0) u += 1.6;
      else if (ch === ' ') u += 0.55;
      else u += 1;
    }
    return u * px * 0.62;
  }
  function measure(el) {
    if (typeof el.getComputedTextLength === 'function') {
      try { const w = el.getComputedTextLength(); if (w > 0) return w; }
      catch (e) { /* detached / hidden — use the estimate */ }
    }
    const f = fontOf(el);
    const px = (el.style && parseFloat(el.style.fontSize)) || f.px;
    return estWidth(el.textContent || '', f, px);
  }
  function fitText(el, maxW) {
    if (!el || !(maxW > 0)) return el;
    if (el.style) el.style.fontSize = '';
    const f = fontOf(el);
    let w = measure(el);
    if (w <= maxW) return el;
    const scale = Math.max(0.85, maxW / w);
    el.style.fontSize = (Math.floor(f.px * scale * 100) / 100) + 'px';
    if (measure(el) <= maxW + 0.25) return el;
    let s = el.textContent || '';
    while (s.length > 1 && measure(el) > maxW) {
      s = s.slice(0, -1);
      el.textContent = s.replace(/[\s·.,(\-–—]+$/, '') + '…';
    }
    return el;
  }

  // ── block + badge + popover ─────────────────────────────────────────────
  function blk(parent, o) {                 // rect + info carrier, clickable
    const g = mk('g', { 'class': 'dg-blk' + (o.cls ? ' ' + o.cls : ''),
                        tabindex: 0, role: 'button' });
    g.appendChild(mk('rect', { x: o.x, y: o.y, width: o.w, height: o.h,
                               rx: o.rx != null ? o.rx : 6,
                               'class': 'dg-bg' + (o.bg ? ' ' + o.bg : '') }));
    if (o.title) g.appendChild(mk('title', {}, o.title));
    if (o.info) {
      g.__dgInfo = o.info;
      g.addEventListener('click', (ev) => { ev.stopPropagation(); openPopover(ev, o.info); });
      g.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openPopover(ev, o.info); }
      });
    }
    parent.appendChild(g);
    return g;
  }
  function badge(g, x, y, level, tip) {     // corner validation dot
    if (level !== 'error' && level !== 'warning') return;
    const c = mk('circle', { cx: x, cy: y, r: 4, 'class': 'dg-dot ' + level });
    c.appendChild(mk('title', {}, tip || (level + ' — see Validation in the YAML panel')));
    g.appendChild(c);
  }
  function fmtBytes(n) {
    if (!(n > 0)) return String(n);
    if (n >= 1048576) return (n / 1048576) + ' MB';
    if (n >= 1024) return (n / 1024) + ' KB';
    return n + ' B';
  }
  function closePopover() { if (pop) { pop.remove(); pop = null; } }
  function openPopover(ev, info) {
    closePopover();
    const rootEl = document.getElementById('diagram-root');
    pop = document.createElement('div');
    pop.className = 'dg-pop';
    pop.addEventListener('click', (e) => e.stopPropagation());
    const h = document.createElement('div');
    h.className = 'dg-pop-title'; h.textContent = info.title;
    pop.appendChild(h);
    const tbl = document.createElement('table');
    (info.rows || []).forEach(([k, v]) => {
      if (v == null || v === '' || v === 'None') return;
      const tr = document.createElement('tr');
      const td0 = document.createElement('td'); td0.textContent = k;
      const td1 = document.createElement('td'); td1.textContent = String(v);
      tr.appendChild(td0); tr.appendChild(td1); tbl.appendChild(tr);
    });
    pop.appendChild(tbl);
    const btn = document.createElement('button');
    btn.textContent = 'Edit in Compose';
    btn.addEventListener('click', () => { closePopover(); App.show('compose'); });
    pop.appendChild(btn);
    rootEl.appendChild(pop);
    const rb = rootEl.getBoundingClientRect();
    const px = ev.clientX - rb.left + rootEl.scrollLeft;
    const py = ev.clientY - rb.top + rootEl.scrollTop;
    pop.style.left = Math.max(4, Math.min(px, rootEl.scrollWidth - 270)) + 'px';
    pop.style.top = (py + 10) + 'px';
  }

  // ── one spine: initiators → bus → grouped target grid ──────────────────
  function buildSpine(root, spine, y0) {
    let y = y0;
    const inis = spine.initiators || [];
    const n = Math.max(1, inis.length);
    const iw = Math.min(200, Math.floor((W - 2 * MX - (n - 1) * 16) / n));
    const total = n * iw + (n - 1) * 16;
    const x0 = (W - total) / 2;
    const busY = y + IH + 26;

    inis.forEach((ini, i) => {
      const x = x0 + i * (iw + 16);
      const g = blk(root, {
        x, y, w: iw, h: IH, cls: 'dg-ini ' + (ini.kind || 'other'),
        title: ini.name + ' (' + (ini.kind || 'other') + ') — reaches ' + ini.reach + ' target(s)',
        info: { title: ini.name, rows: [
          ['instance', ini.instance], ['module', ini.module],
          ['kind', ini.kind], ['reach', ini.reach + ' target(s)'],
          ['passthrough', ini.passthrough] ] },
      });
      txt(g, x + 8, y + 19, 'dg-name', ini.name, 'start', iw - 16);
      txt(g, x + 8, y + 36, 'dg-sub', ini.instance || ini.kind, 'start', iw - 16);
      badge(g, x + iw - 8, y + 8, ini.badge);
      root.appendChild(mk('line', { x1: x + iw / 2, y1: y + IH, x2: x + iw / 2,
                                    y2: busY - 2, 'class': 'dg-wire',
                                    'marker-end': 'url(#dg-ah)' }));
    });

    // bus bar
    const bus = blk(root, { x: MX, y: busY, w: W - 2 * MX, h: 36, cls: 'dg-busbar', rx: 8,
                            title: spine.name + ' · ' + spine.type,
                            info: { title: spine.name, rows: [
                              ['type', spine.type], ['initiators', inis.map(i => i.name).join(', ')] ] } });
    txt(bus, W / 2, busY + 23, 'dg-bus', spine.name + ' — ' + (spine.type || 'bus'),
        'middle', W - 2 * MX - 24);
    y = busY + 36;

    // trunk wires bus → target area
    [0.25, 0.5, 0.75].forEach(f => {
      root.appendChild(mk('line', { x1: W * f, y1: y, x2: W * f, y2: y + 22,
                                    'class': 'dg-wire', 'marker-end': 'url(#dg-ah)' }));
    });
    y += 24;

    // groups, ordered memory / periph / subordinate, then the rest
    const keys = GROUP_ORDER.filter(k => spine.groups[k])
      .concat(Object.keys(spine.groups).filter(k => GROUP_ORDER.indexOf(k) < 0).sort());
    keys.forEach(k => {
      const targets = spine.groups[k] || [];
      const rows = Math.max(1, Math.ceil(targets.length / COLS));
      const gh = 24 + rows * (CH + GAP);
      const grp = mk('g', {});
      grp.appendChild(mk('rect', { x: MX, y, width: W - 2 * MX, height: gh, rx: 8,
                                   'class': 'dg-grp-bg' }));
      root.appendChild(grp);
      txt(grp, MX + PAD, y + 16, 'dg-cap',
          (GROUP_LABEL[k] || k) + ' — ' + targets.length + ' target(s)',
          'start', W - 2 * MX - 2 * PAD);
      targets.forEach((t, i) => {
        const cx = MX + PAD + (i % COLS) * (CW + GAP);
        const cy = y + 24 + Math.floor(i / COLS) * (CH + GAP);
        const g = blk(root, {
          x: cx, y: cy, w: CW, h: CH, cls: 'dg-tgt ' + k,
          title: t.drill ? 'subsystem: open Compose to edit'
                         : t.name + ' @ ' + t.base_hex,
          info: { title: t.name, rows: [
            ['instance', t.instance], ['module', t.module],
            ['window', t.base_hex + ' – ' + t.end_hex],
            ['size', fmtBytes(t.size)], ['region_type', t.region_type],
            ['role', t.role], ['protocol', t.protocol],
            t.drill ? ['drill', 'subsystem: open Compose to edit'] : ['', null] ] },
        });
        txt(g, cx + 8, cy + 18, 'dg-name', t.name, 'start', CW - (t.drill ? 30 : 16));
        txt(g, cx + 8, cy + 35, 'dg-addr', t.base_hex, 'start', CW - 16);
        if (t.drill) {
          const m = txt(g, cx + CW - 8, cy + 36, 'dg-cap dg-drill', '▸▸', 'end');
          m.appendChild(mk('title', {}, 'subsystem: open Compose to edit'));
        }
        badge(g, cx + CW - 8, cy + 8, t.badge);
      });
      y += gh + 14;
    });
    return y;
  }

  function buildGhosts(root, ghosts, y) {
    const rows = Math.max(1, Math.ceil(ghosts.length / COLS));
    const gh = 24 + rows * (CH + GAP);
    const grp = mk('g', {});
    grp.appendChild(mk('rect', { x: MX, y, width: W - 2 * MX, height: gh, rx: 8,
                                 'class': 'dg-grp-bg dg-ghost-strip' }));
    root.appendChild(grp);
    txt(grp, MX + PAD, y + 16, 'dg-cap', 'missing required — failed completeness checks',
        'start', W - 2 * MX - 2 * PAD);
    ghosts.forEach((c, i) => {
      const cx = MX + PAD + (i % COLS) * (CW + GAP);
      const cy = y + 24 + Math.floor(i / COLS) * (CH + GAP);
      const g = blk(root, {
        x: cx, y: cy, w: CW, h: CH, cls: 'dg-ghost-blk ' + (c.level || 'info'),
        title: c.hint || c.detail || c.id,
        info: { title: c.desc || c.id, rows: [
          ['check', c.id], ['level', c.level], ['detail', c.detail], ['hint', c.hint] ] },
      });
      txt(g, cx + 8, cy + 19, 'dg-ghost', c.desc || c.id, 'start', CW - 16);
      txt(g, cx + 8, cy + 36, 'dg-sub', c.level || 'info', 'start', CW - 16);
    });
    return y + gh + 10;
  }

  // ── top-level render ────────────────────────────────────────────────────
  function note(rootEl, msg, cls) {
    const d = document.createElement('div');
    d.className = 'dg-note' + (cls ? ' ' + cls : '');
    d.textContent = msg;
    rootEl.appendChild(d);
  }

  async function refresh() {
    const my = ++seq;                  // overlapping refreshes: last one wins
    const rootEl = document.getElementById('diagram-root');
    if (!rootEl) return;
    closePopover();
    rootEl.innerHTML = '';
    if (!App.currentDraft) {
      note(rootEl, 'No active draft — open one in the YAML panel first.');
      return;
    }
    let d;
    try { d = await App.api('/api/draft/' + App.currentDraft + '/diagram'); }
    catch (e) {
      if (my === seq) note(rootEl, 'diagram fetch failed: ' + e.message, 'err');
      return;
    }
    if (my !== seq) return;            // a newer refresh superseded this one
    rootEl.innerHTML = '';             // clear any note the stale pass left
    if (!d.ok) {
      note(rootEl, 'model does not build — fix tier-1 errors in the YAML panel:', 'err');
      (d.messages || []).forEach(m => note(rootEl, '• ' + (m.message || ''), 'err'));
      return;
    }

    // header: module + desc + tinted counts
    const hdr = document.createElement('div');
    hdr.className = 'dg-header';
    const name = document.createElement('span');
    name.className = 'dg-mod'; name.textContent = d.module;
    const counts = document.createElement('span');
    const ne = d.counts.errors, nw = d.counts.warnings;
    counts.className = 'dg-counts ' + (ne ? 'err' : nw ? 'warn' : 'ok');
    counts.textContent = ne || nw
      ? ne + ' error' + (ne === 1 ? '' : 's') + ', ' + nw + ' warning' + (nw === 1 ? '' : 's')
      : 'clean ✓';
    const desc = document.createElement('span');
    desc.className = 'dg-desc';
    desc.textContent = (d.desc || '').replace(/\s+/g, ' ').trim();
    hdr.appendChild(name); hdr.appendChild(counts); hdr.appendChild(desc);
    // deep-dive: the always-correct full D3 connectivity view (#5). The curated
    // SVG below is heuristic; this opens the real SoCVisualizer page.
    const dd = document.createElement('a');
    dd.className = 'dg-deepdive';
    dd.textContent = 'Open deep-dive ↗';
    dd.title = 'Full D3 connectivity view (the always-correct fallback)';
    dd.href = '/api/draft/' + encodeURIComponent(App.currentDraft) + '/deepdive';
    dd.target = '_blank'; dd.rel = 'noopener';
    hdr.appendChild(dd);
    rootEl.appendChild(hdr);

    const svg = mk('svg', { 'class': 'dg-svg', width: '100%' });
    const defs = mk('defs', {});
    const mark = mk('marker', { id: 'dg-ah', viewBox: '0 0 8 8', refX: 7, refY: 4,
                                markerWidth: 6, markerHeight: 6, orient: 'auto' });
    mark.appendChild(mk('path', { d: 'M0,0 L8,4 L0,8 z', 'class': 'dg-ah' }));
    defs.appendChild(mark);
    svg.appendChild(defs);
    rootEl.appendChild(svg);

    let y = 14;
    (d.spines || []).forEach((s) => { y = buildSpine(svg, s, y) + 16; });
    if ((d.ghosts || []).length) y = buildGhosts(svg, d.ghosts, y);
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + Math.max(220, y + 6));
  }

  const CSS = `
#diagram-root { position: relative; }
.dg-header { display: flex; gap: 12px; align-items: baseline; margin: 2px 2px 8px;
             flex-wrap: wrap; }
.dg-header .dg-mod { font-weight: 700; font-size: 15px; }
.dg-header .dg-counts.err  { color: var(--err); }
.dg-header .dg-counts.warn { color: var(--warn); }
.dg-header .dg-counts.ok   { color: var(--ok); }
.dg-header .dg-deepdive { margin-left: auto; font-size: 12px; white-space: nowrap;
  cursor: pointer; border: 1px solid var(--accent, #4da3ff);
  color: var(--accent, #4da3ff); background: rgba(77,163,255,.08);
  border-radius: 6px; padding: 2px 10px; text-decoration: none; }
.dg-header .dg-deepdive:hover { background: rgba(77,163,255,.2); }
.dg-header .dg-desc { color: var(--dim); font-size: 12px; max-width: 70ch;
                      white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dg-note { color: var(--dim); padding: 18px 6px; font-size: 13px; }
.dg-note.err { color: var(--err); padding: 4px 6px; }
.dg-svg { max-width: 1180px; display: block; }
.dg-svg text { fill: var(--fg); }
.dg-svg .dg-name  { font: 11px system-ui, sans-serif; }
.dg-svg .dg-sub   { font: 10px monospace; fill: var(--dim); }
.dg-svg .dg-addr  { font: 10px monospace; fill: var(--dim); }
.dg-svg .dg-cap   { font: 10px monospace; fill: var(--dim); }
.dg-svg .dg-bus   { font: 600 12px system-ui, sans-serif; }
.dg-svg .dg-ghost { font: italic 10px system-ui, sans-serif; fill: var(--dim); }
.dg-blk { cursor: pointer; }
.dg-blk:focus { outline: none; }
.dg-blk:focus .dg-bg, .dg-blk:hover .dg-bg { stroke: var(--info); }
.dg-bg { fill: var(--panel); stroke: var(--line); stroke-width: 1; }
.dg-ini.cpu   .dg-bg { fill: var(--info); fill-opacity: .14; stroke: var(--info); }
.dg-ini.debug .dg-bg { fill: var(--ok);   fill-opacity: .10; stroke: var(--ok); }
.dg-ini.dma   .dg-bg { fill: var(--warn); fill-opacity: .12; stroke: var(--warn); }
.dg-ini.other .dg-bg { fill: var(--dim);  fill-opacity: .10; stroke: var(--line); }
.dg-busbar .dg-bg { fill: var(--line); fill-opacity: .55; stroke: var(--dim); }
.dg-tgt.memory      .dg-bg { fill: var(--info); fill-opacity: .07; }
.dg-tgt.subordinate .dg-bg { fill: var(--ok);   fill-opacity: .06; }
.dg-grp-bg { fill: var(--panel); fill-opacity: .45; stroke: var(--line);
             stroke-dasharray: none; }
.dg-ghost-strip { stroke-dasharray: 5 4; }
.dg-ghost-blk .dg-bg { fill: none; stroke-dasharray: 5 4; stroke: var(--dim); }
.dg-ghost-blk.error   .dg-bg { stroke: var(--err); }
.dg-ghost-blk.warning .dg-bg { stroke: var(--warn); }
.dg-dot.error   { fill: var(--err); }
.dg-dot.warning { fill: var(--warn); }
.dg-drill { fill: var(--info); }
.dg-wire { stroke: var(--dim); stroke-width: 1.2; }
.dg-ah { fill: var(--dim); }
.dg-pop { position: absolute; z-index: 30; background: var(--panel);
          border: 1px solid var(--info); border-radius: 6px; padding: 10px 12px;
          min-width: 240px; max-width: 360px; box-shadow: 0 6px 18px rgba(0,0,0,.5);
          font-size: 12px; }
.dg-pop .dg-pop-title { font-weight: 700; margin-bottom: 6px; }
.dg-pop table { border-collapse: collapse; margin-bottom: 8px; }
.dg-pop td { padding: 1px 8px 1px 0; vertical-align: top; }
.dg-pop td:first-child { color: var(--dim); font-family: monospace;
                         white-space: nowrap; }
.dg-pop td:last-child { font-family: monospace; word-break: break-all; }
.dg-pop button { font-size: 12px; }
`;

  return {
    init() {
      const st = document.createElement('style');
      st.textContent = CSS;
      document.head.appendChild(st);
      document.addEventListener('click', closePopover);
      refresh();
    },
    onDraft() { refresh(); },
    onShow() { refresh(); },
  };
})());
