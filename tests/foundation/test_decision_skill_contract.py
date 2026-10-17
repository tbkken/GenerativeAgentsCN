"""Decision settings remain exact Skill content across author and package boundaries."""
import json
from pathlib import Path

import pytest

from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_protocol.packages.resources import read_resource_set, skill_document
from generative_agents.ga_protocol.skills.documents import SkillRegistry, SkillRegistryError, SnapshotSkillRegistry
from generative_agents.ga_protocol.skills.decisions import decision_prompt_sha256, validate_compiled_decision
from generative_agents.ga_studio.experiments.builder import ExperimentPackageBuilder, SkillSource
from generative_agents.ga_studio.resources.exchange import ResourceExchangeService
from generative_agents.ga_studio.resources.skills import DatabaseSkillRegistry
from generative_agents.ga_studio.storage.assets import AssetStore
from tests.test_portable_package_protocol import _definition


BODY = "根据输入情境判断是否可以出发、适合散步还是休息、事情紧急程度（不急、尽快、立即），返回调用者。"
QUESTIONS = {
    "ready": {"type": "noul", "instructions": BODY},
    "activity": {"type": "choice", "instructions": BODY,
                 "criteria": {"walk": "散步", "rest": "休息"}},
    "urgency": {"type": "score", "instructions": BODY, "criteria": ["不急", "尽快", "立即"]},
}


def markdown(key="decide", questions=QUESTIONS, llm_type="decision", body=BODY):
    return (f"---\nname: {key}\ndescription: 决策\nllm_type: {llm_type}\n"
            f"decision_prompt_sha256: {decision_prompt_sha256(body)}\n"
            f"decision_questions: {json.dumps(questions, ensure_ascii=False)}\n---\n{body}\n")


def parse(source, *, kind="skill", files=None):
    return SkillRegistry(Path("."))._parse(source, Path("decide/SKILL.md"), kind, file_contents=files or {})


def test_default_generative_and_decision_metadata():
    original = "---\nname: decide\ndescription: 原技能\n---\n完成任务。"
    assert parse(original).llm_type == "generative"
    assert parse(original).decision_questions == {}
    source = markdown()
    document = parse(source)
    assert document.llm_type == document.detail()["llm_type"] == "decision"
    assert document.decision_questions == document.detail()["decision_questions"] == QUESTIONS
    assert document.markdown == source
    assert document.children == ()


@pytest.mark.parametrize("questions", [
    [], {"q": {"type": "unknown", "instructions": "问题"}},
    {"q": {"type": "noul", "instructions": ""}},
    {"q": {"type": "noul", "instructions": "问题", "criteria": {}}},
    {"q": {"type": "choice", "instructions": "问题", "criteria": []}},
    {"q": {"type": "choice", "instructions": "问题", "criteria": {"a": ""}}},
    {"q": {"type": "score", "instructions": "问题", "criteria": [0, 1]}},
    {"q": {"type": "score", "instructions": "问题", "criteria": ["级别"] * 11}},
    {str(i): QUESTIONS["ready"] for i in range(65)},
])
def test_invalid_questions_fail_at_document_boundary(questions):
    with pytest.raises(SkillRegistryError):
        parse(markdown(questions=questions))


def test_duplicate_question_keys_and_unknown_model_types_are_rejected():
    source = markdown(questions={}).replace('decision_questions: {}',
        'decision_questions: {"q":{"type":"noul","instructions":"一"},"q":{"type":"noul","instructions":"二"}}')
    with pytest.raises(SkillRegistryError, match="重复"):
        parse(source)
    with pytest.raises(SkillRegistryError, match="llm_type"):
        parse(markdown(llm_type="typo"))
    with pytest.raises(SkillRegistryError, match="生成式"):
        parse(markdown(llm_type="generative"))


def test_uncompiled_draft_is_readable_but_cannot_execute_or_be_saved(database, tmp_path):
    source = "---\nname: decide\ndescription: 决策\nllm_type: decision\n---\n现在是否可以出发？"
    document = parse(source)
    assert document.decision_questions == {}
    with pytest.raises(SkillRegistryError, match="尚未解析"):
        validate_compiled_decision(document)
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / "cache")
    registry.create(name="decide", description="决策")
    with pytest.raises(SkillRegistryError, match="尚未解析"):
        registry.save("decide", source)


