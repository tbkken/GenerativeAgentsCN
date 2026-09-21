"""Author model connections, credentials and optional probes."""
from generative_agents.ga_studio.storage.credentials import HostModelCredentials
from uuid import uuid4
import httpx
from pydantic import BaseModel, ConfigDict, Field, AnyHttpUrl
from generative_agents.ga_protocol.schemas.experiment import ChatVLLMConfig
from generative_agents.ga_protocol.schemas.experiment import EmbeddingOpenAICompatibleConfig
from generative_agents.ga_studio.resources.catalog import StudioResourceService
from generative_agents.ga_studio.resources.catalog import StudioResourceError
from generative_agents.ga_protocol.schemas.errors import ServiceError

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

class ModelServiceError(ServiceError):

    def __init__(self, status, message):
        super().__init__('MODEL_SERVICE_ERROR', message, status_code=status)

class ModelService:

    def __init__(self, database, root):
        self.resources = StudioResourceService(database)
        self.credentials = HostModelCredentials(database, root)

    def detail(self, item):
        return {**item, 'credential_configured': {purpose: bool(config.get('credential_env')) for purpose, config in item['config'].items() if isinstance(config, dict)}}

    def require(self, model_id):
        try:
            return self.resources.get_model_preset(model_id)
        except StudioResourceError as exc:
            raise ModelServiceError(404, str(exc)) from exc

    def save(self, body, previous=None):
        if body.base_url.username or body.base_url.password or body.base_url.query or body.base_url.fragment:
            raise ModelServiceError(422, '服务地址请填写纯 API Base URL；密钥请填写到 API Key 字段。')
        if body.clear_api_key and body.api_key:
            raise ModelServiceError(422, '请在填写新密钥和清除密钥之间选择一项。')
        if previous and body.row_version != previous['row_version']:
            raise ModelServiceError(409, '配置已被其他操作修改，请重新打开后保存。')
        config = dict((previous or {}).get('config', {}))
        old = config.get(body.purpose, {})
        alias = old.get('credential_env')
        if body.clear_api_key:
            alias = None
        if body.api_key:
            alias = self.credentials.store(body.api_key)
        transport = {'provider': 'vllm' if body.purpose == 'chat' else 'openai_compatible', 'base_url': str(body.base_url).rstrip('/'), 'model': body.model.strip(), 'timeout_seconds': body.timeout_seconds, 'credential_env': alias}
        if not transport['model'] or transport['model'] == 'auto':
            raise ModelServiceError(422, '请填写明确的模型 ID。')
        if body.purpose == 'chat':
            transport.update(max_tokens=body.max_tokens, temperature=body.temperature, retry_attempts=1)
        schema = ChatVLLMConfig if body.purpose == 'chat' else EmbeddingOpenAICompatibleConfig
        config[body.purpose] = schema.model_validate(transport).model_dump(mode='json')
        try:
            if previous:
                if body.row_version != previous['row_version']:
                    raise ModelServiceError(409, '配置已被其他操作修改，请重新打开后保存。')
                saved = self.resources.save_model_preset(previous['id'], config=config, expected_row_version=body.row_version, name=body.name)
            else:
                saved = self.resources.create_model_preset(name=body.name, config=config, preset_key='model-' + uuid4().hex[:16])
            return self.detail(saved)
        except StudioResourceError as exc:
            raise ModelServiceError(422, str(exc)) from exc

    def list_models(self):
        return {'items': [self.detail(item) for item in self.resources.list_model_presets()]}

    def create(self, body: ModelServiceInput):
        return self.save(body)

    def update(self, model_id: str, body: ModelServiceInput):
        return self.save(body, self.require(model_id))

    def delete(self, model_id: str):
        self.require(model_id)
        self.resources.delete('model', model_id)

    def test(self, model_id: str, purpose: str):
        item = self.require(model_id)
        config = item['config'].get(purpose)
        if purpose not in ('chat', 'embedding') or not config:
            raise ModelServiceError(422, '该配置没有所选类型的模型。')
        try:
            key = self.credentials.resolve(config.get('credential_env'))
            endpoint = '/chat/completions' if purpose == 'chat' else '/embeddings'
            payload = {'model': config['model']}
            if purpose == 'chat':
                payload.update(messages=[{'role': 'user', 'content': 'Reply OK.'}], max_tokens=16)
            else:
                payload['input'] = ['连接测试']
            with httpx.Client(timeout=20) as client:
                response = client.post(config['base_url'].rstrip('/') + endpoint, json=payload, headers={'Authorization': f'Bearer {key}'} if key else {})
            if response.status_code in (401, 403):
                raise ModelServiceError(422, '认证失败，请在模型中心填写或更新有效的 API Key。')
            if not response.is_success:
                raise ModelServiceError(422, f'模型服务返回 HTTP {response.status_code}，请检查服务地址和模型 ID。')
            data = response.json()
            if purpose == 'chat':
                if not data.get('choices'):
                    raise ValueError('聊天响应缺少 choices。')
                return {'ok': True, 'message': '聊天模型调用成功。'}
            vector = data['data'][0]['embedding']
            if not vector:
                raise ValueError('Embedding 返回空向量。')
            return {'ok': True, 'message': f'Embedding 调用成功，向量维度 {len(vector)}。'}
        except ModelServiceError:
            raise
        except (ValueError, KeyError, IndexError) as exc:
            raise ModelServiceError(422, '密钥未配置或模型响应格式不正确，请检查配置。') from exc
        except httpx.HTTPError as exc:
            raise ModelServiceError(422, '无法连接模型服务或请求超时，请检查服务地址和运行状态。') from exc
