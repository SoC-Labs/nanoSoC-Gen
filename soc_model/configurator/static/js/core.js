// Copyright 2026, SoC Labs (www.soclabs.org)
// SoC Configurator core: dynamic left-sidebar nav, panel switching, fetch
// helpers, WebSocket hub. (Panel-registry pattern vendored from the multicore
// demo GUI core.js; the grey top tabs were replaced by a grouped sidebar.)
'use strict';

const App = {
  panels: {},                       // id -> {init?, onEvent?, onDraft?, onShow?}
  ws: null,
  currentDraft: null,               // shared draft id (set by the YAML panel)

  // ---- Sidebar nav config -------------------------------------------------
  // Adding a panel is one line: drop {id,label,icon} into a group. `id` must
  // match a <section id="panel-id"> in base.html. First item = default panel.
  NAV: [
    { group: 'Design', items: [
      { id: 'builder',   label: 'Builder',     icon: '🔨' },
      { id: 'canvas',    label: 'Drag & Drop', icon: '🧱' },
      { id: 'diagram',   label: 'Diagram',     icon: '🗺️' },
      { id: 'memmap',    label: 'Memory Map',  icon: '📐' },
    ]},
    { group: 'Edit', items: [
      { id: 'compose',   label: 'Compose',     icon: '🎛️' },
      { id: 'irqmap',    label: 'IRQ Map',     icon: '⚡' },
      { id: 'compare',   label: 'Compare',     icon: '🔀' },
      { id: 'yaml',      label: 'YAML',        icon: '📝' },
    ]},
    { group: 'Build', items: [
      { id: 'build',     label: 'Build',       icon: '🏗️' },
      { id: 'generated', label: 'Built SoC',   icon: '✅' },
      { id: 'artifacts', label: 'Artifacts',   icon: '📦' },
    ]},
  ],

  register(id, panel) { this.panels[id] = panel; },

  setDraft(id) {
    this.currentDraft = id;
    Object.values(this.panels).forEach(p => p.onDraft && p.onDraft(id));
  },

  show(id) {
    document.querySelectorAll('main .panel').forEach(p => p.hidden = true);
    const el = document.getElementById('panel-' + id);
    if (el) el.hidden = false;
    // active-state on sidebar nav items (legacy .tabs button kept as fallback)
    document.querySelectorAll('.sb-item').forEach(b =>
      b.classList.toggle('active', b.dataset.panel === id));
    document.querySelectorAll('.tabs button').forEach(b =>
      b.classList.toggle('active', b.dataset.panel === id));
    this.activePanel = id;
    const p = this.panels[id];
    if (p && p.onShow) p.onShow();
  },

  // ---- Sidebar rendering --------------------------------------------------
  renderNav() {
    const nav = document.getElementById('sb-nav');
    if (!nav) return;
    nav.innerHTML = '';
    const COLLAPSED_KEY = 'soccfg.nav.collapsed.groups';
    let collapsedGroups = {};
    try { collapsedGroups = JSON.parse(localStorage.getItem(COLLAPSED_KEY) || '{}') || {}; }
    catch (e) { collapsedGroups = {}; }

    this.NAV.forEach((grp) => {
      const gEl = document.createElement('div');
      gEl.className = 'sb-group';
      const key = grp.group;
      if (collapsedGroups[key]) gEl.classList.add('collapsed');

      const head = document.createElement('button');
      head.type = 'button';
      head.className = 'sb-group-head';
      head.innerHTML = '<span class="sb-group-label">' + key +
        '</span><span class="sb-group-caret" aria-hidden="true">▾</span>';
      head.addEventListener('click', () => {
        gEl.classList.toggle('collapsed');
        collapsedGroups[key] = gEl.classList.contains('collapsed');
        try { localStorage.setItem(COLLAPSED_KEY, JSON.stringify(collapsedGroups)); } catch (e) {}
      });
      gEl.appendChild(head);

      const list = document.createElement('div');
      list.className = 'sb-group-items';
      grp.items.forEach((it) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'sb-item';
        b.dataset.panel = it.id;
        b.title = it.label;
        b.innerHTML =
          '<span class="sb-accent" aria-hidden="true"></span>' +
          '<span class="sb-ico" aria-hidden="true">' + (it.icon || '•') + '</span>' +
          '<span class="sb-label">' + it.label + '</span>';
        b.addEventListener('click', () => { if (!b.disabled) App.show(it.id); });
        list.appendChild(b);
      });
      gEl.appendChild(list);
      nav.appendChild(gEl);
    });
  },

  // collapse-to-icons toggle for the whole sidebar (persisted)
  initSidebarCollapse() {
    const SB_KEY = 'soccfg.sidebar.collapsed';
    const sb = document.getElementById('sidebar');
    const btn = document.getElementById('sb-collapse');
    if (!sb) return;
    const apply = (on) => {
      sb.classList.toggle('collapsed', !!on);
      if (btn) {
        btn.title = on ? 'Expand sidebar' : 'Collapse sidebar';
        btn.setAttribute('aria-label', btn.title);
      }
    };
    let on = false;
    try { on = localStorage.getItem(SB_KEY) === '1'; } catch (e) {}
    apply(on);
    if (btn) btn.addEventListener('click', () => {
      on = !sb.classList.contains('collapsed');
      apply(on);
      try { localStorage.setItem(SB_KEY, on ? '1' : '0'); } catch (e) {}
    });
  },

  async api(path, opts = {}) {
    if (opts.json !== undefined) {
      opts.method = opts.method || 'POST';
      opts.headers = { 'Content-Type': 'application/json' };
      opts.body = JSON.stringify(opts.json);
      delete opts.json;
    }
    const r = await fetch(path, opts);
    if (!r.ok) {
      let detail = r.statusText;
      try { detail = (await r.json()).detail || detail; } catch (e) {}
      throw new Error(`${r.status}: ${detail}`);
    }
    const ct = r.headers.get('content-type') || '';
    return ct.includes('json') ? r.json() : r.text();
  },

  connectWS() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    this.ws = new WebSocket(`${proto}://${location.host}/ws`);
    this.ws.onmessage = (m) => {
      let ev; try { ev = JSON.parse(m.data); } catch (e) { return; }
      Object.values(this.panels).forEach(p => p.onEvent && p.onEvent(ev));
    };
    this.ws.onclose = () => setTimeout(() => this.connectWS(), 2000);
  },

  debounce(fn, ms) {
    let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  },
};

