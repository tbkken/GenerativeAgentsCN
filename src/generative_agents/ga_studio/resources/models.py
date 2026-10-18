"""Author model connections, credentials and optional probes."""
from generative_agents.ga_studio.storage.credentials import HostModelCredentials
from generative_agents.ga_studio.storage.model_settings import StudioModelSettings
from uuid import uuid4
import httpx
from pydantic import BaseModel, ConfigDict, Field, AnyHttpUrl
from generative_agents.ga_protocol.schemas.experiment import ChatVLLMConfig, DecisionModelConfig
from generative_agents.ga_protocol.schemas.experiment import EmbeddingOpenAICompatibleConfig
from generative_agents.ga_studio.resources.catalog import StudioResourceService
from generative_agents.ga_studio.resources.catalog import StudioResourceError
from generative_agents.ga_protocol.schemas.errors import ServiceError
from generative_agents.ga_studio.storage.models import StudioResourceExchangeState

class ModelServiceInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(pattern='^(chat|embedding|decision)$')
    base_url: AnyHttpUrl
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=8000)
    clear_api_key: bool = False
    row_version: int | None = None
    timeout_seconds: int = Field(default=90, ge=1, le=600)
    max_tokens: int = Field(default=2048, ge=1, le=131072)
    temperature: float = Field(default=0.2, ge=0, le=2)
    retry_attempts: int | None = Field(default=None, ge=1, le=5)
    retry_backoff_seconds: float | None = Field(default=None, ge=0, le=300)
    decision_compiler: bool | None = None

class ModelServiceError(ServiceError):

    def __init__(self, status, message):
        super().__init__('MODEL_SERVICE_ERROR', message, status_code=status)

