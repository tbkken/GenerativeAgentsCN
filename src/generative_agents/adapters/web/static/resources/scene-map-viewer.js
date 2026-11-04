(function(){
  'use strict';
  const replayConflicts=new Map([
    ['This provider has no record of the original Run scene; historical UE view unavailable','SimWorld 没有此 Run 的原始场景记录。'],
    ['The original Run scene content or roster is unavailable for this replay','SimWorld 无法提供此 Run 原有的场景或人物。'],
  ]);
  function validPose(p) {
    return p && Array.isArray(p.position) && p.position.length===3 && p.position.every(Number.isFinite)
      && [p.yaw_deg,p.pitch_deg,p.fov_deg].every(Number.isFinite);
  }
  class SceneMapViewer {
    replayCaptureWidth(previous=0) {
      const width=this.stage?.getBoundingClientRect().width || 0;
      if (!width)return previous || 640;
      // Compare the displayed image in CSS pixels with the midpoints between tiers.
      // A 48 px margin on each side keeps splitter drags and scrollbar changes stable.
      const medium=800+(previous===640?48:previous>640?-48:0);
      const large=1120+(previous===1280?-48:previous>0?48:0);
      return width>=large?1280:width>=medium?960:640;
    }
    scheduleReplayResize(generation) {
      if (generation!==this.generation || !this.replay)return;
      clearTimeout(this.captureTimer);
      this.captureTimer=setTimeout(()=>this.resizeReplayCapture(generation),220);
    }
    async resizeReplayCapture(generation) {
      if (generation!==this.generation || !this.replay || !this.url || !this.ready || !this.canControl || this.resizingCapture)return;
      const width=this.replayCaptureWidth(this.captureWidth);
      if (width===this.captureWidth)return;
      this.resizingCapture=true;
      try {
        const data=await json(this.url+'/capture-size',{width,height:width*9/16});
        if (generation!==this.generation)return;
        this.captureWidth=width;this.serverCaptureWidth=data.capture_size?.width;
        this.captureError='';
      } catch(error) {
        if (generation===this.generation) {
          this.captureError='画面分辨率切换失败 · '+error.message;
          this.showLiveStatus();
        }
      } finally {
        if (generation===this.generation) {
          this.resizingCapture=false;
          // An observer notification may have arrived while this POST was in flight.
          const latest=this.replayCaptureWidth(this.captureWidth);
          if (latest!==width && latest!==this.captureWidth)this.scheduleReplayResize(generation);
        }
      }
    }
    static move(pose, keys, dt) {
    const forward=Number(keys.has('KeyW'))-Number(keys.has('KeyS'));
    const right=Number(keys.has('KeyD'))-Number(keys.has('KeyA'));
    const up=Number(keys.has('KeyE'))-Number(keys.has('KeyQ'));
    const norm=Math.hypot(forward,right,up);
    if (!norm) return false;
    const yaw=pose.yaw_deg*Math.PI/180, pitch=pose.pitch_deg*Math.PI/180;
    const step=dt*(keys.has('ShiftLeft')||keys.has('ShiftRight')?16:6)/norm;
    pose.position[0]+=step*(forward*Math.cos(pitch)*Math.cos(yaw)-right*Math.sin(yaw));
    pose.position[1]+=step*(forward*Math.cos(pitch)*Math.sin(yaw)+right*Math.cos(yaw));
    pose.position[2]+=step*(up+forward*Math.sin(pitch));
    return true;
    }
    constructor(root) { this.root=root;this.generation=0;this.readonly=true;this.changed=false;this.keys=new Set(); }
    setWorld(world,{sessionBase=null,actions=[],replay=null}={}) {
      this.replayPose=null;this.sceneCameras={};
      this.leaveScene();this.world=world;this.definition=world.definition;
      this.sessionBase=this.definition.simworld_scene_id?sessionBase:null;
      this.replay=replay;
      this.actions=actions;this.agentIds=new Set();
      this.render();
      if (this.sessionBase) this.connect();
    }
    setReadOnly() { this.readonly=true; }
    refreshSkillCatalog() { return Promise.resolve(); }
    resize() {}
    fit() {}
    getWorld() { return structuredClone(this.world); }
    render() {
      this.controlSize?.disconnect();
      this.aspect=null;
      const live=Boolean(this.sessionBase);
      this.root.innerHTML=live ? `<section class="scene-viewer scene-viewer-live" aria-label="地图视角">
        <div class="scene-surface"><div class="scene-stage" tabindex="0" aria-label="实时场景。按操作说明查看键盘控制">
          <img data-live alt="UE 实时场景" hidden>
          <div class="scene-feedback" data-feedback><span role="status" data-status></span><button type="button" data-reconnect hidden>重连</button></div>
        </div></div>
        <div class="scene-controls" aria-label="场景相机">
          <div class="scene-navigation" data-actions></div>
          <div class="scene-views" role="group" aria-label="地图机位" data-presets></div>
          <div class="scene-views" role="group" aria-label="智能体初始视角" data-agents hidden></div>
          <button type="button" data-return hidden>返回自由视角</button>
          <div class="scene-utilities"><details><summary aria-label="查看相机操作说明">操作说明</summary><p>点画面后移动鼠标环视，滚轮变焦，Esc 释放鼠标。自由视角可用 WASD 漫游、Q/E 升降。<span data-agent-help hidden>智能体视角在其实验初始位置转头观察。</span></p></details><button type="button" data-fullscreen>全屏</button></div>
        </div></section>`
        : `<section class="scene-viewer scene-unavailable"><div><strong data-name></strong>
          <p role="status" data-status></p><div class="scene-controls" data-actions></div></div></section>`;
      const find=k=>this.root.querySelector(`[data-${k}]`);
      this.stage=this.root.querySelector('.scene-stage'); this.live=find('live');
      this.status=find('status');this.feedback=find('feedback');this.presets=find('presets');this.agents=find('agents');
      this.returnButton=find('return');this.retry=find('reconnect');
      this.agentHelp=find('agent-help');
      for (const action of this.actions) {
        if(live && action.unavailableOnly) continue;
        const button=document.createElement('button');button.type='button';button.textContent=action.label;
        button.onclick=action.run;find('actions').append(button);
      }
      if (!live) find('name').textContent=this.world.world_name;
      if (!live) {
        this.status.textContent='此地图没有关联 SimWorld 在线场景。';
      } else {
        this.retry.onclick=()=>{this.connect();this.replay?.retry();};
        if (this.returnButton) this.returnButton.onclick=()=>this.chooseView('free','');
        const fullscreen=find('fullscreen'), card=this.root.firstElementChild;
        this.controlSize=new ResizeObserver(entries=>{
          const height=entries[0].target.getBoundingClientRect().height;
          card.style.setProperty('--scene-controls-height',Math.ceil(height)+'px');
        });
        this.controlSize.observe(card.querySelector('.scene-controls'));
        fullscreen.hidden=!document.fullscreenEnabled;
        fullscreen.onclick=async()=>{
          try { if(document.fullscreenElement===card) await document.exitFullscreen(); else await card.requestFullscreen(); }
          catch { this.show('此窗口无法进入全屏。'); }
        };
        card.onfullscreenchange=()=>{fullscreen.textContent=document.fullscreenElement===card?'退出全屏':'全屏';};
        this.live.onload=()=>this.presentFrame();
      }
    }
    show(message) {
      this.status.textContent=message;
      if (this.feedback) this.feedback.hidden=!message;
    }
    setAspect(w,h) {
      if (w>0 && h>0 && this.aspect!==w/h) {
        this.aspect=w/h;this.stage?.style?.setProperty('--scene-aspect',String(this.aspect));
      }
    }
    presentFrame() {
      if(!this.ready || !this.frameStarted || !this.live.naturalWidth || !this.live.naturalHeight)return;
      this.setAspect(this.live.naturalWidth,this.live.naturalHeight);
      if(this.hasLiveFrame)return;
      this.hasLiveFrame=true;this.live.hidden=false;
      this.syncReplayCamera();
      this.showLiveStatus();
      this.paintViews();
    }
    showLiveStatus() {
      const switching=this.waitingView?.type==='replay';
      this.show(this.viewError || this.captureError || (!this.canControl?'当前仅可观看。':switching?'正在切换到本步人物视角…':this.replayNotice||''));
    }
    async connect() {
      this.leaveScene();const generation=this.generation;
      const base=this.sessionBase;
      if (!base) return;
      this.active=true;this.retry.disabled=true;this.retry.hidden=true;this.show('正在打开场景…');
      const before=this.opening;
      try {
        if (before) await before.catch(()=>{});
        if (this.closing) await this.closing;
        if (generation!==this.generation) return;
        this.captureWidth=this.replay?this.replayCaptureWidth():0;
        const options=this.replay?{capture_size:{width:this.captureWidth,height:this.captureWidth*9/16}}:{};
        const open=this.opening=json(base,options).then(async data=>{
          if (data.session_id && generation!==this.generation) {
            await this.release(base+'/'+encodeURIComponent(data.session_id));
          }
          return data;
        }).finally(()=>{if(this.opening===open)this.opening=null;});
        const data=await open;
        if (!data.session_id) throw Error('响应缺少场景会话');
        const url=base+'/'+encodeURIComponent(data.session_id);
        if (generation!==this.generation) return;
        this.url=url;this.ready=false;this.snapshots=false;this.frameStarted=0;
        this.pose=null;
        this.currentView={type:'preset',id:'start'};this.waitingView=null;this.freePose=null;this.viewError='';
        if (this.replay) {
          this.captureObserver=new ResizeObserver(()=>this.scheduleReplayResize(generation));
          this.captureObserver.observe(this.stage);
        }
        this.bindControls();this.updateState(data);this.poll(generation);
      } catch(error) {if (generation===this.generation) this.offline(error);}
    }
    updateState(data) {
      if (data.state==='failed'||data.state==='closed') throw Error(data.error||'场景会话已关闭');
      this.canControl=data.can_control!==false && data.role!=='viewer';
      const wasReady=this.ready;
      this.ready=data.state==='ready';
      if (this.ready && this.sceneCameras) {
        if (!this.sceneCameras.initial_pose && validPose(data.pose)) this.sceneCameras.initial_pose=data.pose;
        if (Array.isArray(data.presets)) this.sceneCameras.presets=data.presets;
      }
      this.confirmView(data);
      if (validPose(data.pose) && !this.ownsInput() && !this.pending && !this.sending && !this.waitingView) this.pose=structuredClone(data.pose);
      if (Array.isArray(data.presets)) this.viewButtons('preset',this.presets,data.presets);
      if (Array.isArray(data.agent_views)) {
        this.agentIds=new Set(data.agent_views.map(agent=>agent.agent_key));
        if (this.agentHelp) this.agentHelp.hidden=!this.agentIds.size;
        this.viewButtons('agent',this.agents,data.agent_views);
      }
      this.paintViews();
      if (!this.ready) {this.show('正在载入场景…');return;}
      this.setAspect(this.live.naturalWidth||data.capture_size?.width,this.live.naturalHeight||data.capture_size?.height);
      if (this.replay) {
        this.serverCaptureWidth=data.capture_size?.width;
        if (!wasReady && this.captureObserver)this.scheduleReplayResize(this.generation);
        this.syncReplayCamera();
        this.showLiveStatus();
        return;
      }
      if (!this.frameStarted) {
        this.frameStarted=performance.now();this.live.src=this.url+'/stream';
        this.live.onerror=()=>this.fallback();
      }
      if (!this.snapshots && !this.live.naturalWidth && performance.now()-this.frameStarted>5000) this.fallback();
      if (this.live.naturalWidth && !this.hasLiveFrame) this.presentFrame();
      if (!this.hasLiveFrame) this.show('正在接收实景画面…');
      else {this.syncReplayCamera();this.showLiveStatus();}
    }
    viewButtons(type,group,items) {
      if (!group) return;
      const list=items.map(p=>[type==='agent'?p.agent_key:p.id,p.name]);
      const key=JSON.stringify(list);
      if (group.dataset.options===key) return;
      group.dataset.options=key;group.replaceChildren();group.hidden=!list.length;
      if (!list.length) return;
      const title=document.createElement('span');title.className='scene-view-label';
      title.textContent=type==='agent'?'智能体初始视角':'机位';group.append(title);
      for (const [id,name] of list) {
        const button=document.createElement('button');button.type='button';button.dataset.viewType=type;button.dataset.viewId=id;
        button.textContent=name||id;button.title=type==='agent'?`从${name||id}的实验初始位置观察`:name||id;
        button.onclick=()=>this.chooseView(type,id);group.append(button);
      }
    }
    paintViews() {
      for (const group of [this.presets,this.agents]) {
        for (const button of group?.querySelectorAll?.('button')||[]) {
          const matches=v=>v?.type===button.dataset.viewType && v.id===button.dataset.viewId;
          const canExitFollow=button.dataset.viewType==='preset' && this.replay?.leaveFollow;
          button.disabled=!this.hasLiveFrame||!this.ready||!this.canControl||Boolean(this.replayPose && !canExitFollow);
          button.setAttribute('aria-pressed',String(matches(this.currentView)));
          button.setAttribute('aria-busy',String(matches(this.waitingView)));
        }
      }
      if(this.returnButton) {
        this.returnButton.hidden=this.currentView?.type!=='agent' && this.waitingView?.type!=='agent';
        this.returnButton.disabled=!this.hasLiveFrame||!this.ready||!this.canControl||!this.freePose;
      }
    }
    chooseView(type,id) {
      if (!this.ready||!this.hasLiveFrame||!this.canControl||!validPose(this.pose))return;
      if (this.replayPose) {
        if (type!=='preset'||!this.replay?.leaveFollow)return;
        this.replayPose=null;this.sentReplayPose=null;this.freePose=null;
        this.replay.leaveFollow();
      }
      if (type==='agent' && !this.agentIds?.has(id))return;
      if (type==='agent' && this.currentView?.type!=='agent' && this.waitingView?.type!=='agent') this.freePose=structuredClone(this.pose);
      if (type==='free' && !this.freePose)return;
      const op={agent:'agent-view',preset:'preset',free:'pose'}[type];
      const body=type==='agent'?{agent_key:id}:type==='preset'?{id}:structuredClone(this.freePose);
      this.requestView(type,id,op,body);
    }
    setReplayCamera(pose) {
      if (pose!==null && !validPose(pose)) throw Error('无效的回放相机位姿');
      const previous=this.replayPose;
      if (pose && previous && pose.position.every((n,i)=>n===previous.position[i])
          && pose.yaw_deg===previous.yaw_deg && pose.pitch_deg===previous.pitch_deg && pose.fov_deg===previous.fov_deg) return;
      this.replayPose=pose && structuredClone(pose);
      this.syncReplayCamera();
    }
    syncReplayCamera() {
      if ((!this.hasLiveFrame&&!this.replay)||!this.ready||!this.canControl||!validPose(this.pose))return;
      if (this.replayPose) {
        if (this.sentReplayPose===this.replayPose)return;
        if (!this.sentReplayPose && !(this.waitingView?.type==='free' && this.freePose)) {
          this.freePose=structuredClone(this.pose);
        }
        this.sentReplayPose=this.replayPose;
        this.requestView('replay','','pose',this.replayPose);
        this.showLiveStatus();
      } else if (this.sentReplayPose) {
        this.sentReplayPose=null;
        this.chooseView('free','');
      } else if (this.overviewPose) {
        const pose=this.overviewPose;this.overviewPose=null;
        this.requestView('free','','pose',pose);
      }
    }
    setOverviewCamera(pose) {
      if (!validPose(pose))return;
      this.overviewPose=structuredClone(pose);
      this.syncReplayCamera();
    }
    cancelReplayFrame() {
      const task=this.frameTask;
      if (!task)return;
      this.frameTask=null;
      task.controller.abort();
      task.finish(false);
    }
    presentReplayStep(step,playbackMs,onFirstFrame) {
      this.cancelReplayFrame();
      if (!this.active || !this.sessionBase) return Promise.resolve(false);
      return new Promise(resolve=>{
        const task={step,controller:new AbortController(),finish:resolve};
        this.frameTask=task;
        this.replayNotice=`正在读取 Step ${step} 的人物画面…`;
        this.show(this.replayNotice);
        this.readReplayFrames(task,playbackMs,onFirstFrame).catch(error=>{
          if (task===this.frameTask) this.offline(error);
        });
      });
    }
    async readReplayFrames(task,playbackMs,onFirstFrame) {
      const current=()=>this.frameTask===task;
      const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
      while (current() && !(this.ready && this.url)) await delay(60);
      if (!current())return;
      if (!this.canControl) throw Error('本会话仅可观看，无法加载所选回放步骤');
      // A followed camera's pose must reach UE before we submit the actors.
      while (current() && this.waitingView?.seq===null) await delay(35);
      if (!current())return;
      if (this.viewError) throw Error(this.viewError);
      // The server may still be reading an abandoned POST. Preserve submission order across rapid seeks.
      const before=this.replaySubmission;
      const submission=this.replaySubmission=(async()=>{
        if (before) await before.catch(()=>{});
        if (current()) return json(this.url+'/replay',{step_no:task.step,playback_ms:playbackMs});
      })();
      let state;
      try {state=await submission;} finally {if(this.replaySubmission===submission)this.replaySubmission=null;}
      if (!current())return;
      const record=state?.replay, seq=record?.requested_seq;
      if (record?.run_id!==this.replay.runId || record.requested_step!==task.step || !Number.isInteger(seq) || seq<1)
        throw Error('UE 返回的 Run 或 Step 身份不一致');
      const missing=(record.missing_actors||[]).map(key=>this.replay.names.get(key)||key);
      const warning=missing.length?' · 缺少 UE 形象：'+missing.join('、'):'';
      const frameUrl=this.url+'/snapshot.jpg?replay_seq='+seq;
      let lastFrame=-1, shownCameraSeq=-1, settled=false;
      const deadline=performance.now()+60000;
      while (current()) {
        const cameraSeq=this.appliedCameraSeq || 0;
        const resized=this.captureWidth && this.serverCaptureWidth===this.captureWidth && this.live.naturalWidth!==this.captureWidth;
        if (settled && cameraSeq===shownCameraSeq && !resized) {await delay(80);continue;}
        const response=await fetch(frameUrl+'&after_frame_seq='+lastFrame,{cache:'no-store',signal:task.controller.signal});
        if (!current())return;
        if (!settled && performance.now()>deadline)throw Error(`等待 Step ${task.step} 的 UE 画面超时`);
        if (response.status===204)continue;
        if (!response.ok) {
          const body=await response.json().catch(()=>({}));
          throw Error(body.detail||`UE 回放取帧失败（${response.status}）`);
        }
        const headers=response.headers;
        const sequence=headers.get('X-Frame-Seq'), done=headers.get('X-Replay-Settled');
        if (headers.get('X-Replay-Seq')!==String(seq) || headers.get('X-Replay-Step')!==String(task.step)
            || !/^\d+$/.test(sequence||'') || !['0','1'].includes(done)
            || headers.get('content-type')?.split(';')[0]!=='image/jpeg')throw Error('UE 画面与请求的步骤不符');
        if (Number(sequence)>lastFrame) {
          const blob=await response.blob();
          if (!current())return;
          const url=URL.createObjectURL(blob), frame=new Image();
          try {
            frame.src=url;
            await frame.decode();
            if (!current())return;
            frame.alt=`Step ${task.step} 的 UE 回放画面`;frame.dataset.live='';
            this.live.replaceWith(frame);this.live=frame;
            if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
            this.objectUrl=url;
          } finally {if (this.objectUrl!==url) URL.revokeObjectURL(url);}
          if (!this.hasLiveFrame) {this.hasLiveFrame=true;this.paintViews();}
          this.setAspect(frame.naturalWidth,frame.naturalHeight);
          if (lastFrame<0 || (done==='1' && !settled)) {
            this.replayNotice=`Step ${task.step}`+(done==='1'?' · 已提交位置':' · 已提交路径')+warning;
            this.showLiveStatus();
          }
          if (lastFrame<0)onFirstFrame();
          lastFrame=Number(sequence);shownCameraSeq=cameraSeq;
          if (done==='1' && !settled) {settled=true;task.finish(true);}
        } else {await response.body?.cancel();}
      }
    }
    requestView(type,id,op,body) {
      const view={type,id,seq:null};
      this.keys.clear();this.viewError='';this.waitingView=view;this.pending={op,body,view};this.paintViews();
      if (!this.sending)this.flush(this.generation);
    }
    confirmView(state) {
      if (Number.isInteger(state.applied_seq)) this.appliedCameraSeq=Math.max(this.appliedCameraSeq||0,state.applied_seq);
      const target=this.waitingView;
      if (!target||target.seq===null||!Number.isInteger(state.applied_seq)||state.applied_seq<target.seq||!validPose(state.pose))return;
      this.currentView={type:target.type,id:target.id};this.pose=structuredClone(state.pose);
      this.waitingView=null;
      if (target.type!=='agent' && target.type!=='replay')this.freePose=null;
      this.paintViews();
    }
    async poll(generation) {
      try {
        const data=await json(this.url+'/state');
        if (generation!==this.generation)return;
        this.updateState(data);
        this.timer=setTimeout(()=>this.poll(generation),1200);
      } catch(error) {if (generation===this.generation) this.offline(error);}
    }
    fallback() {
      if (this.snapshots||!this.url) return;
      this.snapshots=true;this.live.onerror=null;this.live.removeAttribute('src');
      const generation=this.generation;
      const next=async()=>{
        try {
          const r=await fetch(this.url+'/snapshot.jpg',{cache:'no-store',signal:this.listeners.signal});
          if (!r.ok) throw Error(`UE 取帧失败（${r.status}）`);
          const blob=await r.blob();
          if (generation!==this.generation)return;
          if (blob.type!=='image/jpeg') throw Error('UE 未返回 JPEG 画面');
          const previous=this.objectUrl;
          this.objectUrl=URL.createObjectURL(blob);this.live.src=this.objectUrl;
          if(previous) URL.revokeObjectURL(previous);
          this.frameTimer=setTimeout(next,220);
        } catch(error){if (generation===this.generation) this.offline(error);}
      };
      next();
    }
    ownsInput() {return document.pointerLockElement===this.stage||this.dragging;}
    bindControls() {
      const listeners=this.listeners=new AbortController();
      const on=(node,event,fn,opts={})=>node.addEventListener(event,fn,{...opts,signal:listeners.signal});
      const update=()=>{if(this.pose) this.queue('pose',structuredClone(this.pose));};
      on(this.stage,'pointerdown',()=>{
        if (this.replayPose||!this.hasLiveFrame||!this.ready||!this.canControl||!this.pose||this.waitingView) return;
        this.stage.focus();this.dragging=true;
        const lock=this.stage.requestPointerLock?.();lock?.catch(()=>{});
      });
      on(window,'pointerup',()=>{this.dragging=false;});
      on(document,'pointerlockchange',()=>{this.dragging=false;this.keys.clear();});
      on(window,'blur',()=>{this.keys.clear();this.dragging=false;});
      on(document,'visibilitychange',()=>{if(document.hidden) { this.keys.clear();this.dragging=false; }});
      on(window,'pagehide',()=>this.leaveScene());
      on(document,'mousemove',e=>{
        if (this.replayPose||!this.canControl||!this.ownsInput()||!this.pose||this.waitingView) return;
        this.pose.yaw_deg=(this.pose.yaw_deg+e.movementX*.16)%360;
        this.pose.pitch_deg=Math.max(-88,Math.min(88,this.pose.pitch_deg-e.movementY*.16));update();
      });
      on(this.stage,'wheel',e=>{
        if (this.replayPose||!this.hasLiveFrame||!this.canControl||!this.ready||!this.pose||this.waitingView)return;
        e.preventDefault();this.pose.fov_deg=Math.max(20,Math.min(125,this.pose.fov_deg+Math.sign(e.deltaY)*3));update();
      },{passive:false});
      on(window,'keydown',e=>{
        if (this.replayPose||!this.hasLiveFrame||!this.ready||!this.canControl||!this.pose||this.waitingView||this.currentView?.type==='agent'||(!this.ownsInput()&&document.activeElement!==this.stage))return;
        if (['KeyW','KeyA','KeyS','KeyD','KeyQ','KeyE','ShiftLeft','ShiftRight'].includes(e.code)) {e.preventDefault();this.keys.add(e.code);}
      });
      on(window,'keyup',e=>this.keys.delete(e.code));
      this.lastTick=performance.now();
      this.tick=setInterval(()=>{
        const now=performance.now(),dt=Math.min((now-this.lastTick)/1000,.15);this.lastTick=now;
        if (this.canControl&&this.pose&&!this.waitingView&&this.currentView?.type!=='agent'&&SceneMapViewer.move(this.pose,this.keys,dt))update();
        if (this.ready&&this.canControl&&this.pending&&!this.sending)this.flush(this.generation);
      },65);
    }
    queue(op,body) {
      if (!this.canControl||this.waitingView)return;
      if (this.currentView?.type==='preset') {this.currentView={type:'free',id:''};this.paintViews();}
      this.pending={op,body};
    }
    async flush(generation) {
      const {op,body,view}=this.pending;this.pending=null;this.sending=true;
      try {
        const data=await json(this.url+'/'+op,body);
        if (generation!==this.generation)return;
        if (view && view===this.waitingView) {
          if (!Number.isInteger(data.request_seq)) throw Error('相机没有确认切换请求');
          view.seq=data.request_seq;
        }
        this.confirmView(data);
      } catch(error){
        if(generation!==this.generation)return;
        if(view && view===this.waitingView) {
          this.waitingView=null;this.viewError=`视角切换失败：${error.message}`;this.show(this.viewError);this.paintViews();
        } else if(!view) this.offline(error);
      }
      finally {
        if(generation===this.generation) {
          this.sending=false;
          if (this.ready&&this.canControl&&this.pending?.view)this.flush(generation);
        }
      }
    }
    offline(error) {
      this.leaveScene();
      if (error.message==='No dedicated preview slot is available') {
        this.show('场景被占用 · SimWorld 的预览会话已满。');
      } else if (error.status===409) {
        const message=replayConflicts.get(error.message) || error.message;
        this.show(`UE 实景暂不可用 · ${message}`);
      } else {
        this.show(`UE 实景已断开 · ${error.message}`);
      }
      this.retry.disabled=false;
      this.retry.hidden=false;
    }
    release(url) {
      const closing=this.closing=json(url+'/close',{},true).catch(()=>{}).finally(()=>{
        if(this.closing===closing)this.closing=null;
      });
      return closing;
    }
    leaveScene() {
      clearTimeout(this.captureTimer);this.captureObserver?.disconnect();this.captureObserver=null;
      this.captureWidth=this.serverCaptureWidth=0;this.resizingCapture=false;this.captureError='';
      this.cancelReplayFrame();this.replayNotice='';this.overviewPose=null;this.appliedCameraSeq=0;
      this.generation++;this.active=false;
      const url=this.url;this.url=null;
      this.listeners?.abort();this.listeners=null;
      clearTimeout(this.timer);clearTimeout(this.frameTimer);clearInterval(this.tick);
      this.ready=false;this.pending=null;this.sending=false;this.dragging=false;this.keys.clear();this.waitingView=null;this.currentView=null;
      this.sentReplayPose=null;
      this.hasLiveFrame=false;
      if (this.live) {this.live.onerror=null;this.live.hidden=true;this.live.removeAttribute('src');}
      if (this.objectUrl) {URL.revokeObjectURL(this.objectUrl);this.objectUrl=null;}
      if(this.stage&&document.pointerLockElement===this.stage) document.exitPointerLock();
      if(document.fullscreenElement && this.root?.contains(document.fullscreenElement)) document.exitFullscreen().catch(()=>{});
      this.paintViews();
      if(url)this.release(url);
    }
    destroy() {this.replayPose=null;this.leaveScene();this.controlSize?.disconnect();this.root.replaceChildren();}
  }
  async function json(url,body,keepalive=false) {
    const response=await fetch(url,{method:body===undefined?'GET':'POST',cache:'no-store',keepalive,
      headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
    let data={};
    if (response.status!==204) data=await response.json().catch(()=>({}));
    if (!response.ok) {
      const error=Error(typeof data.detail==='string'?data.detail:(data.detail?.message||data.error||`UE 服务请求失败（${response.status}）`));
      error.status=response.status;throw error;
    }
    return data;
  }
  window.SceneMapViewer=SceneMapViewer;
})();
