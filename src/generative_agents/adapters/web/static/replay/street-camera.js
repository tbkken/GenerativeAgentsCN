/* Third-person replay cameras use committed Z-up metre coordinates. */
(() => {
  const validPoint=p=>Array.isArray(p) && p.length===3 && p.every(Number.isFinite);
  function track(actor) {
    const path = !actor.path_source || actor.path_source === 'OBSERVED' ? actor.path || [] : [];
    return [actor.from_coord,...path,actor.coord].filter(validPoint);
  }
  function clearOfWalkers(position, tracks) {
    // The swept torso (one metre above the feet) must stay clear of the lens,
    // including between recorded points and when an unselected actor approaches.
    for (const path of tracks) {
      for (let i=0;i<path.length;i++) {
        const a=path[Math.max(0,i-1)], b=path[i];
        const delta=b.map((n,j)=>n-a[j]);
        const offset=position.map((n,j)=>n-a[j]-(j===2?1:0));
        const lengthSquared=delta.reduce((sum,n)=>sum+n*n,0);
        const t=lengthSquared ? Math.max(0,Math.min(1,delta.reduce((sum,n,j)=>sum+n*offset[j],0)/lengthSquared)) : 0;
        if (Math.hypot(...offset.map((n,j)=>n-t*delta[j]))<3.4)return false;
      }
    }
    return true;
  }
  function fixedShot(position, center, halfSpan, aspect, follow, previous) {
    const towards=center.map((n,j)=>n-position[j]), horizontal=Math.hypot(towards[0],towards[1]);
    const distance=Math.hypot(...towards);
    if (distance>Math.max(follow?16:20,3*Math.hypot(...halfSpan))) return null;
    const yaw=Math.atan2(towards[1],towards[0]), pitch=Math.atan2(towards[2],horizontal);
    function requiredFov(yaw,pitch) {
      const cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
      let tangent=0;
      for (const dx of [-halfSpan[0],halfSpan[0]]) for (const dy of [-halfSpan[1],halfSpan[1]]) for (const dz of [-halfSpan[2],halfSpan[2]]) {
        const x=towards[0]+dx,y=towards[1]+dy,z=towards[2]+dz;
        const ahead=x*cy+y*sy, depth=ahead*cp+z*sp;
        if(depth<=0.3)return Infinity;
        tangent=Math.max(tangent,Math.abs(y*cy-x*sy)/depth,Math.abs(z*cp-ahead*sp)*aspect/depth);
      }
      return 2*Math.atan(tangent*1.12)*180/Math.PI;
    }
    const fov=Math.max(follow?55:62,requiredFov(yaw,pitch));
    if (fov>118)return null;
    if (previous && position.every((v,i)=>v===previous.position[i])) {
      const oldYaw=previous.yaw_deg*Math.PI/180,oldPitch=previous.pitch_deg*Math.PI/180;
      const yawDelta=Math.atan2(Math.sin(yaw-oldYaw),Math.cos(yaw-oldYaw));
      if (Math.abs(yawDelta)<0.08 && Math.abs(pitch-oldPitch)<0.08 && Math.abs(previous.fov_deg-fov)<10 &&
          requiredFov(oldYaw,oldPitch)<=previous.fov_deg)return previous;
    }
    return {position:[...position],yaw_deg:yaw*180/Math.PI,pitch_deg:pitch*180/Math.PI,fov_deg:fov};
  }
  function frameActors(scene, actors, aspect, follow, bearing, previous, allActors=actors) {
    const low = [Infinity, Infinity, Infinity];
    const high = [-Infinity, -Infinity, -Infinity];
    for (const actor of actors) {
      for (const point of track(actor)) {
        for (let axis = 0; axis < 3; axis++) {
          low[axis] = Math.min(low[axis], point[axis]);
          high[axis] = Math.max(high[axis], point[axis]);
        }
      }
    }
    if (!Number.isFinite(low[0])) return null;
    // Positions mark the feet. Include the character's body and some space around its silhouette.
    const halfSpan = high.map((v, i) => (v - low[i]) / 2 + (i === 2 ? 1.05 : 0.4));
    const center = high.map((v, i) => (v + low[i]) / 2 + (i === 2 ? 0.9 : 0));
    const tracks=allActors.map(track);
    const cameras=[scene?.initial_pose,...(scene?.presets || []).map(p=>p.pose)].filter(p=>validPoint(p?.position));
    for (const camera of cameras) {
      if (!clearOfWalkers(camera.position,tracks))continue;
      const shot=fixedShot(camera.position,center,halfSpan,aspect,follow,previous);
      if (shot)return shot;
    }
    const observer = cameras[0];
    const position = observer?.position || center.map((v, i) => v + [5, 0, 2][i]);
    const towards = center.map((v, i) => v - position[i]);
    const horizontal = Math.hypot(towards[0], towards[1]);
    const yaw = Number.isFinite(bearing) ? bearing * Math.PI / 180 :
      horizontal ? Math.atan2(towards[1], towards[0]) : (observer?.yaw_deg || 0) * Math.PI / 180;
    const pitch = follow ? -Math.PI / 6 : Math.max(-Math.PI/4,Math.min(-Math.PI/9,Math.atan2(towards[2],horizontal)));
    const direction = [Math.cos(yaw) * Math.cos(pitch), Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch)];
    const right = [-Math.sin(yaw), Math.cos(yaw), 0];
    const up = [-Math.cos(yaw) * Math.sin(pitch), -Math.sin(yaw) * Math.sin(pitch), Math.cos(pitch)];
    const corners = [];
    for (const x of [-halfSpan[0], halfSpan[0]]) {
      for (const y of [-halfSpan[1], halfSpan[1]]) {
        for (const z of [-halfSpan[2], halfSpan[2]]) {
          const dot = axis => x * axis[0] + y * axis[1] + z * axis[2];
          // UE specifies horizontal FOV. Fit both horizontal and vertical edges.
          corners.push({depth:dot(direction), lateral:dot(right), vertical:dot(up)});
        }
      }
    }
    const span=c=>Math.max(Math.abs(c.lateral),Math.abs(c.vertical)*aspect);
    if (previous && previous.yaw_deg===bearing && previous.position[2]>=high[2]+4-1e-6 && clearOfWalkers(previous.position,tracks)) {
      const offset=center.map((v,i)=>v-previous.position[i]);
      const projection=axis=>offset.reduce((sum,v,i)=>sum+v*axis[i],0);
      const depth=projection(direction), x=projection(right), y=projection(up);
      const tangent=Math.tan(previous.fov_deg*Math.PI/360);
      const centered=Math.max(Math.abs(x),Math.abs(y)*aspect)<0.24*depth*tangent;
      if (centered && depth<=12 && corners.every(c=>depth+c.depth>=4 &&
          Math.max(Math.abs(x+c.lateral),Math.abs(y+c.vertical)*aspect)<0.90*(depth+c.depth)*tangent)) {
        return previous;
      }
    }
    // Leave space in front of the closest silhouette, even when two actors meet
    // or walk directly towards the lens. Preserve the observation side across turns.
    const limit = Math.tan(80 * Math.PI / 360);
    const minDistance = Math.max(...corners.map(c => Math.max(4, span(c) * 1.2 / limit) - c.depth));
    // The elevated lens remains above other ground-level walkers crossing the camera.
    const elevationDistance=(high[2]+4.5-center[2])/-direction[2];
    const fitDistance=Math.max(...corners.map(c=>span(c)*1.2/Math.tan(Math.PI/6)-c.depth));
    const distance=Math.max(minDistance,elevationDistance, follow ? 6.5 : Math.min(fitDistance,Math.hypot(...towards)));
    const camera=center.map((v,i)=>v-direction[i]*distance);
    const tangent = Math.max(...corners.map(c => span(c) * 1.2 / (distance + c.depth)));
    return {position:camera, yaw_deg:yaw * 180 / Math.PI, pitch_deg:pitch * 180 / Math.PI,
      fov_deg:Math.max(60, 2 * Math.atan(tangent) * 180 / Math.PI)};
  }

  window.GAStreetCamera = {
    overview(definition, step, aspect=16/9) {
      return frameActors(definition,step?.agents || [],aspect,false,null);
    },
    follow(definition, step, agentKey, bearing, previous=null, aspect=16/9) {
      const actor=step?.agents?.find(item=>item.agent_key===agentKey);
      return actor ? frameActors(definition,[actor],aspect,true,bearing,previous,step.agents) : null;
    },
  };
})();
