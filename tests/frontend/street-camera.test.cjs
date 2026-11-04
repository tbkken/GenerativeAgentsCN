const test = require('node:test');
const assert = require('node:assert/strict');
global.window = {};
require('../../src/generative_agents/adapters/web/static/replay/street-camera.js');
const {overview,follow} = window.GAStreetCamera;

test('Tokyo overview and third-person follow retain the exact authored sidewalk viewpoint through convergence', () => {
  const position = [-42.2, -4.8, 2.9];
  const definition = {initial_pose:{position, yaw_deg:0}};
  const agents = [-9.8, -0.2].map((y,i) => ({agent_key:'p'+i,from_coord:[-46.8,y,0.4], coord:[-46.8,-3,0.4]}));
  const pose = overview(definition, {agents});
  assert.ok(Math.abs(pose.yaw_deg) > 170 && pose.pitch_deg < 0);
  const together=agents.map(a=>({...a,from_coord:a.coord}));
  const tracking=follow(definition,{agents},'p0',pose.yaw_deg);
  for (const shot of [pose,tracking,follow(definition,{agents:together},'p0',tracking.yaw_deg,tracking)]) {
    assert.deepEqual(shot.position,position);
    assert.ok(shot.fov_deg>=55 && shot.fov_deg<=118);
    const yaw=shot.yaw_deg*Math.PI/180,pitch=shot.pitch_deg*Math.PI/180;
    const vertical=z=>{
      const ahead=(-46.8-position[0])*Math.cos(yaw)+(-3-position[1])*Math.sin(yaw);
      const up=z-position[2], depth=ahead*Math.cos(pitch)+up*Math.sin(pitch);
      return (up*Math.cos(pitch)-ahead*Math.sin(pitch))/depth/Math.tan(shot.fov_deg*Math.PI/360)*(16/9);
    };
    assert.ok(Math.abs(vertical(.4))<1 && Math.abs(vertical(2.3))<1);
    assert.ok((vertical(2.3)-vertical(.4))/2<.75,'even the whole body occupies under 75% of the viewport height');
  }
  assert.ok(tracking.fov_deg<pose.fov_deg);
});

test('a crossing segment from an unselected actor rules out a preset even if both endpoints are clear',()=>{
  const alternate=[0,-7,2.7], definition={initial_pose:{position:[0,0,1.7]},
    presets:[{pose:{position:alternate}}]};
  const agents=[{agent_key:'tracked',coord:[6,0,0]},
    {agent_key:'passing',from_coord:[-7,3,0],path:[[-7,0,0],[7,0,0]],coord:[7,3,0]}];
  const pose=follow(definition,{agents},'tracked',0);
  assert.deepEqual(pose.position,alternate);
});

test('an observer surrounded by the recorded path can see the group, including overlapping actors', () => {
  const actor = {from_coord:[-2,0,0], coord:[0,0,0], path:[[-2,0,0],[2,0,0],[0,0,0]]};
  const origin = [0,0,0.9];
  const pose = overview({initial_pose:{position:origin}}, {agents:[actor, actor]});
  assert.notDeepEqual(pose.position, origin);
  assert.ok([...pose.position, pose.yaw_deg, pose.pitch_deg, pose.fov_deg].every(Number.isFinite));
  assert.ok(pose.fov_deg <= 80.01);
  assert.deepEqual(actor.coord, [0,0,0]);
  assert.equal(overview({}, {agents:[]}), null);
});

test('third-person follow stays outside the walkers on head-on and coincident paths and keeps its bearing on a U-turn',()=>{
  const a={agent_key:'a',from_coord:[5,0,0],coord:[0,0,0],path:[[3,0,0],[0,0,0]],to_yaw_deg:180};
  const b={agent_key:'b',from_coord:[-5,0,0],coord:[0,0,0],to_yaw_deg:0};
  const definition={initial_pose:{position:[0,0,1.6],yaw_deg:0}};
  const pose=follow(definition,{agents:[a,b]},'a',-140);
  assert.ok(pose.position[2]>=4-1e-8);
  for(const actor of [a,b]){
    for(const [x,y,z] of [actor.from_coord,actor.coord]){
      assert.ok(Math.hypot(pose.position[0]-x,pose.position[1]-y,pose.position[2]-z-1.6)>2.3);
    }
  }
  const next=follow(definition,{agents:[{...a,from_coord:a.coord,coord:a.from_coord,to_yaw_deg:0}]},'a',pose.yaw_deg);
  assert.deepEqual(next,pose);
  assert.equal(follow(definition,{agents:[a]},'absent',0),null);
});

test('small committed movements stay within one follow shot; the camera repositions when the actor leaves it',()=>{
  const definition={initial_pose:{position:[5,0,3]}};
  const step=y=>({agents:[{agent_key:'walker',coord:[0,y,0]}]});
  const initial=follow(definition,step(0),'walker',180);
  assert.equal(follow(definition,step(0.15),'walker',initial.yaw_deg,initial),initial);
  const later=follow(definition,step(18),'walker',initial.yaw_deg,initial);
  assert.notDeepEqual(later.position,initial.position);
  assert.equal(later.yaw_deg,initial.yaw_deg);
});
