"""Archive and compare the main agent's browser download; no service access."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from generative_agents.ga_protocol.schemas.resources import AgentCore


HERE = Path(__file__).resolve().parent
CASE = HERE.parent
SOURCE = Path(r"C:\Users\D304\Downloads\experiment-agents (1).json")
ARCHIVED = HERE / "ui-exports" / SOURCE.name
EXPERIMENT_ID = "b0b85db8-e439-450b-93ad-670d7d6a9caa"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def timestamp(seconds):
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def main():
    content = SOURCE.read_bytes()
    metadata = SOURCE.stat()
    data = json.loads(content.decode("utf-8-sig"))
    roster = read_json(CASE / "agents" / "roster-reference.json")["agents"]
    placements = {item["agent_key"]: item for item in read_json(CASE / "agents" / "experiment-placement-merge.json")["agents"]}
    records = {item["key"]: item for item in read_json(CASE / "agents" / "config" / "resources" / "index.json")["resources"]}
    expected = {item["proposed_stable_key"]: item for item in roster}
    actual = {item["agent_key"]: item for item in data["agents"]}
    assert len(data["agents"]) == len(actual) == len(expected) == 25
    assert set(actual) == set(expected)
    checks = []
    empty_plans = []
    for key, source in expected.items():
        exported = actual[key]
        original_core = AgentCore.model_validate(records[key]["definition"]).model_dump(mode="json")
        expected_core = dict(original_core, sprite_asset=f"assets/agents/{key}/sprite.png")
        actual_core = AgentCore.model_validate({field: value for field, value in exported.items()
                                               if field not in {"agent_key", "name", "coord", "spatial"}}).model_dump(mode="json")
        assert actual_core == expected_core, key
        assert exported["name"] == source["display_name"], key
        assert exported["coord"] == placements[key]["coord"] == source["initial_coord"], key
        assert exported["spatial"] == placements[key]["spatial"], key
        assert exported["spatial"]["address"]["initial_location"] == source["initial_address"], key
        assert exported["currently"] == source["profile"]["currently"], key
        assert exported["scratch"] == {field: source["profile"][field]
                                       for field in ("age", "innate", "learned", "lifestyle", "daily_plan")}, key
        profile_value = {"name": exported["name"], "scratch": exported["scratch"], "currently": exported["currently"]}
        image_expectations = []
        for asset in source["assets"]:
            prepared = (CASE / asset["copy"]).read_bytes()
            assert digest(prepared) == asset["sha256"]
            image_expectations.append({"role": asset["role"], "expected_source_sha256": asset["sha256"],
                                       "exported_path": exported["portrait_asset" if asset["role"] == "portrait" else "sprite_asset"],
                                       "experiment_asset_bytes_verified": False,
                                       "reason": "UI Agent JSON has file paths only, without image bytes or file hashes."})
        if not source["profile"]["daily_plan"]:
            empty_plans.append({"key": key, "name": source["display_name"]})
        checks.append({"key": key, "name": source["display_name"], "status": "MATCH",
                       "all_agent_core_fields_match_after_documented_sprite_path_normalization": True,
                       "profile_sha256": digest(canonical(profile_value)),
                       "coord": exported["coord"], "initial_address": source["initial_address"],
                       "all_common_addresses_and_known_space_match": True,
                       "spatial_sha256": digest(canonical(exported["spatial"])),
                       "source_daily_plan_empty": not bool(source["profile"]["daily_plan"]),
                       "sprite_path_change": {"prepared": original_core["sprite_asset"], "experiment": exported["sprite_asset"]},
                       "images": image_expectations})

    ARCHIVED.parent.mkdir(parents=True, exist_ok=True)
    if ARCHIVED.exists():
        assert ARCHIVED.read_bytes() == content, "Refuse to overwrite a different UI evidence file."
    else:
        shutil.copy2(SOURCE, ARCHIVED)
    assert ARCHIVED.read_bytes() == content
    report = {
        "status": "PASS_AGENT_CONFIGURATION_COMPARISON_ASSET_BYTES_NOT_INCLUDED",
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "experiment_id": EXPERIMENT_ID,
        "experiment_identity_basis": "Main agent reported this UI download after saving and reloading this experiment; JSON itself contains no experiment ID.",
        "original_download": str(SOURCE), "archived_copy": str(ARCHIVED),
        "bytes": len(content), "sha256": digest(content),
        "filesystem_creation_time_utc": timestamp(metadata.st_ctime),
        "filesystem_modified_time_utc": timestamp(metadata.st_mtime),
        "timestamp_note": "Filesystem download times; export generation time is not embedded in the JSON and is unknown.",
        "export_kind": "UI Agent configuration JSON, schema_version 1; not a ga-package or sealed .gaexp",
        "agent_count": len(checks), "missing_agents": [], "extra_agents": [], "duplicate_agents": [],
        "business_content_differences": [],
        "image_reference_count": 50, "image_bytes_in_export": 0,
        "sprite_path_normalization_count": 25,
        "sprite_path_normalization_basis": "ga_studio/experiments/workspace.py _agent_asset uses assets/agents/{agent_key}/{role}{extension}; walking role is sprite.",
        "empty_source_daily_plan_count": len(empty_plans), "empty_source_daily_plans": empty_plans,
        "ui_completeness_basis": "ga_studio/experiments/editor.py:274 requires name, nonempty daily_plan and two-coordinate placement. AgentScratch.daily_plan defaults to empty; this badge is not a protocol failure.",
        "scope_limitations": ["No image bytes or attachment hashes in this export; experiment copies require a package/image export to verify bytes.",
                              "No independent-environment import, sealing, simulation or child-Skill runtime validation is claimed."],
        "checks": checks,
    }
    (HERE / "agents-ui-export-comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = "\n".join(f"| {item['name']} | `{item['key']}` | {item['coord']} | 一致 | {'原值为空' if item['source_daily_plan_empty'] else '原值保留'} |" for item in checks)
    names = "、".join(item["name"] for item in empty_plans)
    markdown = f"""# 已保存人物配置的 UI 导出核对

