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


def check_workspace(browser, shell, family):
    """Drive the real map manager against scoped test responses, without starting UE or Studio."""
    page = browser.new_page(viewport={"width": 1341, "height": 884})
    public = family == "maps"
    boundary = "/api/studio/resources" if public else "/api/studio/experiments/demo/resources"
    selected = "map-15" if public else "demo"
    base = f"/api/studio/simworld/{'scenes' if public else family}/{selected}/sessions"
    geometry = {"environment_type":"spatial_3d"}
    def detail(key, has_scene):
        name = "预制场景" if has_scene else "历史地图"
        definition = {**geometry, **({"simworld_scene_id":"map-15"} if has_scene else {})}
        return {"id":key,"map_key":key,"name":name,"editable":True,"row_version":1,"environment_type":"spatial_3d",
                "has_prebuilt_scene":has_scene,"available_for_experiment":has_scene,
                "world":{"world_key":key,"world_name":name,"definition":definition,"assets":[]}}
    maps = [detail(selected,True),detail("legacy",False)]
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
            return route.fulfill(json={"items":maps,"total":len(maps),"total_pages":1,"page":1})
        if path == boundary+"/maps/demo/selection":
            assert route.request.method=="PUT"
            assert route.request.post_data_json=={"simworld_scene_id":"map-2","expected_content_sha256":1}
            maps[0]["world"]["definition"]["simworld_scene_id"]="map-2"
            events.append("replace")
            return route.fulfill(json=maps[0])
        if path.startswith(boundary+"/maps/"):
            return route.fulfill(json=next(m for m in maps if m["id"]==path.rsplit("/",1)[1]))
        if path in (boundary+"/map-blueprints","/api/studio/simworld/scenes"):
            return route.fulfill(json=[] if path.endswith("map-blueprints") else {"scenes":[
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
      window.addEventListener('map-workspace:selection',e=>document.body.classList.toggle('map-editor-mode',Boolean(e.detail.mapId)));
      window.addEventListener('map-workspace:modal',e=>document.getElementById(e.detail.id).classList.toggle('open',e.detail.action==='open'));
    }""",public)
    for script in ("resources/resource-scope.js","resources/resource-list.js","resources/simworld-scenes.js","shell/workspace-loader.js","resources/map-workspace.js"):
        page.add_script_tag(content=(STATIC/script).read_text(encoding="utf8"))
    page.evaluate("MapWorkspace.activate()")
    if public:
        assert page.get_by_text("3D · 未保存 UE 场景").is_visible()
        page.locator('#publicScene').select_option('simworld:map-15')
        page.locator('#openPublicScene').click()
    page.wait_for_function("document.querySelector('[data-live]')?.naturalWidth===1280")
    stage=page.locator(".scene-stage")
    assert stage.bounding_box()["width"]>1000
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
        page.get_by_role("button",name="← 公共地图列表").click()
        page.wait_for_function("!document.fullscreenElement && !mapCatalogShell.hidden")
        assert page.locator(".topbar").is_visible() and page.locator("#createMapBtn").is_visible()
        page.locator('.resource-name[data-map-id=legacy]').click()
        assert page.get_by_text("此地图没有关联 SimWorld 在线场景。").is_visible()
        assert not page.locator("[data-map-audit-cards]").count()
        assert events.count("open")==2
        page.get_by_role("button",name="从 SimWorld 目录选图").click()
        assert page.locator("#newMapEnvironmentType").input_value()=="spatial_3d"
        page.locator('#newMapScene').select_option('simworld:map-15')
        assert page.locator('#newMapRules').is_hidden() and page.locator('#newMapWidth').is_hidden()
        assert not page.locator('#mapScenePicker img').count() and page.locator('#mapSceneInfo').is_hidden()
        page.locator('#confirmCreateMap').click()
        page.wait_for_function("document.querySelector('[data-live]')?.naturalWidth===1280")
        assert events.count("open")==3
    else:
        page.get_by_role("button",name="更换实验地图").click()
        page.locator('#replacementMap').select_option('simworld:map-2')
        page.locator('#confirmReplaceMap').click()
        page.wait_for_function("!document.getElementById('replaceMapModal').classList.contains('open')")
        assert events.count("replace")==1
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
          v.connect=()=>{};v.setWorld({world_name:'Test only',definition:{simworld_scene_id:'map-15'}},{sessionBase:'/isolated'});
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
        for width, height, source_width, source_height in [
            (1341, 884, 640, 480), (1341, 884, 1280, 720), (390, 844, 640, 480), (1024, 600, 1280, 720)
        ]:
            page.set_viewport_size({"width": width, "height": height})
            jpeg = io.BytesIO()
            Image.new("RGB", (source_width, source_height), "#536b62").save(jpeg, format="JPEG")
            page.evaluate("data=>fixture.live.src='data:image/jpeg;base64,'+data", base64.b64encode(jpeg.getvalue()).decode())
            page.wait_for_function("w=>fixture.hasLiveFrame && fixture.live.naturalWidth===w", arg=source_width)
            page.wait_for_timeout(300)  # Allow responsive layout and sidebar width transitions.
            geometry = page.evaluate("""()=>{
              const box=e=>{const r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height].map(Math.round)};
              return {image:box(fixture.live), main:box(document.querySelector('.main')),
                controls:box(fixture.root.querySelector('.scene-controls')), scroll:document.documentElement.scrollWidth};
            }""")
            x, y, w, h = geometry["image"]
            controls_top = geometry["controls"][1]
            assert w >= .93 * min(geometry["main"][2], (height - 70) * source_width / source_height), geometry
            assert abs(w / h - source_width / source_height) < .01, geometry
            assert 0 <= controls_top - (y + h) <= 12 and y < height / 3, geometry
            assert geometry["scroll"] <= width and x + w <= width + 1, geometry
            assert not page.locator(".topbar").is_visible() and not page.locator("#experimentScopeNotice").is_visible()
            print(f"{width}x{height}, source {source_width}x{source_height} -> {w}x{h}, controls gap {controls_top-y-h}px")
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
        page.close()
        for family in ("maps","experiments"):
            check_workspace(browser,shell,family)
        for scene_id in (None,"map-15"):
            check_fact_only_replay(browser,scene_id)
        browser.close()


if __name__ == "__main__":
    check()
