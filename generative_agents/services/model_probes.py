"""Explicit, bounded model connection probes for editable experiment Drafts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Literal
from urllib.parse import urlparse

import requests

from sqlalchemy import select

from generative_agents.config import ExperimentDefinition, canonical_json_bytes
from generative_agents.persistence import Database
from generative_agents.persistence.models import ModelProbeStatus
from generative_agents.status import ModelProbeState

from .catalog import SecretService
from .errors import ServiceError
from .experiments import ExperimentService


Purpose = Literal["chat", "embedding"]


class ModelProbeService:
    """Resolve ``auto`` and prove the configured endpoint can serve one tiny call."""

    def __init__(
        self,
        database: Database,
        *,
        experiments: ExperimentService,
        secrets: SecretService,
        session: requests.Session | None = None,
        max_timeout_seconds: int = 30,
    ) -> None:
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            database: 持久化数据库访问对象或会话工厂。 类型：`Database`。
            experiments: 传入当前算法的`experiments`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`ExperimentService`。
            secrets: 传入当前算法的`secrets`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`SecretService`。
            session: 当前数据库会话；事务提交与回滚由调用边界约定。 类型：`requests.Session | None`。 默认值：`None`。
            max_timeout_seconds: `timeout``seconds`允许的最大值。 类型：`int`。 默认值：`30`。

        返回:
            无返回值。
        """
        self._database = database
        self._experiments = experiments
        self._secrets = secrets
        self._session = session or requests.Session()
        self._max_timeout = max(1, max_timeout_seconds)

    def probe(
        self,
        experiment_id: str,
        purpose: Purpose,
        *,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        """执行 `ModelProbeService` 的`probe`操作。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            purpose: 模型用途键，用于从运行私有模型注册表选择对应模型。 类型：`Purpose`。
            expected_lock_version: 调用方读取草稿时看到的乐观锁版本；不一致表示发生并发修改。 类型：`int`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        if purpose not in {"chat", "embedding"}:
            raise ServiceError("INVALID_MODEL_PURPOSE", "模型用途无效", status_code=404)
        draft = self._experiments.get_draft(experiment_id)
        if draft["lock_version"] != expected_lock_version:
            raise ServiceError(
                "REVISION_CONFLICT",
                "草稿已被其他请求修改，请重新载入",
                status_code=409,
                details={
                    "expected_lock_version": expected_lock_version,
                    "actual_lock_version": draft["lock_version"],
                },
            )
        definition = ExperimentDefinition.model_validate(draft["definition"])
        definition, credential_source = self._with_local_unsloth_credential(
            definition, purpose
        )
        model = getattr(definition.models, purpose)
        started = perf_counter()
        config_hash = self._configuration_hash(model)
        self._record_status(
            experiment_id,
            purpose,
            draft_revision_id=draft["id"],
            status=ModelProbeState.CHECKING,
            configuration_hash=config_hash,
        )
        try:
            resolved, service_info = self._probe_model(purpose, model)
            if credential_source:
                service_info["credential_source"] = credential_source
        except ServiceError as exc:
            latency_ms = max(0, round((perf_counter() - started) * 1000))
            details = exc.details if isinstance(exc.details, dict) else {}
            self._record_status(
                experiment_id,
                purpose,
                draft_revision_id=draft["id"],
                status=ModelProbeState.OFFLINE,
                latency_ms=latency_ms,
                configuration_hash=config_hash,
                reason_code=exc.code,
                reason_message=exc.message,
                http_status=details.get("http_status"),
                service=details,
            )
            raise ServiceError(
                exc.code,
                exc.message,
                status_code=exc.status_code,
                details={
                    **details,
                    "purpose": purpose,
                    "latency_ms": latency_ms,
                    "suggestion": "检查 Base URL、模型 ID、鉴权和模型服务进程后重试",
                },
            ) from exc

        payload = definition.model_dump(mode="json", exclude_none=False)
        payload["models"][purpose]["resolved_model"] = resolved
        if purpose == "chat":
            payload["models"][purpose]["context_window"] = service_info.get(
                "context_window"
            )
        saved = self._experiments.update_draft(
            experiment_id=experiment_id,
            expected_lock_version=expected_lock_version,
            definition=ExperimentDefinition.model_validate(payload),
        )
        latency_ms = max(0, round((perf_counter() - started) * 1000))
        self._record_status(
            experiment_id,
            purpose,
            draft_revision_id=saved["id"],
            status=ModelProbeState.ONLINE,
            latency_ms=latency_ms,
            resolved_model=resolved,
            configuration_hash=self._configuration_hash(
                getattr(
                    ExperimentDefinition.model_validate(saved["definition"]).models,
                    purpose,
                )
            ),
            service=service_info,
        )
        return {
            "purpose": purpose,
            "provider": model.provider,
            "resolved_model": resolved,
            "latency_ms": latency_ms,
            "service": service_info,
            "draft_revision_id": saved["id"],
            "lock_version": saved["lock_version"],
            "definition_hash": saved["definition_hash"],
        }

    def status_summary(
        self, experiment_id: str, *, ttl_seconds: int = 900
    ) -> dict[str, Any]:
        """执行 `ModelProbeService` 的`status`摘要操作。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            ttl_seconds: `ttl`采用的秒数。 类型：`int`。 默认值：`900`。

        返回:
            返回以字段名或业务键组织的结构化映射。
        """
        draft = self._experiments.get_draft(experiment_id)
        definition = ExperimentDefinition.model_validate(draft["definition"])
        now = datetime.now(timezone.utc)
        with self._database.session_factory() as session:
            stored = {
                row.purpose: row
                for row in session.scalars(
                    select(ModelProbeStatus).where(
                        ModelProbeStatus.experiment_id == experiment_id
                    )
                )
            }
            items = []
            for purpose in ("chat", "embedding"):
                row = stored.get(purpose)
                model = getattr(definition.models, purpose)
                current_hash = self._configuration_hash(model)
                if row is None:
                    items.append(
                        {
                            "purpose": purpose,
                            "status": ModelProbeState.UNTESTED.value,
                            "checked_at": None,
                            "latency_ms": None,
                            "resolved_model": model.resolved_model,
                            "reason_code": None,
                            "reason_message": None,
                            "suggestion": "点击测试连接，确认当前配置可以完成一次最小调用",
                        }
                    )
                    continue
                checked_at = row.checked_at
                if checked_at is not None and checked_at.tzinfo is None:
                    checked_at = checked_at.replace(tzinfo=timezone.utc)
                stale = (
                    row.configuration_hash != current_hash
                    or row.draft_revision_id != draft["id"]
                    or (
                        checked_at is not None
                        and now - checked_at > timedelta(seconds=ttl_seconds)
                    )
                )
                effective = (
                    ModelProbeState.STALE.value
                    if stale and row.status != ModelProbeState.CHECKING
                    else row.status
                )
                items.append(
                    {
                        "purpose": purpose,
                        "status": effective,
                        "checked_at": checked_at,
                        "latency_ms": row.latency_ms,
                        "resolved_model": row.resolved_model,
                        "reason_code": row.reason_code,
                        "reason_message": row.reason_message,
                        "http_status": row.http_status,
                        "last_success_at": row.last_success_at,
                        "last_failure_at": row.last_failure_at,
                        "service": row.service_json,
                        "suggestion": (
                            "配置已变化或探测已过期，请重新测试连接"
                            if effective == ModelProbeState.STALE
                            else "检查 Base URL、模型 ID、鉴权和服务进程后重试"
                            if effective == ModelProbeState.OFFLINE
                            else None
                        ),
                    }
                )
        counts = {state.value: 0 for state in ModelProbeState}
        for item in items:
            counts[item["status"]] += 1
        return {
            "experiment_id": experiment_id,
            "ttl_seconds": ttl_seconds,
            "items": items,
            "counts": counts,
            "publish_ready": all(
                item["status"] == ModelProbeState.ONLINE for item in items
            ),
        }

    @staticmethod
    def _configuration_hash(model) -> str:
        """执行`configuration`哈希值的内部处理，供当前模块或类复用。

        参数:
            model: 当前调用、筛选或序列化的模型配置或模型实例。

        返回:
            返回处理后的文本或稳定标识。
        """
        payload = model.model_dump(mode="json", exclude_none=False)
        payload.pop("resolved_model", None)
        payload.pop("context_window", None)
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()

    def _record_status(
        self,
        experiment_id: str,
        purpose: Purpose,
        *,
        draft_revision_id: str,
        status: ModelProbeState | str,
        latency_ms: int | None = None,
        resolved_model: str | None = None,
        configuration_hash: str | None = None,
        reason_code: str | None = None,
        reason_message: str | None = None,
        http_status: int | None = None,
        service: dict[str, Any] | None = None,
    ) -> None:
        """记录模型连通性探测结果及其诊断信息。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            purpose: 模型用途键，用于从运行私有模型注册表选择对应模型。 类型：`Purpose`。
            draft_revision_id: 当前正在编辑且受乐观锁保护的草稿修订版本标识。 类型：`str`。
            status: 模型探测状态。允许值：`UNTESTED`、`CHECKING`、`ONLINE`、`OFFLINE`、`STALE`。 类型：`ModelProbeState | str`。
            latency_ms: 模型探测或调用从开始到结束的耗时毫秒数。 类型：`int | None`。 默认值：`None`。
            resolved_model: 传入当前算法的`resolved`模型；其结构与有效范围由类型注解和调用协议共同限定。 类型：`str | None`。 默认值：`None`。
            configuration_hash: `configuration`的内容摘要，用于完整性和幂等校验。 类型：`str | None`。 默认值：`None`。
            reason_code: 传入当前算法的`reason``code`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`str | None`。 默认值：`None`。
            reason_message: 传入当前算法的`reason``message`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`str | None`。 默认值：`None`。
            http_status: 传入当前算法的`http``status`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`int | None`。 默认值：`None`。
            service: 传入当前算法的`service`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`dict[str, Any] | None`。 默认值：`None`。

        返回:
            无返回值。
        """
        now = datetime.now(timezone.utc)
        probe_state = ModelProbeState(status)
        with self._database.session_factory.begin() as session:
            row = session.scalar(
                select(ModelProbeStatus).where(
                    ModelProbeStatus.experiment_id == experiment_id,
                    ModelProbeStatus.purpose == purpose,
                )
            )
            if row is None:
                row = ModelProbeStatus(
                    experiment_id=experiment_id,
                    purpose=purpose,
                    service_json={},
                    status=ModelProbeState.UNTESTED.value,
                    updated_at=now,
                )
                session.add(row)
            row.draft_revision_id = draft_revision_id
            row.status = probe_state.value
            row.latency_ms = latency_ms
            row.resolved_model = resolved_model
            row.configuration_hash = configuration_hash
            row.reason_code = reason_code
            row.reason_message = reason_message
            row.http_status = http_status
            row.service_json = service or {}
            row.checked_at = (
                now if probe_state != ModelProbeState.CHECKING else row.checked_at
            )
            row.updated_at = now
            if probe_state == ModelProbeState.ONLINE:
                row.last_success_at = now
            elif probe_state == ModelProbeState.OFFLINE:
                row.last_failure_at = now

    def resolve_for_publish(
        self,
        experiment_id: str,
        *,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        """解析`for``publish`。

        参数:
            experiment_id: 实验记录的唯一标识。 类型：`str`。
            expected_lock_version: 调用方读取草稿时看到的乐观锁版本；不一致表示发生并发修改。 类型：`int`。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """

        draft = self._experiments.get_draft(experiment_id)
        if draft["lock_version"] != expected_lock_version:
            raise ServiceError(
                "REVISION_CONFLICT",
                "草稿已被其他请求修改，请重新载入",
                status_code=409,
                details={
                    "expected_lock_version": expected_lock_version,
                    "actual_lock_version": draft["lock_version"],
                },
            )
        definition = ExperimentDefinition.model_validate(draft["definition"])
        credential_sources: dict[str, str] = {}
        for purpose in ("chat", "embedding"):
            definition, source = self._with_local_unsloth_credential(
                definition, purpose
            )
            if source:
                credential_sources[purpose] = source
        payload = definition.model_dump(mode="json", exclude_none=False)
        resolutions: list[dict[str, Any]] = []
        for purpose in ("chat", "embedding"):
            model = getattr(definition.models, purpose)
            config_hash = self._configuration_hash(model)
            self._record_status(
                experiment_id,
                purpose,
                draft_revision_id=draft["id"],
                status=ModelProbeState.CHECKING,
                configuration_hash=config_hash,
            )
            started = perf_counter()
            try:
                resolved, service_info = self._probe_model(purpose, model)
                if purpose in credential_sources:
                    service_info["credential_source"] = credential_sources[purpose]
            except ServiceError as exc:
                latency_ms = max(0, round((perf_counter() - started) * 1000))
                details = exc.details if isinstance(exc.details, dict) else {}
                self._record_status(
                    experiment_id,
                    purpose,
                    draft_revision_id=draft["id"],
                    status=ModelProbeState.OFFLINE,
                    latency_ms=latency_ms,
                    configuration_hash=config_hash,
                    reason_code=exc.code,
                    reason_message=exc.message,
                    http_status=details.get("http_status"),
                    service=details,
                )
                raise
            latency_ms = max(0, round((perf_counter() - started) * 1000))
            payload["models"][purpose]["resolved_model"] = resolved
            if purpose == "chat":
                payload["models"][purpose]["context_window"] = service_info.get(
                    "context_window"
                )
            resolutions.append(
                {
                    "purpose": purpose,
                    "provider": model.provider,
                    "resolved_model": resolved,
                    "latency_ms": latency_ms,
                    "service": service_info,
                }
            )
        saved = self._experiments.update_draft(
            experiment_id=experiment_id,
            expected_lock_version=expected_lock_version,
            definition=ExperimentDefinition.model_validate(payload),
        )
        saved_definition = ExperimentDefinition.model_validate(saved["definition"])
        for result in resolutions:
            purpose = result["purpose"]
            self._record_status(
                experiment_id,
                purpose,
                draft_revision_id=saved["id"],
                status=ModelProbeState.ONLINE,
                latency_ms=result["latency_ms"],
                resolved_model=result["resolved_model"],
                configuration_hash=self._configuration_hash(
                    getattr(saved_definition.models, purpose)
                ),
                service=result["service"],
            )
        return {
            "draft_revision_id": saved["id"],
            "lock_version": saved["lock_version"],
            "definition_hash": saved["definition_hash"],
            "resolutions": resolutions,
        }

    def _probe_model(self, purpose: Purpose, model) -> tuple[str, dict[str, Any]]:
        """执行`probe`模型的内部处理，供当前模块或类复用。

        参数:
            purpose: 模型用途键，用于从运行私有模型注册表选择对应模型。 类型：`Purpose`。
            model: 当前调用、筛选或序列化的模型配置或模型实例。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
        """
        timeout = min(int(model.timeout_seconds), self._max_timeout)
        secret = (
            self._secrets.resolve_plaintext(model.secret_ref)
            if model.secret_ref
            else ""
        )
        try:
            return self._execute_probe(purpose, model, secret=secret, timeout=timeout)
        except ServiceError:
            raise
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            raise ServiceError(
                "MODEL_CONNECTION_FAILED",
                "模型连接测试失败",
                status_code=422,
                details={
                    "purpose": purpose,
                    "provider": model.provider,
                    "reason": type(exc).__name__,
                },
            ) from exc

    def _with_local_unsloth_credential(
        self, definition: ExperimentDefinition, purpose: Purpose
    ) -> tuple[ExperimentDefinition, str | None]:
        """Import a valid loopback Unsloth key without exposing it to the browser."""

        model = getattr(definition.models, purpose)
        key = self._valid_cached_unsloth_key(str(model.base_url or ""))
        if not key:
            return definition, None
        current = (
            self._secrets.resolve_plaintext(model.secret_ref)
            if model.secret_ref
            else ""
        )
        if current == key:
            return definition, "local_unsloth_cache"
        saved = self._secrets.create(
            kind="OPENAI_API_KEY",
            value=key,
            supersedes_id=model.secret_ref,
        )
        payload = definition.model_dump(mode="json", exclude_none=False)
        payload["models"][purpose]["secret_ref"] = saved["secret_id"]
        payload["models"][purpose]["resolved_model"] = None
        if purpose == "chat":
            payload["models"][purpose]["context_window"] = None
        return ExperimentDefinition.model_validate(payload), "local_unsloth_cache"

    def _valid_cached_unsloth_key(self, configured_base_url: str) -> str | None:
        parsed = urlparse(configured_base_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            return None
        base_path = parsed.path.rstrip("/")
        if base_path.endswith("/v1"):
            base_path = base_path[:-3]
        authority = parsed.netloc
        server_base = f"{parsed.scheme}://{authority}{base_path}".rstrip("/")
        cache = Path.home() / ".unsloth" / "studio" / "auth" / "agent_api_key.json"
        try:
            document = json.loads(cache.read_text(encoding="utf-8"))
            entry = (document.get("servers") or {}).get(server_base) or {}
        except (OSError, ValueError, TypeError):
            return None
        candidates = [
            key
            for bucket in ("saved", "minted")
            for key in (entry.get(bucket) or ())
            if isinstance(key, str) and key
        ]
        for key in candidates:
            try:
                response = self._session.get(
                    f"{server_base}/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=min(3, self._max_timeout),
                )
                if response.ok:
                    return key
            except requests.RequestException:
                return None
        return None

    def _execute_probe(self, purpose, model, *, secret: str, timeout: int):
        """执行`execute``probe`的内部处理，供当前模块或类复用。

        参数:
            purpose: 模型用途键，用于从运行私有模型注册表选择对应模型。
            model: 当前调用、筛选或序列化的模型配置或模型实例。
            secret: 当前创建、轮换、解析或返回的密钥记录。 类型：`str`。
            timeout: 等待操作的最长秒数；超时后按调用协议返回或抛出异常。 类型：`int`。

        返回:
            返回函数计算得到的结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        provider = model.provider
        configured_model = model.model
        if provider == "hugging_face":
            return configured_model, {"probe": "configuration", "local": True}

        base_url = str(model.base_url).rstrip("/")
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"

        if provider == "ollama":
            resolved = configured_model
            if purpose == "chat":
                response = self._session.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": resolved,
                        "messages": [{"role": "user", "content": "Reply with OK."}],
                        "temperature": 0,
                        "stream": False,
                    },
                    timeout=timeout,
                )
                body = self._validated_json(response)
                if not (body.get("choices") or []):
                    raise ValueError("chat probe returned no choices")
            else:
                response = self._session.post(
                    f"{base_url}/api/embeddings",
                    headers=headers,
                    json={"model": resolved, "prompt": "probe"},
                    timeout=timeout,
                )
                body = self._validated_json(response)
                if not body.get("embedding"):
                    raise ValueError("embedding probe returned no vector")
            return resolved, {"probe": "minimal_call", "local": True}

        resolved = configured_model
        selected_model: dict[str, Any] | None = None
        if configured_model.casefold() == "auto":
            response = self._session.get(
                f"{base_url}/models", headers=headers, timeout=timeout
            )
            body = self._validated_json(response)
            models = [
                item
                for item in body.get("data", [])
                if isinstance(item, dict) and item.get("id")
            ]
            if not models:
                raise ValueError("model discovery returned no IDs")
            selected_model = models[0]
            resolved = selected_model["id"]

        if purpose == "chat":
            response = self._session.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": resolved,
                    "messages": [{"role": "user", "content": "Reply with OK."}],
                    "temperature": 0,
                    "max_tokens": 2,
                    "stream": False,
                },
                timeout=timeout,
            )
            body = self._validated_json(response)
            if not (body.get("choices") or []):
                raise ValueError("chat probe returned no choices")
        else:
            response = self._session.post(
                f"{base_url}/embeddings",
                headers=headers,
                json={"model": resolved, "input": ["probe"]},
                timeout=timeout,
            )
            body = self._validated_json(response)
            data = body.get("data") or []
            if not data or not data[0].get("embedding"):
                raise ValueError("embedding probe returned no vector")
        service_info: dict[str, Any] = {
            "probe": "discovery_and_minimal_call",
            "local": False,
        }
        if selected_model is not None:
            context_window = self._context_window(selected_model)
            if context_window is not None:
                service_info["context_window"] = context_window
        return resolved, service_info

    @staticmethod
    def _context_window(model_info: dict[str, Any]) -> int | None:
        """执行运行上下文`window`的内部处理，供当前模块或类复用。

        参数:
            model_info: 传入当前算法的模型`info`；其结构与有效范围由类型注解和调用协议共同限定。 类型：`dict[str, Any]`。

        返回:
            返回计算得到的整数值或版本号。 没有可用结果时返回 `None`。
        """
        for key in (
            "max_model_len",
            "context_length",
            "max_context_length",
            "context_window",
        ):
            value = model_info.get(key)
            if isinstance(value, int) and value > 0:
                return value
        return None

    @staticmethod
    def _validated_json(response) -> dict[str, Any]:
        """执行`validated``json`的内部处理，供当前模块或类复用。

        参数:
            response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

        返回:
            返回以字段名或业务键组织的结构化映射。

        异常:
            ServiceError: 当输入、资源状态或业务状态不满足服务层约束时抛出。
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        if not getattr(response, "ok", False):
            raise ServiceError(
                "MODEL_ENDPOINT_ERROR",
                "模型服务返回错误状态",
                status_code=422,
                details={"http_status": getattr(response, "status_code", None)},
            )
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("model endpoint returned non-object JSON")
        return body
