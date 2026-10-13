// Copyright 2026, SoC Labs (www.soclabs.org)
// Compare panel (doc 06a #14) — pick two drafts, see validation-count deltas
// and a colour-coded unified YAML diff. Pure composition over GET /api/drafts +
// GET /api/draft/{a}/compare?other={b}.
'use strict';

App.register('compare', (() => {
  const root = () => document.getElementById('compare-root');
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

  let drafts = [];
  let a = null, b = null;

  function opts(sel) {
    return drafts.map(d =>
      `<option value="${esc(d)}"${d === sel ? ' selected' : ''}>${esc(d)}</option>`
    ).join('');
  }

  function vbadge(v, label) {
    if (!v) return '';
    const cls = v.errors ? 'bad' : (v.warnings ? 'warn' : 'ok');
    return `<span class="cmp-v"><b>${esc(label)}</b>
      <span class="cmp-pill ${cls}">${v.errors} err</span>
      <span class="cmp-pill ${v.warnings ? 'warn' : ''}">${v.warnings} warn</span>
      <span class="cmp-pill">${v.checks}/${v.total} checks</span>
      ${v.ok ? '<span class="cmp-pill ok">buildable ✓</span>' : ''}</span>`;
  }

  function diffHtml(diff) {
    if (!diff) return '<div class="cmp-same">No differences — the two drafts are identical.</div>';
    return '<pre class="cmp-diff">' + diff.split('\n').map(line => {
      let c = '';
      if (line.startsWith('+') && !line.startsWith('+++')) c = 'add';
      else if (line.startsWith('-') && !line.startsWith('---')) c = 'del';
      else if (line.startsWith('@@')) c = 'hunk';
      else if (line.startsWith('+++') || line.startsWith('---')) c = 'file';
      return `<span class="${c}">${esc(line)}</span>`;
    }).join('\n') + '</pre>';
  }

  async function runCompare() {
    const el = root();
    const body = el.querySelector('#cmp-body');
    if (!body) return;
    if (!a || !b) { body.innerHTML = '<div class="cmp-hint">pick two drafts</div>'; return; }
    if (a === b) { body.innerHTML = '<div class="cmp-hint">pick two <em>different</em> drafts</div>'; return; }
    body.innerHTML = '<div class="cmp-hint">comparing…</div>';
    try {
      const r = await App.api(`/api/draft/${encodeURIComponent(a)}/compare?other=${encodeURIComponent(b)}`);
      body.innerHTML =
        `<div class="cmp-vrow">${vbadge(r.a_validation, r.a)}${vbadge(r.b_validation, r.b)}</div>`
        + diffHtml(r.diff);
    } catch (e) {
      body.innerHTML = `<div class="cmp-hint">compare failed: ${esc(e.message)}</div>`;
    }
  }

  async function render() {
    const el = root();
    if (!el) return;
    try { drafts = (await App.api('/api/drafts')).drafts || []; }
    catch (e) { drafts = []; }
    if (drafts.length < 2) {
      el.innerHTML = '<div class="cmp-hint">Need at least two drafts to compare. '
        + 'Create or snapshot a draft first.</div>';
      return;
    }
    if (!a || drafts.indexOf(a) < 0) a = App.currentDraft && drafts.indexOf(App.currentDraft) >= 0
      ? App.currentDraft : drafts[0];
    if (!b || drafts.indexOf(b) < 0) b = drafts.find(d => d !== a) || drafts[0];
    el.innerHTML = `
      <div class="cmp-head"><h2>Compare drafts</h2></div>
      <div class="cmp-pickers">
        <label>A <select id="cmp-a">${opts(a)}</select></label>
        <span class="cmp-vs">vs</span>
        <label>B <select id="cmp-b">${opts(b)}</select></label>
      </div>
      <div id="cmp-body"></div>`;
    el.querySelector('#cmp-a').addEventListener('change', e => { a = e.target.value; runCompare(); });
    el.querySelector('#cmp-b').addEventListener('change', e => { b = e.target.value; runCompare(); });
    runCompare();
  }

  const CSS = `
    #compare-root { padding: 14px; }
    #compare-root .cmp-head h2 { margin: 0 0 10px; font-size: 18px;
      background: var(--accent-grad, linear-gradient(90deg,#4da3ff,#3ddbb4));
      -webkit-background-clip: text; background-clip: text;
      -webkit-text-fill-color: transparent; }
    #compare-root .cmp-pickers { display: flex; align-items: center; gap: 12px;
      margin-bottom: 12px; }
    #compare-root .cmp-pickers select { background: var(--bg); color: var(--fg);
      border: 1px solid var(--line); border-radius: 6px; padding: 3px 8px;
      font: 12px monospace; }
    #compare-root .cmp-vs { color: var(--dim); }
    #compare-root .cmp-hint, #compare-root .cmp-same { color: var(--dim);
      font-size: 13px; padding: 12px 2px; }
    #compare-root .cmp-vrow { display: flex; flex-wrap: wrap; gap: 20px;
      margin-bottom: 10px; }
    #compare-root .cmp-v { font-size: 12px; display: inline-flex; gap: 6px;
      align-items: center; flex-wrap: wrap; }
    #compare-root .cmp-pill { font-size: 11px; border: 1px solid var(--line);
      border-radius: 999px; padding: 1px 9px; color: var(--dim); }
    #compare-root .cmp-pill.ok { color: var(--ok); border-color: var(--ok); }
    #compare-root .cmp-pill.warn { color: var(--warn); border-color: var(--warn); }
    #compare-root .cmp-pill.bad { color: var(--err); border-color: var(--err); }
    #compare-root .cmp-diff { font: 11.5px/1.45 monospace; background: #0d1014;
      border: 1px solid var(--line); border-radius: 8px; padding: 10px;
      max-height: 64vh; overflow: auto; white-space: pre; }
    #compare-root .cmp-diff .add { color: var(--ok); }
    #compare-root .cmp-diff .del { color: var(--err); }
    #compare-root .cmp-diff .hunk { color: var(--accent2, #3ddbb4); }
    #compare-root .cmp-diff .file { color: var(--dim); }`;

  let inited = false;
  function boot() {
    if (inited) return;
    inited = true;
    const st = document.createElement('style');
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  return {
    init() { boot(); },
    onDraft() { /* keep current A/B; refresh list lazily on show */ },
    onShow() { boot(); render(); },
  };
})());
