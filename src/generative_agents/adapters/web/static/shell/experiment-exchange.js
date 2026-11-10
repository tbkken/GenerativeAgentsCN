/* Portable experiments and their complete Run packages share one transfer dialog. */
(function expose(root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ExperimentExchange = api;
}(typeof window !== 'undefined' ? window : globalThis, function createExchange() {
  'use strict';

  function downloadName(disposition, fallback) {
    const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition || '');
    const plain = /filename="([^"]+)"|filename=([^;]+)/i.exec(disposition || '');
    let name = plain?.[1] || plain?.[2] || fallback;
    if (encoded) {
      try { name = decodeURIComponent(encoded[1]); } catch (_error) { /* Use the plain filename. */ }
    }
    return name.split(/[\\/]/).pop().replace(/[\x00-\x1f\x7f]/g, '').trim() || fallback;
  }

  function mount(win, {getExperiment = () => null, onImported = () => {}, onOperation = () => {}} = {}) {
    const doc = win.document, $ = id => doc.getElementById(id);
    const dialog = $('experimentExchangeDialog');
    if (!dialog) return null;
    let session = null, contextKey = null;
    const current = request => session === request && dialog.open;
    const status = (message, error = false) => {
      $('experimentExchangeStatus').textContent = message;
      $('experimentExchangeStatus').setAttribute('role', error ? 'alert' : 'status');
    };
    function updateButton() {
      if (!session) return;
      const importing = session.mode === 'import';
      $('experimentExchangeSubmit').disabled = session.busy || session.complete || (importing && !$('experimentExchangeFile').files?.length);
      $('experimentExchangeSubmit').textContent = session.busy ? '正在处理…' : importing ? '导入实验' : '下载资源包';
      $('experimentExchangeFile').disabled = session.busy;
      $('experimentExchangeIncludeRuns').disabled = session.busy;
    }
    function invalidate() {
      session?.controller?.abort();
      session = null;
    }
    function open(mode, experiment = null) {
      if (mode === 'export' && !experiment?.id) return;
      invalidate();
      session = {mode, experiment, busy:false, complete:false};
      $('experimentExchangeTitle').textContent = mode === 'import' ? '导入实验包' : '导出实验资源包';
      $('experimentExchangeImport').hidden = mode !== 'import';
      $('experimentExchangeExport').hidden = mode !== 'export';
      $('experimentExchangeName').textContent = experiment?.name || '当前实验';
      $('experimentExchangeFile').value = '';
      $('experimentExchangeIncludeRuns').checked = true;
      $('experimentExchangeOpen').hidden = true;
      $('experimentExchangeOpen').removeAttribute('href');
      status(experiment?.dirty ? '当前有未保存的修改；此处只导出已保存内容。请先保存需要分享的修改。' : '');
      updateButton();
      if (!dialog.open) dialog.showModal();
    }
    async function request(url, options) {
      const response = await win.fetch(url, options);
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(typeof body.detail === 'string' ? body.detail : body.detail?.message || `请求失败（${response.status}）`);
      }
      return response;
    }
    async function submit() {
      const owner = session;
      if (!owner || owner.busy || owner.complete) return;
      const importing = owner.mode === 'import';
      const file = $('experimentExchangeFile').files?.[0];
      if (importing && !file) return;
      const includeRuns = $('experimentExchangeIncludeRuns').checked;
      owner.busy = true;
      updateButton();
      status(importing ? '正在校验并导入实验及运行记录。关闭窗口后可在操作历史查看结果。' : '正在打包实验资源和运行记录，请稍候…');
      try {
        if (importing) {
          // Imports keep running after a dialog closes, so always retain their outcome.
          const body = new win.FormData();
          body.append('file', file);
          const response = await request('/api/studio/experiment-import', {method:'POST', body});
          const result = await response.json();
          const message = `已导入 ${result.imported_count} 个包，${result.existing_count} 个相同包已存在；实验包含 ${result.run_ids.length} 次运行记录。`;
          onOperation('实验导入完成', message, 'success');
          if (current(owner)) {
            owner.complete = true;
            status(message);
            const link = $('experimentExchangeOpen');
            link.href = `/experiments/${encodeURIComponent(result.experiment_id)}`;
            link.hidden = false;
          }
          // Refresh navigation data without selecting an experiment or changing pages.
          Promise.resolve(onImported(result)).catch(error => onOperation('实验列表刷新失败', error.message, 'error'));
        } else {
          owner.controller = new win.AbortController();
          const id = encodeURIComponent(owner.experiment.id);
          const response = await request(`/api/studio/experiments/${id}/export?include_runs=${includeRuns}`, {signal:owner.controller.signal});
          const blob = await response.blob();
          if (!current(owner)) return;
          const link = doc.createElement('a');
          link.href = win.URL.createObjectURL(blob);
          link.download = downloadName(response.headers.get('content-disposition'), `experiment-${owner.experiment.id}.${includeRuns ? 'zip' : 'gaexp'}`);
          doc.body.appendChild(link);
          link.click();
          link.remove();
          win.setTimeout(() => win.URL.revokeObjectURL(link.href), 60000);
          const message = `${owner.experiment.name || '实验'}的资源包已生成并开始下载${includeRuns ? '，包含全部运行记录' : ''}。`;
          status(message);
          onOperation('实验导出完成', message, 'success');
        }
      } catch (error) {
        if (error.name !== 'AbortError') {
          const title = importing ? '实验导入失败' : '实验导出失败';
          onOperation(title, error.message, 'error');
          if (current(owner)) status(error.message, true);
        }
      } finally {
        owner.busy = false;
        if (current(owner)) updateButton();
      }
    }
    $('importExperimentPackageBtn').addEventListener('click', () => open('import'));
    $('exportExperimentPackageBtn').addEventListener('click', () => open('export', getExperiment()));
    $('experimentExchangeClose').addEventListener('click', () => { invalidate(); dialog.close(); });
    dialog.addEventListener('close', () => { if (!dialog.open) invalidate(); });
    dialog.addEventListener('cancel', invalidate);
    $('experimentExchangeFile').addEventListener('change', () => {
      if (!session || session.busy) return;
      session.complete = false;
      $('experimentExchangeOpen').hidden = true;
      status('');
      updateButton();
    });
    $('experimentExchangeSubmit').addEventListener('click', submit);
    return {
      openImport: () => open('import'),
      openExport: experiment => open('export', experiment),
      setContext(page, experimentId) {
        const key = `${page}:${experimentId || ''}`;
        if (contextKey !== key && dialog.open) { invalidate(); dialog.close(); }
        contextKey = key;
        $('importExperimentPackageBtn').hidden = page !== 'experiments';
        $('exportExperimentPackageBtn').hidden = !experimentId || page === 'experiments';
      },
    };
  }
  return {mount, downloadName};
}));