document.addEventListener('DOMContentLoaded', async () => {
  App.renderNav();
  App.initSidebarCollapse();

  // Undo / redo keyboard shortcuts (#8): Ctrl+Z = undo, Ctrl+Y = redo.
  // Fires a full onDraft refresh on the active panel after the API call so
  // the panel always reflects the current draft state.
  document.addEventListener('keydown', async (e) => {
    if (!App.currentDraft) return;
    const isUndo = e.ctrlKey && !e.shiftKey && e.key === 'z';
    const isRedo = e.ctrlKey && (e.key === 'y' || (e.shiftKey && e.key === 'z'));
    if (!isUndo && !isRedo) return;
    // Don't intercept Ctrl+Z inside text inputs/textareas (let the browser handle those).
    if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) return;
    e.preventDefault();
    const action = isUndo ? 'undo' : 'redo';
    try {
      await App.api(`/api/draft/${App.currentDraft}/${action}`, { method: 'POST', json: {} });
      // Re-notify all panels so they reload the updated draft
      Object.values(App.panels).forEach(p => p.onDraft && p.onDraft(App.currentDraft));
    } catch (err) { /* ignore if no draft or server error */ }
  });
  // legacy fallback: any old-style top tabs still wire up
  document.querySelectorAll('.tabs button[data-panel]').forEach(b =>
    b.addEventListener('click', () => !b.disabled && App.show(b.dataset.panel)));

  // default panel = first nav item
  let first = 'builder';
  if (App.NAV[0] && App.NAV[0].items && App.NAV[0].items[0]) first = App.NAV[0].items[0].id;
  App.show(first);

  App.connectWS();

  // env health chip + dot
  try {
    const p = await App.api('/api/project');
    const bad = Object.entries(p.health || {}).filter(([, v]) => !v).map(([k]) => k);
    const el = document.getElementById('env-health');
    const dot = document.getElementById('env-dot');
    if (el) {
      el.textContent = bad.length ? `env: missing ${bad.join(', ')}` : 'env: ok';
      el.classList.toggle('bad', bad.length > 0);
    }
    if (dot) dot.classList.toggle('bad', bad.length > 0);

    // build-freshness (#3) + lib-shadow (#17) chips in the sidebar footer
    const foot = document.querySelector('.sb-foot');
    if (foot) {
      const bf = p.build_fresh || {};
      const chip = (id) => {
        let c = document.getElementById(id);
        if (!c) { c = document.createElement('div'); c.id = id;
                  c.className = 'sb-chip'; foot.appendChild(c); }
        return c;
      };
      if (bf.state) {
        const c = chip('build-fresh');
        const map = { stale: ['build: stale ⚠', 'warn'],
                      fresh: ['build: fresh ✓', 'ok'],
                      unknown: ['build: —', 'dim'] };
        const [txt, cls] = map[bf.state] || map.unknown;
        c.textContent = txt; c.title = bf.reason || '';
        c.className = 'sb-chip ' + cls;
      }
      if (p.lib_shadows > 0) {
        const c = chip('lib-shadow');
        c.className = 'sb-chip warn';
        c.textContent = 'shadows: ' + p.lib_shadows;
        c.title = p.lib_shadows + ' module name/stem(s) defined in more than '
                + 'one lib dir — last writer wins (see the catalog)';
      }
    }
  } catch (e) { /* leave placeholder */ }

  Object.values(App.panels).forEach(p => p.init && p.init());
});
