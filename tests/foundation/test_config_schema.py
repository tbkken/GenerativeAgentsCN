"""基础能力回归测试：覆盖 ``test_config_schema`` 对应的行为、故障边界和回归约束。"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from generative_agents.config import definition_hash, get_algorithm_profile, validate_for_publish
from generative_agents.config.hashing import canonical_json_bytes
from generative_agents.config.schema import ExperimentDefinition, make_blank_definition


def test_new_experiment_uses_the_real_unsloth_chat_endpoint():
    definition = make_blank_definition(key="unsloth-default", name="Unsloth")

    assert definition.models.chat.provider == "vllm"
    assert definition.models.chat.model == "Qwen3.8-27B-UD-Q4_K_XL"
    assert str(definition.models.chat.base_url).rstrip("/") == (
        "http://127.0.0.1:8888/v1"
    )


def test_canonical_hash_normalizes_key_order_unicode_and_newlines():
    """回归验证 ``test_canonical_hash_normalizes_key_order_unicode_and_newlines`` 所描述的业务结果、故障边界和隔离约束。"""
    first = {"b": "e\u0301\r\nline", "a": {"x": 1}}
    second = {"a": {"x": 1}, "b": "é\nline"}
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert definition_hash(first) == definition_hash(second)


def test_algorithm_profile_is_the_fixed_ga_cn_v1_contract():
    """回归验证 ``test_algorithm_profile_is_the_fixed_ga_cn_v1_contract`` 所描述的业务结果、故障边界和隔离约束。"""
    assert get_algorithm_profile("ga-cn-v1").as_dict() == {
        "sentence_chunk_size": 512,
        "sentence_chunk_overlap": 64,
        "llama_num_output": 1024,
        "llama_context_window": 4096,
        "similarity_top_k": 5,
        "focus_retrieve_max": 30,
        "schedule_decompose_threshold_minutes": 60,
        "path_target_sample_limit": 4,
        "movement_tiles_per_minute": 4,
        "chat_chars_per_minute": 240,
        "default_event_poignancy": 1,
    }
    with pytest.raises(ValueError, match="unsupported"):
        get_algorithm_profile("ga-cn-v2")


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("simulation", "stride_minutes"), 0),
        (("simulation", "max_steps"), 0),
        (("simulation", "checkpoint_retention"), 1),
        (("results", "agent_step_projection_interval_steps"), 0),
    ],
)
def test_schema_rejects_result_changing_boundary_values(path, value):
    """回归验证 ``test_schema_rejects_result_changing_boundary_values`` 所描述的业务结果、故障边界和隔离约束。"""
    payload = make_blank_definition(key="schema-boundary", name="Boundary").model_dump(
        mode="json", exclude_none=False
    )
    cursor = payload
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = value
    with pytest.raises(ValidationError):
        ExperimentDefinition.model_validate(payload)


def test_schema_forbids_unknown_fields_and_naive_time():
    """回归验证 ``test_schema_forbids_unknown_fields_and_naive_time`` 所描述的业务结果、故障边界和隔离约束。"""
    payload = make_blank_definition(key="strict-schema", name="Strict").model_dump(
        mode="json", exclude_none=False
    )
    payload["simulation"]["record_iterval"] = 30
    payload["simulation"]["start_time"] = "2026-02-13T00:00:00"
    with pytest.raises(ValidationError) as exc:
        ExperimentDefinition.model_validate(payload)
    errors = exc.value.errors()
    assert any(error["type"] == "extra_forbidden" for error in errors)
    assert any("UTC offset" in str(error["ctx"]) for error in errors)


@pytest.mark.parametrize(
    "provider_payload",
    [
        {
            "provider": "openai",
            "model": "auto",
            "base_url": "https://api.openai.com/v1",
            "secret_ref": "secret-id",
        },
        {
            "provider": "ollama",
            "model": "auto",
            "base_url": "http://127.0.0.1:11434",
        },
    ],
)
def test_chat_provider_union_rejects_auto_where_unsupported(provider_payload):
    """回归验证 ``test_chat_provider_union_rejects_auto_where_unsupported`` 所描述的业务结果、故障边界和隔离约束。"""
    payload = make_blank_definition(key="provider-check", name="Provider").model_dump(
        mode="json", exclude_none=False
    )
    payload["models"]["chat"] = provider_payload
    with pytest.raises(ValidationError):
        ExperimentDefinition.model_validate(payload)


def test_cross_field_constraints_reject_projection_larger_than_run():
    """回归验证 ``test_cross_field_constraints_reject_projection_larger_than_run`` 所描述的业务结果、故障边界和隔离约束。"""
    payload = make_blank_definition(key="cross-field", name="Cross").model_dump(
        mode="json", exclude_none=False
    )
    payload["simulation"]["max_steps"] = 2
    payload["results"]["agent_step_projection_interval_steps"] = 3
    with pytest.raises(ValidationError, match="must not exceed max_steps"):
        ExperimentDefinition.model_validate(payload)


def test_publication_validation_keeps_incomplete_blank_draft_editable():
    """回归验证 ``test_publication_validation_keeps_incomplete_blank_draft_editable`` 所描述的业务结果、故障边界和隔离约束。"""
    draft = make_blank_definition(key="editable-draft", name="Editable")
    report = validate_for_publish(draft)
    assert not report.valid
    assert {item.code for item in report.errors} >= {
        "NO_ENABLED_AGENT",
        "MODEL_NOT_RESOLVED",
        "WORLD_EMPTY",
    }


def test_publication_warns_before_replay_when_agent_media_is_missing(
    publishable_definition,
):
    report = validate_for_publish(publishable_definition)

    assert {item.code for item in report.warnings} >= {
        "AGENT_PORTRAIT_ASSET_MISSING",
        "AGENT_SPRITE_ASSET_MISSING",
    }
    assert report.valid is True


def test_publication_warns_when_structured_initial_address_is_not_declared(
    publishable_definition,
):
    report = validate_for_publish(publishable_definition)

    issue = next(
        item
        for item in report.warnings
        if item.code == "AGENT_INITIAL_ADDRESS_UNDECLARED"
    )
    assert issue.path == "agents.0.spatial.address.initial_location"
    assert "home > bedroom > bed" in issue.message


def test_publication_rejects_initial_address_that_disagrees_with_coordinate(
    publishable_definition,
):
    payload = copy.deepcopy(
        publishable_definition.model_dump(mode="json", exclude_none=False)
    )
    payload["world"]["definition"]["tiles"].append(
        {
            "coord": [1, 0],
            "collision": False,
            "address": ["office", "desk"],
        }
    )
    payload["world"]["definition"]["size"] = [1, 2]
    payload["world"]["definition"]["map"] = [[0, 0]]
    payload["agents"][0]["spatial"]["address"]["initial_location"] = [
        "test",
        "office",
        "desk",
    ]
    payload["agents"][0]["spatial"]["tree"]["test"]["office"] = ["desk"]

    report = validate_for_publish(ExperimentDefinition.model_validate(payload))

    issue = next(
        item
        for item in report.errors
        if item.code == "AGENT_INITIAL_ADDRESS_MISMATCH"
    )
    assert "[0, 0]" in issue.message
    assert "home > bedroom > bed" in issue.message


def test_definition_hash_changes_with_algorithm_or_seed(publishable_definition):
    """回归验证 ``test_definition_hash_changes_with_algorithm_or_seed`` 所描述的业务结果、故障边界和隔离约束。"""
    original = definition_hash(publishable_definition)
    payload = copy.deepcopy(
        publishable_definition.model_dump(mode="json", exclude_none=False)
    )
    payload["simulation"]["random_seed"] += 1
    assert definition_hash(ExperimentDefinition.model_validate(payload)) != original


def test_publication_rejects_duplicate_enabled_agent_display_names(
    publishable_definition,
):
    """回归验证 ``test_publication_rejects_duplicate_enabled_agent_display_names`` 所描述的业务结果、故障边界和隔离约束。"""
    payload = copy.deepcopy(
        publishable_definition.model_dump(mode="json", exclude_none=False)
    )
    duplicate = copy.deepcopy(payload["agents"][0])
    duplicate["agent_key"] = "duplicate-agent-key"
    payload["agents"].append(duplicate)

    definition = ExperimentDefinition.model_validate(payload)
    report = validate_for_publish(definition)
    assert {item.code for item in report.errors} == {
        "DUPLICATE_ENABLED_AGENT_NAME"
    }

    payload["agents"][-1]["enabled"] = False
    report = validate_for_publish(ExperimentDefinition.model_validate(payload))
    assert "DUPLICATE_ENABLED_AGENT_NAME" not in {
        item.code for item in report.errors
    }


def test_publication_rejects_agent_without_spatial_configuration(
    publishable_definition,
):
    """回归验证 ``test_publication_rejects_agent_without_spatial_configuration`` 所描述的业务结果、故障边界和隔离约束。"""
    payload = copy.deepcopy(
        publishable_definition.model_dump(mode="json", exclude_none=False)
    )
    payload["agents"][0]["spatial"] = {"address": {}, "tree": {}}

    report = validate_for_publish(ExperimentDefinition.model_validate(payload))

    issue = next(
        item for item in report.errors if item.code == "AGENT_SPATIAL_ADDRESS_REQUIRED"
    )
    assert issue.path == "agents.0.spatial"
    assert "Test Agent" in issue.message


def test_publication_rejects_agent_address_missing_from_selected_map(
    publishable_definition,
):
    """回归验证 ``test_publication_rejects_agent_address_missing_from_selected_map`` 所描述的业务结果、故障边界和隔离约束。"""
    payload = copy.deepcopy(
        publishable_definition.model_dump(mode="json", exclude_none=False)
    )
    payload["agents"][0]["spatial"] = {
        "address": {
            "living_area": ["test", "elsewhere", "bedroom"],
            "sleeping": ["test", "elsewhere", "bedroom", "bed"],
        },
        "tree": {"test": {"elsewhere": {"bedroom": ["bed"]}}},
    }

    report = validate_for_publish(ExperimentDefinition.model_validate(payload))

    assert "AGENT_SPATIAL_MAP_ADDRESS_INVALID" in {
        item.code for item in report.errors
    }


def test_publication_names_incompatible_spatial_tree_path(
    publishable_definition,
):
    """回归验证 ``test_publication_names_incompatible_spatial_tree_path`` 所描述的业务结果、故障边界和隔离约束。"""
    payload = copy.deepcopy(
        publishable_definition.model_dump(mode="json", exclude_none=False)
    )
    payload["agents"][0]["spatial"]["tree"]["test"]["elsewhere"] = {
        "room": ["missing object"]
    }

    report = validate_for_publish(ExperimentDefinition.model_validate(payload))

    issue = next(
        item
        for item in report.errors
        if item.code == "AGENT_SPATIAL_MAP_ADDRESS_INVALID"
    )
    assert issue.path == "agents.0.spatial"
    assert "test > elsewhere > room > missing object" in issue.message
