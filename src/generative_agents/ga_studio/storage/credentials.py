"""Studio-owned encrypted model credentials; Run workers receive environment values only."""
import os
import threading
from pathlib import Path
from uuid import uuid4
from generative_agents.ga_protocol.packages.io import read_json, atomic_write_json
from generative_agents.ga_studio.resources.assets import SecretService

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
