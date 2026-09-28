"""Prepare portable browser-test inputs only; all Studio writes happen in the UI."""
from io import BytesIO
from pathlib import Path
import tempfile

from PIL import Image

from generative_agents.ga_protocol.packages.io import seal_directory
from generative_agents.ga_protocol.packages.resources import ResourceRecord, ResourceRef, ResourceSet, write_config_package
from generative_agents.ga_protocol.schemas.resources import AgentCore
from generative_agents.ga_studio.resources.maps import normalize_public_world
from tests.foundation.test_navigation import navigation_world


def prepare():
    root = Path(tempfile.mkdtemp(prefix="ga-resource-tabs-ui-"))
    files, records = {}, []
    for key, size in (("portrait", (64, 64)), ("sprite", (128, 128))):
        content = BytesIO()
        Image.new("RGBA", size, "#47917b").save(content, format="PNG")
        files[f"assets/{key}.png"] = content.getvalue()
    records.append(ResourceRecord(kind="agent", key="tabs-agent", name="Tab 验收人物", description="合并菜单验收",
        definition=AgentCore(scratch={"age": 29}, portrait_asset="assets/portrait.png", sprite_asset="assets/sprite.png").model_dump(mode="json"), attachments=list(files)))
    member = ResourceRef(kind="agent", key="tabs-agent")
    records.append(ResourceRecord(kind="crowd", key="tabs-crowd", name="Tab 验收人群", definition={"members": [member.model_dump(mode="json", exclude_none=True)]}, dependencies=[member]))
    for key, kind, children in (("tabs-observe", "atomic", []), ("tabs-pack", "pack", ["tabs-observe"]), ("tabs-brain", "brain", ["tabs-pack", "tabs-observe"])):
        path = f"skills/items/{key}/SKILL.md"
        files[path] = (f"---\nname: {key}\ndescription: 合并菜单验收\n---\n\n" + "\n".join(f"调用 ${child}。" for child in children) + "\n记录观察结果。\n").encode()
        attachments = [path]
        if kind == "atomic":
            for name, content in (("scripts/note.py", b'print("note")\n'), ("templates/note.txt", "验收模板\n".encode())):
                name = f"skills/items/{key}/{name}"
                files[name] = content
                attachments.append(name)
        records.append(ResourceRecord(kind="skill", key=key, name=key, description="合并菜单验收",
            definition={"skill_kind": kind, "entrypoint": path}, attachments=attachments,
            dependencies=[ResourceRef(kind="skill", key=child) for child in children]))
    world = normalize_public_world(navigation_world()).model_dump(mode="json")
    for key in ("world_key", "world_name", "map_id", "map_snapshot_hash"):
        world.pop(key, None)
    records.append(ResourceRecord(kind="map", key="tabs-map", name="Tab 验收地图", definition=world))
    records.append(ResourceRecord(kind="model", key="tabs-model", name="Tab 验收模型", definition={
        "chat": {"provider": "vllm", "model": "test", "base_url": "http://127.0.0.1:9999/v1"},
        "embedding": {"provider": "openai_compatible", "model": "test", "base_url": "http://127.0.0.1:9999/v1"}}))
    roots = [ResourceRef(kind=record.kind, key=record.key) for record in records if record.key not in {"tabs-agent", "tabs-observe", "tabs-pack"}]
    package = write_config_package(root / "input", ResourceSet(resources=records, files=files, roots=roots))
    seal_directory(package, root / "tabs-config.zip")
    print(root, flush=True)


if __name__ == "__main__":
    prepare()
