/* Shared menu/Tab navigation for author resources and experiment copies. */
(function expose(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ResourceTabs = api;
}(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';
  function group(page) {
    if (['public-agents', 'agents', 'crowds'].includes(page)) return 'agents';
    if (['skills', 'brains'].includes(page)) return 'skills';
    return null;
  }
  function tabs(page, experiment = false) {
    const current = group(page);
    return current === 'agents'
      ? [{page: experiment ? 'agents' : 'public-agents', label: '智能体'}, {page: 'crowds', label: '人群'}]
      : current === 'skills' ? [{page: 'skills', label: '技能'}, {page: 'brains', label: '大脑'}] : [];
  }
  function menuPage(page, experiment = false) { return tabs(page, experiment)[0]?.page || page; }
  function mount(win, navigate) {
    const doc = win.document, bar = doc.getElementById('resourceTabs');
    const experiment = Boolean(win.ResourceScope?.experimentId);
    function sync(page) {
      const items = tabs(page, experiment);
      bar.hidden = !items.length;
      bar.setAttribute('aria-label', group(page) === 'agents' ? '智能体资源' : '技能资源');
      // Keep the buttons mounted while switching siblings, preserving focus.
      if (bar.dataset.group !== group(page)) {
        bar.dataset.group = group(page) || '';
        bar.innerHTML = items.map(item => `<button type="button" role="tab" id="resource-tab-${item.page}" data-resource-page="${item.page}" aria-controls="page-${item.page}">${item.label}</button>`).join('');
      }
      bar.querySelectorAll('[data-resource-page]').forEach(button => {
        const active = button.dataset.resourcePage === page;
        button.setAttribute('aria-selected', String(active));
        button.tabIndex = active ? 0 : -1;
        const panel = doc.getElementById(`page-${button.dataset.resourcePage}`);
        panel.setAttribute('role', 'tabpanel');
        panel.setAttribute('aria-labelledby', button.id);
      });
      if (items.length) win.ResourceList.remember(`menu-${group(page)}`, {tab: page});
    }
    bar.addEventListener('click', event => {
      const button = event.target.closest('[data-resource-page]');
      if (button && button.getAttribute('aria-selected') !== 'true') navigate(button.dataset.resourcePage);
    });
    bar.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      const buttons = [...bar.querySelectorAll('[data-resource-page]')];
      const index = buttons.indexOf(event.target);
      if (index < 0) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next].focus();
      navigate(buttons[next].dataset.resourcePage);
    });
    return {sync, target(page) {
      const saved = win.ResourceList.read(`menu-${group(page)}`).tab;
      return tabs(page, experiment).some(item => item.page === saved) ? saved : page;
    }};
  }
  return {group, tabs, menuPage, mount};
}));
