/* One document belongs to either the author workspace or one experiment. */
(function () {
  'use strict';
  const match = location.pathname.match(/^\/experiments\/([^/]+)\/?$/);
  const experimentId = match ? decodeURIComponent(match[1]) : null;
  const scope = {
    experimentId, editable: false, digest: null,
    base: experimentId ? `/api/studio/experiments/${encodeURIComponent(experimentId)}/resources` : '/api/studio/resources',
    url(path) { return `${this.base}${path}`; },
    pageUrl(page, values = {}) {
      const params = new URLSearchParams({ view: page, ...values });
      return `${experimentId ? `/experiments/${encodeURIComponent(experimentId)}` : '/'}?${params}`;
    },
    assetUrl(path) {
      if (!experimentId || !path) return path || '';
      return `/api/studio/experiments/${encodeURIComponent(experimentId)}/assets/${String(path).replace(/^assets\//, '').split('/').map(encodeURIComponent).join('/')}`;
    },
    setExperiment(experiment) {
      if (experiment.experiment_id !== experimentId) return;
      this.editable = experiment.editable;
      this.digest = experiment.content_sha256;
      document.body.classList.toggle('experiment-sealed', !this.editable);
      const notice = document.getElementById('experimentScopeNotice');
      notice.querySelector('strong').textContent = experiment.name;
      notice.querySelector('span').textContent = this.editable
        ? '实验独立副本 · 修改仅影响当前实验，不会改变基础配置'
        : '实验独立副本 · 已封存，只读；需要修改时复制为新实验';
    },
    options(options = {}) {
      if (!experimentId || !options.method || options.method === 'GET') return options;
      const body = options.body ? JSON.parse(options.body) : {};
      if (!body.row_version && !body.expected_content_sha256) body.expected_content_sha256 = this.digest;
      return { ...options, body: JSON.stringify(body) };
    },
    saved(result) {
      if (!experimentId) return;
      this.digest = result?.row_version || result?.content_sha256 || this.digest;
      window.dispatchEvent(new CustomEvent('experiment-resource:saved', { detail: { experimentId } }));
    },
  };
  window.ResourceScope = scope;
  document.body.classList.toggle('experiment-workspace', Boolean(experimentId));
})();
