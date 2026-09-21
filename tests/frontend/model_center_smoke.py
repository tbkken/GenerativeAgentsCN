"""Run manually: python tests/frontend/model_center_smoke.py (isolated local services)."""
import json
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import uvicorn
from playwright.sync_api import sync_playwright
from generative_agents.adapters.web.app import create_studio_app


class Gateway(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        if self.headers.get('Authorization') != 'Bearer browser-test-key':
            self.send_response(401)
            self.end_headers()
            return
        data = ({'data': [{'embedding': [0.1, 0.2, 0.3]}]} if self.path.endswith('/embeddings') else {
            'id': 'test', 'object': 'chat.completion', 'choices': [{'index': 0, 'message': {
                'role': 'assistant', 'content': '项目进度已确认，明天提交测试结果。'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 10, 'total_tokens': 22}})
        encoded = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main():
    gateway = ThreadingHTTPServer(('127.0.0.1', 0), Gateway)
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='ga-model-ui-') as temp:
            app = create_studio_app(database_url='sqlite:///' + (Path(temp) / 'studio.db').as_posix(), var_dir=temp)
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            base = f'http://127.0.0.1:{sock.getsockname()[1]}'
            server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
            thread.start()
            try:
                for _ in range(100):
                    if server.started:
                        break
                    time.sleep(.1)
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(base + '/?view=model-catalog', wait_until='domcontentloaded')
                    page.locator('#modelServiceForm').wait_for()
                    ids = {}
                    for purpose in ['chat', 'embedding']:
                        page.locator('#addModelService-' + purpose).click()
                        page.locator('#modelServiceName').fill('浏览器测试-' + purpose)
                        page.locator('#modelServiceModel').fill('test-' + purpose)
                        page.locator('#modelServiceUrl').fill(f'http://127.0.0.1:{gateway.server_port}/v1')
                        page.locator('#modelServiceKey').fill('browser-test-key')
                        with page.expect_response(lambda r: r.url.endswith('/model-services') and r.request.method == 'POST') as response:
                            page.locator('#saveModelService').click()
                        result = response.value.json()
                        assert response.value.status == 201, result
                        ids[purpose] = result['id']
                        page.wait_for_function("document.getElementById('modelServiceStatus').textContent.includes('模型已保存')")
                        assert page.locator('#modelServiceKey').input_value() == ''
                        page.locator('#testModelService').click()
                        page.wait_for_function("document.getElementById('modelServiceStatus').textContent.includes('调用成功')")
                    page.reload(wait_until='domcontentloaded')
                    page.locator(f'[data-model-id="{ids["chat"]}"]').click()
                    assert page.locator('#modelServiceKey').input_value() == ''
                    assert '已配置' in page.locator('#modelServiceKey').get_attribute('placeholder')
                    page.request.post(base + '/api/studio/resources/skills', data={
                        'name': 'browser-social', 'description': '交流项目进度', 'kind': 'atomic'})
                    page.goto(base + '/?view=skills', wait_until='domcontentloaded')
                    page.wait_for_function('!!window.SkillWorkspace')
                    page.evaluate("async()=>{await SkillWorkspace.activate('skills');await SkillWorkspace.openSkill('browser-social')}")
                    page.locator('[data-skill-tab="run"]').click()
                    page.wait_for_function("!document.getElementById('skillRunModel').disabled")
                    page.locator('#skillRunModel').select_option(ids['chat'])
                    page.locator('#skillRunInput').fill('林晨向赵悦询问项目进度。')
                    page.locator('#skillRunButton').click()
                    page.locator('.skill-result-text').wait_for(timeout=30000)
                    assert '项目进度已确认' in page.locator('#skillRunOutput').inner_text()
                    assert page.locator('.skill-trace').is_visible()
                    page.goto(base + '/', wait_until='domcontentloaded')
                    page.locator('#createExperimentBtn').click()
                    page.locator('#newExperimentName').fill('模型选择测试')
                    page.locator('#wizardNext').click()
                    page.locator('#newExperimentChatModel').select_option(ids['chat'])
                    page.locator('#newExperimentEmbeddingModel').select_option(ids['embedding'])
                    assert not errors, errors
                    browser.close()
                    print('PASS: encrypted model setup, authenticated Chat/Embedding probes, reload, Skill result/trace and experiment selectors')
            finally:
                server.should_exit = True
                thread.join(10)
    finally:
        gateway.shutdown()


if __name__ == '__main__':
    main()
