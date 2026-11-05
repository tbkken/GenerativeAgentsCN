"""Measure the live image inside the real shell using an isolated headless Chromium page."""
import base64
import io
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image
from playwright.sync_api import sync_playwright


STATIC = Path(__file__).resolve().parents[2] / "src/generative_agents/adapters/web/static"
POSE = {"position": [12, 14, 2], "yaw_deg": 0, "pitch_deg": 0, "fov_deg": 80}
EYE = {**POSE, "position": [4, 5, 1.6]}


def assert_map_overlay(page, expected_text=(), fullscreen=False):
    page.locator("#page-maps").evaluate("el=>Promise.all(el.getAnimations({subtree:true}).map(a=>a.finished.catch(()=>{})))")
    geometry = page.evaluate("""()=>{
      const viewer=document.querySelector('.scene-viewer-map');
      const box=e=>{const r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height]};
      const panel=viewer.querySelector('.scene-panel'),info=viewer.querySelector('.scene-map-info');
      const controls=viewer.querySelector('.scene-controls'),button=controls.querySelector('button:not([hidden])');
      const r=button.getBoundingClientRect();
      return {stage:box(viewer.querySelector('.scene-stage')),image:box(viewer.querySelector('[data-live]')),
        main:box(document.querySelector('.main')),panel:box(panel),info:box(info),controls:box(controls),
        mapInfo:info.innerText,fit:getComputedStyle(viewer.querySelector('[data-live]')).objectFit,
        buttonOnTop:button.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)),
        viewport:[innerWidth,innerHeight],scroll:document.documentElement.scrollWidth};
    }""")
    stage, panel, info, controls = (geometry[key] for key in ("stage", "panel", "info", "controls"))
    expected = [0, 0, *geometry["viewport"]] if fullscreen else geometry["main"]
    assert all(abs(actual - wanted) <= 1 for actual, wanted in zip(stage, expected)), geometry
    assert all(abs(actual - wanted) <= 1 for actual, wanted in zip(geometry["image"], stage)), geometry
    assert geometry["fit"] == "cover" and geometry["buttonOnTop"], geometry
    assert panel[0] >= stage[0] + 8 and panel[1] >= stage[1] + 8, geometry
    assert panel[0] + panel[2] <= stage[0] + stage[2] - 8, geometry
    assert panel[1] + panel[3] <= stage[1] + stage[3] - 8, geometry
    assert info[1] + info[3] <= controls[1] + 1, geometry
    assert geometry["scroll"] <= geometry["viewport"][0], geometry
    assert stage[0] + stage[2] <= geometry["viewport"][0] + 1, geometry
    assert stage[1] + stage[3] <= geometry["viewport"][1] + 1, geometry
    for text in expected_text:
        assert text in geometry["mapInfo"], geometry
    return geometry


def assert_planar_layout(page):
    page.locator('[data-node-name]').wait_for(state='visible')
    assert page.locator('[data-node-name]').input_value() == '平面验收地图'
    page.locator('#page-maps').evaluate("el=>Promise.all(el.getAnimations({subtree:true}).map(a=>a.finished.catch(()=>{})))")
    geometry = page.evaluate("""()=>{
      const box=selector=>{const r=document.querySelector(selector).getBoundingClientRect();return [r.x,r.y,r.width,r.height]};
      return {host:box('#publicMapEditor'),editor:box('.map-editor-v2'),layout:box('.me2-layout'),
        canvas:box('.me2-canvas-host'),inspector:box('.me2-inspector'),field:box('[data-node-name]'),
        width:innerWidth,scroll:document.documentElement.scrollWidth};
    }""")
    host, editor, canvas, inspector, field = (geometry[key] for key in ('host','editor','canvas','inspector','field'))
    assert abs(host[2] - editor[2]) <= 2 and abs(host[3] - editor[3]) <= 2, geometry
    assert canvas[2] >= editor[2] * .3 and canvas[3] > 180, geometry
    assert inspector[2] <= editor[2] * .5 and inspector[2] >= 200, geometry
    assert canvas[0] + canvas[2] <= inspector[0] + 1, geometry
    assert field[0] >= inspector[0] and field[0] + field[2] <= inspector[0] + inspector[2], geometry
    assert geometry['scroll'] <= geometry['width'], geometry


