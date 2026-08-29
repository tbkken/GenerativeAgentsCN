/** 两日通勤资源组合演示；只驱动演示 DOM，不参与正式实验运行。 */
(() => {
  const stage = document.getElementById('commuteDemoStage');
  const topTitle = document.getElementById('commuteTopTitle');
  const topActions = document.getElementById('commuteTopActions');
  const experimentNavigation = document.getElementById('experimentNavigation');
  const cursor = document.getElementById('demoCursor');
  const toast = document.getElementById('demoToast');
  const play = document.getElementById('autoDemo');
  const speed = document.getElementById('demoSpeed');
  const mapRevision = window.CommuteMap.revision;
  const brainRevision = {
    name: '跨日通勤 Brain',
    revisionId: 'brain-rev-commute-003',
    hash: '36bc7f1a…c91e',
  };
  let sharedMapMount = null;
  let replayTimer = 0;
  let index = 0;
  let playing = false;
  let timer = 0;
  let toastTimer = 0;

  const steps = [
    { title: '从实验中心新建草稿', caption: '实验是资源组合和运行容器。', target: '#newExperimentButton' },
    { title: '定义研究问题', caption: '先写清变量和观察目标。', target: '#wizardNext' },
    { title: '选择精确资源 Revision', caption: '地图和 Brain 都必须是已发布的不可变版本。', target: '#wizardNext' },
    { title: '确认隔离的实验草稿', caption: '只保存 Revision 引用，不复制资源编辑器。', target: '#createExperiment' },
    { title: '核对资源组合', caption: '在概览统一选择 Brain、地图和运行参数。', target: '[data-page="agents"]' },
    { title: '查看参与 Agent', caption: 'Agent 是实验副本；汽车只是工具。', target: '#editAgent' },
    { title: '配置 Agent 级感知', caption: '目标、位置、视野和注意力都属于 Agent。', target: '#saveAgent' },
    { title: '配置模型与运行', caption: '模型超时和重试有明确上限。', target: '#reviewPublish' },
    { title: '发布不可变快照', caption: 'Run 冻结 Brain 依赖闭包、地图和 Agent。', target: '#confirmLaunch' },
    { title: '查看统一事实回放', caption: '回放只消费 StepResult，不重新解释 LLM 文本。', target: '#timelinePlay' },
  ];

  const chip = (text, tone = 'teal') => `<span class="chip ${tone}">${text}</span>`;

  function setHubShell() {
    experimentNavigation.hidden = true;
    topTitle.innerHTML = '<strong>实验中心</strong>';
    topActions.innerHTML = '<button class="btn btn-primary" id="newExperimentButton">＋ 新建实验</button>';
  }

  function setExperimentShell(active = 'overview', running = false) {
    const completed = active === 'results';
    experimentNavigation.hidden = false;
    topTitle.innerHTML = `<div class="experiment-title-stack"><div class="experiment-title-line"><strong>两日通勤：迟到压力与出行方式</strong>${running ? '<span class="state-pill published">运行中</span>' : completed ? '<span class="state-pill published">已完成</span>' : '<span class="draft-pill">草稿</span>'}</div><div class="experiment-header-meta"><span>负责人：交通安全研究组</span><span>Draft Revision · exp-rev-001</span></div></div>`;
    topActions.innerHTML = completed
      ? '<button class="btn">复制为对照实验</button><button class="btn">导出快照</button>'
      : '<button class="btn">复制实验</button><button class="btn">保存草稿</button>';
    document.querySelectorAll('#experimentNavigation .nav-item').forEach((button) => {
      button.classList.toggle('page-subnav-active', button.dataset.page === active);
    });
    bindPageNav();
  }

  function bindPageNav() {
    const routes = { overview: 4, agents: 5, models: 7, results: 9 };
    document.querySelectorAll('[data-page]').forEach((button) => {
      button.onclick = () => {
        if (routes[button.dataset.page] !== undefined) go(routes[button.dataset.page], true);
      };
    });
  }

  function hub() {
    setHubShell();
    stage.innerHTML = `<section><div class="page-heading"><div><h1>实验中心</h1><p>组合已发布资源，创建、运行并比较可复现的 Agent 仿真实验。</p></div><button class="btn btn-primary" id="newExperimentButton">＋ 新建实验</button></div><div class="experiment-list"><article class="experiment-row"><div class="experiment-name"><span class="experiment-icon">⌁</span><div><strong>两日通勤：迟到压力与出行方式</strong><span>同一 Agent 在跨日压力下选择驾车或步行</span></div></div><div class="experiment-cell"><span>资源快照</span><strong>地图 v1 · Brain v3</strong></div><div class="experiment-cell hide-compact"><span>Agent / 最近运行</span><strong>1 / 已完成</strong></div><div class="experiment-actions">${chip('已完成')}<button class="btn btn-sm">打开</button></div></article></div></section>`;
    document.querySelectorAll('#newExperimentButton').forEach((button) => {
      button.onclick = () => go(1, true);
    });
  }

  function wizard(stepNumber) {
    hub();
    const content = {
      1: `<h3>这个实验要验证什么？</h3><div class="form-grid"><div class="field full"><label>实验名称</label><input class="control" value="两日通勤：迟到压力与出行方式" /></div><div class="field full"><label>实验目标</label><textarea class="control">观察同一 Agent 在不同起床时间下，是否会基于剩余时间选择开车或步行，并遵守红绿灯、门禁与停车规则。</textarea></div><div class="field"><label>负责人</label><input class="control" value="交通安全研究组" /></div><div class="field"><label>标签</label><input class="control" value="跨日行为、通勤" /></div></div>`,
      2: `<h3>选择 Brain、地图与人群 Revision</h3><div class="creation-resource-grid"><div class="resource-choice selected">${chip('已发布')}<div class="field"><label>Brain Revision</label><select class="control"><option>${brainRevision.name} · v3</option></select></div><span>${brainRevision.revisionId} · ${brainRevision.hash}</span></div><div class="resource-choice selected">${chip('已发布')}<div class="field"><label>地图 Revision</label><select class="control"><option>${mapRevision.name} · ${mapRevision.version}</option></select></div><span>${mapRevision.revisionId} · 不创建实验覆盖层</span></div><div class="resource-choice selected full">${chip('1 Agent')}<div class="field"><label>Crowd Revision</label><div class="crowd-list"><label class="crowd-option"><input type="checkbox" checked /> 单人通勤研究组 · v1（林晨）</label></div></div><span>导入后成为当前实验的 Agent 副本。</span></div></div>`,
      3: `<h3>确认实验草稿</h3><div class="create-summary"><div class="create-summary-row"><span>实验</span><strong>两日通勤：迟到压力与出行方式</strong></div><div class="create-summary-row"><span>Brain</span><strong>${brainRevision.name} · ${brainRevision.revisionId}</strong></div><div class="create-summary-row"><span>地图</span><strong>${mapRevision.name} · ${mapRevision.revisionId}</strong></div><div class="create-summary-row"><span>Agent</span><strong>林晨 · 1 人</strong></div><div class="create-summary-row"><span>初始状态</span><strong>草稿 · 未发布 · 不启动</strong></div></div><div class="wizard-note">实验只保存精确 Revision 引用。资源内容要变化时，请先在资源库发布新版本。</div>`,
    }[stepNumber];
    stage.insertAdjacentHTML('beforeend', `<div class="modal-backdrop"><div class="modal wide"><div class="modal-head"><div><h2>新建实验</h2><p>没有默认地图，也不会自动跟随资源的最新版本。</p></div><button class="close">×</button></div><div class="wizard-steps">${[1, 2, 3].map((n) => `<div class="wizard-step ${n === stepNumber ? 'active' : n < stepNumber ? 'done' : ''}"><i>${n < stepNumber ? '✓' : n}</i><span>${['定义实验', '选择资源', '确认创建'][n - 1]}</span></div>`).join('')}</div><div class="modal-body"><div class="wizard-panel active">${content}</div></div><div class="modal-foot">${stepNumber > 1 ? '<button class="btn" id="wizardBack">上一步</button>' : ''}<button class="btn btn-primary" id="${stepNumber === 3 ? 'createExperiment' : 'wizardNext'}">${stepNumber === 3 ? '创建并进入实验' : '下一步'}</button></div></div></div>`);
    document.getElementById(stepNumber === 3 ? 'createExperiment' : 'wizardNext').onclick = () => go(stepNumber === 3 ? 4 : index + 1, true);
    const back = document.getElementById('wizardBack');
    if (back) back.onclick = () => go(index - 1, true);
  }

  function composition() {
    setExperimentShell('overview');
    stage.innerHTML = `<section><div class="page-heading"><div><h1>实验概览</h1><p>在一个页面核对资源组合和运行定义；资源编辑留在全局工作区。</p></div><span class="revision-meta">Draft Revision · <code>exp-rev-001</code></span></div><div class="overview-grid"><main class="card experiment-definition"><div class="section-head"><div><h2>资源组合</h2><p>保存的是不可变 Revision 身份，不是资源内容副本。</p></div>${chip('完整')}</div><div class="form-grid"><div class="field"><label>Brain Revision</label><select class="control"><option>${brainRevision.name} · v3 · ${brainRevision.revisionId}</option></select></div><div class="field"><label>地图 Revision</label><select class="control"><option>${mapRevision.name} · ${mapRevision.version} · ${mapRevision.revisionId}</option></select></div></div><div class="source-lock" style="margin-top:14px"><span>⌾</span><div><strong>依赖身份已锁定</strong><small>Brain ${brainRevision.hash} · Map ${mapRevision.revisionId}</small></div></div><div class="world-map shared-world-map" style="margin-top:14px"><div id="worldSharedMap" class="commute-map-host"></div><span class="map-badge-fixed">只读预览 · ${mapRevision.revisionId}</span></div><button class="btn btn-primary btn-block" data-page="agents" style="margin-top:14px">下一步：参与 Agent</button></main><aside class="card card-pad"><h3 style="margin-top:0">边界说明</h3><ul class="principles"><li>地图内容在全局地图工作区编辑</li><li>Brain SOP 在全局 Brain 工作区编辑</li><li>发布新版本后回此处重新选择</li><li>实验没有地图覆盖层和私有 Brain</li></ul></aside></div></section>`;
    sharedMapMount = window.CommuteMap.render(document.getElementById('worldSharedMap'), { phase: 9 });
    bindPageNav();
  }

  function agents() {
    setExperimentShell('agents');
    stage.innerHTML = `<section><div class="page-heading"><div><h1>参与 Agent</h1><p>编辑当前实验副本的目标、初始位置、模型覆盖和感知上限。</p></div><button class="btn btn-primary">＋ 添加 Agent Revision</button></div><div class="agent-table"><div class="table-head"><span></span><span>Agent</span><span>目标</span><span>初始位置</span><span>状态</span><span>操作</span></div><div class="agent-row"><input class="checkbox" type="checkbox" checked/><div class="agent-person"><span class="agent-avatar">🧑🏻</span><div><strong>林晨</strong><span>agent.lin-chen · Crowd v1</span></div></div><div class="truncate">09:00 前到达公司，根据时间压力选择交通方式</div><div class="location">住宅 / 卧室</div><div>${chip('已启用')}</div><button class="btn btn-sm" id="editAgent">编辑</button></div></div><div class="notice" style="margin-top:14px">汽车是 Game Object/工具，不是第二个 Agent；其世界变化仍须通过 world-act 和 World Commit。</div></section>`;
    document.getElementById('editAgent').onclick = () => go(6, true);
  }

  function agentModal() {
    agents();
    stage.insertAdjacentHTML('beforeend', `<div class="modal-backdrop"><div class="modal wide agent-modal"><div class="modal-head"><div><h2>编辑 Agent · 林晨</h2><p>这些字段只作用于当前实验副本。</p></div><button class="close">×</button></div><div class="modal-body"><div class="form-grid"><div class="field full"><label>实验目标</label><textarea class="control">根据剩余时间选择步行或开车，并遵守交通信号。</textarea></div><div class="field"><label>初始位置</label><input class="control" value="住宅 / 卧室" /></div><div class="field"><label>模型覆盖</label><select class="control"><option>使用实验 Chat 模型</option></select></div><div class="field"><label>视野硬上限</label><div class="input-unit"><input class="control" value="10"/><span>格</span></div></div><div class="field"><label>注意力带宽</label><div class="input-unit"><input class="control" value="12"/><span>候选</span></div></div></div><section class="resource-section" style="margin-top:16px"><h3>持有工具</h3><article class="tool-card"><span class="tool-visual">🚙</span><div class="tool-copy"><strong>林晨的汽车</strong><span>Game Object · controller_agent_id 指向林晨</span><code>tool.car-01 · company.vehicle.enter</code></div>${chip('可交互')}</article></section></div><div class="modal-foot"><button class="btn">取消</button><button class="btn btn-primary" id="saveAgent">保存 Agent</button></div></div></div>`);
    document.getElementById('saveAgent').onclick = () => {
      showToast('Agent 配置已保存', '视野与注意力上限已写入 Agent 副本');
      setTimeout(() => go(7, false), 520);
    };
  }

  function modelAndRun() {
    setExperimentShell('models');
    stage.innerHTML = `<section><div class="page-heading"><div><h1>模型与运行</h1><p>模型连接和运行基础设施参数集中在这里；Brain 决定认知调用顺序。</p></div>${chip('服务已连接')}</div><div class="overview-grid"><main class="card experiment-definition"><div class="section-head"><div><h2>模型服务</h2><p>逻辑调用与物理尝试分别统计。</p></div></div><div class="form-grid"><div class="field"><label>Chat 模型</label><input class="control" value="Qwen3-32B" /></div><div class="field"><label>Embedding 模型</label><input class="control" value="text-embedding-v3" /></div><div class="field"><label>请求超时</label><div class="input-unit"><input class="control" value="120"/><span>秒</span></div></div><div class="field"><label>传输重试</label><div class="input-unit"><input class="control" value="3"/><span>次，最多 5</span></div></div></div><div class="section-head" style="margin-top:20px"><div><h2>运行参数</h2></div></div><div class="form-grid"><div class="field"><label>虚拟开始时间</label><input class="control" value="2026-08-31 07:30 +08:00" /></div><div class="field"><label>总步数</label><input class="control" value="156" /></div><div class="field"><label>每步时长</label><div class="input-unit"><input class="control" value="10"/><span>分钟</span></div></div><div class="field"><label>检查点间隔</label><div class="input-unit"><input class="control" value="12"/><span>步</span></div></div></div><button class="btn btn-primary btn-block launch-button" id="reviewPublish">预检并发布</button></main><aside class="card card-pad"><h3 style="margin-top:0">发布前冻结</h3><div class="checklist"><div class="check-item"><span class="check-mark">✓</span><div><strong>Brain 依赖闭包</strong><span>${brainRevision.revisionId}</span></div><span>通过</span></div><div class="check-item"><span class="check-mark">✓</span><div><strong>地图 Revision</strong><span>${mapRevision.revisionId}</span></div><span>通过</span></div><div class="check-item"><span class="check-mark">✓</span><div><strong>Agent 初始语义</strong><span>1 / 1</span></div><span>通过</span></div></div></aside></div></section>`;
    document.getElementById('reviewPublish').onclick = () => go(8, true);
  }

  function publishModal() {
    modelAndRun();
    stage.insertAdjacentHTML('beforeend', `<div class="modal-backdrop"><div class="modal wide"><div class="modal-head"><div><h2>发布并启动实验</h2><p>下面的资源身份和行为哈希会写入不可变 Run Manifest。</p></div><button class="close">×</button></div><div class="modal-body"><div class="create-summary"><div class="create-summary-row"><span>实验 Revision</span><strong>experiment:two-day-commute@v1</strong></div><div class="create-summary-row"><span>Brain 快照</span><strong>${brainRevision.revisionId} · ${brainRevision.hash}</strong></div><div class="create-summary-row"><span>地图快照</span><strong>${mapRevision.revisionId}</strong></div><div class="create-summary-row"><span>Agent</span><strong>agent.lin-chen · vision 10 · attention 12</strong></div><div class="create-summary-row"><span>事实合同</span><strong>Event(SPO) + structured_payload</strong></div></div></div><div class="modal-foot"><button class="btn">取消</button><button class="btn btn-primary" id="confirmLaunch">确认发布并启动</button></div></div></div>`);
    document.getElementById('confirmLaunch').onclick = () => {
      showToast('实验 v1 已完成', '正在打开同一 Run 的确定性回放');
      setTimeout(() => go(9, false), 520);
    };
  }

  const replayFrames = [
    { time: '周一 08:43', mode: 'car', progress: 0.08, event: '进入汽车并离开住宅', action: '驾驶', detail: 'AGENT_ACTED · tool.car-01' },
    { time: '周一 08:47', mode: 'car', progress: 0.36, event: '路口 A 红灯停车', action: '等待', detail: 'AGENT_WAITED · 红灯' },
    { time: '周一 08:55', mode: 'car', progress: 1, event: '停入 P03 并下车', action: '停车', detail: 'GAME_OBJECT_STATE_CHANGED' },
    { time: '周二 07:40', mode: 'walk', progress: 0.34, event: '进入行人等待区', action: '步行', detail: 'AGENT_MOVED' },
    { time: '周二 07:54', mode: 'walk', progress: 1, event: '提前抵达公司', action: '到达', detail: 'AGENT_ACTED' },
  ];

  function results() {
    setExperimentShell('results');
    stage.innerHTML = `<section><div class="page-heading"><div><h1>实验结果</h1><p>Run <code>run-commute-001</code> · Attempt 1 · StepResult 是唯一事实来源。</p></div>${chip('已完成')}</div><div class="result-tabs"><button class="result-tab active">仿真回放</button><button class="result-tab">Agent</button><button class="result-tab">运行诊断</button><button class="result-tab">结果与导出</button></div><div class="timeline-layout"><div class="card result-map"><div id="replaySharedMap" class="commute-map-host"></div><span class="cm-time-badge" id="mapTimeLabel">—</span><span class="replay-map-revision">${mapRevision.revisionId}</span></div><aside class="card timeline-stream"><div class="timeline-toolbar replay-sidebar-controls"><div class="timeline-controls"><button class="timeline-button" id="timelinePrev">‹</button><button class="timeline-button primary" id="timelinePlay">▶</button><button class="timeline-button" id="timelineNext">›</button></div><div class="timeline-current"><strong id="timelineTime">—</strong><span id="timelineStep">Step —</span></div><input id="timelineRange" type="range" min="0" max="${replayFrames.length - 1}" value="0"/></div><div class="replay-inspector"><h3>事实检查器</h3><dl><dt>动作</dt><dd id="replayAction">—</dd><dt>事件</dt><dd id="replayEvent">—</dd><dt>结构化负载</dt><dd id="replayPayload">—</dd></dl></div></aside></div></section>`;
    sharedMapMount = window.CommuteMap.render(document.getElementById('replaySharedMap'), { phase: 9 });
    let frame = 0;
    const stop = () => {
      clearInterval(replayTimer);
      replayTimer = 0;
      document.getElementById('timelinePlay').textContent = '▶';
    };
    const applyFrame = (next) => {
      frame = Math.max(0, Math.min(replayFrames.length - 1, next));
      const item = replayFrames[frame];
      document.getElementById('timelineRange').value = String(frame);
      document.getElementById('timelineTime').textContent = item.time;
      document.getElementById('timelineStep').textContent = `Step ${frame + 1} / ${replayFrames.length}`;
      document.getElementById('mapTimeLabel').textContent = item.time;
      document.getElementById('replayAction').textContent = item.action;
      document.getElementById('replayEvent').textContent = item.event;
      document.getElementById('replayPayload').textContent = item.detail;
      sharedMapMount.setPlayback({ mode: item.mode, progress: item.progress });
    };
    document.getElementById('timelinePlay').onclick = () => {
      if (replayTimer) return stop();
      document.getElementById('timelinePlay').textContent = 'Ⅱ';
      replayTimer = setInterval(() => frame >= replayFrames.length - 1 ? stop() : applyFrame(frame + 1), 800);
    };
    document.getElementById('timelinePrev').onclick = () => { stop(); applyFrame(frame - 1); };
    document.getElementById('timelineNext').onclick = () => { stop(); applyFrame(frame + 1); };
    document.getElementById('timelineRange').oninput = (event) => { stop(); applyFrame(Number(event.target.value)); };
    applyFrame(0);
    bindPageNav();
  }

  function render() {
    clearTimeout(timer);
    clearInterval(replayTimer);
    replayTimer = 0;
    sharedMapMount?.destroy?.();
    sharedMapMount = null;
    const screens = [hub, () => wizard(1), () => wizard(2), () => wizard(3), composition, agents, agentModal, modelAndRun, publishModal, results];
    screens[index]();
    document.getElementById('demoCounter').textContent = `实验配置 · ${index + 1}/${steps.length}`;
    document.getElementById('demoTitle').textContent = steps[index].title;
    document.getElementById('demoCaption').textContent = steps[index].caption;
    document.getElementById('demoProgress').style.width = `${((index + 1) / steps.length) * 100}%`;
    requestAnimationFrame(() => moveCursor(steps[index].target));
    if (playing) schedule();
  }

  function moveCursor(selector) {
    const target = document.querySelector(selector);
    if (!target) return cursor.classList.remove('visible');
    const box = target.getBoundingClientRect();
    cursor.style.left = `${box.left + Math.min(box.width * 0.72, box.width - 8)}px`;
    cursor.style.top = `${box.top + Math.min(box.height * 0.64, box.height - 6)}px`;
    cursor.classList.add('visible', 'clicking');
    setTimeout(() => cursor.classList.remove('clicking'), 480);
  }

  function showToast(head, body) {
    document.getElementById('toastTitle').textContent = head;
    document.getElementById('toastCopy').textContent = body;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), 1700);
  }

  function schedule() {
    clearTimeout(timer);
    if (index >= steps.length - 1) {
      playing = false;
      play.textContent = '▶';
      return;
    }
    timer = setTimeout(() => go(index + 1, false), 2400 * Number(speed.value));
  }

  function go(next, manual) {
    index = Math.max(0, Math.min(steps.length - 1, next));
    if (manual && playing) {
      playing = false;
      play.textContent = '▶';
    }
    render();
  }

  document.getElementById('previousStep').onclick = () => go(index - 1, true);
  document.getElementById('nextStep').onclick = () => go(index + 1, true);
  document.getElementById('restartDemo').onclick = () => go(0, true);
  play.onclick = () => {
    playing = !playing;
    play.textContent = playing ? 'Ⅱ' : '▶';
    if (playing) schedule(); else clearTimeout(timer);
  };
  speed.onchange = () => { if (playing) schedule(); };
  render();
  if (new URLSearchParams(location.search).get('autoplay') === '1') setTimeout(() => play.click(), 700);
})();
