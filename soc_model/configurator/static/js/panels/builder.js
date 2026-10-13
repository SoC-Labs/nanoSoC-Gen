// Copyright 2026, SoC Labs (www.soclabs.org)
// Builder panel (doc 06a) — the guided "spec a SoC from scratch" wizard.
// Landing (start cards) -> new-SoC form -> workbench (palette + readiness).
// Renders into #builder-root. Server truth only: every add/remove response
// carries validation and the instance list is re-read from /dict.
//
// The workbench palette consumes GET /api/components — the droppable packaged
// IP catalog (generic labels, categories, tweakable knobs with HARD limits).
// Clicking a card opens an inline TWEAK PANEL (size dropdowns / number / enum)
// before [Add to SoC] posts {module, component_id, params}. Min limits are
// enforced client-side (choices >= min, number min attr) and the server's
// 400 "hard limit" is toasted gracefully.
'use strict';

App.register('builder', (() => {
  const root = () => document.getElementById('builder-root');

  // cached server data
  let catalog = null;        // /api/catalog {modules[], templates[], ...}
  let project = null;        // /api/project {adapter:{top_yaml,...}, health}
  let components = null;      // /api/components {components:[...]}
  let mode = 'landing';      // landing | newform  (workbench when draft open)
  let busy = false;          // an add/remove/create in flight
  let lastValidation = null; // last validation payload for the open draft
  let palFilter = '';        // live palette search text
  let openTweak = null;      // component id whose tweak panel is expanded
  let rawOpen = false;       // is the "Advanced / raw modules" group expanded

  // category -> {label, var, fallback colour} for the palette grouping.
  const CATS = [
    ['subsystem',  'Subsystems',  '--cat-subsystem',  '#8b5cf6'],
    ['cpu',        'CPU',         '--cat-cpu',        '#4da3ff'],
    ['memory',     'Memory',      '--cat-memory',     '#3ddbb4'],
    ['peripheral', 'Peripherals', '--cat-peripheral', '#f4b740'],
    ['debug',      'Debug',       '--cat-debug',      '#ff8fb1'],
    ['dma',        'DMA',         '--cat-dma',        '#ff6b6b'],
    ['other',      'Other',       '--cat-other',      '#8aa0b3'],
  ];
  const CAT_LABEL = {}, CAT_VAR = {}, CAT_FB = {};
  CATS.forEach(c => { CAT_LABEL[c[0]] = c[1]; CAT_VAR[c[0]] = c[2];
                      CAT_FB[c[0]] = c[3]; });

  const CLOCKS = [
    ['10000000', '10 MHz'], ['25000000', '25 MHz'],
    ['50000000', '50 MHz'], ['75000000', '75 MHz'],
    ['100000000', '100 MHz'], ['custom', 'Custom…'],
  ];

  const CSS = `
  #builder-root { max-width: 1180px; margin: 0 auto; }
  #builder-root .b-hero { padding: 26px 6px 18px; }
  #builder-root .b-hero h1 { margin: 0 0 6px; font-size: 26px;
    background: var(--accent-grad,
      linear-gradient(90deg, var(--accent, #4da3ff), var(--accent2, #3ddbb4)));
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent; color: var(--accent, #4da3ff);
    display: inline-block; }
  #builder-root .b-hero p { margin: 0; color: var(--dim); font-size: 13px; }

  #builder-root .b-cards { display: flex; flex-wrap: wrap; gap: 14px;
    padding: 4px 6px 10px; }
  #builder-root .b-card { width: 200px; min-height: 110px; cursor: pointer;
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 8px; padding: 14px; box-sizing: border-box;
    transition: transform 150ms ease, box-shadow 150ms ease,
      border-color 150ms ease; }
  #builder-root .b-card:hover { transform: translateY(-3px);
    box-shadow: 0 8px 22px rgba(0,0,0,.45);
    border-color: var(--accent, #4da3ff); }
  #builder-root .b-card h3 { margin: 0 0 6px; font-size: 14px; }
  #builder-root .b-card .sub { color: var(--dim); font-size: 11.5px;
    line-height: 1.45; }
  #builder-root .b-card.primary { border-color: var(--accent, #4da3ff);
    background: linear-gradient(160deg,
      rgba(77,163,255,.16), rgba(61,219,180,.07)), var(--panel); }
  #builder-root .b-card.primary h3 { color: var(--accent, #4da3ff); }
  #builder-root .b-card .badge { display: inline-block; font-size: 10px;
    text-transform: uppercase; letter-spacing: .7px; color: var(--dim);
    border: 1px solid var(--line); border-radius: 999px; padding: 1px 8px;
    margin-bottom: 8px; }
  #builder-root .b-card.primary .badge { color: var(--accent, #4da3ff);
    border-color: var(--accent, #4da3ff); }

  #builder-root h2.b-sect { font-size: 12px; text-transform: uppercase;
    letter-spacing: .8px; color: var(--dim); margin: 18px 6px 8px; }
  #builder-root .b-resume { display: flex; flex-wrap: wrap; gap: 10px;
    padding: 0 6px 20px; }
  #builder-root .b-mini { display: flex; align-items: center; gap: 10px;
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 6px; padding: 8px 12px; cursor: pointer;
    transition: transform 150ms ease, border-color 150ms ease; }
  #builder-root .b-mini:hover { transform: translateY(-2px);
    border-color: var(--accent2, #3ddbb4); }
  #builder-root .b-mini .nm { font-family: monospace; font-size: 12.5px; }
  #builder-root .b-mini .del { color: var(--dim); border: 0;
    background: none; cursor: pointer; font-size: 13px; padding: 0 2px; }
  #builder-root .b-mini .del:hover { color: var(--err); }

  #builder-root .b-form { margin: 8px 6px; max-width: 460px;
    background: var(--panel); border: 1px solid var(--accent, #4da3ff);
    border-radius: 8px; padding: 16px 18px; }
  #builder-root .b-form h3 { margin: 0 0 12px; font-size: 15px; }
  #builder-root .b-form label { display: block; color: var(--dim);
    font-size: 11px; text-transform: uppercase; letter-spacing: .6px;
    margin: 10px 0 4px; }
  #builder-root .b-form input[type=text],
  #builder-root .b-form input[type=number],
  #builder-root .b-form select { width: 100%; box-sizing: border-box;
    background: #11151a; color: var(--fg); border: 1px solid var(--line);
    border-radius: 4px; padding: 6px 8px; font: 13px monospace; }
  #builder-root .b-form .btns { margin-top: 16px; display: flex; gap: 8px; }
  #builder-root .b-form .err { color: var(--err); font-size: 12px;
    margin-top: 8px; }
  #builder-root button.b-go { background: var(--accent-grad,
      linear-gradient(90deg, var(--accent, #4da3ff), var(--accent2, #3ddbb4)));
    color: #0c1014; font-weight: 600; border: 0; border-radius: 5px;
    padding: 7px 16px; cursor: pointer; transition: filter 150ms ease; }
  #builder-root button.b-go:hover { filter: brightness(1.15); }
  #builder-root button.b-go:disabled { filter: grayscale(.7); cursor: wait; }

  /* ---- workbench ---- */
  #builder-root .b-bench { display: grid;
    grid-template-columns: minmax(300px, 380px) 1fr; gap: 16px;
    padding: 6px; align-items: start; }
  #builder-root .b-bhead { display: flex; align-items: baseline; gap: 12px;
    padding: 12px 6px 4px; flex-wrap: wrap; }
  #builder-root .b-bhead .ttl { font-size: 18px; font-weight: 600; }
  #builder-root .b-bhead .id { color: var(--dim); font: 12px monospace; }
  #builder-root .b-bhead .back { margin-left: auto; }
  #builder-root .b-col { background: var(--panel); border: 1px solid
    var(--line); border-radius: 8px; padding: 12px; }
  #builder-root .b-col > h3 { margin: 0 0 10px; font-size: 12px;
    text-transform: uppercase; letter-spacing: .8px; color: var(--dim); }
  #builder-root input.b-filter { width: 100%; box-sizing: border-box;
    background: #11151a; color: var(--fg); border: 1px solid var(--line);
    border-radius: 4px; padding: 6px 9px; font: 12px monospace;
    margin-bottom: 10px; }

  /* palette: category groups of generic-named component cards */
  #builder-root .b-pal { max-height: 560px; overflow-y: auto;
    margin: 0 -3px; padding: 0 3px; }
  #builder-root .b-grp { margin-bottom: 12px; }
  #builder-root .b-grp-h { display: flex; align-items: center; gap: 7px;
    font-size: 10.5px; text-transform: uppercase; letter-spacing: .8px;
    color: var(--cat, #8aa0b3); margin: 0 2px 6px; font-weight: 600; }
  #builder-root .b-grp-h .dot { width: 8px; height: 8px; border-radius: 2px;
    background: var(--cat, #8aa0b3); flex: 0 0 8px; }
  #builder-root .b-grp-h .ct { color: var(--dim); font-weight: 400; }
  #builder-root .b-grp.raw .b-grp-h { cursor: pointer; }
  #builder-root .b-grp.raw .b-grp-h .caret { margin-left: auto;
    transition: transform 150ms ease; }
  #builder-root .b-grp.raw.open .b-grp-h .caret { transform: rotate(90deg); }

  #builder-root .b-comp { border: 1px solid var(--line); border-left: 3px
    solid var(--cat, #8aa0b3); border-radius: 6px; padding: 8px 10px;
    margin-bottom: 6px; background: #161b21; cursor: pointer;
    transition: transform 150ms ease, border-color 150ms ease,
      box-shadow 150ms ease; }
  #builder-root .b-comp:hover { transform: translateY(-1px);
    box-shadow: 0 4px 14px rgba(0,0,0,.4);
    border-color: var(--cat, #8aa0b3); }
  #builder-root .b-comp.sel { box-shadow: 0 0 0 1px var(--cat, #8aa0b3); }
  #builder-root .b-comp .lbl { display: flex; align-items: center; gap: 8px;
    font-size: 13px; font-weight: 500; }
  #builder-root .b-comp .lbl .grip { color: var(--dim); cursor: grab;
    font-size: 12px; letter-spacing: -2px; }
  #builder-root .b-comp .lbl .kn { margin-left: auto; font-size: 10px;
    color: var(--cat, #8aa0b3); border: 1px solid var(--cat, #8aa0b3);
    border-radius: 999px; padding: 0 7px; font-weight: 400; }
  #builder-root .b-comp .dsc { color: var(--dim); font-size: 11px;
    line-height: 1.4; margin-top: 3px;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden; }
  #builder-root .b-comp.unavail { opacity: .5; cursor: default;
    border-left-color: var(--line); }
  #builder-root .b-comp.unavail:hover { transform: none; box-shadow: none;
    border-color: var(--line); }
  #builder-root .b-comp .needs { color: var(--warn); font-size: 10.5px;
    font-family: monospace; margin-top: 3px; }
  #builder-root .b-comp.raw { border-left-color: var(--line); }
  #builder-root .b-comp.raw .lbl { font: 12px monospace; }

  /* inline tweak panel */
  #builder-root .b-tweak { border: 1px solid var(--cat, #4da3ff);
    border-radius: 6px; background: linear-gradient(160deg,
      rgba(77,163,255,.08), transparent), #11151a; padding: 11px 12px;
    margin: -2px 0 8px; animation: b-tw-in .14s ease; }
  @keyframes b-tw-in { from { opacity: 0; transform: translateY(-4px); }
    to { opacity: 1; transform: none; } }
  #builder-root .b-tweak .tw-h { font-size: 12.5px; font-weight: 600;
    margin-bottom: 6px; color: var(--cat, #4da3ff); }
  #builder-root .b-tweak .tw-d { color: var(--dim); font-size: 11px;
    line-height: 1.4; margin-bottom: 9px; }
  #builder-root .b-knob { margin-bottom: 10px; }
  #builder-root .b-knob label { display: block; font-size: 11px;
    color: var(--fg); margin-bottom: 3px; font-weight: 500; }
  #builder-root .b-knob select,
  #builder-root .b-knob input[type=number] { width: 100%; box-sizing:
    border-box; background: #0d1115; color: var(--fg);
    border: 1px solid var(--line); border-radius: 4px; padding: 5px 7px;
    font: 12px monospace; }
  #builder-root .b-knob .kd { color: var(--dim); font-size: 10.5px;
    margin-top: 2px; line-height: 1.35; }
  #builder-root .b-knob .kd .floor { color: var(--warn); }
  #builder-root .b-tweak .tw-btns { display: flex; gap: 8px;
    margin-top: 4px; }
  #builder-root .b-tweak .tw-add { flex: 1 1 auto; }
  #builder-root .b-tweak .tw-cancel { border: 1px solid var(--line);
    background: var(--panel); color: var(--dim); border-radius: 5px;
    padding: 7px 12px; cursor: pointer; transition: all 150ms ease; }
  #builder-root .b-tweak .tw-cancel:hover { color: var(--fg);
    border-color: var(--fg); }

  #builder-root .b-meter { height: 10px; border-radius: 999px;
    background: #11151a; border: 1px solid var(--line); overflow: hidden;
    margin: 4px 0 4px; }
  #builder-root .b-meter .fill { height: 100%; width: 0;
    background: var(--accent-grad,
      linear-gradient(90deg, var(--accent, #4da3ff), var(--accent2, #3ddbb4)));
    transition: width .6s cubic-bezier(.2,.8,.2,1); }
  #builder-root .b-meter-lbl { display: flex; justify-content: space-between;
    color: var(--dim); font-size: 11px; margin-bottom: 10px; }
  #builder-root .b-check { list-style: none; margin: 0 0 12px; padding: 0; }
  #builder-root .b-check li { display: flex; gap: 8px; align-items:
    baseline; font-size: 12.5px; padding: 5px 6px; border-radius: 4px; }
  #builder-root .b-check li .ic { width: 14px; flex: 0 0 14px; }
  #builder-root .b-check li.pass .ic { color: var(--ok); }
  #builder-root .b-check li.fail-error .ic { color: var(--err); }
  #builder-root .b-check li.fail-warning .ic { color: var(--warn); }
  #builder-root .b-check li.fail-info .ic { color: var(--info); }
  #builder-root .b-check li.fail-error { animation: b-pulse 2.4s ease
    infinite; }
  @keyframes b-pulse {
    0%, 100% { background: rgba(255,107,107,.06); }
    50%      { background: rgba(255,107,107,.16); }
  }
  #builder-root .b-check .hint { display: block; color: var(--dim);
    font-size: 11px; margin-top: 1px; }
  #builder-root .b-check .hint b { color: var(--fg); font-weight: 500; }
  #builder-root .b-check .b-fix { margin-top: 4px; cursor: pointer;
    border: 1px solid var(--accent, #4da3ff); color: var(--accent, #4da3ff);
    background: rgba(77,163,255,.08); border-radius: 999px;
    padding: 1px 10px; font-size: 11px; }
  #builder-root .b-check .b-fix:hover { background: rgba(77,163,255,.2); }

  #builder-root .b-insts { display: flex; flex-wrap: wrap; gap: 7px;
    margin-bottom: 12px; }
  #builder-root .b-inst { display: inline-flex; align-items: center;
    gap: 7px; border: 1px solid var(--line); border-radius: 999px;
    background: #161b21; padding: 4px 6px 4px 12px; font: 12px monospace;
    transition: border-color 150ms ease; }
  #builder-root .b-inst:hover { border-color: var(--accent2, #3ddbb4); }
  #builder-root .b-inst .mod { color: var(--dim); font-size: 10.5px; }
  #builder-root .b-inst .rm { border: 0; background: none; cursor: pointer;
    color: var(--dim); font-size: 12px; padding: 0 4px; }
  #builder-root .b-inst .rm:hover { color: var(--err); }
  #builder-root .b-counts { display: flex; gap: 8px; margin-bottom: 6px; }
  #builder-root .b-cnt { font-size: 11px; border: 1px solid var(--line);
    border-radius: 999px; padding: 2px 10px; color: var(--dim); }
  #builder-root .b-cnt.err { color: var(--err); border-color: var(--err); }
  #builder-root .b-cnt.warn { color: var(--warn); border-color:
    var(--warn); }
  #builder-root .b-cnt.ok { color: var(--ok); border-color: var(--ok); }

  #builder-root .b-foot { grid-column: 1 / -1; display: flex; gap: 8px;
    flex-wrap: wrap; padding: 4px 0 14px; }
  #builder-root .b-foot button { border: 1px solid var(--line);
    background: var(--panel); color: var(--fg); border-radius: 5px;
    padding: 6px 14px; cursor: pointer; transition: all 150ms ease; }
  #builder-root .b-foot button:hover { border-color:
    var(--accent, #4da3ff); color: var(--accent, #4da3ff); }
  #builder-root .b-note { color: var(--dim); padding: 14px 6px; }
  #builder-root .b-err { color: var(--err); padding: 14px 6px;
    font-family: monospace; white-space: pre-wrap; }

  /* toasts live on <body>, not inside the panel */
  #builder-toasts { position: fixed; right: 18px; bottom: 18px;
    z-index: 1000; display: flex; flex-direction: column; gap: 8px;
    max-width: 380px; }
  #builder-toasts .toast { background: var(--panel); border: 1px solid
    var(--line); border-left: 3px solid var(--accent, #4da3ff);
    border-radius: 6px; padding: 9px 12px; font-size: 12.5px;
    box-shadow: 0 8px 22px rgba(0,0,0,.5); animation: b-toast-in .15s
    ease; }
  #builder-toasts .toast.warn { border-left-color: var(--warn); }
  #builder-toasts .toast.err { border-left-color: var(--err); }
  #builder-toasts .toast.ok { border-left-color: var(--ok); }
  #builder-toasts .toast .t2 { color: var(--warn); display: block;
    margin-top: 3px; font-size: 11.5px; }
  @keyframes b-toast-in {
    from { transform: translateY(8px); opacity: 0; }
    to   { transform: none; opacity: 1; }
  }
  `;

  // ---------------------------------------------------------- helpers
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function toast(msg, cls, extra) {
    let box = document.getElementById('builder-toasts');
    if (!box) {
      box = document.createElement('div');
      box.id = 'builder-toasts';
      document.body.appendChild(box);
    }
    const t = document.createElement('div');
    t.className = 'toast ' + (cls || '');
    t.innerHTML = esc(msg) +
      (extra ? `<span class="t2">${esc(extra)}</span>` : '');
    box.appendChild(t);
    setTimeout(() => { if (t.parentNode) t.parentNode.removeChild(t); },
               6500);
  }

  function tplName(path) {
    const base = String(path).split('/').pop() || String(path);
    return base.replace(/\.ya?ml$/i, '');
  }

  async function loadStatic() {
    if (!catalog) {
      try { catalog = await App.api('/api/catalog'); }
      catch (e) { catalog = { modules: [], templates: [] }; }
    }
    if (!project) {
      try { project = await App.api('/api/project'); }
      catch (e) { project = {}; }
    }
    if (!components) {
      try { components = (await App.api('/api/components')).components || []; }
      catch (e) { components = []; }
    }
  }

  // ---------------------------------------------------------- landing
  function landingHtml(drafts) {
    const tpls = (catalog && catalog.templates) || [];
    const topYaml = project && project.adapter && project.adapter.top_yaml;
    const cards = [];
    cards.push(`
      <div class="b-card primary" data-act="scratch">
        <span class="badge">blank canvas</span>
        <h3>Start from scratch</h3>
        <div class="sub">A clean skeleton — clock, reset and a CPU port on
          an empty AHB interconnect. Drop in packaged blocks from the palette
          and watch the readiness meter fill up.</div>
      </div>`);
    tpls.forEach(t => cards.push(`
      <div class="b-card" data-act="template" data-path="${esc(t)}">
        <span class="badge">template</span>
        <h3>${esc(tplName(t))}</h3>
        <div class="sub">Start from the ${esc(tplName(t))} template and
          customise it.</div>
      </div>`));
    if (topYaml) cards.push(`
      <div class="b-card" data-act="fromtop">
        <span class="badge">this project</span>
        <h3>Open project top</h3>
        <div class="sub">Load <code>${esc(topYaml)}</code> as a draft and
          evolve the existing SoC.</div>
      </div>`);
    const resume = (drafts || []).map(d => `
      <div class="b-mini" data-id="${esc(d)}">
        <span class="nm">${esc(d)}</span>
        <button class="del" title="delete draft">✕</button>
      </div>`).join('');
    return `
      <div class="b-hero">
        <h1>Design a SoC</h1>
        <p>Pick a starting point, drop in packaged IP with one click, tweak its
           memory sizes, and follow the checklist to a buildable system.</p>
      </div>
      <div class="b-cards">${cards.join('')}</div>
      ${mode === 'newform' ? newFormHtml() : ''}
      ${resume ? `<h2 class="b-sect">Resume a draft</h2>
                  <div class="b-resume">${resume}</div>` : ''}`;
  }

  function newFormHtml() {
    const opts = CLOCKS.map(c =>
      `<option value="${c[0]}" ${c[0] === '50000000' ? 'selected' : ''}>
         ${esc(c[1])}</option>`).join('');
    return `
      <div class="b-form" id="b-newform">
        <h3>New SoC from scratch</h3>
        <label for="b-nm">Name</label>
        <input type="text" id="b-nm" value="my_soc" spellcheck="false">
        <label for="b-clk">System clock</label>
        <select id="b-clk">${opts}</select>
        <div id="b-clk-custom" style="display:none">
          <label for="b-clk-mhz">Custom clock (MHz)</label>
          <input type="number" id="b-clk-mhz" value="50" min="1" max="1000">
        </div>
        <label for="b-aw">Address width</label>
        <select id="b-aw"><option value="32" selected>32-bit</option></select>
        <div class="btns">
          <button class="b-go" id="b-create">Create</button>
          <button id="b-cancel">Cancel</button>
        </div>
        <div class="err" id="b-form-err"></div>
      </div>`;
  }

  async function createDraft(body, label) {
    if (busy) return;
    busy = true;
    try {
      const res = await App.api('/api/drafts', { json: body });
      toast(`Draft "${label}" created — let's build`, 'ok');
      mode = 'landing';
      App.setDraft(res.id);          // broadcasts onDraft -> workbench
    } catch (e) {
      toast('Could not create draft: ' + e.message, 'err');
      const eb = document.getElementById('b-form-err');
      if (eb) eb.textContent = e.message;
    } finally { busy = false; }
  }

  function bindLanding(el) {
    el.querySelectorAll('.b-card').forEach(card =>
      card.addEventListener('click', () => {
        const act = card.dataset.act;
        if (act === 'scratch') {
          mode = 'newform';
          render();
        } else if (act === 'template') {
          createDraft({ name: tplName(card.dataset.path),
                        template: card.dataset.path },
                      tplName(card.dataset.path));
        } else if (act === 'fromtop') {
          createDraft({ name: 'project-top', from_top: true },
                      'project-top');
        }
      }));
    el.querySelectorAll('.b-mini').forEach(m => {
      m.addEventListener('click', (ev) => {
        if (ev.target.classList.contains('del')) return;
        App.setDraft(m.dataset.id);
      });
      const del = m.querySelector('.del');
      if (del) del.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const id = m.dataset.id;
        if (!confirm(`Delete draft "${id}"? This cannot be undone.`)) return;
        try {
          await App.api('/api/draft/' + encodeURIComponent(id),
                        { method: 'DELETE' });
          toast(`Draft "${id}" deleted`, 'ok');
        } catch (e) { toast('Delete failed: ' + e.message, 'err'); }
        render();
      });
    });
    const form = el.querySelector('#b-newform');
    if (form) {
      const clk = form.querySelector('#b-clk');
      clk.addEventListener('change', () => {
        form.querySelector('#b-clk-custom').style.display =
          clk.value === 'custom' ? '' : 'none';
      });
      form.querySelector('#b-cancel').addEventListener('click', () => {
        mode = 'landing'; render();
      });
      form.querySelector('#b-create').addEventListener('click', () => {
        const name = (form.querySelector('#b-nm').value || '').trim()
                     || 'my_soc';
        let hz = clk.value;
        if (hz === 'custom') {
          const mhz = parseFloat(form.querySelector('#b-clk-mhz').value);
          if (!(mhz > 0)) {
            form.querySelector('#b-form-err').textContent =
              'enter a clock frequency in MHz';
            return;
          }
          hz = String(Math.round(mhz * 1000000));
        }
        createDraft({
          name: name,
          scratch: {
            name: name,
            clk_hz: parseInt(hz, 10),
            addr_w: parseInt(form.querySelector('#b-aw').value, 10) || 32,
          },
        }, name);
      });
    }
  }

  // -------------------------------------------------------- workbench
  function meterHtml(val) {
    const list = (val && val.checklist) || [];
    const passed = list.filter(c => c.passed).length;
    const total = list.length;
    const pct = total ? Math.round(100 * passed / total) : 0;
    const rows = list.map(c => {
      const cls = c.passed ? 'pass' : ('fail-' + (c.level || 'error'));
      const hint = (!c.passed && (c.hint || c.detail))
        ? `<span class="hint">→ <b>${esc(c.hint || '')}</b>
             ${c.detail ? ' — ' + esc(c.detail) : ''}</span>` : '';
      const fix = (!c.passed && c.fixable)
        ? `<button class="b-fix" data-rule="${esc(c.id)}"
             title="apply the canonical fix for this rule">apply fix</button>`
        : '';
      return `<li class="${cls}" title="${esc(c.id || '')}">
        <span class="ic">${c.passed ? '✓' : '✗'}</span>
        <span>${esc(c.desc || c.id || '?')}${hint}${fix}</span></li>`;
    }).join('');
    const errs = (val && val.errors) || 0;
    const warns = (val && val.warnings) || 0;
    return `
      <div class="b-counts">
        <span class="b-cnt ${errs ? 'err' : 'ok'}">${errs} error${errs === 1 ? '' : 's'}</span>
        <span class="b-cnt ${warns ? 'warn' : ''}">${warns} warning${warns === 1 ? '' : 's'}</span>
        ${val && val.ok ? '<span class="b-cnt ok">valid ✓</span>' : ''}
      </div>
      <div class="b-meter"><div class="fill" data-pct="${pct}"></div></div>
      <div class="b-meter-lbl"><span>readiness</span>
        <span>${passed}/${total} checks · ${pct}%</span></div>
      <ul class="b-check">${rows ||
        '<li><span class="ic">·</span><span>no checklist rules loaded</span></li>'}</ul>`;
  }

  // ---- palette of packaged components, grouped by category --------------
  function catColour(cat) {
    const v = CAT_VAR[cat] || '--cat-other';
    const fb = CAT_FB[cat] || '#8aa0b3';
    return `--cat: var(${v}, ${fb})`;
  }

  function compMatches(c, f) {
    if (!f) return true;
    const hay = (c.label + ' ' + c.desc + ' ' + c.module + ' ' +
                 (c.category || '')).toLowerCase();
    return hay.indexOf(f) >= 0;
  }

  function compCardHtml(c) {
    const kn = (c.knobs || []).length;
    if (!c.available) {
      return `
        <div class="b-comp unavail" style="${catColour(c.category)}"
             title="not in this project's library">
          <div class="lbl">${esc(c.label)}</div>
          <div class="dsc">${esc(c.desc || '')}</div>
          <div class="needs">(needs ${esc(c.module)})</div>
        </div>`;
    }
    const sel = openTweak === c.id ? ' sel' : '';
    const knBadge = kn ? `<span class="kn">${kn} knob${kn === 1 ? '' : 's'}</span>`
                       : '';
    return `
      <div class="b-comp${sel}" style="${catColour(c.category)}"
           data-comp="${esc(c.id)}" draggable="true"
           title="click to tweak &amp; add">
        <div class="lbl"><span class="grip">⠿</span>${esc(c.label)}${knBadge}</div>
        <div class="dsc">${esc(c.desc || '')}</div>
      </div>`;
  }

  function rawCardHtml(c) {
    return `
      <div class="b-comp raw" style="${catColour(c.category)}"
           data-comp="${esc(c.id)}" draggable="true"
           title="raw module ${esc(c.module)} — click to add">
        <div class="lbl"><span class="grip">⠿</span>${esc(c.module)}</div>
      </div>`;
  }

  // the expandable knob editor, injected after the selected card.
  function tweakHtml(c) {
    const knobs = (c.knobs || []).map(k => knobHtml(k)).join('');
    return `
      <div class="b-tweak" style="${catColour(c.category)}" data-tweak="${esc(c.id)}">
        <div class="tw-h">Configure ${esc(c.label)}</div>
        ${c.desc ? `<div class="tw-d">${esc(c.desc)}</div>` : ''}
        ${knobs || '<div class="tw-d">No tweakable knobs — drops in with ' +
                    'sensible defaults.</div>'}
        <div class="tw-btns">
          <button class="b-go tw-add">+ Add to SoC</button>
          <button class="tw-cancel">Cancel</button>
        </div>
      </div>`;
  }

  // size label for an addr_w/bytes minimum (so we can show "min: 1 KB").
  function minLabel(k) {
    if (k.min == null) return null;
    const ch = (k.choices || []).filter(o => o.value === k.min)[0];
    if (ch && ch.label) return ch.label;
    if (k.kind === 'addr_w') return String(k.min);
    return String(k.min) + (k.unit ? ' ' + k.unit : '');
  }

  function knobHtml(k) {
    const def = k.default;
    let input;
    if ((k.kind === 'addr_w' || k.kind === 'bytes') && (k.choices || []).length) {
      // SIZE DROPDOWN — only choices >= min are offered (hard floor in UI).
      const opts = k.choices
        .filter(o => k.min == null || o.value >= k.min)
        .map(o => `<option value="${o.value}" ${o.value === def ? 'selected' : ''}>
             ${esc(o.label)}</option>`).join('');
      input = `<select data-knob="${esc(k.param)}" data-num="1">${opts}</select>`;
    } else if (k.kind === 'enum' && (k.choices || []).length) {
      const opts = k.choices.map(o =>
        `<option value="${esc(o.value)}" ${o.value === def ? 'selected' : ''}>
           ${esc(o.label != null ? o.label : o.value)}</option>`).join('');
      input = `<select data-knob="${esc(k.param)}">${opts}</select>`;
    } else {
      // plain int — number input with a HARD min/max attr.
      const mn = k.min != null ? ` min="${k.min}"` : '';
      const mx = k.max != null ? ` max="${k.max}"` : '';
      input = `<input type="number" data-knob="${esc(k.param)}" data-num="1"
        value="${def != null ? def : ''}"${mn}${mx} spellcheck="false">`;
    }
    const ml = minLabel(k);
    const desc = k.desc ? esc(k.desc) + ' ' : '';
    const floor = ml ? `<span class="floor">min: ${esc(ml)}</span>` : '';
    const unit = (k.unit && k.kind === 'int') ? ' (' + esc(k.unit) + ')' : '';
    return `
      <div class="b-knob">
        <label>${esc(k.label || k.param)}${unit}</label>
        ${input}
        ${(desc || floor) ? `<div class="kd">${desc}${floor}</div>` : ''}
      </div>`;
  }

  function paletteHtml(filter) {
    const all = components || [];
    if (!all.length) {
      return '<div class="b-note">no components in the library.</div>';
    }
    const f = (filter || '').toLowerCase();
    const packaged = all.filter(c => c.packaged && compMatches(c, f));
    const raw = all.filter(c => !c.packaged && compMatches(c, f));

    let html = '';
    CATS.forEach(cat => {
      const inCat = packaged.filter(c => c.category === cat[0]);
      if (!inCat.length) return;
      // available first, then dimmed unavailables.
      inCat.sort((a, b) => (a.available === b.available) ? 0
                          : (a.available ? -1 : 1));
      html += `
        <div class="b-grp" style="${catColour(cat[0])}">
          <div class="b-grp-h"><span class="dot"></span>${esc(cat[1])}
            <span class="ct">${inCat.length}</span></div>
          ${inCat.map(compCardHtml).join('')}
        </div>`;
    });

    if (raw.length) {
      const open = rawOpen || !!f;     // a filter force-opens raw too
      html += `
        <div class="b-grp raw ${open ? 'open' : ''}" id="b-raw"
             style="${catColour('other')}">
          <div class="b-grp-h" data-raw-toggle>
            <span class="dot"></span>Advanced / raw modules
            <span class="ct">${raw.length}</span>
            <span class="caret">›</span>
          </div>
          ${open ? raw.map(rawCardHtml).join('') : ''}
        </div>`;
    }

    if (!html) {
      return '<div class="b-note">no components match the filter.</div>';
    }
    return html;
  }

  function instancesHtml(insts) {
    if (!insts.length) {
      return '<div class="b-note">nothing added yet — pick a packaged block ' +
             'from the palette to get going.</div>';
    }
    return `<div class="b-insts">${insts.map(i => `
      <span class="b-inst" data-inst="${esc(i.instance_name)}">
        ${esc(i.instance_name)}
        <span class="mod">${esc(i.module || '?')}</span>
        <button class="rm" title="remove ${esc(i.instance_name)}">✕</button>
      </span>`).join('')}</div>`;
  }

  function benchHtml(dict, val, name) {
    const insts = ((dict && dict.module && dict.module.instances) || [])
      .filter(i => i && i.instance_name);
    return `
      <div class="b-bhead">
        <span class="ttl">${esc(name)}</span>
        <span class="id">draft: ${esc(App.currentDraft)}</span>
        <button class="back" id="b-back">‹ Start screen</button>
      </div>
      <div class="b-bench">
        <div class="b-col">
          <h3>Drop-in components</h3>
          <input type="text" class="b-filter" id="b-filter"
                 placeholder="search packaged IP…" spellcheck="false"
                 value="${esc(palFilter)}">
          <div class="b-pal" id="b-pal">${paletteHtml(palFilter)}</div>
        </div>
        <div class="b-col">
          <h3>Your SoC</h3>
          <div id="b-meter">${meterHtml(val)}</div>
          <h3>Components (${insts.length})</h3>
          <div id="b-instlist">${instancesHtml(insts)}</div>
        </div>
        <div class="b-foot">
          <button data-show="compose">Fine-tune in Compose</button>
          <button data-show="diagram">View Diagram</button>
          <button data-show="memmap">Memory Map</button>
          <button data-show="yaml">Export YAML</button>
          <button data-act="snapshot" title="save a named copy of this draft">Snapshot</button>
          <button data-act="template" title="save this draft as a reusable template">Save as template</button>
          <button data-act="share" title="copy a single-blob JSON report (yaml + validation + adapter)">Share JSON</button>
          <button data-show="build" class="b-go"
                  style="margin-left:auto">Generate RTL ›</button>
        </div>
      </div>`;
  }

  function animateMeter(el) {
    el.querySelectorAll('.b-meter .fill').forEach(f => {
      // width starts at 0 in CSS; set on the next frame so it animates
      requestAnimationFrame(() =>
        requestAnimationFrame(() => { f.style.width = f.dataset.pct + '%'; }));
    });
  }

  function compById(id) {
    return (components || []).filter(c => c.id === id)[0] || null;
  }

  // gather knob values from the open tweak panel into a params object.
  function collectParams(tweakEl) {
    const params = {};
    tweakEl.querySelectorAll('[data-knob]').forEach(inp => {
      let v = inp.value;
      if (v === '' || v == null) return;
      if (inp.dataset.num) {
        const n = Number(v);
        if (!isNaN(n)) v = n;
      }
      params[inp.dataset.knob] = v;
    });
    return params;
  }

  async function addComponent(comp, params) {
    if (busy || !App.currentDraft || !comp) return;
    busy = true;
    setAddButtons(true);
    try {
      const body = { module: comp.module, component_id: comp.id };
      if (params && Object.keys(params).length) body.params = params;
      const res = await App.api(
        `/api/draft/${App.currentDraft}/component`, { json: body });
      const wired = (res.wired || []).length;
      const unwired = (res.unwired || []).length;
      toast(`Added ${res.instance}` +
            (res.target ? ` @ window ${res.target}` : '') +
            ` — ${wired} port${wired === 1 ? '' : 's'} auto-wired` +
            (wired ? ` (${(res.wired || []).slice(0, 4).join(', ')}` +
                     (wired > 4 ? ', …' : '') + ')' : ''),
            unwired ? 'warn' : 'ok',
            unwired ? `${unwired} port${unwired === 1 ? '' : 's'} need ` +
                      'manual wiring — fine-tune in Compose' : '');
      lastValidation = res.validation || null;
      openTweak = null;
      await renderBench();             // re-read instances from /dict
    } catch (e) {
      // server enforces the same hard limits; surface them as a toast.
      const msg = e.message || '';
      if (msg.toLowerCase().indexOf('hard limit') >= 0) {
        toast('Blocked: ' + msg.replace(/^\d+:\s*/, ''), 'err');
      } else {
        toast(`Add ${comp.label} failed: ` + msg, 'err');
      }
      setAddButtons(false);
    } finally { busy = false; }
  }

  async function draftAction(act) {
    if (busy || !App.currentDraft) return;
    const id = App.currentDraft;
    try {
      if (act === 'snapshot') {
        const name = prompt('Snapshot name:', 'v1');
        if (name == null) return;
        const r = await App.api(`/api/draft/${id}/snapshot`, { json: { name } });
        toast(`Snapshot saved as draft "${r.id}"`, 'ok');
      } else if (act === 'template') {
        const name = prompt('Template name:', id);
        if (name == null) return;
        const r = await App.api(`/api/draft/${id}/save-as-template`,
                                { json: { name } });
        toast(`Saved template "${r.name}" — available under New from template`, 'ok');
      } else if (act === 'share') {
        const blob = await App.api(`/api/draft/${id}/share`);
        const text = JSON.stringify(blob, null, 2);
        let copied = false;
        try {
          if (navigator.clipboard) { await navigator.clipboard.writeText(text); copied = true; }
        } catch (e) { /* clipboard blocked — fall through */ }
        toast(copied ? 'Shareable JSON copied to clipboard'
                     : 'Shareable JSON ready (clipboard blocked — see console)',
              'ok', copied ? '' : 'logged to console');
        if (!copied && window.console) console.log(text);
      }
    } catch (e) {
      const m = (e.message || '').replace(/^\d+:\s*/, '');
      toast(`${act} failed: ${m}`, 'err');
    }
  }

  async function fixRule(ruleId) {
    if (busy || !App.currentDraft || !ruleId) return;
    busy = true;
    try {
      const res = await App.api(`/api/draft/${App.currentDraft}/fix`,
                                { json: { rule_id: ruleId } });
      toast('Fixed: ' + (res.message || ruleId), 'ok');
      lastValidation = res.validation || null;
      await renderBench();
    } catch (e) {
      toast(`Auto-fix ${ruleId} failed: ` + (e.message || ''), 'err');
    } finally { busy = false; }
  }

  async function removeComponent(inst) {
    if (busy || !App.currentDraft) return;
    if (!confirm(`Remove "${inst}" (and its address windows)?`)) return;
    busy = true;
    try {
      const res = await App.api(
        `/api/draft/${App.currentDraft}/component/` +
        encodeURIComponent(inst), { method: 'DELETE' });
      const rt = (res.removed_targets || []).length;
      toast(`Removed ${inst}` +
            (rt ? ` (+${rt} address window${rt === 1 ? '' : 's'})` : ''),
            'ok');
      lastValidation = res.validation || null;
      await renderBench();
    } catch (e) {
      toast(`Remove ${inst} failed: ` + e.message, 'err');
    } finally { busy = false; }
  }

  function setAddButtons(disabled) {
    const el = root();
    if (el) el.querySelectorAll('.b-tweak .tw-add')
      .forEach(b => { b.disabled = disabled; });
  }

  function bindBench(el) {
    const back = el.querySelector('#b-back');
    if (back) back.addEventListener('click', () => {
      lastValidation = null;
      App.setDraft(null);              // all panels back to no-draft state
    });
    const filt = el.querySelector('#b-filter');
    if (filt) filt.addEventListener('input', App.debounce(() => {
      palFilter = filt.value.trim();
      const pal = el.querySelector('#b-pal');
      if (pal) { pal.innerHTML = paletteHtml(palFilter); bindPal(el); }
    }, 120));
    bindPal(el);
    el.querySelectorAll('#b-instlist .rm').forEach(b =>
      b.addEventListener('click', () =>
        removeComponent(b.parentElement.dataset.inst)));
    el.querySelectorAll('.b-fix').forEach(b =>
      b.addEventListener('click', () => fixRule(b.dataset.rule)));
    el.querySelectorAll('.b-foot button[data-show]').forEach(b =>
      b.addEventListener('click', () => App.show(b.dataset.show)));
    el.querySelectorAll('.b-foot button[data-act]').forEach(b =>
      b.addEventListener('click', () => draftAction(b.dataset.act)));
  }

  // re-render only the palette column (keeps focus/scroll churn minimal).
  function refreshPalette(el) {
    const pal = el.querySelector('#b-pal');
    if (pal) { pal.innerHTML = paletteHtml(palFilter); bindPal(el); }
  }

  function bindPal(el) {
    // raw group toggle
    const rt = el.querySelector('[data-raw-toggle]');
    if (rt) rt.addEventListener('click', () => {
      rawOpen = !rawOpen; refreshPalette(el);
    });

    // packaged component card -> expand / collapse the inline tweak panel
    el.querySelectorAll('.b-comp[data-comp]:not(.raw):not(.unavail)')
      .forEach(card => {
        card.addEventListener('click', (ev) => {
          if (ev.target.closest('.b-tweak')) return;  // ignore inner clicks
          const id = card.dataset.comp;
          openTweak = (openTweak === id) ? null : id;
          refreshPalette(el);
        });
        // drag-and-drop: a card can be dragged onto the SoC column to add it
        card.addEventListener('dragstart', (ev) => {
          ev.dataTransfer.setData('text/plain', card.dataset.comp);
          ev.dataTransfer.effectAllowed = 'copy';
        });
      });

    // raw module card -> add immediately (no knobs)
    el.querySelectorAll('.b-comp.raw[data-comp]').forEach(card => {
      card.addEventListener('click', () => {
        const c = compById(card.dataset.comp);
        if (c) addComponent(c, null);
      });
      card.addEventListener('dragstart', (ev) => {
        ev.dataTransfer.setData('text/plain', card.dataset.comp);
        ev.dataTransfer.effectAllowed = 'copy';
      });
    });

    // inject + wire the inline tweak panel under the selected card
    if (openTweak) {
      const card = el.querySelector(`.b-comp[data-comp="${cssEsc(openTweak)}"]`);
      const c = compById(openTweak);
      if (card && c) {
        card.insertAdjacentHTML('afterend', tweakHtml(c));
        const tw = card.nextElementSibling;
        const add = tw.querySelector('.tw-add');
        if (add) add.addEventListener('click', (ev) => {
          ev.stopPropagation();
          addComponent(c, collectParams(tw));
        });
        const cx = tw.querySelector('.tw-cancel');
        if (cx) cx.addEventListener('click', (ev) => {
          ev.stopPropagation();
          openTweak = null; refreshPalette(el);
        });
        tw.addEventListener('click', (ev) => ev.stopPropagation());
      }
    }

    // drop target: the "Your SoC" column accepts dragged cards
    const drop = el.querySelector('#b-instlist') &&
                 el.querySelector('#b-instlist').parentElement;
    if (drop) {
      drop.addEventListener('dragover', (ev) => {
        ev.preventDefault(); ev.dataTransfer.dropEffect = 'copy';
      });
      drop.addEventListener('drop', (ev) => {
        ev.preventDefault();
        const id = ev.dataTransfer.getData('text/plain');
        const c = compById(id);
        if (!c) return;
        if ((c.knobs || []).length && c.packaged) {
          // has knobs: open the tweak panel rather than blind-adding.
          openTweak = id; refreshPalette(el);
        } else {
          addComponent(c, null);
        }
      });
    }
  }

  // CSS.escape shim (Node v10 / old browsers): only need it for our ids.
  function cssEsc(s) {
    return String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  }

  // ----------------------------------------------------------- render
  async function renderBench() {
    const el = root();
    if (!el || !App.currentDraft) return;
    let dict = null, val = lastValidation;
    try {
      const jobs = [App.api(`/api/draft/${App.currentDraft}/dict`)];
      if (!val) {
        jobs.push(App.api(`/api/draft/${App.currentDraft}/validate`,
                          { json: {} }));
      }
      const out = await Promise.all(jobs);
      dict = out[0];
      if (!val) val = out[1];
    } catch (e) {
      el.innerHTML = `<div class="b-err">${esc(e.message)}</div>
        <div class="b-note"><button id="b-back">‹ Start screen</button></div>`;
      const back = el.querySelector('#b-back');
      if (back) back.addEventListener('click', () => App.setDraft(null));
      return;
    }
    lastValidation = null;             // one-shot: next refresh re-validates
    const name = (dict && dict.module && dict.module.name) ||
                 App.currentDraft;
    el.innerHTML = benchHtml(dict, val, name);
    bindBench(el);
    animateMeter(el);
  }

  async function render() {
    const el = root();
    if (!el) return;
    await loadStatic();
    if (App.currentDraft) { await renderBench(); return; }
    let drafts = [];
    try { drafts = (await App.api('/api/drafts')).drafts || []; }
    catch (e) { /* show landing without resume cards */ }
    el.innerHTML = landingHtml(drafts);
    bindLanding(el);
  }

  return {
    init() {
      const st = document.createElement('style');
      st.textContent = CSS;
      document.head.appendChild(st);
      render();
    },
    onDraft() { lastValidation = null; openTweak = null; render(); },
    onShow() { render(); },
  };
})());
