// Copyright 2026, SoC Labs (www.soclabs.org)
// Built SoC panel: surfaces the GENERATED SoC from the model-view JSON
// artifact (<build_dir>/reports/*_model_view.json, emitted by `make soc`
// alongside the connectivity HTML). Shows build meta + a compact summary
// rendered from /api/generated, and opens the full D3 connectivity viewer
// (/generated — the same report page, fed live by the JSON) in a new tab.
// Renders into #generated-root.
'use strict';

App.register('generated', (() => {
  const root = () => document.getElementById('generated-root');

  const CSS = `
  #generated-root .gn-bar { display: flex; gap: 10px; align-items: center;
    margin-bottom: 14px; flex-wrap: wrap; }
  #generated-root .gn-open { font-weight: bold; padding: 6px 16px; }
  #generated-root .gn-meta { color: var(--dim); font-family: monospace;
    font-size: 11px; }
  #generated-root .gn-note { color: var(--dim); padding: 16px 4px; }
  #generated-root .gn-err { color: var(--err); font-family: monospace;
    white-space: pre-wrap; }
  #generated-root h3.gn-head { margin: 14px 0 6px; font-size: 12px;
    color: var(--info); font-family: monospace; }
  #generated-root .gn-cards { display: flex; gap: 10px; flex-wrap: wrap; }
  #generated-root .gn-card { border: 1px solid var(--line); border-radius: 4px;
    padding: 8px 14px; min-width: 96px; background: #0d1014; }
  #generated-root .gn-card .num { font: bold 20px/1.3 monospace;
    color: var(--fg); }
  #generated-root .gn-card .lbl { color: var(--dim); font-size: 11px; }
  #generated-root ul.gn-tree { list-style: none; margin: 4px 0; padding: 0;
    font: 12px/1.6 monospace; max-width: 720px; }
  #generated-root ul.gn-tree li { border-bottom: 1px solid var(--line);
    padding: 1px 0; }
  #generated-root ul.gn-tree .mod { color: var(--dim); }
  #generated-root ul.gn-tree .kids { color: var(--info); font-size: 11px; }
  #generated-root .gn-desc { color: var(--dim); font-size: 12px;
    max-width: 720px; margin: 4px 0 0; white-space: pre-line; }
  `;

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function age(mtime) {
    if (!mtime) return '';
    let s = Math.max(0, Date.now() / 1000 - mtime);
    if (s < 90) return Math.round(s) + 's ago';
    if (s < 5400) return Math.round(s / 60) + 'min ago';
    if (s < 129600) return (s / 3600).toFixed(1) + 'h ago';
    return (s / 86400).toFixed(1) + 'd ago';
  }

  function card(num, lbl) {
    return `<div class="gn-card"><div class="num">${esc(num)}</div>
            <div class="lbl">${esc(lbl)}</div></div>`;
  }

  function render(d) {
    const el = root();
    const meta = d.meta || {};
    const graph = d.graph || { nodes: [], links: [] };
    const cards =
      card((graph.nodes || []).length, 'diagram nodes') +
      card((graph.links || []).length, 'diagram links') +
      card((d.interconnect_info || []).length, 'interconnects') +
      card((d.address_map || []).length, 'address windows') +
      card(Object.keys(d.all_graphs || {}).length, 'drillable modules') +
      card((d.assigns || []).length, 'assigns') +
      card((d.validation || []).length, 'validation msgs');

    const tree = (d.hierarchy || []).map(n =>
      `<li><strong>${esc(n.name)}</strong>
           <span class="mod">: ${esc(n.module)}</span>
           ${n.children && n.children.length
             ? `<span class="kids">[+${n.children.length} children]</span>` : ''}
           ${n.addressable ? '<span class="kids">[addr]</span>' : ''}</li>`)
      .join('');

    el.innerHTML = `
      <div class="gn-bar">
        <button class="gn-open" id="gn-open">Open full viewer ↗</button>
        <button id="gn-refresh">Refresh</button>
        <span class="gn-meta">${esc(meta.name || '?')} —
          ${esc(meta.source_file || '')}
          ${meta.source_mtime ? ' (built ' + esc(age(meta.source_mtime)) + ')' : ''}</span>
      </div>
      ${meta.desc ? `<div class="gn-desc">${esc(meta.desc)}</div>` : ''}
      <h3 class="gn-head">model summary</h3>
      <div class="gn-cards">${cards}</div>
      <h3 class="gn-head">top-level hierarchy</h3>
      <ul class="gn-tree">${tree || '<li>(no instances)</li>'}</ul>`;

    el.querySelector('#gn-open')
      .addEventListener('click', () => window.open('/generated'));
    el.querySelector('#gn-refresh').addEventListener('click', refresh);
  }

  async function refresh() {
    const el = root();
    if (!el) return;
    el.innerHTML = '<div class="gn-note">loading…</div>';
    let d;
    try {
      d = await App.api('/api/generated');
    } catch (e) {
      if (String(e.message).startsWith('404')) {
        el.innerHTML = `<div class="gn-note">no built SoC yet — run the
          <em>Build</em> panel (or <code>make soc</code>) to generate the
          model-view JSON, then refresh.</div>
          <div class="gn-bar"><button id="gn-refresh">Refresh</button></div>`;
        el.querySelector('#gn-refresh').addEventListener('click', refresh);
      } else {
        el.innerHTML = `<div class="gn-err">${esc(e.message)}</div>`;
      }
      return;
    }
    render(d);
  }

  return {
    init() {
      const st = document.createElement('style');
      st.textContent = CSS;
      document.head.appendChild(st);
    },
    onShow() { refresh(); },
  };
})());
