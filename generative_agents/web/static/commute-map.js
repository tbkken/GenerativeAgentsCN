/** 两日通勤演示共享的只读地图 Revision 和 Canvas 渲染器。 */
(() => {
  const revision = Object.freeze({
    name: '住宅—公司两日通勤地图',
    stableKey: 'commute-home-office',
    version: 'v1',
    revisionId: 'map-rev-commute-001',
    size: '96 × 56',
    distanceDrivingKm: 1.8,
    distanceWalkingKm: 1.2,
  });

  const signal = (x, y) => `<g><rect class="cm-signal-post" x="${x-9}" y="${y-9}" width="18" height="18" rx="5"/><circle class="cm-signal-light" cx="${x}" cy="${y}" r="4"/></g>`;
  const intersection = (x, number, phase) => `<g data-phase="${phase}">
    <rect class="cm-road" x="${x-60}" y="0" width="120" height="680"/>
    <path class="cm-lane-line" d="M${x-40} 0V680M${x-20} 0V680M${x+20} 0V680M${x+40} 0V680"/>
    <path class="cm-center-line" d="M${x} 0V680"/>
    <rect class="cm-sidewalk" x="${x-78}" y="0" width="18" height="680"/><rect class="cm-sidewalk" x="${x+60}" y="0" width="18" height="680"/>
    <rect class="cm-crosswalk" x="${x-52}" y="264" width="104" height="20"/><rect class="cm-crosswalk" x="${x-52}" y="396" width="104" height="20"/>
    <rect class="cm-crosswalk" x="${x-74}" y="288" width="20" height="104"/><rect class="cm-crosswalk" x="${x+54}" y="288" width="20" height="104"/>
    <path class="cm-stop-line" d="M${x-52} 292H${x+52}M${x-52} 388H${x+52}M${x-50} 292V388M${x+50} 292V388"/>
    <g transform="translate(${x-46},244)"><rect class="cm-label-bg" width="92" height="26" rx="6"/><text class="cm-label" x="9" y="17">路口 ${number}</text></g>
  </g>`;
  const signals = x => signal(x-66,276)+signal(x+66,276)+signal(x-66,404)+signal(x+66,404);

  function markup() {
    return `<svg class="cm-map" viewBox="0 0 1200 680" role="img" aria-label="住宅到公司两日通勤地图，包含两个三车道路口、八条斑马线、交通信号灯、车辆门禁和停车场">
      <defs><pattern id="cm-grid" width="20" height="20" patternUnits="userSpaceOnUse"><rect width="20" height="20" fill="#c9d9bd"/><path d="M20 0H0V20" fill="none" stroke="rgba(44,75,67,.12)" stroke-width="1"/></pattern><pattern id="cm-crosswalk" width="12" height="12" patternUnits="userSpaceOnUse"><rect width="7" height="12" fill="rgba(255,255,255,.94)"/></pattern></defs>
      <rect class="cm-grid" width="1200" height="680"/>
      <g data-phase="1"><rect class="cm-zone" x="50" y="450" width="220" height="190" rx="12"/><rect class="cm-building" x="80" y="485" width="150" height="105" rx="6"/><path class="cm-building-roof" d="M68 500L155 442L242 500Z"/><g transform="translate(68,603)"><rect class="cm-label-bg" width="170" height="45" rx="7"/><text class="cm-label" x="12" y="19">林晨住宅</text><text class="cm-label-sub" x="12" y="34">sector.home / driveway</text></g><rect class="cm-zone" x="920" y="40" width="235" height="215" rx="12"/><rect class="cm-building" x="960" y="64" width="160" height="92" rx="6"/><g transform="translate(950,46)"><rect class="cm-label-bg" width="175" height="45" rx="7"/><text class="cm-label" x="12" y="19">公司园区</text><text class="cm-label-sub" x="12" y="34">sector.office / campus</text></g></g>
      <g data-phase="2"><rect class="cm-road" x="0" y="280" width="1200" height="120"/><path class="cm-lane-line" d="M0 300H1200M0 320H1200M0 360H1200M0 380H1200"/><path class="cm-center-line" d="M0 340H1200"/><rect class="cm-sidewalk" x="0" y="260" width="1200" height="20"/><rect class="cm-sidewalk" x="0" y="400" width="1200" height="20"/><rect class="cm-road" x="130" y="400" width="50" height="88"/><rect class="cm-road" x="930" y="156" width="60" height="124"/><text class="cm-mini-label" x="210" y="315">WESTBOUND · 3 LANES</text><text class="cm-mini-label" x="820" y="374">EASTBOUND · 3 LANES</text></g>
      ${intersection(430,'A',3)}${intersection(750,'B',4)}
      <g data-phase="5"><path class="cm-walk-route" d="M155 470V432H430H750H950V170"/><path class="cm-route" d="M155 470V340H430H750H960V210"/></g>
      <g data-phase="6">${signals(430)}${signals(750)}<circle class="cm-sensor" cx="430" cy="340" r="98"/><circle class="cm-sensor" cx="750" cy="340" r="98"/></g>
      <g data-phase="7"><path class="cm-gate" d="M930 270H990"/><rect class="cm-gate-post" x="923" y="258" width="12" height="25" rx="3"/><rect class="cm-gate-post" x="987" y="258" width="12" height="25" rx="3"/><g transform="translate(892,226)"><rect class="cm-label-bg" width="142" height="34" rx="6"/><text class="cm-label" x="10" y="21">车辆门禁</text></g><rect class="cm-parking" x="1010" y="168" width="128" height="80" rx="8"/><rect class="cm-parking-slot occupied" x="1020" y="180" width="30" height="52"/><rect class="cm-parking-slot occupied" x="1058" y="180" width="30" height="52"/><rect class="cm-parking-slot" x="1096" y="180" width="30" height="52"/><text x="1025" y="212" font-size="10">P01</text><text x="1063" y="212" font-size="10">P02</text><text x="1101" y="212" font-size="10">P03</text></g>
      <g data-phase="8"><g transform="translate(352,432)"><rect class="cm-semantic-bg" width="156" height="25" rx="6"/><text class="cm-semantic-text" x="8" y="16">pedestrian.waiting.a</text></g><g transform="translate(672,432)"><rect class="cm-semantic-bg" width="156" height="25" rx="6"/><text class="cm-semantic-text" x="8" y="16">pedestrian.waiting.b</text></g><g transform="translate(846,418)"><rect class="cm-semantic-bg" width="184" height="25" rx="6"/><text class="cm-semantic-text" x="8" y="16">vehicle.network → gate</text></g></g>
      <g class="cm-operation cm-op-zone"><path class="cm-operation-stroke" d="M70 455H250V630H70Z"/><circle class="cm-brush" r="8"/></g><g class="cm-operation cm-op-road"><path class="cm-operation-stroke" d="M80 340H1120"/><circle class="cm-brush" r="8"/></g><g class="cm-operation cm-op-int1"><path class="cm-operation-stroke" d="M365 245H495V435H365Z"/><circle class="cm-brush" r="8"/></g><g class="cm-operation cm-op-int2"><path class="cm-operation-stroke" d="M685 245H815V435H685Z"/><circle class="cm-brush" r="8"/></g><g class="cm-operation cm-op-walk"><path class="cm-operation-stroke" d="M110 420H1080"/><circle class="cm-brush" r="8"/></g><g class="cm-operation cm-op-signals"><path class="cm-operation-stroke" d="M360 248H500V432H680V248H820V432"/><circle class="cm-brush" r="8"/></g><g class="cm-operation cm-op-facilities"><path class="cm-operation-stroke" d="M920 268H1140V158"/><circle class="cm-brush" r="8"/></g><g class="cm-operation cm-op-semantics"><path class="cm-operation-stroke" d="M340 455L850 455"/><circle class="cm-brush" r="8"/></g>
      <path class="cm-trail" data-trail></path><g class="cm-actor" data-actor><circle class="cm-actor-ring" r="21"/><text class="cm-actor-glyph" y="1" data-actor-glyph>🚙</text></g>
    </svg>`;
  }

  const carPath = [[155,470],[155,340],[430,340],[750,340],[955,340],[960,270],[1060,210]];
  const walkPath = [[155,470],[155,432],[430,432],[750,432],[950,432],[950,270],[990,170]];
  function pointOnPath(points, progress){const p=Math.max(0,Math.min(1,progress))*(points.length-1);const i=Math.min(points.length-2,Math.floor(p));const t=p-i;return [points[i][0]+(points[i+1][0]-points[i][0])*t,points[i][1]+(points[i+1][1]-points[i][1])*t];}
  function partialPath(points, progress){const count=Math.max(1,Math.ceil(progress*(points.length-1)));const current=pointOnPath(points,progress);return `M${points[0][0]} ${points[0][1]} `+points.slice(1,count+1).map(p=>`L${p[0]} ${p[1]}`).join(' ')+` L${current[0]} ${current[1]}`;}

  function render(host,{phase=9,operation='',playback=null}={}){
    host.classList.add('commute-map-host');host.innerHTML=markup();const svg=host.querySelector('.cm-map');svg.dataset.phase=String(phase);svg.dataset.operation=operation||'';
    const controller={
      revision,
      setPhase(nextPhase,nextOperation=''){svg.dataset.phase=String(nextPhase);svg.dataset.operation=nextOperation||'';},
      setPlayback(state){const actor=svg.querySelector('[data-actor]');const glyph=svg.querySelector('[data-actor-glyph]');const trail=svg.querySelector('[data-trail]');if(!state){actor.classList.remove('visible');trail.setAttribute('d','');return;}const mode=state.mode==='walk'?'walk':'car';const points=mode==='walk'?walkPath:carPath;const [x,y]=pointOnPath(points,state.progress||0);actor.setAttribute('transform',`translate(${x} ${y})`);actor.classList.add('visible');glyph.textContent=mode==='walk'?'🚶':'🚙';trail.setAttribute('class',`cm-trail ${mode}`);trail.setAttribute('d',partialPath(points,state.progress||0));},
      destroy(){host.innerHTML='';host.classList.remove('commute-map-host');}
    };if(playback)controller.setPlayback(playback);return controller;
  }
  window.CommuteMap=Object.freeze({revision,render});
})();
