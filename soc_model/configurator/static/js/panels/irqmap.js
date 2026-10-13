// Copyright 2026, SoC Labs (www.soclabs.org)
// IRQ Map panel (doc 06a #1) — renders every glue_logic `concat` block as a
// bit -> source-signal matrix and shows which instance/port consumes its
// output, so you can see at a glance whether an IRQ actually reaches a CPU's
// NVIC (the cc_periph incident: IRQs wired to the block boundary but never
// concatenated onto the NVIC). Read from GET /irqmap; phase 2 makes each bit's
// source signal editable in place — a change PUTs /irq and re-renders from the
// returned irqmap (blank signal clears the bit -> 1'b0).
'use strict';

App.register('irqmap', (() => {
  const root = () => document.getElementById('irqmap-root');
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

  let busy = false;

  // every distinct source signal seen anywhere in the matrix -> datalist of
  // candidates so editing a bit can pick a known signal (still free-text).
  function candidates(concats) {
    const set = new Set();
    (concats || []).forEach(c => (c.bits || []).forEach(b => {
      if (b.signal) set.add(b.signal);
    }));
    return Array.from(set).sort();
  }

  function tableHtml(c) {
    const cons = c.consumer;
    const head = cons
      ? `<span class="irq-cons ${cons.is_irq ? 'irq' : 'warn'}">→ ${esc(cons.instance)}.${esc(cons.port)}${cons.is_irq ? ' (NVIC)' : ' (not an IRQ port?)'}</span>`
      : '<span class="irq-cons warn">→ output unconsumed — does NOT reach any core</span>';
    const rows = (c.bits || []).map(b =>
      `<tr class="${b.signal ? '' : 'z'}">
         <td class="irq-bit">[${b.bit}]</td>
         <td><input class="irq-edit" type="text" list="irq-sigs"
              placeholder="1'b0" value="${esc(b.signal || '')}"
              data-output="${esc(c.output || c.name)}" data-bit="${b.bit}"></td>
       </tr>`
    ).join('');
    return `<div class="irq-card">
      <div class="irq-card-h">
        <span class="irq-out">${esc(c.output || c.name)}</span>
        <span class="irq-meta">${c.mapped}/${c.width} bits mapped</span>
      </div>
      ${head}
      <table class="irq-tbl"><tbody>${rows ||
        '<tr><td colspan="2" class="dim">no bits</td></tr>'}</tbody></table>
    </div>`;
  }

  async function applyEdit(output, bit, signal) {
    if (busy || !App.currentDraft) return;
    busy = true;
    try {
      const res = await App.api(`/api/draft/${App.currentDraft}/irq`,
        { method: 'PUT', json: { output, bit: Number(bit), signal } });
      busy = false;
      draw(res.irqmap || { concats: [] });
    } catch (e) {
      busy = false;
      if (App.toast) App.toast(`IRQ bit ${output}[${bit}] failed: ${e.message}`, 'err');
      render();   // re-sync from server on failure
    }
  }

  function wire(el) {
    el.querySelectorAll('input.irq-edit').forEach(inp => {
      inp.addEventListener('change', () =>
        applyEdit(inp.dataset.output, inp.dataset.bit, inp.value.trim()));
    });
  }

  function draw(m) {
    const el = root();
    if (!el) return;
    const concats = m.concats || [];
    if (!concats.length) {
      el.innerHTML = `<div class="irq-head"><h2>IRQ Map</h2></div>
        <div class="irq-empty">No <code>glue_logic</code> <code>concat</code>
        blocks in this SoC — IRQ lines (if any) are wired directly, not via a
        NVIC concat. Add a concat to aggregate per-peripheral IRQs onto a core's
        NVIC port.</div>`;
      return;
    }
    const opts = candidates(concats)
      .map(s => `<option value="${esc(s)}">`).join('');
    el.innerHTML = `<div class="irq-head">
        <h2>IRQ Map</h2>
        <span class="dim">${concats.length} concat block(s) — edit a bit's
          source signal inline (blank = 1'b0)</span></div>
      <datalist id="irq-sigs">${opts}</datalist>
      <div class="irq-grid">${concats.map(tableHtml).join('')}</div>`;
    wire(el);
  }

  async function render() {
    const el = root();
    if (!el) return;
    if (!App.currentDraft) {
      el.innerHTML = '<div class="irq-empty">Open a draft to see its IRQ map.</div>';
      return;
    }
    let m;
    try {
      m = await App.api(`/api/draft/${App.currentDraft}/irqmap`);
    } catch (e) {
      el.innerHTML = `<div class="irq-empty">Could not load IRQ map: ${esc(e.message)}</div>`;
      return;
    }
    draw(m);
  }

  // styles (scoped, injected once)
  const CSS = `
    #irqmap-root { padding: 14px; }
    #irqmap-root .irq-head { display: flex; align-items: baseline; gap: 12px;
      margin-bottom: 12px; }
    #irqmap-root .irq-head h2 { margin: 0; font-size: 18px;
      background: var(--accent-grad, linear-gradient(90deg,#4da3ff,#3ddbb4));
      -webkit-background-clip: text; background-clip: text;
      -webkit-text-fill-color: transparent; }
    #irqmap-root .irq-empty { color: var(--dim); font-size: 13px; max-width: 560px; }
    #irqmap-root .irq-empty code { color: var(--accent, #4da3ff); }
    #irqmap-root .irq-grid { display: flex; flex-wrap: wrap; gap: 14px; }
    #irqmap-root .irq-card { width: 280px; border: 1px solid var(--line);
      border-radius: 10px; background: var(--panel); padding: 10px 12px; }
    #irqmap-root .irq-card-h { display: flex; justify-content: space-between;
      align-items: baseline; gap: 8px; }
    #irqmap-root .irq-out { font: 12.5px monospace; font-weight: 700;
      color: var(--fg); word-break: break-all; }
    #irqmap-root .irq-meta { font: 10.5px monospace; color: var(--dim);
      white-space: nowrap; }
    #irqmap-root .irq-cons { display: block; font: 11px monospace;
      margin: 6px 0 8px; }
    #irqmap-root .irq-cons.irq { color: var(--ok); }
    #irqmap-root .irq-cons.warn { color: var(--warn); }
    #irqmap-root .irq-tbl { width: 100%; border-collapse: collapse;
      font: 11px monospace; }
    #irqmap-root .irq-tbl td { border-top: 1px solid var(--line);
      padding: 2px 4px; }
    #irqmap-root .irq-tbl tr.z td { color: var(--dim); }
    #irqmap-root .irq-bit { color: var(--accent2, #3ddbb4); width: 42px; }
    #irqmap-root .irq-edit { width: 100%; box-sizing: border-box;
      font: 11px monospace; color: var(--fg); background: transparent;
      border: 1px solid transparent; border-radius: 4px; padding: 1px 3px; }
    #irqmap-root .irq-edit:hover { border-color: var(--line); }
    #irqmap-root .irq-edit:focus { outline: none; border-color: var(--accent, #4da3ff);
      background: var(--bg, #0d1117); }
    #irqmap-root .irq-edit::placeholder { color: var(--dim); }`;

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
    onDraft() { render(); },
    onShow() { boot(); render(); },
  };
})());