def check_workspace(browser, shell, family):
    """Drive the real map manager against scoped test responses, without starting UE or Studio."""
    page = browser.new_page(viewport={"width": 1341, "height": 884})
    public = family == "maps"
    boundary = "/api/studio/resources" if public else "/api/studio/experiments/demo/resources"
    selected = "local-map" if public else "demo"
    base = f"/api/studio/simworld/{'scenes/map-15' if public else family+'/'+selected}/sessions"
    def detail(key, scene_id, name="本地地图名称"):
        definition = {"environment_type":"spatial_3d","simworld_scene_id":scene_id}
        return {"id":key,"map_key":key,"name":name,"description":"本地用途说明","editable":not public,"row_version":1,"environment_type":"spatial_3d",
                "available_for_experiment":True,
                "world":{"world_key":key,"world_name":name,"definition":definition,"assets":[]}}
    maps = [detail(selected,"map-15"),detail("classroom-map","map-2","会议讨论场景")]
    if public:
        planar = detail('planar-map', None, '平面验收地图')
        planar['environment_type'] = 'spatial_2d'
        planar['world']['definition'] = {'environment_type':'spatial_2d','size':[32,48],'tile_size':32}
        social = detail('social-map', None, '群聊验收地图')
        social['environment_type'] = 'social_1d'
        social['world']['definition'] = {'environment_type':'social_1d','presentation':'group_chat','description':'交流场所背景'}
        maps.extend([planar, social])
    events = []
    frame = io.BytesIO()
    Image.new("RGB",(1280,720),"#556456").save(frame,format="JPEG")
    pose = dict(POSE)
    count = seq = 0
    def serve(route):
        nonlocal count, seq, pose
        path = urlparse(route.request.url).path
        if path in ("/","/experiments/demo"):
            return route.fulfill(content_type="text/html; charset=utf-8",body=shell)
        if path.startswith("/static/console/"):
            target=(STATIC/path.removeprefix("/static/console/")).resolve()
            target.relative_to(STATIC.resolve())
            return route.fulfill(path=target)
        if path in (boundary+"/maps","/api/studio/resources/maps"):
            if route.request.method=="POST":
                assert route.request.post_data_json=={"name":"街区教学地图","map_key":"teaching-map","description":"用于出行教学", "environment_type":"spatial_3d","simworld_scene_id":"map-15"}
                created=detail("created-map","map-15","街区教学地图")
                created["map_key"]=created["world"]["world_key"]="teaching-map"
                created["description"]="用于出行教学"
                maps.append(created)
                events.append("create")
                return route.fulfill(json=created)
            return route.fulfill(json={"items":maps,"total":len(maps),"total_pages":1,"page":1})
        if path == boundary+"/maps/demo/selection":
            assert route.request.method=="PUT"
            assert route.request.post_data_json=={"map_id":"classroom-map","expected_content_sha256":1}
            maps[0]["world"]["definition"]["simworld_scene_id"]="map-2"
            events.append("replace")
            return route.fulfill(json=maps[0])
        if path.startswith(boundary+"/maps/"):
            return route.fulfill(json=next(m for m in maps if m["id"]==path.rsplit("/",1)[1]))
        if path == boundary+"/skills":
            return route.fulfill(json={"items":[],"page":1,"total_pages":1})
        if path == "/api/studio/simworld/scenes":
            return route.fulfill(json={"scenes":[
                {"id":"map-15","name":"Tokyo","previewable":True},
                {"id":"map-2","name":"Classroom","previewable":True},
                {"id":"missing","name":"Not installed","previewable":False}]})
        if path==base or path.startswith(base+"/"):
            action="open" if path==base else path.rsplit("/",1)[1]
            events.append(action)
            if action in ("stream","snapshot.jpg"):
                return route.fulfill(content_type="image/jpeg",body=frame.getvalue())
            if action=="open": count+=1
            if action=="preset": seq+=1
            if action=="pose":
                seq+=1
                pose=route.request.post_data_json
            data={"session_id":str(count),"state":"ready","pose":pose,"request_seq":seq,"applied_seq":seq,
                  "presets":[{"id":"start","name":"初始机位","pose":POSE},{"id":"east","name":"东街口","pose":POSE}]}
            if action=="open":
                data["agent_views"]=[] if public else [{"agent_key":"a","name":"林青","pose":EYE}]
            return route.fulfill(json=data)
        return route.abort()
    page.route("**/*",serve)
    page.goto("http://viewer.test"+("/" if public else "/experiments/demo")+"?view=maps")
    for css in ("shell/console-ux.css","resources/map-workspace.css","resources/resource-list.css"):
        page.add_style_tag(content=(STATIC/css).read_text(encoding="utf8"))
    page.evaluate("""(publicMap)=>{
      if(!publicMap)document.body.classList.add('experiment-workspace');
      document.querySelectorAll('.page').forEach(p=>p.classList.toggle('active',p.id==='page-maps'));
      createMapBtn.hidden=false;
      window.addEventListener('map-workspace:selection',e=>{
        const selected=Boolean(e.detail.mapId);
        document.body.classList.toggle('map-editor-mode',selected);
        mapEditorTopbarContext.hidden=!selected;defaultTopbarContext.hidden=selected;hubActions.hidden=selected;
      });
      window.addEventListener('map-workspace:modal',e=>document.getElementById(e.detail.id).classList.toggle('open',e.detail.action==='open'));
    }""",public)
    for script in ("resources/resource-scope.js","resources/resource-list.js","resources/simworld-scenes.js","shell/workspace-loader.js","resources/map-workspace.js"):
        page.add_script_tag(content=(STATIC/script).read_text(encoding="utf8"))
    page.evaluate("MapWorkspace.activate()")
    if public:
        assert page.get_by_role('button',name='本地地图名称',exact=True).is_visible()
        page.locator('#createMapBtn').click()
        page.locator('#newMapEnvironmentType').select_option('spatial_3d')
        page.locator('#newMapName').fill('街区教学地图')
        page.locator('#newMapKey').fill('teaching-map')
        page.locator('#newMapDescription').fill('用于出行教学')
        page.locator('#newMapScene').select_option('simworld:map-15')
        assert page.locator('#newMapName').is_visible() and page.locator('#newMapKey').is_visible()
        assert page.locator('#newMapDescription').is_visible() and page.locator('#newMapWidth').is_hidden()
        page.locator('#confirmCreateMap').click()
    page.wait_for_function("document.querySelector('[data-live]')?.naturalWidth===1280")
    stage=page.locator(".scene-stage")
    assert stage.bounding_box()["width"]>1000
    metadata = ("街区教学地图", "teaching-map", "用于出行教学") if public else ("本地地图名称", "demo", "本地用途说明")
    assert_map_overlay(page, metadata)
    assert "Tokyo" not in page.locator('.scene-map-info').inner_text()
    assert page.locator("[data-agents]").is_visible()!=public
    assert not page.locator("[data-feedback]").is_visible()
    page.get_by_role("button",name="东街口").click()
    page.wait_for_function("document.querySelector('[data-view-id=east]')?.getAttribute('aria-pressed')==='true'")
    stage.focus();page.keyboard.down("w");page.wait_for_timeout(220);page.keyboard.up("w")
    page.wait_for_function("document.querySelector('[data-view-id=east]')?.getAttribute('aria-pressed')==='false'")
    assert "pose" in events
    page.evaluate("MapWorkspace.deactivate()")
    page.wait_for_function("!document.querySelector('[data-live]').getAttribute('src')")
    page.evaluate("MapWorkspace.activate()")   # Returning to the same URL opens a fresh session.
    page.wait_for_function("document.querySelector('[data-live]')?.naturalWidth===1280")
    assert events.count("open")==2 and "close" in events
    if public:
        page.get_by_role("button",name="全屏",exact=True).click()
        page.wait_for_function("Boolean(document.fullscreenElement)")
        assert_map_overlay(page, metadata, fullscreen=True)
        page.get_by_role("button",name="← 公共地图列表").click()
        page.wait_for_function("!document.fullscreenElement && !mapCatalogShell.hidden")
        assert page.locator(".topbar").is_visible() and page.locator("#createMapBtn").is_visible()
        assert events.count("create")==1
        page.get_by_role("button",name="街区教学地图",exact=True).click()
        page.wait_for_function("document.querySelector('[data-live]')?.naturalWidth===1280")
        assert events.count("open")==3
        assert page.locator('#saveMapBtn').is_hidden()
        assert not page.get_by_role("button",name="地图设置",exact=True).count()
        assert_map_overlay(page, metadata)
        page.get_by_role('button', name='← 公共地图列表').click()
        page.get_by_role('button', name='平面验收地图', exact=True).click()
        for width in (1341, 1024, 800):
            page.set_viewport_size({'width':width,'height':884})
            page.locator('.main').evaluate("el=>Promise.all(el.getAnimations({subtree:true}).map(a=>a.finished.catch(()=>{})))")
            assert_planar_layout(page)
        page.get_by_role('button', name='地图列表', exact=True).click()
        page.get_by_role('button', name='群聊验收地图', exact=True).click()
        social_form = page.locator('.social-map-editor')
        assert social_form.get_by_label('地图名称', exact=True).input_value() == '群聊验收地图'
        assert social_form.get_by_label('稳定键', exact=True).input_value() == 'social-map'
        assert social_form.get_by_label('地图类型', exact=True).input_value() == '1D 社交空间'
        assert social_form.get_by_label('展现方式', exact=True).input_value() == '群聊'
        assert social_form.get_by_label('用途说明', exact=True).input_value() == '交流场所背景'
        assert page.locator('.social-map-editor p,.social-map-editor small').count() == 0
        assert page.locator('.social-map-editor [data-map-validation]').is_hidden()
        assert page.locator('.social-map-editor').bounding_box()['width'] > 500
        page.get_by_role('button', name='地图列表', exact=True).click()
        page.get_by_role('button', name='街区教学地图', exact=True).click()
        page.wait_for_function("document.querySelector('[data-live]')?.naturalWidth===1280")
        assert_map_overlay(page, metadata)
        page.get_by_role('button', name='← 公共地图列表').click()
        page.get_by_role('button', name='平面验收地图', exact=True).click()
        assert_planar_layout(page)
    else:
        page.get_by_role("button",name="更换实验地图").click()
        page.locator('#replacementMap').select_option('classroom-map')
        page.locator('#confirmReplaceMap').click()
        page.wait_for_function("!document.getElementById('replaceMapModal').classList.contains('open')")
        assert events.count("replace")==1
        page.wait_for_function("document.querySelector('[data-live]')?.naturalWidth===1280")
        assert_map_overlay(page, metadata)
    page.close()
    print(f"{family}: shared session open, camera controls, wide frame and navigation passed")


