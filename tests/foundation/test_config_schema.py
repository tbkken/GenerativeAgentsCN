from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from generative_agents.config import definition_hash, get_algorithm_profile, validate_for_publish
from generative_agents.config.hashing import canonical_json_bytes
from generative_agents.config.schema import ExperimentDefinition, make_blank_definition


def test_canonical_hash_normalizes_key_order_unicode_and_newlines():
    first = {"b": "e\u0301\r\nline", "a": {"x": 1}}
    second = {"a": {"x": 1}, "b": "é\nline"}
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert definition_hash(first) == definition_hash(second)


def test_algorithm_profile_is_the_fixed_ga_cn_v1_contract():
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
        (("results", "replay_interpolation_frames"), 121),
        (("behavior", "memory", "recency_decay"), 0),
    ],
)
def test_schema_rejects_result_changing_boundary_values(path, value):
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
    payload = make_blank_definition(key="provider-check", name="Provider").model_dump(
        mode="json", exclude_none=False
    )
    payload["models"]["chat"] = provider_payload
    with pytest.raises(ValidationError):
        ExperimentDefinition.model_validate(payload)


def test_cross_field_constraints_reject_projection_larger_than_run():
    payload = make_blank_definition(key="cross-field", name="Cross").model_dump(
        mode="json", exclude_none=False
    )
    payload["simulation"]["max_steps"] = 2
    payload["results"]["agent_step_projection_interval_steps"] = 3
    with pytest.raises(ValidationError, match="must not exceed max_steps"):
        ExperimentDefinition.model_validate(payload)


def test_publication_validation_keeps_incomplete_blank_draft_editable():
    draft = make_blank_definition(key="editable-draft", name="Editable")
    report = validate_for_publish(draft)
    assert not report.valid
    assert {item.code for item in report.errors} >= {
        "NO_ENABLED_AGENT",
        "MODEL_NOT_RESOLVED",
        "PROMPT_EMPTY",
        "WORLD_EMPTY",
    }


def test_definition_hash_changes_with_algorithm_or_seed(publishable_definition):
    original = definition_hash(publishable_definition)
    payload = copy.deepcopy(
        publishable_definition.model_dump(mode="json", exclude_none=False)
    )
    payload["simulation"]["random_seed"] += 1
    assert definition_hash(ExperimentDefinition.model_validate(payload)) != original


def test_publication_rejects_duplicate_enabled_agent_display_names(
    publishable_definition,
):
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
