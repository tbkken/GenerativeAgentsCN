"""Large binary attachments remain exchangeable after archive staging is released."""
from __future__ import annotations

from pathlib import Path

from generative_agents.ga_protocol.packages.attachments import AttachmentFiles
from generative_agents.ga_protocol.packages.io import seal_directory, sha256_file
from generative_agents.ga_protocol.packages.resources import (
    ResourceRecord, ResourceSet, read_resource_set, select_resources, write_config_package,
)
from generative_agents.ga_protocol.schemas.resources import ResourceRef


def test_seal_select_and_reexport_stream_opaque_attachments(tmp_path, monkeypatch):
    source = tmp_path / "data.bin"
    with source.open("wb") as stream:
        for _ in range(8):
            stream.write(b"x" * 1024 * 1024)
    digest = sha256_file(source)
    name = "assets/source.bin"
    resource = ResourceRecord(kind="evaluator", key="measure", name="measure",
                              definition={"asset_path": name}, attachments=[name])
    ref = ResourceRef(kind=resource.kind, key=resource.key)
    original = AttachmentFiles.__getitem__
    def reject_materialization(files, key):
        if key == name:
            raise AssertionError("binary attachment was loaded into heap")
        return original(files, key)
    monkeypatch.setattr(AttachmentFiles, "__getitem__", reject_materialization)
    config = write_config_package(tmp_path / "config", ResourceSet([resource], {name: source}, [ref]))
    archive = seal_directory(config, tmp_path / "config.zip")
    extracted = read_resource_set(archive)
    selected = select_resources(extracted, [ref])
    archive.unlink()
    del extracted
    assert isinstance(selected.files.source(name), Path)
    copied = write_config_package(tmp_path / "copy", selected)
    assert sha256_file(copied / name) == digest