def check_fact_only_replay(browser, scene_id):
    page=browser.new_page(viewport={"width":1200,"height":760})
    opened=[]
    def serve(route):
        path=urlparse(route.request.url).path
        if path=="/":
            return route.fulfill(content_type="text/html",body='<div class="timeline-layout"><div class="result-map" id="map"><canvas></canvas></div></div>')
        if path.endswith("/manifest"):
            geometry={"environment_type":"spatial_3d"}
            if scene_id:
                geometry["simworld_scene_id"]=scene_id
            else:
                geometry["prebuilt_scene"]={"map_uri":"/Game/DoNotOpen"}
            return route.fulfill(json={"schema_version":2,"run_id":"history","experiment_id":"e","available_step":2,
                "world":{"world_name":"历史街区","definition":geometry},"agents":[{"agent_key":"walker","display_name":"小林"}]})
        if path.endswith("/steps"):
            return route.fulfill(json={"run_id":"history","available_step":2,"result_version":2,
                "steps":[{"step_no":n,"agents":[{"agent_key":"walker","coord":[n,0,0],"action":{"description":f"走到第 {n} 米"}}]} for n in (1,2)]})
        if path=="/api/studio/simworld/runs/history/sessions":
            opened.append(path)
            return route.fulfill(status=503,json={"detail":"场景服务不可用"})
        raise AssertionError(path)
    page.route("**/*",serve)
    page.goto("http://replay.test/")
    for css in ("resources/map-workspace.css","replay/street-replay.css"):
        page.add_style_tag(content=(STATIC/css).read_text(encoding="utf8"))
    page.add_style_tag(content=".timeline-layout{height:520px}.result-map{width:70%;height:100%}")
    for script in ("replay/replay-player.js","resources/scene-map-viewer.js","replay/street-camera.js","replay/street-replay.js"):
        page.add_script_tag(content=(STATIC/script).read_text(encoding="utf8"))
    page.evaluate("""async()=>{
      window.player=new GAStreetReplayPlayer({canvas:document.querySelector('canvas'),container:document.querySelector('#map'),isTimelineVisible:()=>true});
      await player.loadRun('history');
    }""")
    if scene_id:
        assert opened==["/api/studio/simworld/runs/history/sessions"]
        assert page.get_by_text("UE 实景已断开 · 场景服务不可用",exact=True).is_visible()
        assert page.get_by_role("button",name="重连").is_visible()
        assert page.locator('[data-live]').is_hidden()
        assert not page.locator('.scene-viewer-map').count()
        assert page.locator('[data-live]').evaluate("image=>getComputedStyle(image).objectFit") == "contain"
        page.locator('.street-replay > details > summary').click()
    else:
        assert not opened and page.get_by_text("此地图没有关联 SimWorld 在线场景。").is_visible()
        assert not page.locator('[data-live]').count()
    for n in (1,2,1):
        page.evaluate("(n)=>player.seek(n)",n)
        assert page.get_by_text(f"小林 · 位置 [{n}, 0, 0]",exact=True).is_visible()
    assert "undefined" not in page.locator(".street-replay").inner_text()
    page.close()


