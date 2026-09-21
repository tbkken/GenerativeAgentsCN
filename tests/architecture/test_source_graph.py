from tools.check_source_boundaries import ROOT, check_boundaries, source_graph


def test_every_product_module_respects_transitive_architecture_boundaries():
    graph, owners, errors = source_graph()
    assert len(graph) > 80
    assert {"ga_protocol", "ga_studio", "ga_runtime", "ga_replay", "adapters"} <= set(owners.values())
    assert not errors
    assert check_boundaries() == []


def test_no_retired_top_level_packages_or_compatibility_shims():
    assert {path.name for path in ROOT.iterdir() if path.is_dir() and path.name != "__pycache__"} == {
        "ga_protocol", "ga_studio", "ga_runtime", "ga_replay", "adapters"
    }
    for package in ("ga_studio", "ga_runtime", "ga_replay"):
        assert (ROOT / package / "api.py").is_file()
