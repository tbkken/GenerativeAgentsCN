"""Studio-owned encrypted model credentials; Run workers receive environment values only."""
import os
import re
import threading
from pathlib import Path
from uuid import uuid4
from generative_agents.ga_protocol.packages.io import read_json, atomic_write_json
from generative_agents.ga_studio.resources.assets import SecretService

class HostModelCredentials:
    _lock = threading.Lock()

    def __init__(self, database, root):
        self.database = database
        self.path = Path(root) / 'model-credentials.json'
        self.secrets = SecretService(database, var_dir=root)

    def store(self, value, *, alias=None):
        """Bind an unconfigured imported name, or rotate to a fresh name.

        Existing bindings and process environment values always retain their
        meaning for already copied experiments and Runs.
        """
        if alias is not None and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', alias):
            raise ValueError('模型凭据环境变量名称不合法。')
        with self._lock:
            bindings = read_json(self.path) if self.path.exists() else {}
            if not alias or alias in bindings or os.getenv(alias, ''):
                alias = 'GA_MODEL_' + uuid4().hex.upper()
            secret = self.secrets.create(kind='GENERIC_TOKEN', value=value)
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

    def configured(self, aliases):
        """Report presence in one metadata query without decrypting author secrets."""
        from sqlalchemy import select
        from generative_agents.ga_studio.storage.models import Secret
        aliases = set(aliases)
        bindings = read_json(self.path) if self.path.exists() else {}
        requested = {bindings[alias] for alias in aliases if alias in bindings}
        with self.database.session_factory() as session:
            present = set(session.scalars(select(Secret.id).where(Secret.id.in_(requested)))) if requested else set()
        return {alias: bool(alias) and (bindings[alias] in present if alias in bindings else bool(os.getenv(alias, '')))
                for alias in aliases}

    def run_environment(self, run_root):
        from generative_agents.ga_protocol.packages.definition import _experiment_definition
        experiment = Path(run_root) / 'experiment'
        _manifest, definition = _experiment_definition(experiment)
        models = definition['models']
        return {item['credential_env']: self.resolve(item['credential_env'])
                for purpose in ('chat', 'embedding')
                if (item := models.get(purpose, {})).get('credential_env')}