def check():
    shell = (STATIC / "shell/experiment-console.html").read_text(encoding="utf8")
    shell = re.sub(r"<script\b[^>]*>[\s\S]*?</script>|<link\b[^>]*>", "", shell, flags=re.I)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=os.environ.get("GA_TEST_CHROME"))
        page = browser.new_page(viewport={"width": 1341, "height": 884})
        page.route("**/*", lambda route: route.abort())
        page.set_content(shell)
        for file in ("shell/console-ux.css", "resources/resource-list.css", "resources/map-workspace.css"):
            page.add_style_tag(content=(STATIC / file).read_text(encoding="utf8"))
        page.add_script_tag(content=(STATIC / "resources/scene-map-viewer.js").read_text(encoding="utf8"))
        page.evaluate("""({pose,eye})=>{
          document.body.classList.add('experiment-workspace','map-editor-mode');
          document.querySelectorAll('.page').forEach(el=>el.classList.toggle('active',el.id==='page-maps'));
          mapCatalogShell.hidden=true; mapEditorShell.hidden=false; mapEditorShell.querySelector('nav').hidden=true;
          publicMapEditor.replaceChildren(document.createElement('div'));
          const v=window.fixture=new SceneMapViewer(publicMapEditor.firstChild);
          v.connect=()=>{};v.setWorld({world_name:'Test only',definition:{simworld_scene_id:'map-15'}},{sessionBase:'/isolated',
            mapInfo:{name:'本地教学地图',key:'local-teaching-map',description:'本地用途说明，与远端场景名称独立。'}});
          window.calls=[];
          let state={state:'ready',pose,request_seq:0,applied_seq:0,frame_seq:1,
            presets:[{id:'start',name:'起点',pose},{id:'cross',name:'街口',pose}],
            agent_views:[{agent_key:'alex',name:'阿力',pose:eye}]};
          window.fetch=async(url,options)=>{
            const body=JSON.parse(options.body);calls.push({url,body});
            state={...state,request_seq:state.request_seq+1};
            const target=url.endsWith('agent-view')?eye:url.endsWith('/pose')?body:pose;
            setTimeout(()=>{state={...state,applied_seq:state.request_seq,pose:target};v.updateState(state);},80);
            return Response.json(state);
          };
          v.url='/isolated';v.currentView={type:'preset',id:'start'};v.frameStarted=performance.now();
          v.bindControls();v.updateState(state);
        }""", {"pose": POSE, "eye": EYE})
        assert page.get_by_text("正在接收实景画面…").is_visible()
        assert page.locator('.scene-map-info').get_by_text('本地教学地图', exact=True).is_visible()
        for width, height, source_width, source_height in [
            (1341, 884, 640, 480), (1341, 884, 1280, 720), (390, 844, 640, 480), (1024, 600, 1280, 720)
        ]:
            page.set_viewport_size({"width": width, "height": height})
            jpeg = io.BytesIO()
            Image.new("RGB", (source_width, source_height), "#536b62").save(jpeg, format="JPEG")
            page.evaluate("data=>fixture.live.src='data:image/jpeg;base64,'+data", base64.b64encode(jpeg.getvalue()).decode())
            page.wait_for_function("w=>fixture.hasLiveFrame && fixture.live.naturalWidth===w", arg=source_width)
            page.wait_for_timeout(300)  # Allow responsive layout and sidebar width transitions.
            geometry = assert_map_overlay(page, ('本地教学地图', 'local-teaching-map', '本地用途说明，与远端场景名称独立。'))
            x, y, w, h = geometry["image"]
            assert not page.locator(".topbar").is_visible() and not page.locator("#experimentScopeNotice").is_visible()
            print(f"{width}x{height}, source {source_width}x{source_height} -> filled {w}x{h}, controls overlay inside frame")
        person = page.get_by_role("button", name="阿力")
        person.click()
        page.wait_for_function("fixture.currentView.type==='agent'")
        assert person.get_attribute("aria-pressed") == "true"
        page.locator(".scene-stage").focus()
        page.keyboard.down("w")
        page.wait_for_timeout(240)
        page.keyboard.up("w")
        assert len(page.evaluate("calls")) == 1
        page.locator(".scene-stage").dispatch_event("wheel", {"deltaY": -2})
        page.wait_for_function("calls.length===2")
        assert page.evaluate("calls.at(-1).body.position") == EYE["position"]
        page.get_by_role("button", name="返回自由视角").click()
        page.wait_for_function("fixture.currentView.type==='free'")
        assert page.evaluate("calls.at(-1).body") == POSE
        page.get_by_role("button", name="街口").click()
        page.wait_for_function("fixture.currentView.id==='cross'")
        assert page.get_by_role("button", name="街口").get_attribute("aria-pressed") == "true"
        page.set_viewport_size({"width": 1341, "height": 884})
        page.evaluate("""data=>{
          fixture.setWorld({world_name:'Replay only',definition:{simworld_scene_id:'map-15'}},
            {sessionBase:'/isolated',replay:{runId:'layout-check'}});
          fixture.ready=true;fixture.canControl=true;fixture.frameStarted=performance.now();
          fixture.live.src='data:image/jpeg;base64,'+data;
        }""", base64.b64encode(jpeg.getvalue()).decode())
        page.wait_for_function("fixture.hasLiveFrame && fixture.live.naturalWidth===1280")
        assert not page.locator('.scene-viewer-map').count()
        replay_geometry=page.evaluate("""()=>{
          const image=fixture.live.getBoundingClientRect(),controls=fixture.root.querySelector('.scene-controls').getBoundingClientRect();
          return {width:image.width,height:image.height,imageBottom:image.bottom,controlsTop:controls.top,
            fit:getComputedStyle(fixture.live).objectFit};
        }""")
        assert replay_geometry["fit"]=="contain", replay_geometry
        assert abs(replay_geometry["width"]/replay_geometry["height"]-1280/720)<.01, replay_geometry
        assert 0 <= replay_geometry["controlsTop"]-replay_geometry["imageBottom"] <= 12, replay_geometry
        page.close()
        for family in ("maps","experiments"):
            check_workspace(browser,shell,family)
        for scene_id in (None,"map-15"):
            check_fact_only_replay(browser,scene_id)
        browser.close()


if __name__ == "__main__":
    check()
