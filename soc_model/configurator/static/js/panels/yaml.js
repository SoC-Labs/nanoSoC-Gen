// Copyright 2026, SoC Labs (www.soclabs.org)
// YAML panel (doc 06a M4): drafts, raw editor, debounced 3-tier validation,
// completeness checklist.
'use strict';

App.register('yaml', (() => {
  const $ = (id) => document.getElementById(id);
  let current = null;          // draft id
  let dirty = false;

  async function refreshDrafts(select) {
    const { drafts } = await App.api('/api/drafts');
    $('draft-select').innerHTML =
      drafts.map(d => `<option value="${d}">${d}</option>`).join('') ||
      '<option value="">(no drafts)</option>';
    if (select) { $('draft-select').value = select; }
    return drafts;
  }

  async function openDraft(id) {
    if (!id) return;
    const d = await App.api('/api/draft/' + id);
    current = id;
    App.setDraft(id);                 // other panels follow the active draft
    $('yaml-text').value = d.yaml;
    dirty = false;
    validateNow();
  }

  function renderValidation(res) {
    $('val-counts').textContent =
      `— ${res.errors} error(s), ${res.warnings} warning(s)`;
    $('messages').innerHTML = res.messages.map(m => {
      const loc = [m.instance, m.port].filter(Boolean).join('.');
      const rule = m.rule ? ` [${m.rule}]` : '';
      const hint = m.hint ? `<span class="hint">hint: ${m.hint}</span>` : '';
      return `<li class="${m.level}">t${m.tier}/${m.category}${rule} ` +
             `${loc ? loc + ': ' : ''}${m.message}${hint}</li>`;
    }).join('') || '<li class="info">clean ✓</li>';
    const cl = res.checklist || [];
    const pass = cl.filter(c => c.passed).length;
    $('check-counts').textContent = cl.length ? `— ${pass}/${cl.length}` : '';
    $('checklist').innerHTML = cl.map(c =>
      `<li class="${c.level}${c.passed ? ' pass' : ''}">${c.desc || c.id}` +
      `${!c.passed && c.hint ? `<span class="hint">${c.hint}</span>` : ''}</li>`
    ).join('');
  }

  async function save() {
    if (!current) return;
    $('save-state').textContent = 'saving…';
    try {
      const res = await App.api('/api/draft/' + current,
                                { method: 'PUT', json: { yaml: $('yaml-text').value } });
      dirty = false;
      $('save-state').textContent = 'saved ✓';
      renderValidation(res);
    } catch (e) {
      $('save-state').textContent = '';
      $('messages').innerHTML = `<li class="error">${e.message}</li>`;
    }
  }
  const validateNow = App.debounce(save, 400);

  async function newFromTemplate() {
    const cat = await App.api('/api/catalog');
    if (!cat.templates.length) { alert('no template dirs configured'); return; }
    const pick = prompt('Template:\n' +
      cat.templates.map((t, i) => `${i}: ${t.split('/').pop()}`).join('\n'), '0');
    if (pick === null) return;
    const tpl = cat.templates[parseInt(pick, 10)];
    if (!tpl) return;
    const name = prompt('Draft name:', tpl.split('/').pop().replace('.yaml', ''));
    if (!name) return;
    const { id } = await App.api('/api/drafts', { json: { name, template: tpl } });
    await refreshDrafts(id);
    openDraft(id);
  }

  return {
    async init() {
      $('draft-select').addEventListener('change',
        (e) => openDraft(e.target.value));
      $('draft-new').addEventListener('click', newFromTemplate);
      $('draft-from-top').addEventListener('click', async () => {
        const { id } = await App.api('/api/drafts',
                                     { json: { name: 'top', from_top: true } });
        await refreshDrafts(id);
        openDraft(id);
      });
      $('yaml-text').addEventListener('input', () => {
        dirty = true;
        $('save-state').textContent = '…';
        validateNow();
      });
      const drafts = await refreshDrafts();
      if (drafts.length) openDraft(drafts[0]);
    },
  };
})());
