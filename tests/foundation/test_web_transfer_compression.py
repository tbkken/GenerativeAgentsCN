"""Remote experiment pages transfer large maps without changing package bytes."""
import gzip
from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient

from generative_agents.adapters.web.app import create_studio_app
from generative_agents.ga_protocol.packages.definition import _experiment_definition, write_experiment_definition
from generative_agents.ga_protocol.packages.io import read_json, write_integrity_manifest
from tests.test_portable_package_protocol import _experiment


def test_large_experiment_detail_is_compressed_losslessly(tmp_path):
    var = tmp_path / "var"
    root = _experiment(var / "packages")
    _, definition = _experiment_definition(root)
    world = definition["world"]["definition"]
    template = world["tiles"][0]
    world["size"] = [40, 40]
    world["tiles"] = [{**template, "coord": [x, y]} for y in range(40) for x in range(40)]
    write_experiment_definition(root, definition)
    write_integrity_manifest(root)
    integrity = (root / "integrity/sha256.json").read_bytes()
    experiment_id = read_json(root / "manifest.json")["experiment"]["experiment_id"]
    app = create_studio_app(database_url=f"sqlite:///{tmp_path / 'studio.db'}", var_dir=var)
    with TestClient(app) as client:
        client.post("/api/studio/packages/rebuild").raise_for_status()
        overview = client.get(f"/api/studio/experiments/{experiment_id}").json()
        assert 'world' not in overview['definition']
        assert 'definition' not in overview['current_draft']
        url = f"/api/studio/experiments/{experiment_id}?view=definition"
        plain = client.get(url, headers={"Accept-Encoding": "identity"})
        plain.raise_for_status()
        assert "content-encoding" not in plain.headers
        assert len(plain.content) > 200_000
        with client.stream("GET", url, headers={"Accept-Encoding": "gzip"}) as compressed:
            compressed.raise_for_status()
            assert compressed.headers["content-encoding"] == "gzip"
            assert "accept-encoding" in compressed.headers["vary"].lower()
            wire = b"".join(compressed.iter_raw())
        assert gzip.decompress(wire) == plain.content
        assert len(wire) < len(plain.content) / 5
        assert client.get(url).json()["definition"]["world"]["definition"]["size"] == [40, 40]
    assert (root / "integrity/sha256.json").read_bytes() == integrity


def test_console_scripts_compress_while_package_download_remains_a_zip(tmp_path):
    var = tmp_path / "var"
    root = _experiment(var / "packages")
    experiment_id = read_json(root / "manifest.json")["experiment"]["experiment_id"]
    app = create_studio_app(database_url=f"sqlite:///{tmp_path / 'studio.db'}", var_dir=var)
    with TestClient(app) as client:
        script = "/static/console/shell/console-api.js"
        plain = client.get(script, headers={"Accept-Encoding": "identity"})
        with client.stream("GET", script, headers={"Accept-Encoding": "gzip"}) as compressed:
            compressed.raise_for_status()
            assert compressed.headers["content-encoding"] == "gzip"
            assert "no-cache" in compressed.headers["cache-control"]
            assert gzip.decompress(b"".join(compressed.iter_raw())) == plain.content
        client.post("/api/studio/packages/rebuild").raise_for_status()
        client.post(f"/api/studio/experiments/{experiment_id}/seal").raise_for_status()
        response = client.get(f"/api/studio/packages/experiment/{experiment_id}/download",
                              headers={"Accept-Encoding": "gzip"})
        response.raise_for_status()
        assert "content-encoding" not in response.headers
        with ZipFile(BytesIO(response.content)) as archive:
            assert archive.testzip() is None
            assert "integrity/sha256.json" in archive.namelist()
