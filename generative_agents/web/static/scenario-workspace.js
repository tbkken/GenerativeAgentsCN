(() => {
  'use strict';

  const state = {
    experimentId: null,
    draft: null,
    assembly: null,
    templates: [],
    bundlesByRevision: new Map(),
    active: false,
  };

  const $ = id => document.getElementById(id);
  const escapeHtml = value => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');

  async function api(path, options = {}) {
    const response = await fetch(`/api/v1${path}`, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.error?.message || `HTTP ${response.status}`);
      error.details = payload.error;
      throw error;
    }
    return payload;
  }

  function notify(message, title = '场景装配') {
    window.dispatchEvent(new CustomEvent('scenario-workspace:toast', { detail: { message, title } }));
  }

  function report(error) {
    window.dispatchEvent(new CustomEvent('scenario-workspace:error', { detail: { error } }));
  }

  function selectedExperimentId() {
    return new URL(window.location.href).searchParams.get('experiment_id');
  }

  async function load() {
    const experimentId = selectedExperimentId();
    if (!experimentId) throw new Error('请先选择实验');
    state.experimentId = experimentId;
    const [draft, assembly, templates, bundles] = await Promise.all([
      api(`/experiments/${experimentId}/draft`),
      api(`/experiments/${experimentId}/draft/capability-assembly`),
      api('/scenario-templates'),
      api('/capability-bundles?page_size=100'),
    ]);
    if (state.experimentId !== experimentId) return;
    state.draft = draft;
    state.assembly = assembly;
    state.templates = templates.items || [];
    state.bundlesByRevision = new Map(
      (bundles.items || [])
        .filter(item => item.current_published)
        .map(item => [item.current_published.id, item])
    );
    render();
  }

  function render() {
    renderMode();
    renderTemplates();
    renderAssembly();
  }

  function renderMode() {
    const mode = state.assembly?.extension?.mode || 'LEGACY_TOWN';
    const host = $('scenarioMode');
    host.classList.toggle('legacy', mode === 'LEGACY_TOWN');
    host.querySelector('strong').textContent = mode === 'CAPABILITY_COMPOSED'
      ? '能力组合运行时'
      : '斯坦福小镇兼容运行时';
  }

  function renderTemplates() {
    const select = $('scenarioTemplateSelect');
    const previous = select.value;
    select.innerHTML = state.templates.length
      ? state.templates.map(item => {
          const revision = item.current_published;
          return `<option value="${escapeHtml(revision?.id || '')}">${escapeHtml(item.name)} · v${escapeHtml(revision?.revision_no || '—')}</option>`;
        }).join('')
      : '<option value="">没有可用模板</option>';
    if (previous && [...select.options].some(option => option.value === previous)) select.value = previous;
    renderSelectedTemplate();
  }

  function selectedTemplate() {
    const revisionId = $('scenarioTemplateSelect').value;
    return state.templates.find(item => item.current_published?.id === revisionId) || null;
  }

  function renderSelectedTemplate() {
    const template = selectedTemplate();
    const contract = template?.current_published?.contract;
    $('scenarioTemplateSummary').innerHTML = contract
      ? `<strong>${escapeHtml(contract.name)}</strong><br>${escapeHtml(contract.summary)}<br><code>${escapeHtml(template.template_key)} · ${escapeHtml(template.current_published.contract_hash.slice(0, 12))}</code>`
      : '选择模板后查看它需要的角色和能力。';
    if (contract) {
      $('scenarioDuration').value = contract.blueprint.clock.duration_ms;
      $('scenarioSnapshotInterval').value = contract.blueprint.clock.snapshot_interval_ms;
    }
    const agents = state.draft?.definition?.agents || [];
    $('scenarioActorSlots').innerHTML = (contract?.actor_slots || []).map(slot => {
      const options = agents.filter(agent => agent.enabled).map(agent =>
        `<option value="${escapeHtml(agent.agent_key)}">${escapeHtml(agent.name)} · ${escapeHtml(agent.agent_key)}</option>`
      ).join('');
      const icon = slot.role === 'DRIVER' ? '🚗' : slot.role === 'PEDESTRIAN' ? '🚶' : '●';
      return `<div class="scenario-slot" data-slot-key="${escapeHtml(slot.slot_key)}">
        <span class="scenario-slot-icon">${icon}</span>
        <div><label>${escapeHtml(slot.name)} <code>${escapeHtml(slot.slot_key)}</code></label><select class="control" data-slot-select>${options}</select><small>${escapeHtml(slot.description)}</small></div>
      </div>`;
    }).join('');
    const selects = [...document.querySelectorAll('[data-slot-select]')];
    selects.forEach((selectNode, index) => {
      if (agents[index]) selectNode.value = agents[index].agent_key;
    });
  }

  function renderAssembly() {
    const extension = state.assembly?.extension;
    if (!extension) return;
    $('scenarioAssemblyJson').value = JSON.stringify(extension, null, 2);
    const mounts = extension.capability_mounts || [];
    const enabled = mounts.filter(item => item.enabled !== false);
    const inheritedTasks = (state.assembly?.schedule?.tasks || []).filter(
      item => item.source_kind && item.source_kind !== 'SCENARIO_MOUNT'
    );
    const channels = new Set([...enabled.flatMap(item => [
      ...Object.values(item.input_bindings || {}),
      ...Object.values(item.output_bindings || {}),
    ]), ...inheritedTasks.flatMap(item => [
      ...Object.values(item.input_bindings || {}),
      ...Object.values(item.output_bindings || {}),
    ])]);
    $('scenarioChainSummary').innerHTML = extension.mode === 'LEGACY_TOWN'
      ? '<span>兼容模式：不加载能力组合场景</span>'
      : `<span>${extension.actors.length} 个物理角色</span><span>${extension.tool_instances.length} 个工具实例</span><span>${enabled.length} 个显式挂载</span><span>${inheritedTasks.length} 个资产继承任务</span><span>${channels.size} 条场景通道</span>`;
    const explicitCards = enabled.map(mount => {
      const bundle = state.bundlesByRevision.get(mount.capability_bundle_revision_id);
      const inputs = Object.entries(mount.input_bindings || {}).map(([port, channel]) =>
        `<span class="scenario-channel">${escapeHtml(port)} ← ${escapeHtml(channel)}</span>`
      ).join('');
      const outputs = Object.entries(mount.output_bindings || {}).map(([port, channel]) =>
        `<span class="scenario-channel output">${escapeHtml(port)} → ${escapeHtml(channel)}</span>`
      ).join('');
      return `<article class="scenario-mount"><div><strong>${escapeHtml(bundle?.name || mount.mount_key)}</strong><code>${escapeHtml(mount.mount_key)}</code></div><div class="scenario-channel-list">${inputs}${outputs || '<span class="scenario-channel output">内部输出</span>'}</div><span class="scenario-mount-state">可调度</span></article>`;
    });
    const inheritedCards = inheritedTasks.map(task => {
      const inputs = Object.entries(task.input_bindings || {}).map(([port, channel]) =>
        `<span class="scenario-channel">${escapeHtml(port)} ← ${escapeHtml(channel)}</span>`
      ).join('');
      const outputs = Object.entries(task.output_bindings || {}).map(([port, channel]) =>
        `<span class="scenario-channel output">${escapeHtml(port)} → ${escapeHtml(channel)}</span>`
      ).join('');
      const sourceLabel = task.source_kind === 'SPATIAL_ASSET_ATTACHMENT' ? '画块继承' : '工具继承';
      return `<article class="scenario-mount inherited"><div><strong>${escapeHtml(task.capability_name || task.task_key)}</strong><code>${escapeHtml(task.source_ref)} · ${sourceLabel}</code></div><div class="scenario-channel-list">${inputs}${outputs || '<span class="scenario-channel output">内部输出</span>'}</div><span class="scenario-mount-state">${escapeHtml(task.trigger)}</span></article>`;
    });
    const cards = [...explicitCards, ...inheritedCards];
    $('scenarioMountList').innerHTML = cards.length ? cards.join('') : '<div class="empty-state"><strong>尚未装配能力场景</strong><span>选择模板即可生成全部配置。</span></div>';
    renderSchedule();
  }

  function renderSchedule() {
    const extension = state.assembly?.extension || {};
    const schedule = state.assembly?.schedule || {};
    const clock = extension.clock || {};
    const stats = [
      ['物理 tick', clock.base_tick_ms ? Math.ceil(clock.duration_ms / clock.base_tick_ms).toLocaleString() : '—'],
      ['能力执行', Number(schedule.total_executions || 0).toLocaleString()],
      ['LLM 决策', Number(schedule.estimated_llm_decisions || 0).toLocaleString()],
      ['结果快照', clock.snapshot_interval_ms ? Math.ceil(clock.duration_ms / clock.snapshot_interval_ms).toLocaleString() : '—'],
    ];
    $('scenarioScheduleStats').innerHTML = stats.map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join('');
  }

  async function applyTemplate() {
    const template = selectedTemplate();
    if (!template) throw new Error('请选择场景模板');
    const actorBindings = {};
    document.querySelectorAll('[data-slot-key]').forEach(row => {
      actorBindings[row.dataset.slotKey] = row.querySelector('[data-slot-select]').value;
    });
    if (new Set(Object.values(actorBindings)).size !== Object.keys(actorBindings).length) {
      throw new Error('每个物理角色必须绑定不同的 Agent');
    }
    const payload = {
      lock_version: state.assembly.lock_version,
      actor_bindings: actorBindings,
      clock_overrides: {
        duration_ms: Number($('scenarioDuration').value),
        snapshot_interval_ms: Number($('scenarioSnapshotInterval').value),
      },
      mount_parameter_overrides: {},
    };
    await api(`/experiments/${state.experimentId}/draft/scenario-templates/${template.current_published.id}/apply`, {
      method: 'POST', body: JSON.stringify(payload),
    });
    await refreshAfterMutation();
    notify('模板已生成地图、工具、角色、能力挂载和通道配置。');
  }

  async function useLegacy() {
    await api(`/experiments/${state.experimentId}/draft/capability-assembly`, {
      method: 'PUT',
      body: JSON.stringify({ lock_version: state.assembly.lock_version, extension: { mode: 'LEGACY_TOWN' } }),
    });
    await refreshAfterMutation();
    notify('已切回斯坦福小镇兼容运行时。');
  }

  async function saveJson() {
    let extension;
    try { extension = JSON.parse($('scenarioAssemblyJson').value); }
    catch (error) { throw new Error(`JSON 无法解析：${error.message}`); }
    await api(`/experiments/${state.experimentId}/draft/capability-assembly`, {
      method: 'PUT',
      body: JSON.stringify({ lock_version: state.assembly.lock_version, extension }),
    });
    await refreshAfterMutation();
    notify('高级场景配置已保存。');
  }

  async function validate() {
    const report = await api(`/experiments/${state.experimentId}/draft/capability-assembly/validate`, { method: 'POST' });
    const host = $('scenarioValidation');
    host.hidden = false;
    host.className = `scenario-validation ${report.valid ? 'valid' : 'invalid'}`;
    host.innerHTML = report.valid
      ? `<strong>执行链有效</strong><div>${report.schedule.total_executions.toLocaleString()} 次能力执行，预计 ${report.schedule.estimated_llm_decisions.toLocaleString()} 次 LLM 决策。</div>`
      : `<strong>执行链不可发布</strong><ul>${report.errors.map(item => `<li><code>${escapeHtml(item.code)}</code> ${escapeHtml(item.message)}</li>`).join('')}</ul>`;
  }

  function toggleSaveTemplate(open) {
    const panel = $('scenarioSaveTemplatePanel');
    panel.hidden = !open;
    if (open) {
      $('scenarioTemplateName').value = `${state.draft?.name || '实验'}场景模板`;
      $('scenarioTemplateName').focus();
    }
  }

  async function saveAsTemplate() {
    if (state.assembly?.extension?.mode !== 'CAPABILITY_COMPOSED') {
      throw new Error('请先装配能力场景，再保存为模板');
    }
    const name = $('scenarioTemplateName').value.trim();
    if (!name) throw new Error('请输入模板名称');
    const created = await api(`/experiments/${state.experimentId}/draft/scenario-templates`, {
      method: 'POST',
      body: JSON.stringify({
        name,
        description: $('scenarioTemplateDescription').value.trim(),
      }),
    });
    const draft = created.current_draft;
    await api(`/scenario-templates/${created.id}/draft/publish`, {
      method: 'POST',
      body: JSON.stringify({ draft_revision_id: draft.id, lock_version: draft.lock_version }),
    });
    const templates = await api('/scenario-templates');
    state.templates = templates.items || [];
    toggleSaveTemplate(false);
    renderTemplates();
    const saved = state.templates.find(item => item.id === created.id);
    if (saved?.current_published) $('scenarioTemplateSelect').value = saved.current_published.id;
    renderSelectedTemplate();
    notify('当前装配已校验并发布为可复用场景模板。');
  }

  async function refreshAfterMutation() {
    const [draft, assembly] = await Promise.all([
      api(`/experiments/${state.experimentId}/draft`),
      api(`/experiments/${state.experimentId}/draft/capability-assembly`),
    ]);
    state.draft = draft;
    state.assembly = assembly;
    render();
    window.dispatchEvent(new CustomEvent('scenario-workspace:experiment-draft', {
      detail: { experimentId: state.experimentId, draft },
    }));
  }

  function bind() {
    if (state.active) return;
    state.active = true;
    $('scenarioTemplateSelect').addEventListener('change', renderSelectedTemplate);
    $('scenarioApplyTemplate').addEventListener('click', () => applyTemplate().catch(report));
    $('scenarioUseLegacy').addEventListener('click', () => useLegacy().catch(report));
    $('scenarioSaveJson').addEventListener('click', () => saveJson().catch(report));
    $('scenarioValidate').addEventListener('click', () => validate().catch(report));
    $('scenarioOpenSaveTemplate').addEventListener('click', () => toggleSaveTemplate(true));
    $('scenarioCancelSaveTemplate').addEventListener('click', () => toggleSaveTemplate(false));
    $('scenarioConfirmSaveTemplate').addEventListener('click', () => saveAsTemplate().catch(report));
  }

  window.ScenarioWorkspace = {
    async activate() {
      bind();
      await load();
    },
  };
})();
