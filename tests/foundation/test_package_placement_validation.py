"""Spatial safety migrated from obsolete publication preflight to .gaexp validation."""
import copy

import pytest

from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.packages.io import atomic_write_json
from generative_agents.ga_protocol.packages.io import read_json
from generative_agents.ga_protocol.packages.validation import validate_experiment_directory
from generative_agents.ga_protocol.packages.io import write_integrity_manifest
from tests.test_portable_package_protocol import _experiment


@pytest.mark.parametrize("invalid", ["missing", "wrong_address", "missing_tree", "blocked", "outside", "duplicate_key"])
def test_package_rejects_invalid_agent_placement(tmp_path, invalid):
    root = _experiment(tmp_path)
    manifest = read_json(root / "manifest.json")
    world_path = root / manifest["entrypoints"]["world"]
    world = read_json(world_path)
    address = list(world["definition"]["tiles"][0]["address"])
    agent = {"agent_key": "reader", "name": "Reader", "coord": [0, 0],
             "spatial": {"address": {"initial_location": address},
                         "tree": {address[0]: {address[1]: {address[2]: [address[3]]}}}}}
    agents_path = root / manifest["entrypoints"]["agents"]
    atomic_write_json(agents_path, {"agents": [agent]})
    write_integrity_manifest(root)
    validate_experiment_directory(root)
    agents = [agent]
    if invalid == "missing":
        agent["spatial"]["address"] = {}
    elif invalid == "wrong_address":
        agent["spatial"]["address"]["initial_location"] = address[:3]
    elif invalid == "missing_tree":
        agent["spatial"]["tree"] = {}
    elif invalid == "blocked":
        world["definition"]["tiles"][0]["collision"] = True
    elif invalid == "outside":
        agent["coord"] = [1, 0]
    else:
        agents.append(copy.deepcopy(agent))
    atomic_write_json(world_path, world)
    atomic_write_json(agents_path, {"agents": agents})
    write_integrity_manifest(root)
    with pytest.raises(PackageError, match="Agent"):
        validate_experiment_directory(root)
