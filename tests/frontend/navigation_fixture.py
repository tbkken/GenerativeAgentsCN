"""Isolated browser fixture: real editor assets and real navigation HTTP API."""
from pathlib import Path
import runpy

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from generative_agents.ga_studio.resource_api import create_resource_router
from generative_agents.services.maps import normalize_public_world

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / 'generative_agents/web/static'
app = FastAPI()
app.include_router(create_resource_router(None, None))
app.mount('/static/console', StaticFiles(directory=STATIC), name='static')


@app.get('/fixture')
def fixture():
    world = runpy.run_path(str(ROOT / 'tests/foundation/test_navigation.py'))['navigation_world']()
    return normalize_public_world(world).model_dump(mode='json')


@app.get('/', response_class=HTMLResponse)
def page():
    console = (STATIC / 'experiment-console.html').read_text(encoding='utf-8')
    style = console.split('<style>', 1)[1].split('</style>', 1)[0]
    return ('<!doctype html><html><head><style>'+style+'</style>'
        '<link rel="stylesheet" href="/static/console/map-workspace.css">'
        '<style>body{display:block;overflow:hidden;padding:16px}.map-editor-v2{height:calc(100vh - 32px);min-height:0;display:grid;grid-template-rows:54px auto minmax(0,1fr)}.me2-layout{height:auto;min-height:0}</style>'
        '</head><body><div id="editor"></div>'
        '<script src="/static/console/map-navigation.js"></script>'
        '<script src="/static/console/map-editor-v2.js"></script>'
        '<script>MapEditorV2.prototype.loadDocument=async function(){};'
        'window.editor=new MapEditorV2(document.getElementById("editor"));'
        'editor.root.addEventListener("map-editor-v2:request-edit",()=>editor.createMaterialCanvas());'
        'fetch("/fixture").then(r=>r.json()).then(w=>editor.setWorld(w));</script></body></html>')
