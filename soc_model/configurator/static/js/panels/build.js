// Copyright 2026, SoC Labs (www.soclabs.org)
// Build panel (doc 06a M8 scaffold + #9 PARAM_OVERRIDES): stage selection,
// per-param --config-override editor, run/cancel, live WebSocket log + chips.
'use strict';

App.register('build', (() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

  function chips(stages) {
    $('stage-chips').innerHTML = stages.map(s =>
      `<span class="chip ${s.status}" id="chip-${s.name}">` +
      `${s.name}${s.seconds ? ` (${s.seconds}s)` : ''}</span>`).join('');
  }

  function appendLog(line) {
    const log = $('build-log');
    const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 4;
    log.textContent += line + '\n';
    if (stick) log.scrollTop = log.scrollHeight;
  }

  async function refresh() {
    const st = await App.api('/api/build/status');
    $('build-status').textContent = st.status;
    if (st.stages) chips(st.stages);
  }

  // ---- #9 overrides editor ------------------------------------------------
  async function loadParams() {
    const box = $('override-list');
    if (!box) return;
    if (!App.currentDraft) {
      box.innerHTML = '<span class="dim">open a draft to edit its params</span>';
      return;
    }
    let params = [];
    try {
      params = (await App.api(`/api/draft/${App.currentDraft}/params`)).params || [];
    } catch (e) { box.innerHTML = '<span class="dim">no params</span>'; return; }
    if (!params.length) {
      box.innerHTML = '<span class="dim">this SoC declares no params:</span>';
      return;
    }
    box.innerHTML = params.map(p => {
      const def = p.default == null ? '' : p.default;
      return `<label class="ovr-row" title="${esc(p.desc || '')}">
        <span class="ovr-name">${esc(p.name)}</span>
        <input class="ovr-in" data-name="${esc(p.name)}"
               placeholder="${esc(String(def))}" spellcheck="false">
        <span class="ovr-def">default ${esc(String(def))}</span></label>`;
    }).join('');
  }

  function collectOverrides() {
    const out = {};
    document.querySelectorAll('#override-list .ovr-in').forEach(inp => {
      const v = inp.value.trim();
      if (v !== '') out[inp.dataset.name] = v;
    });
    return out;
  }

  return {
    async init() {
      $('build-run').addEventListener('click', async () => {
        const stages = [...document.querySelectorAll('input.stage:checked')]
          .map(c => c.value);
        const overrides = collectOverrides();
        $('build-log').textContent = '';
        const n = Object.keys(overrides).length;
        if (n) appendLog(`[overrides] ${n} --config-override flag(s) applied`);
        try {
          await App.api('/api/build', { json: { stages, overrides } });
        } catch (e) { appendLog('ERROR: ' + e.message); }
        refresh();
      });
      $('build-cancel').addEventListener('click',
        () => App.api('/api/build/cancel', { json: {} }).then(refresh));
      loadParams();
      refresh();
    },
    onDraft() { loadParams(); },
    onShow() { loadParams(); refresh(); },
    onEvent(ev) {
      if (ev.type === 'line') appendLog(`[${ev.stage}] ${ev.line}`);
      else if (ev.type === 'stage' || ev.type === 'job') refresh();
    },
  };
})());
