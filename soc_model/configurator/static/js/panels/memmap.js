// Copyright 2026, SoC Labs (www.soclabs.org)
// Memory-map panel (doc 06a): per-interconnect address rulers, window tables
// and the auto-pack (preview/apply) form. Renders into #memmap-root.
'use strict';

App.register('memmap', (() => {
  const root = () => document.getElementById('memmap-root');
  let lastDraft = null;

  const CSS = `
  #memmap-root .mm-ic { margin-bottom: 26px; }
  #memmap-root h2.mm-name { margin: 4px 0 2px; font-size: 14px; }
  #memmap-root .mm-span { color: var(--dim); font-family: monospace;
    font-size: 11px; margin-bottom: 6px; }
  #memmap-root .mm-ruler { display: flex; height: 52px; border: 1px solid
    var(--line); border-radius: 4px; overflow: hidden; background: #11151a; }
  #memmap-root .mm-seg { position: relative; min-width: 30px; height: 100%;
    border-right: 1px solid var(--bg); cursor: pointer; overflow: hidden;
    padding: 3px 4px 0 4px; }
  #memmap-root .mm-seg:last-child { border-right: 0; }
  #memmap-root .mm-seg.memory { background: #1d3a2a; }
  #memmap-root .mm-seg.periph { background: #1f3046; }
  #memmap-root .mm-seg.other  { background: #3a3424; }
  #memmap-root .mm-seg.overlap { background: #4a1f24;
    outline: 1px solid var(--err); outline-offset: -1px; }
  #memmap-root .mm-seg:hover { filter: brightness(1.35); }
  #memmap-root .mm-seg .lbl { display: block; font-size: 11px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #memmap-root .mm-seg .tick { position: absolute; left: 2px; bottom: 1px;
    font: 9px/1 monospace; color: var(--dim); white-space: nowrap; }
  #memmap-root .mm-gap { min-width: 0; background:
    repeating-linear-gradient(45deg, #14181d 0 6px, #181d23 6px 12px); }
  #memmap-root .mm-end { text-align: right; font: 10px/1.4 monospace;
    color: var(--dim); }
  #memmap-root .mm-legend { display: flex; gap: 14px; align-items: center;
    margin: 4px 0 8px; font-size: 11px; color: var(--dim); flex-wrap: wrap; }
  #memmap-root .mm-legend .sw { display: inline-block; width: 11px;
    height: 11px; border-radius: 2px; margin-right: 4px;
    vertical-align: -1px; }
  #memmap-root .mm-legend .err-note { color: var(--err); }
  #memmap-root table.mm-tbl { border-collapse: collapse; width: 100%;
    font: 12px/1.4 monospace; margin-bottom: 8px; }
  #memmap-root table.mm-tbl th, #memmap-root table.mm-tbl td {
    border: 1px solid var(--line); padding: 3px 8px; text-align: left; }
  #memmap-root table.mm-tbl th { color: var(--dim); font-weight: 400;
    text-transform: uppercase; font-size: 10px; letter-spacing: .5px; }
  #memmap-root table.mm-tbl tr.overlap td { color: var(--err); }
  #memmap-root table.mm-tbl tr.hilite td { background: #243140; }
  #memmap-root .mm-pack { border: 1px solid var(--line); border-radius: 4px;
    padding: 8px 10px; background: var(--panel); }
  #memmap-root .mm-pack h4 { margin: 0 0 6px; font-size: 11px;
    text-transform: uppercase; letter-spacing: .6px; color: var(--dim); }
  #memmap-root .mm-pack input[type=text] { width: 110px;
    background: #11151a; color: var(--fg); border: 1px solid var(--line);
    border-radius: 4px; padding: 4px 6px; font: 12px monospace; }
  #memmap-root .mm-pack label { color: var(--dim); font-size: 12px; }
  #memmap-root .mm-pack .pack-out { margin-top: 8px; }
  #memmap-root .pack-val { font-size: 12px; margin: 6px 0; }
  #memmap-root .pack-val.ok { color: var(--ok); }
  #memmap-root .pack-val.bad { color: var(--err); }
  #memmap-root .pack-note { color: var(--warn); font-size: 12px;
    margin: 6px 0; }
  #memmap-root .pack-err { color: var(--err); font-size: 12px;
    margin: 6px 0; font-family: monospace; white-space: pre-wrap; }
  #memmap-root td.chg { color: var(--warn); }
  #memmap-root .mm-note { color: var(--dim); padding: 16px 4px; }
  `;

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  const hex = (n) => '0x' + Number(n).toString(16);
  function humanSize(n) {
    if (n >= 1 << 30) return (n / (1 << 30)) + 'G';
    if (n >= 1 << 20) return (n / (1 << 20)) + 'M';
    if (n >= 1 << 10) return (n / (1 << 10)) + 'K';
    return n + 'B';
  }

  function segClass(t) {
    if (t.overlaps && t.overlaps.length) return 'overlap';
    if (t.region_type === 'memory') return 'memory';
    if (t.region_type === 'periph') return 'periph';
    return 'other';
  }

  // One ruler: a flex row, each target a segment with flex-grow ~ size and a
  // min-width so 16M windows in a 2.8G span stay visible/clickable; address
  // gaps get hatched filler segments that may collapse to zero.
  function rulerHtml(ic, idx) {
    const span = ic.span || { lo: 0, hi: 0 };
    const total = Math.max(1, span.hi - span.lo + 1);
    let cursor = span.lo;
    const segs = [];
    ic.targets.forEach((t, ti) => {
      if (t.base > cursor) {
        const g = t.base - cursor;
        segs.push(`<div class="mm-gap" style="flex:${(g / total).toFixed(6)} 1 0"
          title="(unmapped) ${hex(cursor)}..${hex(t.base - 1)}"></div>`);
      }
      const tip = `${t.name}\n${t.base_hex}..${t.end_hex}  (${humanSize(t.size)})` +
        `\naccess: ${t.sw_access || '-'}` +
        (t.overlaps && t.overlaps.length
          ? `\nOVERLAPS: ${t.overlaps.join(', ')}` : '');
      segs.push(
        `<div class="mm-seg ${segClass(t)}" data-row="mm-r-${idx}-${ti}"
           style="flex:${(t.size / total).toFixed(6)} 1 0" title="${esc(tip)}">
           <span class="lbl">${esc(t.name)}</span>
           <span class="tick">${esc(t.base_hex)}</span>
         </div>`);
      cursor = t.end + 1;
    });
    if (cursor <= span.hi) {
      segs.push(`<div class="mm-gap" style="flex:${((span.hi - cursor + 1) / total).toFixed(6)} 1 0"
        title="(unmapped) ${hex(cursor)}..${hex(span.hi)}"></div>`);
    }
    return `<div class="mm-ruler">${segs.join('')}</div>
            <div class="mm-end">${hex(span.hi + 1)}</div>`;
  }

  function legendHtml(ic) {
    const nOv = ic.targets.filter(t => t.overlaps && t.overlaps.length).length;
    return `<div class="mm-legend">
      <span><span class="sw" style="background:#1d3a2a"></span>memory</span>
      <span><span class="sw" style="background:#1f3046"></span>periph</span>
      <span><span class="sw" style="background:#3a3424"></span>other</span>
      <span><span class="sw" style="background:#4a1f24;outline:1px solid var(--err)"></span>overlap</span>
      ${nOv ? `<span class="err-note">⚠ ${nOv} window(s) overlap — fix bases or auto-pack</span>` : ''}
    </div>`;
  }

  function tableHtml(ic, idx) {
    const rows = ic.targets.map((t, ti) => {
      const ov = t.overlaps && t.overlaps.length;
      return `<tr id="mm-r-${idx}-${ti}" class="${ov ? 'overlap' : ''}">
        <td>${esc(t.name)}</td><td>${esc(t.base_hex)}</td>
        <td>${esc(t.end_hex)}</td><td>${esc(t.size_hex)}</td>
        <td>${esc(t.region_type || '-')}</td><td>${esc(t.sw_access || '-')}</td>
        <td>${ov ? esc(t.overlaps.join(', ')) : '—'}</td></tr>`;
    }).join('');
    return `<table class="mm-tbl">
      <thead><tr><th>name</th><th>base</th><th>end</th><th>size</th>
      <th>type</th><th>sw access</th><th>overlaps</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  }

  function packFormHtml(ic, idx) {
    return `<div class="mm-pack" data-ic="${esc(ic.name)}">
      <h4>Auto-pack — ${esc(ic.name)}</h4>
      <div class="row">
        <label>from <input type="text" class="pack-from" value="0x20000000"></label>
        <label>align <input type="text" class="pack-align" value="16M"></label>
        <label><input type="checkbox" class="pack-repack"> repack all</label>
        <button class="pack-preview">Preview</button>
        <button class="pack-apply">Apply</button>
      </div>
      <div class="pack-out"></div>
    </div>`;
  }

  function packResultHtml(res) {
    const v = res.validation || {};
    const changed = res.table.filter(r => r.before !== r.after).length;
    const rows = res.table.map(r =>
      `<tr><td>${esc(r.name)}</td><td>${hex(r.before)}</td>
       <td class="${r.before !== r.after ? 'chg' : ''}">${esc(r.after_hex)}</td></tr>`
    ).join('');
    return `
      <div class="pack-val ${v.errors ? 'bad' : 'ok'}">
        ${res.applied ? 'applied' : 'preview'}: ${changed} window(s) move ·
        validation: ${v.errors || 0} error(s), ${v.warnings || 0} warning(s)
        ${v.ok ? '✓' : ''}
      </div>
      <table class="mm-tbl"><thead><tr><th>name</th><th>before</th>
        <th>after</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  async function doPack(box, apply) {
    const out = box.querySelector('.pack-out');
    out.innerHTML = '<span class="dim">packing…</span>';
    try {
      const res = await App.api(
        `/api/draft/${App.currentDraft}/pack`,
        { json: {
            interconnect: box.dataset.ic,
            from: box.querySelector('.pack-from').value.trim(),
            align: box.querySelector('.pack-align').value.trim(),
            repack_all: box.querySelector('.pack-repack').checked,
            apply: !!apply,
          } });
      out.innerHTML =
        (res.applied
          ? '<div class="pack-note">draft rewritten — reopen in the YAML ' +
            'panel to see the new bases</div>'
          : '') + packResultHtml(res);
      if (res.applied) refresh();   // re-fetch rulers; .pack-out is rebuilt
    } catch (e) {
      out.innerHTML = `<div class="pack-err">${esc(e.message)}</div>`;
    }
  }

  async function refresh() {
    const el = root();
    if (!el) return;
    if (!App.currentDraft) {
      el.innerHTML = '<div class="mm-note">No draft open — open one in the ' +
        'YAML panel first.</div>';
      return;
    }
    el.innerHTML = '<div class="mm-note">loading…</div>';
    let d;
    try {
      d = await App.api(`/api/draft/${App.currentDraft}/memmap`);
    } catch (e) {
      el.innerHTML = `<div class="pack-err">${esc(e.message)}</div>`;
      return;
    }
    if (!d.ok || !d.interconnects || !d.interconnects.length) {
      el.innerHTML = '<div class="mm-note">no interconnect address map in ' +
        'this draft.</div>';
      return;
    }
    el.innerHTML = d.interconnects.map((ic, idx) => `
      <div class="mm-ic">
        <h2 class="mm-name">${esc(ic.name)}</h2>
        <div class="mm-span">span ${hex(ic.span.lo)} .. ${hex(ic.span.hi)}
          · ${ic.targets.length} window(s)</div>
        ${rulerHtml(ic, idx)}
        ${legendHtml(ic)}
        ${tableHtml(ic, idx)}
        ${packFormHtml(ic, idx)}
      </div>`).join('');

    // segment click -> highlight matching table row
    el.querySelectorAll('.mm-seg[data-row]').forEach(seg =>
      seg.addEventListener('click', () => {
        el.querySelectorAll('tr.hilite')
          .forEach(r => r.classList.remove('hilite'));
        const row = document.getElementById(seg.dataset.row);
        if (row) {
          row.classList.add('hilite');
          row.scrollIntoView({ block: 'nearest' });
        }
      }));
    // pack buttons
    el.querySelectorAll('.mm-pack').forEach(box => {
      box.querySelector('.pack-preview')
        .addEventListener('click', () => doPack(box, false));
      box.querySelector('.pack-apply')
        .addEventListener('click', () => doPack(box, true));
    });
  }

  return {
    init() {
      const st = document.createElement('style');
      st.textContent = CSS;
      document.head.appendChild(st);
      refresh();
    },
    onDraft(id) { lastDraft = id; refresh(); },
    onShow() {
      if (App.currentDraft !== lastDraft) { lastDraft = App.currentDraft; }
      refresh();
    },
  };
})());
