"""Explicit, bounded model connection probes for editable experiment Drafts."""

from __future__ import annotations

from time import perf_counter
from typing import Any, Literal

import requests

from generative_agents.config import ExperimentDefinition
from generative_agents.persistence import Database

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
        model = getattr(definition.models, purpose)
        timeout = min(int(model.timeout_seconds), self._max_timeout)
        secret = self._secrets.resolve_plaintext(model.secret_ref) if model.secret_ref else ""
        started = perf_counter()
        try:
            resolved, service_info = self._execute_probe(
                purpose, model, secret=secret, timeout=timeout
            )
        except ServiceError:
            raise
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            raise ServiceError(
                "MODEL_CONNECTION_FAILED",
                "模型连接测试失败",
                status_code=422,
                details={"purpose": purpose, "provider": model.provider, "reason": type(exc).__name__},
            ) from exc

        payload = definition.model_dump(mode="json", exclude_none=False)
        payload["models"][purpose]["resolved_model"] = resolved
        saved = self._experiments.update_draft(
            experiment_id=experiment_id,
            expected_lock_version=expected_lock_version,
            definition=ExperimentDefinition.model_validate(payload),
        )
        return {
            "purpose": purpose,
            "provider": model.provider,
            "resolved_model": resolved,
            "latency_ms": max(0, round((perf_counter() - started) * 1000)),
            "service": service_info,
            "draft_revision_id": saved["id"],
            "lock_version": saved["lock_version"],
            "definition_hash": saved["definition_hash"],
        }

    def _execute_probe(self, purpose, model, *, secret: str, timeout: int):
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
        if configured_model.casefold() == "auto":
            response = self._session.get(
                f"{base_url}/models", headers=headers, timeout=timeout
            )
            body = self._validated_json(response)
            models = [item.get("id") for item in body.get("data", []) if item.get("id")]
            if not models:
                raise ValueError("model discovery returned no IDs")
            resolved = models[0]

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
        return resolved, {"probe": "discovery_and_minimal_call", "local": False}

    @staticmethod
    def _validated_json(response) -> dict[str, Any]:
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