**25 / 25 人物配置一致，无缺少、增加或重复人物。** 主 Agent 报告已在实验 `{EXPERIMENT_ID}` 中通过 UI 合并原位置，保存并重新加载，然后从 Agent 页下载本 JSON。此 JSON 本身没有实验 ID；实验归属依赖上述 UI 操作记录。

核对覆盖中文姓名、当前目标、年龄／天生特质／背景经历／生活方式／日常计划、启用状态、完整感知配置、原始坐标、完整四层初始地址、全部常用地址与已知空间、头像／行走图引用及布局。所有人设与空间值均与本案准备来源严格一致；包括位置选择器中心点不同的五人，导出仍保存原坐标。

## 原始证据

- 下载来源：`{SOURCE}`。
- 原字节归档：[experiment-agents (1).json](<ui-exports/experiment-agents (1).json>)。
- 字节数：`{len(content)}`；SHA256：`{digest(content)}`。
- 文件系统创建时间 UTC：`{timestamp(metadata.st_ctime)}`；修改时间 UTC：`{timestamp(metadata.st_mtime)}`。
- 上述为下载文件系统时间；JSON 未内嵌生成时间，导出生成时刻记为未知。
- 文件是 Agent 配置 JSON（`schema_version: 1`），不是正式 `.gaexp` 或完整协议包，不据此承诺异机运行。

## 原图引用与校验边界

头像引用均为 `assets/agents/<key>/portrait.png`。行走图从准备包的 `texture.png` 统一归一化为实验内 `sprite.png`，共 25 项；符合 `workspace.py` 的 `_agent_asset` 按角色命名逻辑。布局保留 `4x3`，显示缩放与模型覆盖均保留空值，未补造速度。

本 JSON 包含 50 条图片路径，不包含图片字节或附件哈希。因此这里只确认引用配置匹配，**尚不能验证实验中 50 张图片的字节哈希**。来源副本 SHA256 已复核并列入机器报告，待正式包或图片文件经 UI 导出后再与实验字节对比。

## “待补充”说明

原来源有 {len(empty_plans)} 人的 `daily_plan` 本来为空：{names}。UI 标记“定义完整”的判断要求 `daily_plan` 非空，因此这些人物显示“待补充”符合该判断；协议允许空日常计划。本次保持原值，不编写新增计划，不将此徽标当作导入错误或运行验证失败。

| 人物 | 稳定键 | 保存后坐标 | 人设／完整空间 | 日常计划 |
| --- | --- | --- | --- | --- |
{rows}

逐人字段摘要、配置哈希与 50 图预期源哈希见 [机器比较报告](agents-ui-export-comparison.json)。本次只读 UI 下载与作者资料，没有访问 HTTP、数据库、`var/` 或活动实验目录。
"""
    (HERE / "agents-ui-export-comparison.md").write_text(markdown, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "agent_count", "bytes", "sha256", "empty_source_daily_plan_count", "archived_copy")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
