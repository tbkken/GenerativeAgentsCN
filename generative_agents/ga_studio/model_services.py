"""Model authoring and encrypted host credentials; never imported by Runtime."""

import os
import threading
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, AnyHttpUrl

from generative_agents.ga_protocol import atomic_write_json, read_json
from generative_agents.services.catalog import SecretService
from generative_agents.config.schema import ChatVLLMConfig, EmbeddingOpenAICompatibleConfig
from .resources import StudioResourceService, StudioResourceError


class HostModelCredentials:
    _lock = threading.Lock()

    def __init__(self, database, root):
        self.path = Path(root) / 'model-credentials.json'
        self.secrets = SecretService(database, var_dir=root)

    def store(self, value):
        # A fresh opaque environment name preserves credentials of existing packages.
        alias = 'GA_MODEL_' + uuid4().hex.upper()
        secret = self.secrets.create(kind='GENERIC_TOKEN', value=value)
        with self._lock:
            bindings = read_json(self.path) if self.path.exists() else {}
            bindings[alias] = secret['secret_id']
            atomic_write_json(self.path, bindings)
        return alias

    def resolve(self, alias):
        if not alias:
            return ''
        bindings = read_json(self.path) if self.path.exists() else {}
        if alias in bindings:
            return self.secrets.resolve_plaintext(bindings[alias])
        value = os.getenv(alias, '')
        if not value:
            raise ValueError('模型密钥在本机未配置，请在模型中心重新保存 API Key。')
        return value

    def run_environment(self, run_root):
        experiment = Path(run_root) / 'experiment'
        manifest = read_json(experiment / 'manifest.json')
        models = read_json(experiment / manifest['entrypoints']['models'])
        return {item['credential_env']: self.resolve(item['credential_env'])
                for purpose in ('chat', 'embedding')
                if (item := models.get(purpose, {})).get('credential_env')}


class ModelServiceInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(pattern='^(chat|embedding)$')
    base_url: AnyHttpUrl
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=8000)
    clear_api_key: bool = False
    row_version: int | None = None
    timeout_seconds: int = Field(default=90, ge=1, le=600)
    max_tokens: int = Field(default=2048, ge=1, le=131072)
    temperature: float = Field(default=0.2, ge=0, le=2)


def create_model_service_router(database, root):
    router = APIRouter(prefix='/api/studio/resources/model-services', tags=['models'])
    resources = StudioResourceService(database)
    credentials = HostModelCredentials(database, root)

    def detail(item):
        return {**item, 'credential_configured': {
            purpose: bool(config.get('credential_env'))
            for purpose, config in item['config'].items() if isinstance(config, dict)}}

    def require(model_id):
        try:
            return resources.get_model_preset(model_id)
        except StudioResourceError as exc:
            raise HTTPException(404, str(exc)) from exc

    def save(body, previous=None):
        if body.base_url.username or body.base_url.password or body.base_url.query or body.base_url.fragment:
            raise HTTPException(422, '服务地址请填写纯 API Base URL；密钥请填写到 API Key 字段。')
        if body.clear_api_key and body.api_key:
            raise HTTPException(422, '请在填写新密钥和清除密钥之间选择一项。')
        if previous and body.row_version != previous['row_version']:
            raise HTTPException(409, '配置已被其他操作修改，请重新打开后保存。')
        config = dict((previous or {}).get('config', {}))
        old = config.get(body.purpose, {})
        alias = old.get('credential_env')
        if body.clear_api_key:
            alias = None
        if body.api_key:
            alias = credentials.store(body.api_key)
        transport = {
            'provider': 'vllm' if body.purpose == 'chat' else 'openai_compatible',
            'base_url': str(body.base_url).rstrip('/'), 'model': body.model.strip(),
            'timeout_seconds': body.timeout_seconds, 'credential_env': alias,
        }
        if not transport['model'] or transport['model'] == 'auto':
            raise HTTPException(422, '请填写明确的模型 ID。')
        if body.purpose == 'chat':
            transport.update(max_tokens=body.max_tokens, temperature=body.temperature, retry_attempts=1)
        schema = ChatVLLMConfig if body.purpose == 'chat' else EmbeddingOpenAICompatibleConfig
        config[body.purpose] = schema.model_validate(transport).model_dump(mode='json')
        try:
            if previous:
                if body.row_version != previous['row_version']:
                    raise HTTPException(409, '配置已被其他操作修改，请重新打开后保存。')
                saved = resources.save_model_preset(previous['id'], config=config,
                    expected_row_version=body.row_version, name=body.name)
            else:
                saved = resources.create_model_preset(name=body.name, config=config,
                    preset_key='model-' + uuid4().hex[:16])
            return detail(saved)
        except StudioResourceError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get('')
    def list_models():
        return {'items': [detail(item) for item in resources.list_model_presets()]}

    @router.post('', status_code=201)
    def create(body: ModelServiceInput):
        return save(body)

    @router.put('/{model_id}')
    def update(model_id: str, body: ModelServiceInput):
        return save(body, require(model_id))

    @router.delete('/{model_id}', status_code=204)
    def delete(model_id: str):
        require(model_id)
        resources.delete('model', model_id)

    @router.post('/{model_id}/test/{purpose}')
    def test(model_id: str, purpose: str):
        item = require(model_id)
        config = item['config'].get(purpose)
        if purpose not in ('chat', 'embedding') or not config:
            raise HTTPException(422, '该配置没有所选类型的模型。')
        try:
            key = credentials.resolve(config.get('credential_env'))
            endpoint = '/chat/completions' if purpose == 'chat' else '/embeddings'
            payload = {'model': config['model']}
            if purpose == 'chat':
                payload.update(messages=[{'role': 'user', 'content': 'Reply OK.'}], max_tokens=16)
            else:
                payload['input'] = ['连接测试']
            with httpx.Client(timeout=20) as client:
                response = client.post(config['base_url'].rstrip('/') + endpoint, json=payload,
                    headers={'Authorization': f'Bearer {key}'} if key else {})
            if response.status_code in (401, 403):
                raise HTTPException(422, '认证失败，请在模型中心填写或更新有效的 API Key。')
            if not response.is_success:
                raise HTTPException(422, f'模型服务返回 HTTP {response.status_code}，请检查服务地址和模型 ID。')
            data = response.json()
            if purpose == 'chat':
                if not data.get('choices'):
                    raise ValueError('聊天响应缺少 choices。')
                return {'ok': True, 'message': '聊天模型调用成功。'}
            vector = data['data'][0]['embedding']
            if not vector:
                raise ValueError('Embedding 返回空向量。')
            return {'ok': True, 'message': f'Embedding 调用成功，向量维度 {len(vector)}。'}
        except HTTPException:
            raise
        except (ValueError, KeyError, IndexError) as exc:
            raise HTTPException(422, '密钥未配置或模型响应格式不正确，请检查配置。') from exc
        except httpx.HTTPError as exc:
            raise HTTPException(422, '无法连接模型服务或请求超时，请检查服务地址和运行状态。') from exc

    return router
