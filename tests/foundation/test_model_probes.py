"""基础能力回归测试：覆盖 ``test_model_probes`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

import pytest

from generative_agents.config import ExperimentDefinition
from generative_agents.services.catalog import SecretService
from generative_agents.services.errors import ServiceError
from generative_agents.services.model_probes import ModelProbeService


class _Response:
    """为 ``_Response`` 相关场景组织共享测试状态、输入或断言。"""
    def __init__(self, body, status_code=200):
        """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
        self._body = body
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    def json(self):
        """为本测试模块封装 ``json`` 辅助步骤，减少重复的场景搭建代码。"""
        return self._body


class _Session:
    """为 ``_Session`` 相关场景组织共享测试状态、输入或断言。"""
    def __init__(self):
        """为本测试模块封装 ``__init__`` 辅助步骤，减少重复的场景搭建代码。"""
        self.calls = []

    def get(self, url, **kwargs):
        """为本测试模块封装 ``get`` 辅助步骤，减少重复的场景搭建代码。"""
        self.calls.append(("GET", url, kwargs))
        return _Response(
            {"data": [{"id": "resolved-test-model", "max_model_len": 40_000}]}
        )

    def post(self, url, **kwargs):
        """为本测试模块封装 ``post`` 辅助步骤，减少重复的场景搭建代码。"""
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/embeddings"):
            return _Response({"data": [{"embedding": [0.1, 0.2]}]})
        return _Response({"choices": [{"message": {"content": "OK"}}]})


class _FailingEmbeddingSession(_Session):
    """为 ``_FailingEmbeddingSession`` 相关场景组织共享测试状态、输入或断言。"""
    def post(self, url, **kwargs):
        """为本测试模块封装 ``post`` 辅助步骤，减少重复的场景搭建代码。"""
        if url.endswith("/embeddings"):
            self.calls.append(("POST", url, kwargs))
            return _Response({}, status_code=503)
        return super().post(url, **kwargs)


def _make_models_auto(service, created: dict, draft: dict) -> dict:
    """为本测试模块封装 ``_make_models_auto`` 辅助步骤，减少重复的场景搭建代码。"""
    payload = draft["definition"]
    payload["models"]["chat"]["model"] = "auto"
    payload["models"]["chat"]["resolved_model"] = None
    payload["models"]["chat"]["context_window"] = None
    payload["models"]["embedding"]["model"] = "auto"
    payload["models"]["embedding"]["resolved_model"] = None
    return service.update_draft(
        experiment_id=created["id"],
        expected_lock_version=draft["lock_version"],
        definition=ExperimentDefinition.model_validate(payload),
    )


def test_auto_model_probe_pins_resolved_model_into_the_same_draft(
    service, database, tmp_path
):
    """回归验证 ``test_auto_model_probe_pins_resolved_model_into_the_same_draft`` 所描述的业务结果、故障边界和隔离约束。"""
    created = service.create_experiment(name="Probe", source_type="BLANK")
    draft = service.get_draft(created["id"])
    draft = _make_models_auto(service, created, draft)
    session = _Session()
    probes = ModelProbeService(
        database,
        experiments=service,
        secrets=SecretService(database, var_dir=tmp_path / "var"),
        session=session,
    )

    chat = probes.probe(
        created["id"], "chat", expected_lock_version=draft["lock_version"]
    )
    embedding = probes.probe(
        created["id"], "embedding", expected_lock_version=chat["lock_version"]
    )

    saved = service.get_draft(created["id"])
    assert chat["resolved_model"] == embedding["resolved_model"] == "resolved-test-model"
    assert saved["definition"]["models"]["chat"]["resolved_model"] == "resolved-test-model"
    assert saved["definition"]["models"]["chat"]["context_window"] == 40_000
    assert saved["definition"]["models"]["embedding"]["resolved_model"] == "resolved-test-model"
    assert [method for method, _url, _kwargs in session.calls] == [
        "GET", "POST", "GET", "POST"
    ]
    assert chat["service"]["context_window"] == 40_000


def test_publish_preflight_resolves_all_auto_models_with_one_draft_write(
    service, database, tmp_path
):
    """回归验证 ``test_publish_preflight_resolves_all_auto_models_with_one_draft_write`` 所描述的业务结果、故障边界和隔离约束。"""
    created = service.create_experiment(name="Auto publish", source_type="BLANK")
    draft = service.get_draft(created["id"])
    draft = _make_models_auto(service, created, draft)
    session = _Session()
    probes = ModelProbeService(
        database,
        experiments=service,
        secrets=SecretService(database, var_dir=tmp_path / "var"),
        session=session,
    )

    prepared = probes.resolve_for_publish(
        created["id"], expected_lock_version=draft["lock_version"]
    )

    saved = service.get_draft(created["id"])
    assert prepared["lock_version"] == draft["lock_version"] + 1
    assert saved["lock_version"] == draft["lock_version"] + 1
    assert [item["purpose"] for item in prepared["resolutions"]] == [
        "chat",
        "embedding",
    ]
    assert saved["definition"]["models"]["chat"]["resolved_model"] == "resolved-test-model"
    assert saved["definition"]["models"]["embedding"]["resolved_model"] == "resolved-test-model"
    assert [method for method, _url, _kwargs in session.calls] == [
        "GET",
        "POST",
        "GET",
        "POST",
    ]


def test_publish_preflight_does_not_partially_pin_models_on_probe_failure(
    service, database, tmp_path
):
    """回归验证 ``test_publish_preflight_does_not_partially_pin_models_on_probe_failure`` 所描述的业务结果、故障边界和隔离约束。"""
    created = service.create_experiment(name="Atomic auto publish", source_type="BLANK")
    draft = service.get_draft(created["id"])
    draft = _make_models_auto(service, created, draft)
    probes = ModelProbeService(
        database,
        experiments=service,
        secrets=SecretService(database, var_dir=tmp_path / "var"),
        session=_FailingEmbeddingSession(),
    )

    with pytest.raises(ServiceError) as error:
        probes.resolve_for_publish(
            created["id"], expected_lock_version=draft["lock_version"]
        )

    assert error.value.code == "MODEL_ENDPOINT_ERROR"
    unchanged = service.get_draft(created["id"])
    assert unchanged["lock_version"] == draft["lock_version"]
    assert unchanged["definition"]["models"]["chat"]["resolved_model"] is None
    assert unchanged["definition"]["models"]["embedding"]["resolved_model"] is None
