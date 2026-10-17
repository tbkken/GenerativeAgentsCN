"""The authoring catalog and object runtime expose identical interface contracts."""

import copy

from generative_agents.ga_protocol.skills.interfaces import object_interface_catalog
from generative_agents.ga_protocol.skills.interfaces import simulation_interface_catalog
from generative_agents.ga_runtime.skills.objects import ObjectAPI


def test_object_interfaces_match_authoring_catalog_without_mutating_agent_contract():
    agent_catalog = copy.deepcopy(simulation_interface_catalog())
    api = ObjectAPI(None, None, binding=None, memory_stream=object())
    descriptors = api.descriptors()
    assert descriptors == object_interface_catalog()
    names = {item["name"] for item in descriptors}
    assert "world-navigate" not in names
    assert "memory-stream-search" in names
    action = next(item for item in descriptors if item["name"] == "world-act")
    props = action["parameters"]["properties"]
    assert set(props["action_type"]["enum"]) == {"ACT", "WAIT", "SET_OBJECT_STATE"}
    assert {"responses", "target_agent_key", "evidence_ids", "idempotency_key"} <= props.keys()
    assert not {"target_coord", "target_address", "participant_agent_keys"} & props.keys()
    assert props["responses"]["items"]["required"] == ["request_id", "message"]
    assert props["responses"]["items"]["additionalProperties"] is False
    assert simulation_interface_catalog() == agent_catalog
    # Actor-specific consumers may narrow a descriptor without changing later calls.
    props["action_type"]["enum"].clear()
    assert api.descriptors() == object_interface_catalog()
    assert simulation_interface_catalog() == agent_catalog


def test_object_interfaces_without_memory_keep_the_full_nonmemory_contract():
    api = ObjectAPI(None, None, binding=None)
    descriptors = api.descriptors()
    assert descriptors == [
        item for item in object_interface_catalog()
        if not item["name"].startswith("memory-")
    ]
    assert {item["name"] for item in descriptors} == {"world-perceive", "world-act"}