def test_compiled_parameters_require_current_body_and_complete_instructions():
    validate_compiled_decision(parse(markdown()))
    with pytest.raises(SkillRegistryError, match="Prompt 已修改"):
        validate_compiled_decision(parse(markdown() + "\n新增条件。"))
    with pytest.raises(SkillRegistryError, match="完整 Skill 正文"):
        validate_compiled_decision(parse(markdown(questions={"ready": {"type": "noul", "instructions": "摘录"}})))


@pytest.mark.parametrize("body,kind,files", [
    ("判断", "brain", {}), ("调用 $child。", "skill", {}),
    ("调用 @world-perceive。", "skill", {}),
    ("判断", "skill", {"scripts/main.py": b"print('ignored')"}),
])
def test_decision_leaf_cannot_silently_ignore_orchestration(body, kind, files):
    with pytest.raises(SkillRegistryError):
        parse(markdown(body=body), kind=kind, files=files)


def test_decision_save_exchange_and_runtime_snapshot_keep_exact_questions(database, tmp_path):
    registry = DatabaseSkillRegistry(database, cache_root=tmp_path / "cache")
    registry.create(name="decide", description="决策")
    saved = registry.save("decide", markdown())
    assert saved.detail()["decision_questions"] == QUESTIONS
    snapshot = SnapshotSkillRegistry(registry.snapshot(["decide"]), root=tmp_path / "runtime")
    assert snapshot.get("decide").decision_questions == QUESTIONS
    exchange = ResourceExchangeService(database, asset_store=AssetStore(tmp_path / "assets"))
    archive = exchange.export_resource("skill", "decide", tmp_path / "decide.zip")
    resources = read_resource_set(archive)
    document = skill_document(resources.resources[0], resources)
    assert document.markdown == saved.markdown
    assert document.llm_type == "decision" and document.decision_questions == QUESTIONS


def test_experiment_requires_decision_model_and_embeds_questions(tmp_path):
    sources = []
    for key, kind, source in (
        ("test-brain", "brain", "---\nname: test-brain\ndescription: brain\n---\n调用 $decide。"),
        ("decide", "skill", markdown()),
    ):
        path = tmp_path / "author" / key / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(source, encoding="utf-8")
        sources.append(SkillSource(key, "brain" if kind == "brain" else "sub_skill", path, editor_kind=kind))
    builder = ExperimentPackageBuilder()
    with pytest.raises(PackageError, match="决策模型"):
        builder.build_directory(tmp_path / "missing-model", definition=_definition(), skills=sources, brain_skill="test-brain")
    definition = _definition()
    definition["models"]["decision"] = {
        "provider": "unsloth", "model": "laya-multilingual", "base_url": "http://127.0.0.1:5003/v1/systemone",
    }
    package = builder.build_archive(tmp_path / "decision.gaexp", staging_directory=tmp_path / "experiment",
                                    definition=definition, skills=sources, brain_skill="test-brain")
    resources = read_resource_set(package)
    record = next(item for item in resources.resources if item.kind == "skill" and item.key == "decide")
    assert skill_document(record, resources).decision_questions == QUESTIONS


def test_object_root_requires_a_generative_orchestrator(tmp_path):
    from tests.runtime.test_object_skill_runtime import world

    definition = _definition()
    definition["world"]["definition"] = world()
    definition["models"]["decision"] = {
        "provider": "unsloth", "model": "laya-multilingual", "base_url": "http://127.0.0.1:5003/v1",
    }
    sources = []
    for key, kind, source in (
        ("test-brain", "brain", "---\nname: test-brain\ndescription: brain\n---\n根据实际情况行动。"),
        ("facility", "object", markdown("facility")),
    ):
        path = tmp_path / key / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(source, encoding="utf-8")
        sources.append(SkillSource(key, kind, path))
    with pytest.raises(PackageError, match="对象根 Skill"):
        ExperimentPackageBuilder().build_directory(tmp_path / "invalid-object", definition=definition,
            skills=sources, brain_skill="test-brain", object_roots=["facility"])
