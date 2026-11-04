/* SimWorld renders the referenced level. The Run owns the committed facts. */
window.GAStreetReplayPlayer = class extends window.GAReplayPlayer {
  constructor(options) {
    super(options);
    this.container = options.container;
    this.isTimelineVisible = options.isTimelineVisible;
    this.onSelection = options.onSelection;
  }

  _createGame(manifest) {
    this.canvas.hidden = true;
    this.container.classList.add('street-result-map');
    const host = document.createElement('div');
    host.className = 'street-replay';
    const scene = document.createElement('div');
    scene.className = 'street-replay-scene';
    this.waitingForStep=manifest.available_step===0;
    if (this.waitingForStep) {
      const waiting=document.createElement('p');
      waiting.className='street-replay-waiting';
      waiting.setAttribute('role','status');
      waiting.textContent='尚无已提交的步骤。首个 Step 提交后，时间线将自动显示回放。';
      scene.append(waiting);
    }
    const facts = document.createElement('details');
    const summary = document.createElement('summary');
    this.sceneId=manifest.world.definition.simworld_scene_id || null;
    summary.textContent = this.sceneId ? `SimWorld 场景 · ${this.sceneId} ｜ 查看本步空间事实` : '本步空间事实';
    facts.open=!this.sceneId;
    const note = document.createElement('p');
    note.textContent = '位置、朝向和动作来自本 Run 已提交的步骤。';
    this.factList = document.createElement('ul');
    facts.append(summary, note, this.factList);
    facts.ontoggle = () => { if (facts.open) this.renderFacts(); };
    this.activities = document.createElement('ul');
    this.activities.className = 'street-activities';
    this.activities.setAttribute('aria-label','本步人物活动');
    host.append(scene, this.activities, facts);
    this.container.prepend(host);
    this.host = host;
    this.facts = facts;
    this.view = new window.SceneMapViewer(scene);
    this.setSceneVisible(this.isTimelineVisible());
  }

  setSceneVisible(visible) {
    if (!this.view || visible === this.sceneVisible) return;
    this.sceneVisible = visible;
    if (!visible) {
      super.pause();
      this.view.leaveScene();
      return;
    }
    if (!this.availableStep) return;
    this.openScene();
    if (this.currentStep) this.seek(this.currentStep).catch(error=>this._fail(error));
  }

  openScene() {
    this.waitingForStep=false;
    this.initialOverview=true;
    this.view.setWorld(this.manifest.world, {
      sessionBase: `/api/studio/simworld/runs/${encodeURIComponent(this.runId)}/sessions`,
      replay: {runId:this.runId, names:new Map(this.manifest.agents.map(a=>[a.agent_key,a.display_name])),
        leaveFollow:()=>this.onSelection?.(null),
        retry:()=>{this.initialOverview=true;this.seek(this.currentStep || 1).catch(error=>this._fail(error));}},
      actions:[{label:'人物全景',run:()=>{
        if (this.followedAgentKey) this.onSelection?.(null);
        this.showOverview();
      }}],
    });
  }

  updateCamera() {
    if (!this.sceneVisible) return;
    const pose=this.followedAgentKey ? window.GAStreetCamera.follow(this.view.sceneCameras,
      this.currentStepFact,this.followedAgentKey,this.followYaw,this.followPose) : null;
    if (pose)this.followYaw=pose.yaw_deg;
    this.followPose=pose;
    this.view.setReplayCamera(pose);
  }

  followAgent(key) {
    if (this.followedAgentKey === key) return;
    this.followYaw=key ? this.view?.pose?.yaw_deg : null;
    this.followPose=null;
    this.followedAgentKey = key;
    this.updateCamera();
  }

  showOverview() {
    const pose=window.GAStreetCamera.overview(this.view.sceneCameras,this.currentStepFact);
    if (pose)this.view.setOverviewCamera(pose);
  }

  async seek(stepNo, options) {
    if ((this.timer || this.playRequest) && !options?.playback && !options?.startPlayback) super.pause();
    if (stepNo===1 && this.currentStep>1 && !options?.playback) this.playedOpening=false;
    this.view?.cancelReplayFrame();
    return super.seek(stepNo, options);
  }

  async _preparePlayback(start) {
    if (!this.sceneId || this.view?.active===false) return super._preparePlayback(start);
    if (start<this.availableStep && (start!==1 || this.playedOpening)) {
      return super._preparePlayback(start);
    }
    const request=this.playRequest;
    if (this.availableStep===1 && this.playedOpening) {
      // Re-arm a single-step run: the renderer deduplicates identical submissions.
      await this.seek(1,{startPlayback:true});
      if (this.playRequest!==request)return;
    }
    await this.seek(1,{startPlayback:true,playback:true});
    if (this.playRequest===request && this.availableStep===1) super.pause();
  }

  // Each committed 3D segment already consumes its display time on the render host.
  _playbackDelay() { return this.sceneId && this.view?.active!==false ? 0 : super._playbackDelay(); }

  pause(options) {
    const wasPlaying=Boolean(this.timer || this.playRequest);
    super.pause(options);
    if (wasPlaying && this.sceneVisible && this.animatingStep) {
      this.seek(this.animatingStep).catch(error=>this._fail(error));
    }
  }

  async _renderStep(step, options) {
    if (this.sceneVisible && this.waitingForStep) this.openScene();
    this.updateCamera();
    if (this.sceneVisible && this.initialOverview) {
      this.initialOverview=false;
      if (!this.followedAgentKey)this.showOverview();
    }
    const request=this.seekRequest;
    const publish=()=>{
      if (request!==this.seekRequest)return;
      this.activities.replaceChildren();
      for (const agent of step.agents) {
        const action=agent.action||{}, label=document.createElement('li');
        const name=this.agentDefinitions.get(agent.agent_key)?.display_name || agent.agent_key;
        label.textContent=name+' · '+(action.description || [action.predicate,action.object].filter(Boolean).join(' ') || action.kind || '未记录活动描述');
        this.activities.append(label);
      }
      if (this.facts.open) this.renderFacts();
      this.onStep({step, availableStep:this.availableStep});
    };
    if (!this.sceneVisible || !this.sceneId || this.view?.active===false) {publish();return;}
    const playbackMs=options?.playback && (this.timer || this.playRequest)?Math.round(Math.min(3000,Math.max(600,1500/this.speed))):0;
    if (playbackMs && step.step_no===1) this.playedOpening=true;
    this.animatingStep=playbackMs?step.step_no:null;
    const shown=await this.view.presentReplayStep(step.step_no,playbackMs,publish);
    if (request!==this.seekRequest || !this.facts)return;
    this.animatingStep=null;
    if (!shown) {
      if (playbackMs && step.step_no===1) this.playedOpening=false;
      super.pause();publish();
    }
  }

  renderFacts() {
    const step = this.currentStepFact;
    this.factList.replaceChildren();
    const append = text => {
      const row = document.createElement('li');
      row.textContent = text;
      this.factList.append(row);
    };
    if (!step) { append('等待首个已提交 Step'); return; }
    for (const agent of step.agents) {
      const name = this.agentDefinitions.get(agent.agent_key)?.display_name || agent.agent_key;
      const position = agent.coord.join(', ');
      const trail = (agent.path || []).map(point => `[${point.join(', ')}]`).join(' → ');
      const clip = agent.action?.presentation_clip;
      append(`${name} · 位置 [${position}]${trail ? ' · 已走路径 ' + trail : ''}${clip ? ' · 已提交动画 ' + clip : ''}`);
    }
    for (const [id,state] of Object.entries(this._renderWorldState(step.step_no))) {
      append(`${id} · ${JSON.stringify(state)}`);
    }
  }

  destroy() {
    this.view?.destroy();
    this.view = null;
    this.sceneVisible = false;
    this.host?.remove();
    this.host = this.facts = this.factList = this.activities = null;
    this.animatingStep=null;
    this.container.classList.remove('street-result-map');
    this.playedOpening=false;
    this.followedAgentKey=null;this.followYaw=null;this.followPose=null;
    this.canvas.hidden = false;
    super.destroy();
  }
};