class ModelService:

    def __init__(self, database, root):
        self.resources = StudioResourceService(database)
        self.credentials = HostModelCredentials(database, root)
        self.settings = StudioModelSettings(root)

    def detail(self, item):
        configs = {purpose: config for purpose, config in item['config'].items() if isinstance(config, dict)}
        aliases = self.credentials.configured(config.get('credential_env') for config in configs.values())
        configured = {purpose: aliases[config.get('credential_env')] for purpose, config in configs.items()}
        return {**item, 'credential_configured': configured,
                'decision_compiler': self.settings.decision_compiler_model_id() == item['id']}

    def require(self, model_id):
        try:
            return self.resources.get_model_preset(model_id)
        except StudioResourceError as exc:
            raise ModelServiceError(404, str(exc)) from exc

    def save(self, body, previous=None):
        if body.decision_compiler is not None and body.purpose != 'chat':
            raise ModelServiceError(422, '只有生成式模型可用于解析决策 Skill。')
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
            imported_alias = None
            if previous and alias:
                with self.resources.database.session_factory() as session:
                    if session.get(StudioResourceExchangeState, ('model', previous['id'])) is not None:
                        imported_alias = alias
            alias = self.credentials.store(body.api_key, alias=imported_alias)
        providers = {'chat': 'vllm', 'embedding': 'openai_compatible', 'decision': 'unsloth'}
        transport = {'provider': providers[body.purpose], 'base_url': str(body.base_url).rstrip('/'), 'model': body.model.strip(), 'timeout_seconds': body.timeout_seconds, 'credential_env': alias}
        if not transport['model'] or transport['model'].casefold() == 'auto':
            raise ModelServiceError(422, '请填写明确的模型 ID。')
        if body.purpose == 'chat':
            transport.update(max_tokens=body.max_tokens, temperature=body.temperature)
            for field in ('retry_attempts', 'retry_backoff_seconds'):
                value = getattr(body, field)
                if value is not None:
                    transport[field] = value
                elif field in old:
                    transport[field] = old[field]
        schema = {'chat': ChatVLLMConfig, 'embedding': EmbeddingOpenAICompatibleConfig,
                  'decision': DecisionModelConfig}[body.purpose]
        config[body.purpose] = schema.model_validate(transport).model_dump(mode='json')
        try:
            if previous:
                if body.row_version != previous['row_version']:
                    raise ModelServiceError(409, '配置已被其他操作修改，请重新打开后保存。')
                saved = self.resources.save_model_preset(previous['id'], config=config, expected_row_version=body.row_version, name=body.name)
            else:
                saved = self.resources.create_model_preset(name=body.name, config=config, preset_key='model-' + uuid4().hex[:16])
            if body.decision_compiler is not None:
                self.settings.set_decision_compiler(saved['id'], body.decision_compiler)
            return self.detail(saved)
        except StudioResourceError as exc:
            raise ModelServiceError(422, str(exc)) from exc

    def list_models(self, *, query='', page=1, page_size=20, purpose=None):
        result = self.resources.list_summaries('model', query=query, page=page, page_size=page_size, purpose=purpose)
        configured = self.credentials.configured(config.get('credential_env')
            for item in result['items'] for config in item['purposes'])
        compiler_id = self.settings.decision_compiler_model_id()
        for item in result['items']:
            item['decision_compiler'] = compiler_id == item['id']
            item['credential_configured'] = {}
            for config in item['purposes']:
                config['credential_configured'] = configured[config.pop('credential_env')]
                item['credential_configured'][config['purpose']] = config['credential_configured']
        return result

    def get_model(self, model_id):
        return self.detail(self.require(model_id))

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
        if purpose not in ('chat', 'embedding', 'decision') or not config:
            raise ModelServiceError(422, '该配置没有所选类型的模型。')
        try:
            key = self.credentials.resolve(config.get('credential_env'))
            endpoint = {'chat': '/chat/completions', 'embedding': '/embeddings', 'decision': '/systemone'}[purpose]
            payload = {'model': config['model']}
            if purpose == 'chat':
                payload.update(messages=[{'role': 'user', 'content': 'Reply OK.'}], max_tokens=16)
            elif purpose == 'decision':
                payload.update(state='这是一次模型服务连接测试。', questions={
                    'connection': {'type': 'noul', 'instructions': '这段文字是否在进行连接测试？'}})
            else:
                payload['input'] = ['连接测试']
            timeout = config['timeout_seconds'] if purpose == 'decision' else 20
            with httpx.Client(timeout=timeout) as client:
                response = client.post(config['base_url'].rstrip('/') + endpoint, json=payload, headers={'Authorization': f'Bearer {key}'} if key else {})
            if response.status_code in (401, 403):
                raise ModelServiceError(422, '认证失败，请在模型中心填写或更新有效的 API Key。')
            if not response.is_success:
                raise ModelServiceError(422, f'模型服务返回 HTTP {response.status_code}，请检查服务地址和模型 ID。')
            data = response.json()
            if purpose == 'chat':
                if not data.get('choices'):
                    raise ValueError('生成式模型响应缺少 choices。')
                return {'ok': True, 'message': '生成式模型调用成功。'}
            if purpose == 'decision':
                answer = data['answers']['connection']
                probability = answer['noul']
                if (answer.get('type') != 'noul' or isinstance(probability, bool)
                        or not isinstance(probability, (int, float)) or not 0 <= probability <= 1):
                    raise ValueError('决策模型响应缺少有效的 noul 结果。')
                return {'ok': True, 'message': '决策模型调用成功。'}
            vector = data['data'][0]['embedding']
            if not vector:
                raise ValueError('Embedding 返回空向量。')
            return {'ok': True, 'message': f'Embedding 调用成功，向量维度 {len(vector)}。'}
        except ModelServiceError:
            raise
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ModelServiceError(422, '密钥未配置或模型响应格式不正确，请检查配置。') from exc
        except httpx.HTTPError as exc:
            raise ModelServiceError(422, '无法连接模型服务或请求超时，请检查服务地址和运行状态。') from exc
