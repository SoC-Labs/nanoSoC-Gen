// Copyright 2026, SoC Labs (www.soclabs.org)
// Compose panel (doc 06a M5): structured forms over GET/PUT /api/draft/{id}/dict.
// Local working copy of the module dict; Save button + 600ms debounced autosave
// PUT the whole dict back and render the returned validation inline.
'use strict';

App.register('compose', (() => {
  const root = () => document.getElementById('compose-root');
  let dict = null;            // working copy: {module: {...}}
  let draftOf = null;         // draft id the working copy belongs to
  let dirty = false;
  let modules = null;         // catalog module names (lazy, cached)
  let hints = null;           // field tooltip hints (#4, lazy, cached)
  let lastVal = null;         // last validation result
  let showMsgs = false;       // validation message list expanded?
  const expanded = new Set(); // expanded instance rows (by index)

  // ---------- small helpers ----------
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
  const mod = () => (dict && dict.module) || {};

  // one-line tooltips for the enumerated selects
  const TT_REGION = {
    '': 'unset — use the generator default',
    'memory': 'memory: RAM/ROM-style storage region (code/data, visible to the linker/memory map)',
    'periph': 'periph: memory-mapped peripheral registers (device I/O, never executed from)',
  };
  const TT_SW = {
    '': 'unset — use the generator default',
    'rw': 'rw: software may read and write (data regions/registers)',
    'rwx': 'rwx: read, write and execute (RAM that holds code and data)',
    'rx': 'rx: read and execute, no writes (ROM / XiP code)',
    'ro': 'ro: read-only (constant data / status)',
    'r': 'r: read-only',
    'w': 'w: write-only (e.g. trigger/command registers)',
  };
  const TT_PROTO = {
    '': 'unset — inherit the interconnect protocol',
    'ahb': 'ahb: native AHB-Lite target on the matrix',
    'apb': 'apb: APB peripheral (reached through an AHB-to-APB bridge)',
  };

  // 0x-hex render for ints; $PARAM / other strings pass through untouched.
  function hex(v) {
    if (typeof v === 'number' && isFinite(v))
      return '0x' + (v >>> 0 === v ? v.toString(16).toUpperCase().padStart(8, '0')
                                   : v.toString(16).toUpperCase());
    return v == null ? '' : String(v);
  }
  // radix-detecting parse; non-numeric strings (e.g. "$SYS_ADDR_W") kept as-is.
  function parseNum(s) {
    const t = String(s).trim();
    if (/^0x[0-9a-f]+$/i.test(t)) return parseInt(t, 16);
    if (/^-?[0-9]+$/.test(t)) return parseInt(t, 10);
    return s;
  }

  // Return a title attribute string for a field name, using the hints dict (#4).
  // Empty string when no hint is available so callers can always interpolate.
  function ht(field) {
    const h = hints && hints[field];
    return h ? ` title="${esc(h)}"` : '';
  }

  // dotted-path setter into mod(); numeric segments index arrays.
  function setPath(path, value) {
    const seg = path.split('.');
    let o = mod();
    for (let i = 0; i < seg.length - 1; i++) {
      const k = /^[0-9]+$/.test(seg[i]) ? parseInt(seg[i], 10) : seg[i];
      if (o[k] == null) o[k] = /^[0-9]+$/.test(seg[i + 1]) ? [] : {};
      o = o[k];
    }
    const last = seg[seg.length - 1];
    if (value === undefined) delete o[last]; else o[last] = value;
  }

  // ---------- save / validation ----------
  async function save() {
    if (!dict || !App.currentDraft) return;
    const st = document.getElementById('compose-state');
    if (st) st.textContent = 'saving…';
    try {
      lastVal = await App.api('/api/draft/' + App.currentDraft + '/dict',
                              { method: 'PUT', json: dict });
      dirty = false;
      if (st) st.textContent = 'saved ✓';
    } catch (e) {
      if (st) st.textContent = 'save failed: ' + e.message;
      lastVal = null;
    }
    applyValidation();
  }
  const autosave = App.debounce(save, 600);
  function markDirty() {
    dirty = true;
    const st = document.getElementById('compose-state');
    if (st) st.textContent = '…';
    autosave();
  }

  function applyValidation() {
    const msgs = (lastVal && lastVal.messages) || [];
    // mini-bar chips
    const ce = document.getElementById('compose-chip-err');
    const cw = document.getElementById('compose-chip-warn');
    if (ce) {
      if (!lastVal) {
        ce.textContent = '—'; ce.className = 'cmp-chip';
        if (cw) cw.hidden = true;
      } else {
        const e = lastVal.errors || 0, w = lastVal.warnings || 0;
        ce.textContent = e ? e + ' error' + (e === 1 ? '' : 's') : '✓ no errors';
        ce.className = 'cmp-chip ' + (e ? 'bad' : 'ok');
        if (cw) {
          cw.hidden = !w;
          cw.textContent = w + ' warning' + (w === 1 ? '' : 's');
          cw.className = 'cmp-chip warn';
        }
      }
    }
    // inline message list (sticky bar dropdown)
    const tg = document.getElementById('compose-msgtoggle');
    if (tg) {
      tg.hidden = !msgs.length;
      tg.textContent = (showMsgs ? '▾ hide' : '▸ show') +
                       ' messages (' + msgs.length + ')';
    }
    const box = document.getElementById('compose-msgs');
    if (box) {
      box.hidden = !(showMsgs && msgs.length);
      box.innerHTML = msgs.map(mm => {
        const where = [mm.instance, mm.port].filter(Boolean).join(' / ');
        return `<div class="cmp-msg ${esc(mm.level)}"><b>[${esc(mm.level)}]</b>` +
               (where ? ` <span class="cmp-msg-where">${esc(where)}</span>` : '') +
               ` ${esc(mm.message)}</div>`;
      }).join('');
    }
    // per-row highlights + tooltips
    const rows = root() ? root().querySelectorAll('[data-names]') : [];
    rows.forEach(r => { r.classList.remove('c-err', 'c-warn'); r.removeAttribute('title'); });
    if (msgs.length) {
      rows.forEach(r => {
        const names = (r.dataset.names || '').split(/\s+/).filter(Boolean);
        const hits = msgs.filter(m =>
          (m.instance && names.indexOf(m.instance) >= 0) ||
          (m.port && names.indexOf(m.port) >= 0));
        if (!hits.length) return;
        r.classList.add(hits.some(m => m.level === 'error') ? 'c-err' : 'c-warn');
        r.title = hits.map(m => `[${m.level}] ${m.message}`).join('\n');
      });
    }
    // section cards: accent left-border when their content has hits
    if (root()) {
      root().querySelectorAll('details.cmp-sec').forEach(d => {
        const hasErr = !!d.querySelector('.c-err');
        d.classList.toggle('cmp-hit-err', hasErr);
        d.classList.toggle('cmp-hit-warn', !hasErr && !!d.querySelector('.c-warn'));
      });
    }
  }

  // ---------- render ----------
  function instNames() {
    return (mod().instances || []).map(i => i.instance_name).filter(Boolean);
  }
  function instOptions(sel) {
    const names = instNames();
    if (sel && names.indexOf(sel) < 0) names.push(sel);  // keep odd refs visible
    return ['<option value=""></option>'].concat(names.map(n =>
      `<option value="${esc(n)}"${n === sel ? ' selected' : ''}>${esc(n)}</option>`
    )).join('');
  }
  function selOptions(opts, sel, titles) {
    return opts.map(o => {
      const tt = titles && titles[o] ? ` title="${esc(titles[o])}"` : '';
      return `<option value="${esc(o)}"${String(sel == null ? '' : sel) === o ? ' selected' : ''}${tt}>${o || '(none)'}</option>`;
    }).join('');
  }
  function section(id, label, body, count) {
    const badge = count == null ? '' : `<span class="cmp-count">${esc(count)}</span>`;
    return `<details class="cmp-sec" data-sec="${id}" open>` +
           `<summary>${esc(label)}${badge}</summary><div class="cmp-body">${body}</div></details>`;
  }

  function renderParams() {
    const p = mod().params || {};
    const rows = Object.keys(p).map(k => {
      const v = p[k];
      const dflt = (v && typeof v === 'object') ? v.default : v;
      return `<tr data-names="${esc(k)}">
        <td><input data-act="param-name" data-key="${esc(k)}" value="${esc(k)}"></td>
        <td><input data-act="param-default" data-key="${esc(k)}" value="${esc(dflt == null ? '' : dflt)}"></td>
        <td><button data-act="del-param" data-key="${esc(k)}" title="delete param">×</button></td></tr>`;
    }).join('');
    return `<table class="cmp-tbl"><tr><th>name</th><th>default</th><th></th></tr>${rows}</table>
      <button data-act="add-param">+ add param</button>`;
  }

  function renderInstances() {
    const ins = mod().instances || [];
    const rows = ins.map((i, ix) => {
      const modKey = ('module' in i || !('rtl_module' in i)) ? 'module' : 'rtl_module';
      const conns = i.connections || [];
      const sub = !expanded.has(ix) ? '' :
        `<tr class="cmp-sub"><td colspan="5"><div class="cmp-conns">` +
        conns.map((c, ci) =>
          `<div><input data-path="instances.${ix}.connections.${ci}.port" value="${esc(c && c.port)}" placeholder="port">
           <input class="wide" data-path="instances.${ix}.connections.${ci}.conn" value="${esc(c && c.conn)}" placeholder="conn">
           <button data-act="del-conn" data-idx="${ix}" data-ci="${ci}" title="delete connection">×</button></div>`).join('') +
        `<button data-act="add-conn" data-idx="${ix}">+ add connection</button></div></td></tr>`;
      return `<tr data-names="${esc(i.instance_name || '')}">
        <td><button data-act="toggle-inst" data-idx="${ix}" title="${expanded.has(ix) ? 'hide' : 'show'} connections">${expanded.has(ix) ? '▾' : '▸'}</button></td>
        <td><input data-path="instances.${ix}.instance_name" data-rerender="1" value="${esc(i.instance_name)}"${ht('instance_name')}></td>
        <td><input data-path="instances.${ix}.${modKey}" list="compose-modlist" value="${esc(i[modKey])}"${ht('module')}></td>
        <td><input type="checkbox" data-path="instances.${ix}.addressable" data-bool="1" title="${hints && hints.addressable ? esc(hints.addressable) : 'addressable: instance owns a slice of the address map'}"${i.addressable ? ' checked' : ''}></td>
        <td><button data-act="del-inst" data-idx="${ix}" title="delete instance">×</button></td></tr>${sub}`;
    }).join('');
    return `<table class="cmp-tbl">
      <tr><th></th><th>instance_name</th><th>module</th><th>addr</th><th></th></tr>${rows}</table>
      <button data-act="add-inst">+ add instance</button>`;
  }

  function renderInterconnect(ic, ci) {
    const tgts = ic.targets || [];
    const trows = tgts.map((t, ti) => {
      const base = `interconnects.${ci}.targets.${ti}`;
      const ttR = TT_REGION[t.region_type] || 'region_type: memory (RAM/ROM) vs periph (registers)';
      const ttS = TT_SW[t.sw_access] || 'sw_access: software permissions (read/write/execute)';
      const ttP = TT_PROTO[t.protocol] || 'protocol: ahb (native) or apb (bridged)';
      return `<tr data-names="${esc(t.name || '')} ${esc(t.instance || '')}">
        <td><input data-path="${base}.name" data-rerender="1" value="${esc(t.name)}"${ht('name')}></td>
        <td><select data-path="${base}.instance"${ht('instance')}>${instOptions(t.instance)}</select></td>
        <td><input class="num" data-path="${base}.base" data-num="1" value="${esc(hex(t.base))}"${ht('base')}></td>
        <td><input class="num" data-path="${base}.size" data-num="1" value="${esc(hex(t.size))}"${ht('size')}></td>
        <td><select data-path="${base}.region_type" data-blank-del="1" title="${esc(ttR)}">${selOptions(['', 'memory', 'periph'], t.region_type, TT_REGION)}</select></td>
        <td><select data-path="${base}.sw_access" data-blank-del="1" title="${esc(ttS)}">${selOptions(['', 'rw', 'rwx', 'rx', 'ro', 'r', 'w'], t.sw_access, TT_SW)}</select></td>
        <td><input data-path="${base}.role" value="${esc(t.role)}"${ht('role')}></td>
        <td><select data-path="${base}.protocol" data-blank-del="1" title="${esc(ttP)}">${selOptions(['', 'ahb', 'apb'], t.protocol, TT_PROTO)}</select></td>
        <td><button data-act="del-target" data-ic="${ci}" data-ti="${ti}" title="delete target">×</button></td></tr>`;
    }).join('');
    const inits = ic.initiators || [];
    const irows = inits.map((ini, ii) => {
      const reach = (ini.targets || []).map(t => (t && t.name) || t);
      const boxes = tgts.map(t =>
        `<label class="cmp-reach"><input type="checkbox" data-act="init-tgt" data-ic="${ci}" data-ii="${ii}"
          data-tname="${esc(t.name)}"${reach.indexOf(t.name) >= 0 ? ' checked' : ''}> ${esc(t.name)}</label>`).join('');
      return `<tr data-names="${esc(ini.name || '')} ${esc(ini.instance || '')}">
        <td><input data-path="interconnects.${ci}.initiators.${ii}.name" value="${esc(ini.name)}"></td>
        <td><select data-path="interconnects.${ci}.initiators.${ii}.instance">${instOptions(ini.instance)}</select></td>
        <td class="cmp-reachlist">${boxes || '<span class="dim">(no targets)</span>'}</td>
        <td><button data-act="del-init" data-ic="${ci}" data-ii="${ii}" title="delete initiator">×</button></td></tr>`;
    }).join('');
    return section('ic-' + ci, 'Interconnect: ' + (ic.name || '(unnamed)'),
      `<h4>Targets</h4><table class="cmp-tbl">
       <tr><th>name</th><th>instance</th><th>base</th><th>size</th><th>region</th>
       <th>sw_access</th><th>role</th><th>protocol</th><th></th></tr>${trows}</table>
       <button data-act="add-target" data-ic="${ci}">+ add target</button>
       <h4>Initiators</h4><table class="cmp-tbl">
       <tr><th>name</th><th>instance</th><th>reachable targets</th><th></th></tr>${irows}</table>
       <button data-act="add-init" data-ic="${ci}">+ add initiator</button>`,
      tgts.length + ' targets · ' + inits.length + ' initiators');
  }

  function render() {
    const r = root();
    if (!r) return;
    if (!App.currentDraft) {
      r.innerHTML = '<p class="cmp-hint">Open a draft first (Builder tab → create one, or YAML tab → pick).</p>';
      return;
    }
    if (!dict) { r.innerHTML = '<p class="cmp-hint">loading…</p>'; return; }
    const open = {};
    r.querySelectorAll('details[data-sec]').forEach(d => { open[d.dataset.sec] = d.open; });
    const m = mod();
    let html = `<div class="cmp-head">
        <button data-act="goto-builder" class="cmp-crumb" title="Back to the Builder wizard">◀ Builder</button>
        <span class="cmp-bc" title="draft · module">${esc(App.currentDraft)}${m.name ? `<span class="cmp-bc-mod"> · ${esc(m.name)}</span>` : ''}</span>
        <button data-act="save">Save</button>
        <span id="compose-state" class="dim"></span>
        <span id="compose-chip-err" class="cmp-chip">—</span>
        <span id="compose-chip-warn" class="cmp-chip" hidden></span>
        <button data-act="toggle-msgs" id="compose-msgtoggle" class="cmp-msgbtn" hidden></button>
        <div id="compose-msgs" class="cmp-msgs" hidden></div>
      </div>
      <datalist id="compose-modlist">${(modules || []).map(x => `<option value="${esc(x)}">`).join('')}</datalist>`;
    html += section('module', 'Module',
      `<div class="cmp-kv"><label>name</label><input data-path="name" value="${esc(m.name)}"${ht('name')}></div>
       <div class="cmp-kv"><label>desc</label><textarea data-path="desc" rows="3"${ht('desc')}>${esc(m.desc)}</textarea></div>`);
    html += section('params', 'Params', renderParams(),
                    Object.keys(m.params || {}).length);
    html += section('instances', 'Instances', renderInstances(),
                    (m.instances || []).length);
    (m.interconnects || []).forEach((ic, ci) => { html += renderInterconnect(ic, ci); });
    html += '<button data-act="add-ic">+ add interconnect</button>';
    r.innerHTML = html;
    r.querySelectorAll('details[data-sec]').forEach(d => {
      if (d.dataset.sec in open) d.open = open[d.dataset.sec];
    });
    if (dirty) { const st = document.getElementById('compose-state'); if (st) st.textContent = '…'; }
    applyValidation();
  }

  // ---------- event delegation ----------
  function onEdit(e) {
    const t = e.target;
    if (!dict || !t.dataset) return;
    if (t.dataset.path) {
      let v;
      if (t.dataset.bool) v = t.checked;
      else if (t.dataset.num) v = parseNum(t.value);
      else v = t.value;
      if (t.dataset.blankDel && v === '') v = undefined;   // drop optional keys
      setPath(t.dataset.path, v);
      markDirty();
      if (t.dataset.num && e.type === 'change') {
        // on blur: normalize ints to padded 0x form; flag junk (keep the text)
        t.classList.remove('bad');
        const raw = String(t.value).trim();
        if (typeof v === 'number' && isFinite(v)) t.value = hex(v);
        else if (raw !== '' && !/^\$/.test(raw)) t.classList.add('bad');
      }
      if (t.dataset.rerender && e.type === 'change') render();
      return;
    }
    const act = t.dataset.act;
    if (!act || e.type !== 'change') return;
    const m = mod();
    if (act === 'param-name') {                              // rename (on blur)
      const oldK = t.dataset.key, newK = t.value.trim();
      if (!newK || newK === oldK) return;
      const p = m.params || {}, np = {};
      Object.keys(p).forEach(k => { np[k === oldK ? newK : k] = p[k]; });
      m.params = np; markDirty(); render();
    } else if (act === 'param-default') {
      const p = (m.params = m.params || {});
      const cur = p[t.dataset.key];
      if (cur && typeof cur === 'object') cur.default = parseNum(t.value);
      else p[t.dataset.key] = parseNum(t.value);
      markDirty();
    } else if (act === 'init-tgt') {
      const ic = (m.interconnects || [])[+t.dataset.ic]; if (!ic) return;
      const ini = (ic.initiators || [])[+t.dataset.ii]; if (!ini) return;
      let lst = ini.targets = ini.targets || [];
      const has = (x) => ((x && x.name) || x) === t.dataset.tname;
      if (t.checked) { if (!lst.some(has)) lst.push({ name: t.dataset.tname }); }
      else ini.targets = lst.filter(x => !has(x));
      markDirty();
    }
  }

  function onClick(e) {
    const t = e.target.closest('[data-act]');
    if (!t) return;
    const act = t.dataset.act;
    if (act === 'goto-builder') { App.show('builder'); return; }
    if (!dict) return;
    if (act === 'toggle-msgs') { showMsgs = !showMsgs; applyValidation(); return; }
    const m = mod();
    const ics = () => (m.interconnects = m.interconnects || []);
    const reindex = (set, gone) => {        // keep expansion state across splice
      const next = new Set();
      set.forEach(i => { if (i !== gone) next.add(i > gone ? i - 1 : i); });
      set.clear(); next.forEach(i => set.add(i));
    };
    switch (act) {
      case 'save': save(); return;
      case 'toggle-inst': {
        const ix = +t.dataset.idx;
        expanded.has(ix) ? expanded.delete(ix) : expanded.add(ix);
        render(); return;
      }
      case 'add-param': {
        const p = (m.params = m.params || {});
        let n = 'NEW_PARAM', k = 0;
        while (n in p) n = 'NEW_PARAM_' + (++k);
        p[n] = { type: 'int', default: 0 };
        break;
      }
      case 'del-param': delete (m.params || {})[t.dataset.key]; break;
      case 'add-inst':
        (m.instances = m.instances || []).push(
          { instance_name: 'u_new_' + (m.instances ? m.instances.length : 0),
            module: '', addressable: false, connections: [] });
        break;
      case 'del-inst':
        (m.instances || []).splice(+t.dataset.idx, 1);
        reindex(expanded, +t.dataset.idx);
        break;
      case 'add-conn': {
        const i = (m.instances || [])[+t.dataset.idx]; if (!i) return;
        (i.connections = i.connections || []).push({ port: '', conn: '' });
        break;
      }
      case 'del-conn': {
        const i = (m.instances || [])[+t.dataset.idx]; if (!i) return;
        (i.connections || []).splice(+t.dataset.ci, 1);
        break;
      }
      case 'add-target': {
        const ic = ics()[+t.dataset.ic]; if (!ic) return;
        (ic.targets = ic.targets || []).push(
          { name: '', instance: '', base: 0, size: 0x1000,
            sw_access: 'rw', region_type: 'periph' });
        break;
      }
      case 'del-target': {
        const ic = ics()[+t.dataset.ic]; if (!ic) return;
        (ic.targets || []).splice(+t.dataset.ti, 1);
        break;
      }
      case 'add-init': {
        const ic = ics()[+t.dataset.ic]; if (!ic) return;
        (ic.initiators = ic.initiators || []).push({ name: '', targets: [] });
        break;
      }
      case 'del-init': {
        const ic = ics()[+t.dataset.ic]; if (!ic) return;
        (ic.initiators || []).splice(+t.dataset.ii, 1);
        break;
      }
      case 'add-ic':
        ics().push({ name: 'new_interconnect', gen: true, type: 'ahb_lite',
                     targets: [], initiators: [] });
        break;
      default: return;
    }
    markDirty();
    render();
  }

  // ---------- load ----------
  async function load() {
    if (!App.currentDraft) { dict = null; draftOf = null; render(); return; }
    render();   // shows "loading…" when dict is stale
    try {
      if (modules === null) {
        try { modules = (await App.api('/api/catalog')).modules || []; }
        catch (e) { modules = []; }
      }
      if (hints === null) {
        try { hints = (await App.api('/api/draft/' + App.currentDraft + '/hints')).hints || {}; }
        catch (e) { hints = {}; }
      }
      dict = await App.api('/api/draft/' + App.currentDraft + '/dict');
      if (!dict || typeof dict.module !== 'object' || dict.module === null)
        dict = { module: (dict && dict.module) || {} };
      draftOf = App.currentDraft;
      dirty = false;
      expanded.clear();
      lastVal = await App.api('/api/draft/' + App.currentDraft + '/validate',
                              { json: {} });
    } catch (e) {
      dict = null; draftOf = null;
      const r = root();
      if (r) { r.innerHTML = `<p class="cmp-hint">failed to load dict: ${esc(e.message)}</p>`; }
      return;
    }
    render();
  }

  return {
    init() {
      const css = document.createElement('style');
      css.textContent = `
        #compose-root { padding: 8px; }
        .cmp-hint { color: var(--dim); padding: 12px; }
        /* --- sticky validation/header mini-bar --- */
        .cmp-head { position: sticky; top: 0; z-index: 6;
                    display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
                    margin: -8px -8px 12px; padding: 8px 12px;
                    background: var(--panel); border-bottom: 1px solid var(--line);
                    box-shadow: 0 2px 8px rgba(0,0,0,.18); }
        .cmp-crumb { font-weight: 600; }
        #compose-root .cmp-crumb { border: none; background: transparent;
          color: var(--accent, #4da3ff); padding: 2px 4px; }
        #compose-root .cmp-crumb:hover { text-decoration: underline; border: none; }
        .cmp-bc { font-weight: 600; color: var(--fg); }
        .cmp-bc-mod { font-weight: normal; color: var(--dim); }
        .cmp-chip { border: 1px solid var(--line); border-radius: 999px;
                    padding: 2px 10px; font-size: 12px; color: var(--dim); }
        .cmp-chip.ok   { color: var(--ok);   border-color: var(--ok);
                         background: rgba(80,200,120,.08); }
        .cmp-chip.warn { color: var(--warn); border-color: var(--warn);
                         background: rgba(230,180,60,.08); }
        .cmp-chip.bad  { color: var(--err);  border-color: var(--err);
                         background: rgba(229,83,75,.08); }
        #compose-root .cmp-msgbtn { border: none; background: transparent;
          color: var(--accent, #4da3ff); font-size: 12px; padding: 2px 4px; }
        #compose-root .cmp-msgbtn:hover { text-decoration: underline; border: none; }
        .cmp-msgs { flex-basis: 100%; max-height: 32vh; overflow: auto;
                    border-top: 1px dashed var(--line); padding-top: 6px;
                    font-family: monospace; font-size: 12px; }
        .cmp-msg { padding: 1px 0; color: var(--fg); }
        .cmp-msg.error b   { color: var(--err); }
        .cmp-msg.warning b { color: var(--warn); }
        .cmp-msg-where { color: var(--accent, #4da3ff); }
        /* --- section cards --- */
        .cmp-sec { border: 1px solid var(--line);
                   border-left: 3px solid transparent; border-radius: 8px;
                   margin: 10px 0; background: var(--panel);
                   box-shadow: 0 1px 3px rgba(0,0,0,.12); }
        .cmp-sec.cmp-hit-warn { border-left-color: var(--accent, #4da3ff); }
        .cmp-sec.cmp-hit-err  { border-left-color: var(--err); }
        .cmp-sec > summary { cursor: pointer; padding: 8px 12px; color: var(--fg);
                             font-weight: 600; display: flex; align-items: center;
                             gap: 10px; user-select: none; border-radius: 8px; }
        .cmp-sec > summary:hover { color: var(--accent, #4da3ff); }
        .cmp-sec[open] > summary { border-bottom: 1px solid var(--line);
                                   border-radius: 8px 8px 0 0; }
        .cmp-count { font-weight: normal; font-size: 11px;
                     color: var(--accent, #4da3ff);
                     border: 1px solid var(--accent, #4da3ff);
                     border-radius: 999px; padding: 0 8px; opacity: .85; }
        .cmp-body { padding: 8px 12px 12px; }
        .cmp-body h4 { margin: 10px 0 4px; color: var(--dim);
                       text-transform: uppercase; font-size: 11px;
                       letter-spacing: .06em; }
        /* --- tables: zebra, sticky header, comfy rows --- */
        .cmp-tbl { border-collapse: collapse; margin-bottom: 6px; width: 100%; }
        .cmp-tbl th { text-align: left; color: var(--dim); font-weight: 600;
                      font-size: 11px; text-transform: uppercase;
                      letter-spacing: .05em; padding: 6px;
                      position: sticky; top: 46px; z-index: 2;
                      background: var(--panel);
                      border-bottom: 1px solid var(--line); }
        .cmp-tbl td { padding: 5px 6px; vertical-align: middle; }
        .cmp-tbl tr:nth-child(even):not(.cmp-sub) td {
          background: rgba(127,127,127,.07); }
        .cmp-tbl tr[data-names].c-err  > td { outline: 1px solid var(--err); }
        .cmp-tbl tr[data-names].c-warn > td { outline: 1px solid var(--warn); }
        .cmp-tbl input, .cmp-tbl select, .cmp-kv input, .cmp-kv textarea {
          background: var(--bg); color: var(--fg); border: 1px solid var(--line);
          border-radius: 4px; padding: 4px 6px; font: inherit; }
        .cmp-tbl input:focus, .cmp-tbl select:focus,
        .cmp-kv input:focus, .cmp-kv textarea:focus {
          outline: none; border-color: var(--accent, #4da3ff);
          box-shadow: 0 0 0 2px rgba(77,163,255,.22); }
        .cmp-tbl input.bad { border-color: var(--err);
          box-shadow: 0 0 0 2px rgba(229,83,75,.25); }
        .cmp-tbl input.num { width: 9em; font-family: monospace; }
        .cmp-conns input { margin: 2px 2px; } .cmp-conns input.wide { width: 26em; }
        .cmp-sub > td { background: var(--bg); }
        .cmp-kv { display: flex; gap: 8px; margin: 6px 0; }
        .cmp-kv label { width: 5em; color: var(--dim); padding-top: 4px; }
        .cmp-kv input, .cmp-kv textarea { flex: 1; max-width: 46em; }
        .cmp-reachlist { max-width: 44em; }
        .cmp-reach { display: inline-flex; gap: 3px; align-items: center;
                     margin: 0 8px 2px 0; font-size: 12px; white-space: nowrap; }
        /* --- buttons --- */
        #compose-root button { background: var(--panel); color: var(--fg);
          border: 1px solid var(--line); border-radius: 4px;
          padding: 2px 10px; cursor: pointer; }
        #compose-root button:hover { border-color: var(--accent, #4da3ff); }
        #compose-root button[data-act="save"] {
          color: var(--accent, #4da3ff); border-color: var(--accent, #4da3ff);
          font-weight: 600; }
        #compose-root button[data-act="save"]:hover {
          background: rgba(77,163,255,.12); }
        #compose-root button[data-act^="add-"] { display: block; width: 100%;
          text-align: center; border: 1px dashed var(--line); background: transparent;
          color: var(--dim); padding: 6px 8px; border-radius: 6px; margin: 2px 0 4px; }
        #compose-root button[data-act^="add-"]:hover {
          border-color: var(--accent, #4da3ff); color: var(--accent, #4da3ff);
          background: rgba(77,163,255,.06); }
        #compose-root button[data-act^="del-"] { border: none;
          background: transparent; color: var(--dim); padding: 1px 6px; }
        #compose-root button[data-act^="del-"]:hover { color: var(--err); }
        #compose-root button[data-act="toggle-inst"] { border: none;
          background: transparent; color: var(--accent, #4da3ff); padding: 1px 6px; }`;
      document.head.appendChild(css);
      const r = root();
      if (r) {
        r.addEventListener('input', onEdit);
        r.addEventListener('change', onEdit);
        r.addEventListener('click', onClick);
      }
      render();
    },
    onDraft() { load(); },
    onShow() {
      if (App.currentDraft !== draftOf) load();
      else if (!dict) render();
    },
  };
})());
