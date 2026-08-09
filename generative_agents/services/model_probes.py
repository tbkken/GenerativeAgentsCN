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
        started = perf_counter()
        resolved, service_info = self._probe_model(purpose, model)

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

    def resolve_for_publish(
        self,
        experiment_id: str,
        *,
        expected_lock_version: int,
    ) -> dict[str, Any]:
        """Resolve all unresolved ``auto`` models before the publish transaction.

        All network calls complete before the single Draft write.  A failed
        Embedding probe therefore cannot leave Chat pinned as a partial update.
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
        payload = definition.model_dump(mode="json", exclude_none=False)
        resolutions: list[dict[str, Any]] = []
        definition_changed = False
        for purpose in ("chat", "embedding"):
            model = getattr(definition.models, purpose)
            if model.model.casefold() != "auto":
                continue
            started = perf_counter()
            resolved, service_info = self._probe_model(purpose, model)
            context_window = (
                service_info.get("context_window") if purpose == "chat" else None
            )
            definition_changed = (
                definition_changed
                or model.resolved_model != resolved
                or (
                    purpose == "chat"
                    and model.context_window != context_window
                )
            )
            payload["models"][purpose]["resolved_model"] = resolved
            if purpose == "chat":
                payload["models"][purpose]["context_window"] = context_window
            resolutions.append(
                {
                    "purpose": purpose,
                    "provider": model.provider,
                    "resolved_model": resolved,
                    "latency_ms": max(0, round((perf_counter() - started) * 1000)),
                    "service": service_info,
                }
            )

        if not definition_changed:
            return {
                "draft_revision_id": draft["id"],
                "lock_version": draft["lock_version"],
                "definition_hash": draft["definition_hash"],
                "resolutions": resolutions,
            }

        saved = self._experiments.update_draft(
            experiment_id=experiment_id,
            expected_lock_version=expected_lock_version,
            definition=ExperimentDefinition.model_validate(payload),
        )
        return {
            "draft_revision_id": saved["id"],
            "lock_version": saved["lock_version"],
            "definition_hash": saved["definition_hash"],
            "resolutions": resolutions,
        }

    def _probe_model(self, purpose: Purpose, model) -> tuple[str, dict[str, Any]]:
        timeout = min(int(model.timeout_seconds), self._max_timeout)
        secret = self._secrets.resolve_plaintext(model.secret_ref) if model.secret_ref else ""
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
