/* Public model services and write-only credentials. */
(function () {
  'use strict';
  const API = '/api/studio/resources/model-services';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const $ = id => document.getElementById(id);
  let items = [], selected = null, purpose = 'chat', generation = 0, dirty = false;
  let editing = false, initialized = false;
  const list = window.ResourceList;
  async function request(path = '', options = {}) {
    const response = await fetch(API + path, {headers: {'Content-Type': 'application/json'}, ...options});
    const data = response.status === 204 ? null : await response.json();
    if (!response.ok) throw new Error(data?.detail?.message || data?.detail || `请求失败 (${response.status})`);
    return data;
  }
  function message(text) { $('modelServiceStatus').textContent = text; }
  async function loadChoices(select, kind, value = '') {
    select.disabled = true;
    select.replaceChildren(new Option('正在加载模型…', ''));
    try {
      const result = await request();
      if (!select.isConnected) return;
      const available = result.items.filter(item => item.config[kind]);
      select.replaceChildren(new Option(available.length ? '请选择已配置模型' : '请先到模型中心添加模型', ''));
      available.forEach(item => select.add(new Option(`${item.name} · ${item.config[kind].model}`, item.id)));
      if (available.some(item => item.id === value)) select.value = value;
    } catch (error) {
      if (select.isConnected) select.replaceChildren(new Option(error.message, ''));
    } finally { if (select.isConnected) select.disabled = false; }
  }
  async function mayLeave() {
    return !dirty || await window.confirmResourceDeletion({title:'放弃未保存修改', name:'模型配置', message:'当前修改尚未保存。', confirmLabel:'放弃修改'});
  }
  function renderList() {
    const saved = list.read('model-catalog'), query = saved.query.toLocaleLowerCase();
    const filtered = list.sorted(items).filter(item => (!saved.kind || item.config[saved.kind]) && (!query || `${item.name} ${item.config.chat?.model || ''} ${item.config.embedding?.model || ''}`.toLocaleLowerCase().includes(query)));
    const data = list.slice(filtered, saved.page);
    list.remember('model-catalog', {page: data.page});
    $('modelServiceList').innerHTML = data.items.map(item => {
      const kinds = ['chat','embedding'].filter(kind => item.config[kind]);
      return list.row({name: item.name, icon: '⌁',
        description: kinds.map(kind => `${kind === 'chat' ? '聊天' : '向量'}：${item.config[kind].model}`).join(' · '),
        badges: [kinds.map(kind => kind === 'chat' ? '聊天' : '向量').join(' + ')],
        meta: kinds.map(kind => `${kind === 'chat' ? '聊天' : '向量'}${item.credential_configured?.[kind] ? '密钥已配置' : '未配置密钥'}`),
        open: {'data-model-id': item.id}, actions: [{label: '删除模型', danger: true, attributes: {'data-delete-model': item.id}}],
      });
    }).join('') || list.empty('模型', Boolean(query || saved.kind));
    $('modelServiceList').querySelectorAll('[data-model-id]').forEach(button => button.onclick = () => openEditor(button.dataset.modelId).catch(report));
    $('modelServiceList').querySelectorAll('[data-delete-model]').forEach(button => button.onclick = () => deleteModel(items.find(item => item.id === button.dataset.deleteModel)).catch(report));
    list.pager($('modelListFooter'), {...data, onPage: page => {list.remember('model-catalog', {page, scroll: 0}); renderList();}});
  }
  function report(error) { window.showToast?.(error.message || String(error), '操作失败'); }
  async function deleteModel(target) {
    if (!target || !await window.confirmResourceDeletion({type:'模型', name:target.name, message:'此配置的聊天和向量服务会一并删除。已创建实验持有自己的副本，不受影响。'})) return;
    await request(`/${target.id}`, {method:'DELETE'});
    selected=null; dirty=false; editing=false;
    list.route('model-catalog');
    await activate();
    window.showToast?.(`模型“${target.name}”已删除`, '删除完成');
  }
  async function openEditor(id = null, push = true) {
    if (!await mayLeave()) return;
    if (push) list.capture('model-catalog');
    selected = id ? items.find(item => item.id === id) : null;
    if (id && !selected) throw new Error('该模型已不存在，请返回列表重新选择');
    generation++;
    purpose = selected && !selected.config.chat ? 'embedding' : 'chat';
    dirty=false; editing=true;
    if (push) list.route('model-catalog', id ? {model_id:id} : {create:'1'});
    render();
    window.scrollTo({top:0, behavior:'instant'});
  }
  function render() {
    $('modelCatalogShell').hidden = editing;
    $('modelEditorShell').hidden = !editing;
    $('createModelResourceBtn').hidden = editing;
    if (!editing) { renderList(); return; }
    ['chat','embedding'].forEach(kind => {
      const button = $(`addModelService-${kind}`);
      button.classList.toggle('btn-primary', kind === purpose);
      button.setAttribute('aria-pressed', String(kind === purpose));
    });
    const config = selected?.config?.[purpose] || {};
    $('modelServiceEditor').innerHTML = `<div class="panel-intro"><div><h2>${selected ? '编辑' : '新增'}${purpose === 'chat' ? '聊天模型' : 'Embedding 模型'}</h2><p>兼容 OpenAI API 的服务。API Key 加密保存，不会回显或写入实验包。</p></div></div>
      <form id="modelServiceForm" class="form-grid">
        <div class="field"><label for="modelServiceName">配置名称</label><input class="control" id="modelServiceName" required maxlength="120" value="${esc(selected?.name || '')}"></div>
        <div class="field"><label for="modelServiceModel">模型 ID</label><input class="control" id="modelServiceModel" required value="${esc(config.model || '')}" placeholder="服务提供的完整模型 ID"></div>
        <div class="field" style="grid-column:1/-1"><label for="modelServiceUrl">服务地址</label><input class="control" id="modelServiceUrl" type="url" required value="${esc(config.base_url || '')}" placeholder="http://127.0.0.1:8888/v1"></div>
        <div class="field" style="grid-column:1/-1"><label for="modelServiceKey">API Key</label><input class="control" id="modelServiceKey" type="password" autocomplete="new-password" placeholder="${config.credential_env ? '密钥已配置；留空保持不变' : '服务无需认证时可留空'}"><label><input type="checkbox" id="modelServiceClearKey">清除当前配置的密钥</label></div>
        <div class="field"><label for="modelServiceTimeout">超时（秒）</label><input class="control" id="modelServiceTimeout" type="number" min="1" max="600" required value="${esc(config.timeout_seconds || 90)}"></div>
        ${purpose === 'chat' ? `<div class="field"><label for="modelServiceTokens">最大输出 tokens</label><input class="control" id="modelServiceTokens" type="number" min="1" max="131072" required value="${esc(config.max_tokens || 2048)}"></div><div class="field"><label for="modelServiceTemperature">Temperature</label><input class="control" id="modelServiceTemperature" type="number" min="0" max="2" step="0.1" required value="${esc(config.temperature ?? 0.2)}"></div>` : ''}
        <div class="inline-actions" style="grid-column:1/-1"><button class="btn btn-primary" id="saveModelService" type="submit">保存模型</button><button class="btn" id="testModelService" type="button" ${selected ? '' : 'disabled'}>测试连接</button><button class="btn btn-danger" id="deleteModelService" type="button" ${selected ? '' : 'disabled'}>删除配置</button></div>
      </form><p id="modelServiceStatus" role="status" aria-live="polite"></p>`;
    $('modelServiceForm').oninput = () => { dirty = true; };
    $('modelServiceForm').onsubmit = async event => {
      event.preventDefault();
      const token = generation;
      const body = {name:$('modelServiceName').value.trim(), purpose, model:$('modelServiceModel').value.trim(), base_url:$('modelServiceUrl').value.trim(),
        api_key:$('modelServiceKey').value || null, clear_api_key:$('modelServiceClearKey').checked, row_version:selected?.row_version || null,
        timeout_seconds:Number($('modelServiceTimeout').value), max_tokens:Number($('modelServiceTokens')?.value || 2048), temperature:Number($('modelServiceTemperature')?.value || 0)};
      $('saveModelService').disabled = true;
      try {
        const saved = await request(selected ? `/${selected.id}` : '', {method:selected ? 'PUT':'POST', body:JSON.stringify(body)});
        if (token !== generation) return;
        selected = saved; dirty = false;
        items = (await request()).items;
        if (token !== generation) return;
        list.route('model-catalog', {model_id:saved.id});
        render(); message('模型已保存，可在技能试运行和实验创建时选择。');
      } catch (error) { if (token === generation) { message(error.message); $('saveModelService').disabled = false; } }
    };
    $('testModelService').onclick = async () => {
      if (dirty) { message('请先保存修改，再测试当前配置。'); return; }
      const token = generation;
      $('testModelService').disabled = true; message('正在执行模型请求…');
      try { const result = await request(`/${selected.id}/test/${purpose}`, {method:'POST'}); if (token === generation) message(result.message); }
      catch (error) { if (token === generation) message(error.message); }
      finally { if (token === generation) $('testModelService').disabled = false; }
    };
    $('deleteModelService').onclick = () => deleteModel(selected).catch(error => message(error.message));
  }
  async function activate() {
    init();
    const token = ++generation;
    const saved = list.read('model-catalog');
    $('modelServiceSearch').value = saved.query;
    $('modelServicePurpose').value = saved.kind;
    editing = false; dirty = false;
    $('modelCatalogShell').hidden = false;
    $('modelEditorShell').hidden = true;
    list.loading($('modelServiceList'), '模型');
    try {
      const result = await request();
      if (token !== generation) return;
      items=result.items;
      $('modelServiceList').removeAttribute('aria-busy');
      const params = new URLSearchParams(location.search), id = params.get('model_id');
      if (id || params.get('create')) await openEditor(id, false);
      else {render(); list.restore('model-catalog');}
    } catch (error) {
      if (token !== generation) return;
      list.error($('modelServiceList'), '模型', error, activate);
      $('modelListFooter').hidden = true;
    } finally { if (token === generation) $('modelServiceList').removeAttribute('aria-busy'); }
  }
  function init() {
    if (initialized) return;
    initialized = true;
    $('modelServiceSearch').oninput = event => {list.remember('model-catalog', {query:event.target.value, page:1, scroll:0}); renderList();};
    $('modelServicePurpose').onchange = event => {list.remember('model-catalog', {kind:event.target.value, page:1, scroll:0}); renderList();};
    $('createModelResourceBtn').onclick = () => openEditor().catch(report);
    $('backToModelList').onclick = async () => {
      if (!await mayLeave()) return;
      dirty=false; editing=false; selected=null;
      list.route('model-catalog');
      await activate();
    };
    ['chat','embedding'].forEach(kind => $(`addModelService-${kind}`).onclick = async () => {
      if (!await mayLeave()) return;
      generation++; purpose=kind; dirty=false; render();
    });
  }
  window.ModelWorkspace = {activate, loadChoices, mayLeave, deactivate() {generation++;}};
})();
