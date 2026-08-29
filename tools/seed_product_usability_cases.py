"""Idempotently seed the retained product-usability experiment library.

The script only creates or updates experiments whose names start with ``UX-``.
It never deletes experiments, revisions, runs, maps, or artifacts.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


BASE_URL = "http://127.0.0.1:8000/api/v1"


CASES: list[dict[str, Any]] = [
    {
        "code": "UX-01",
        "name": "UX-01 新手默认模板创建路径",
        "goal": "验证首次用户能否理解默认模板、配置规模、校验状态并定位发布入口。",
        "source": "BUILTIN_DEFAULT",
        "agents": 25,
        "simulation": {"max_steps": 1000, "stride_minutes": 10},
    },
    {
        "code": "UX-02",
        "name": "UX-02 空白实验首次配置引导",
        "goal": "验证从空白开始时，Agent、模型、地图、Prompt 的缺失状态是否可理解且有明确下一步。",
        "source": "BLANK",
    },
    {
        "code": "UX-03",
        "name": "UX-03 单Agent晨间日程观察",
        "goal": "验证最小单角色实验的配置、日程生成和结果查看路径。",
        "source": "BUILTIN_DEFAULT",
        "agents": 1,
        "simulation": {
            "max_steps": 12,
            "stride_minutes": 10,
            "checkpoint_interval_steps": 3,
        },
    },
    {
        "code": "UX-04",
        "name": "UX-04 双Agent Brain会话实验",
        "goal": "验证双角色社交是否由所选 Brain Skill 的自然语言 SOP 稳定驱动。",
        "source": "BUILTIN_DEFAULT",
        "agents": 2,
        "simulation": {"max_steps": 36, "stride_minutes": 10},
    },
    {
        "code": "UX-05",
        "name": "UX-05 五Agent公共事件传播",
        "goal": "验证小群体信息传播实验的角色选择、Prompt 调整和结果对比路径。",
        "source": "BUILTIN_DEFAULT",
        "agents": 5,
        "simulation": {"max_steps": 72, "stride_minutes": 10},
    },
    {
        "code": "UX-06",
        "name": "UX-06 二十五Agent整镇负载",
        "goal": "验证完整 25 Agent 配置时列表、批量编辑、校验和运行成本提示。",
        "source": "BUILTIN_DEFAULT",
        "agents": 25,
        "simulation": {
            "max_steps": 144,
            "stride_minutes": 10,
            "checkpoint_interval_steps": 12,
        },
    },
    {
        "code": "UX-07",
        "name": "UX-07 三Agent多日长时运行",
        "goal": "验证多日、细步长、大步数实验的成本预估、检查点和恢复配置。",
        "source": "BUILTIN_DEFAULT",
        "agents": 3,
        "simulation": {
            "max_steps": 1000,
            "stride_minutes": 5,
            "checkpoint_interval_steps": 10,
            "checkpoint_retention": 5,
        },
    },
    {
        "code": "UX-08",
        "name": "UX-08 高频状态记录与追踪",
        "goal": "验证高频状态投影、模型调用留痕和结果数据量提示。",
        "source": "BUILTIN_DEFAULT",
        "agents": 2,
        "simulation": {
            "max_steps": 120,
            "stride_minutes": 5,
        },
        "results": {
            "agent_step_projection_interval_steps": 1,
            "capture_model_payloads": True,
        },
    },
    {
        "code": "UX-09",
        "name": "UX-09 随机种子对照A-42",
        "goal": "与 UX-10 对照，验证用户能否辨认唯一变量并复现实验。",
        "source": "BUILTIN_DEFAULT",
        "agents": 3,
        "simulation": {"max_steps": 100, "stride_minutes": 10, "random_seed": 42},
    },
    {
        "code": "UX-10",
        "name": "UX-10 随机种子对照B-99",
        "goal": "与 UX-09 对照，仅改变随机种子，验证复制和差异比较能力。",
        "source": "BUILTIN_DEFAULT",
        "agents": 3,
        "simulation": {"max_steps": 100, "stride_minutes": 10, "random_seed": 99},
    },
    {
        "code": "UX-11",
        "name": "UX-11 Brain Skill线性SOP",
        "goal": "验证自然语言 Brain SOP、子 Skill 选择、版本保存和依赖冻结。",
        "source": "BUILTIN_DEFAULT",
        "agents": 2,
        "simulation": {"max_steps": 24, "stride_minutes": 10},
    },
    {
        "code": "UX-12",
        "name": "UX-12 Brain Skill条件SOP",
        "goal": "验证自然语言条件、停止规则和子 Skill 调用在复杂 Brain SOP 中的可用性。",
        "source": "BUILTIN_DEFAULT",
        "agents": 3,
        "simulation": {"max_steps": 48, "stride_minutes": 10},
    },
    {
        "code": "UX-13",
        "name": "UX-13 LLM结构化输出与重试",
        "goal": "验证 JSON Schema、结构校验、失败重试和错误提示是否对非程序员友好。",
        "source": "BUILTIN_DEFAULT",
        "agents": 2,
        "simulation": {"max_steps": 24, "stride_minutes": 10},
    },
    {
        "code": "UX-14",
        "name": "UX-14 自定义地图Revision选择",
        "goal": "验证地图创建、发布以及实验选择不可变地图 Revision 的端到端路径。",
        "source": "BUILTIN_DEFAULT",
        "agents": 2,
        "simulation": {"max_steps": 24, "stride_minutes": 10},
    },
    {
        "code": "UX-15",
        "name": "UX-15 模型服务不可用校验",
        "goal": "验证模型端点不可达时的检测、定位、修复建议和发布阻断体验。",
        "source": "BUILTIN_DEFAULT",
        "agents": 1,
        "simulation": {"max_steps": 6, "stride_minutes": 10},
        "invalid_models": True,
    },
    {
        "code": "UX-16",
        "name": "UX-16 超长名称与多语言边界-Research-研究-リサーチ-1234567890-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "goal": "验证长名称、中英日混排、搜索、卡片截断和详情页标题显示。",
        "source": "BUILTIN_DEFAULT",
        "agents": 1,
        "simulation": {"max_steps": 6, "stride_minutes": 10},
    },
]


def api_request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """向本地产品 API 发送 JSON 请求，并把非成功响应转换为可读异常。"""

    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> {exc.code}: {body}") from exc


def seed() -> list[dict[str, Any]]:
    """幂等创建产品易用性评审所需的实验样例并返回创建结果。"""

    map_items = api_request("GET", "/maps?status=PUBLISHED&page_size=100")["items"]
    if not map_items or not map_items[0].get("current_published"):
        raise RuntimeError("请先创建并发布一张用户地图，再运行 UX 样例种子")
    map_revision_id = map_items[0]["current_published"]["id"]
    brain = api_request("GET", "/skills/stanford-town-brain")
    crowd_items = api_request("GET", "/crowds?status=PUBLISHED&page_size=100")["items"]
    if not crowd_items or not crowd_items[0].get("current_published"):
        raise RuntimeError("请先发布一个 Crowd Revision，再运行 UX 样例种子")
    crowd_revision_id = crowd_items[0]["current_published"]["id"]
    existing = api_request("GET", "/experiments?page_size=50&sort=created_at")["items"]
    by_code = {
        case["code"]: next(
            (item for item in existing if item["name"].startswith(case["code"])),
            None,
        )
        for case in CASES
    }
    results: list[dict[str, Any]] = []

    for case in CASES:
        experiment = by_code[case["code"]]
        if experiment is None:
            experiment = api_request(
                "POST",
                "/experiments",
                {
                    "name": case["name"],
                    "goal": case["goal"],
                    "source": {"type": "BLANK"},
                    "brain_skill": brain["name"],
                    "brain_revision_id": brain["revision_id"],
                    "map_revision_id": map_revision_id,
                    "crowd_revision_ids": (
                        [] if case["source"] == "BLANK" else [crowd_revision_id]
                    ),
                },
            )
            action = "created"
        else:
            if experiment.get("status") != "DRAFT":
                results.append(
                    {
                        "code": case["code"],
                        "action": "skipped",
                        "id": experiment["id"],
                        "status": experiment.get("status"),
                        "reason": "published experiments and retained runs are immutable",
                    }
                )
                continue
            experiment = api_request(
                "PATCH",
                f"/experiments/{experiment['id']}",
                {
                    "row_version": experiment["row_version"],
                    "name": case["name"],
                    "goal": case["goal"],
                },
            )
            action = "updated"

        draft = api_request("GET", f"/experiments/{experiment['id']}/draft")
        definition = draft["definition"]
        definition["experiment"]["name"] = case["name"]
        definition["experiment"]["goal"] = case["goal"]

        if case["source"] != "BLANK":
            definition["agents"] = definition["agents"][: case.get("agents", len(definition["agents"]))]
            definition["simulation"].update(case.get("simulation", {}))
            definition["results"].update(case.get("results", {}))
            if case.get("invalid_models"):
                definition["models"]["chat"]["base_url"] = "http://127.0.0.1:59999/v1"
                definition["models"]["embedding"]["base_url"] = "http://127.0.0.1:59998/v1"

        updated = api_request(
            "PUT",
            f"/experiments/{experiment['id']}/draft",
            {"lock_version": draft["lock_version"], "data": definition},
        )
        results.append(
            {
                "code": case["code"],
                "action": action,
                "id": experiment["id"],
                "agents": len(updated["definition"]["agents"]),
                "max_steps": updated["definition"]["simulation"]["max_steps"],
            }
        )

    return results


if __name__ == "__main__":
    print(json.dumps({"items": seed()}, ensure_ascii=False, indent=2))
