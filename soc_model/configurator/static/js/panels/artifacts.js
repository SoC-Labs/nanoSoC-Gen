// Copyright 2026, SoC Labs (www.soclabs.org)
// Artifacts panel (doc 06a): build_soc browser grouped by top-level dir,
// inline text viewer, html opens in a new tab. Renders into #artifacts-root.
'use strict';

App.register('artifacts', (() => {
  const root = () => document.getElementById('artifacts-root');

  const CSS = `
  #artifacts-root .af-bar { display: flex; gap: 10px; align-items: baseline;
    margin-bottom: 10px; }
  #artifacts-root .af-dir { color: var(--dim); font-family: monospace;
    font-size: 11px; }
  #artifacts-root .af-zip { cursor: pointer; border: 1px solid
    var(--accent, #4da3ff); color: var(--accent, #4da3ff); background:
    rgba(77,163,255,.08); border-radius: 6px; padding: 2px 10px;
    font-size: 12px; text-decoration: none; }
  #artifacts-root .af-zip:hover { background: rgba(77,163,255,.2); }
  #artifacts-root h3.af-group { margin: 12px 0 4px; font-size: 12px;
    text-transform: none; letter-spacing: .4px; color: var(--info);
    font-family: monospace; }
  #artifacts-root table.af-tbl { border-collapse: collapse; width: 100%;
    max-width: 860px; font: 12px/1.5 monospace; }
  #artifacts-root table.af-tbl td { padding: 2px 10px 2px 0;
    border-bottom: 1px solid var(--line); }
  #artifacts-root td.af-size { color: var(--dim); text-align: right;
    white-space: nowrap; width: 1%; }
  #artifacts-root a.af-file { color: var(--fg); text-decoration: none;
    cursor: pointer; }
  #artifacts-root a.af-file:hover { color: var(--info);
    text-decoration: underline; }
  #artifacts-root a.af-file.html::after { content: ' ↗'; color: var(--dim); }
  #artifacts-root .af-bin { color: var(--dim); }
  #artifacts-root .af-note { color: var(--dim); padding: 16px 4px; }
  #artifacts-root .af-err { color: var(--err); font-family: monospace;
    white-space: pre-wrap; }
  #artifacts-root .af-viewer { margin-top: 14px; border: 1px solid
    var(--line); border-radius: 4px; background: #0d1014; }
  #artifacts-root .af-viewer .vhead { display: flex; gap: 10px;
    align-items: center; padding: 6px 10px; border-bottom: 1px solid
    var(--line); font-family: monospace; font-size: 12px; }
  #artifacts-root .af-viewer .vhead .vpath { color: var(--info);
    flex: 1; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; }
  #artifacts-root .af-viewer pre { margin: 0; padding: 10px;
    max-height: 55vh; overflow: auto; font: 12px/1.5 monospace;
    white-space: pre; color: var(--fg); }
  `;

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function humanSize(n) {
    if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MiB';
    if (n >= 1024) return (n / 1024).toFixed(1) + ' KiB';
    return n + ' B';
  }

  function fileUrl(path) {
    return '/api/artifacts/file?path=' + encodeURIComponent(path);
  }

  function rowHtml(f) {
    const isHtml = f.path.toLowerCase().endsWith('.html');
    const name = isHtml || f.text
      ? `<a class="af-file ${isHtml ? 'html' : ''}" data-path="${esc(f.path)}"
           data-html="${isHtml ? 1 : 0}">${esc(f.path)}</a>`
      : `<span class="af-bin" title="binary — not viewable">${esc(f.path)}</span>`;
    return `<tr><td>${name}</td>
            <td class="af-size">${humanSize(f.size)}</td></tr>`;
  }

  async function viewFile(path) {
    const el = root();
    let viewer = el.querySelector('.af-viewer');
    if (!viewer) {
      viewer = document.createElement('div');
      viewer.className = 'af-viewer';
      el.appendChild(viewer);
    }
    viewer.innerHTML = `<div class="vhead">
        <span class="vpath">${esc(path)}</span>
        <button class="af-close">Close</button></div>
      <pre>loading…</pre>`;
    viewer.querySelector('.af-close')
      .addEventListener('click', () => viewer.remove());
    try {
      const r = await fetch(fileUrl(path));
      if (!r.ok) throw new Error(r.status + ' ' + r.statusText);
      viewer.querySelector('pre').textContent = await r.text();
    } catch (e) {
      viewer.querySelector('pre').textContent = 'ERROR: ' + e.message;
    }
    viewer.scrollIntoView({ block: 'nearest' });
  }

  async function refresh() {
    const el = root();
    if (!el) return;
    el.innerHTML = '<div class="af-note">loading…</div>';
    let d;
    try {
      d = await App.api('/api/artifacts');
    } catch (e) {
      el.innerHTML = `<div class="af-err">${esc(e.message)}</div>`;
      return;
    }
    const bar = `<div class="af-bar">
      <button id="af-refresh">Refresh</button>
      ${(d.files || []).length ? '<a class="af-zip" href="/api/artifacts/zip"'
        + ' title="download all artifacts as a zip">⬇ Download .zip</a>' : ''}
      <span class="af-dir">${esc(d.build_dir || '')}</span></div>`;
    if (!d.exists) {
      el.innerHTML = bar +
        '<div class="af-note">no build_soc yet — run Build.</div>';
      el.querySelector('#af-refresh').addEventListener('click', refresh);
      return;
    }
    // group by top-level directory ('(top)' for root-level files)
    const groups = {};
    (d.files || []).forEach(f => {
      const g = f.path.includes('/') ? f.path.split('/')[0] + '/' : '(top)';
      (groups[g] = groups[g] || []).push(f);
    });
    el.innerHTML = bar + Object.keys(groups).sort().map(g =>
      `<h3 class="af-group">${esc(g)}</h3>
       <table class="af-tbl"><tbody>
         ${groups[g].map(rowHtml).join('')}
       </tbody></table>`).join('');

    el.querySelector('#af-refresh').addEventListener('click', refresh);
    el.querySelectorAll('a.af-file').forEach(a =>
      a.addEventListener('click', () => {
        if (a.dataset.html === '1') window.open(fileUrl(a.dataset.path));
        else viewFile(a.dataset.path);
      }));
  }

  return {
    init() {
      const st = document.createElement('style');
      st.textContent = CSS;
      document.head.appendChild(st);
      refresh();
    },
    onShow() { refresh(); },
  };
})());
