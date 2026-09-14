"""Exercise actual MCP/Brain contracts in an isolated test scene, not Studio data."""
from pathlib import Path
import importlib.util
import json
import sys
import tempfile
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]
sys.path.insert(0, str(REPO))

from generative_agents.ga_runtime.memory import FileMemoryStream
from generative_agents.runtime.brain import BrainRuntime
from generative_agents.skills import SkillRegistry, SkillRuntime


def main():
    spec = importlib.util.spec_from_file_location("creek_probe_fixture", REPO / "tests/runtime/test_brain_capability_runtime.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    server = fixture._server()
    agent = server.game.get_agent("agent-1")
    agent.scratch = SimpleNamespace(currently="正在长椅旁阅读课程讲义，接下来继续阅读。", age=21, innate="好奇", learned="测试人物背景", lifestyle="白天阅读", daily_plan="09:00阅读课程讲义")
    agent.goals = ["读完讲义"]
    agent.tags = ["student"]
    agent.schedule = SimpleNamespace(abstract=lambda: {})
    agent.spatial = SimpleNamespace(tree={}, address={"initial_location": list(server.iteration.address)})
    agent.concepts = []
    variables = BrainRuntime._agent_variables(agent)
    checks = [{"name": "agent_context_injection", "provided_on_agent": ["age", "innate", "learned", "lifestyle", "daily_plan", "goals", "tags"], "actual_variable_keys": sorted(variables), "full_profile_injected": "daily_plan" in variables or "scratch" in variables}]
    source_root = ROOT / "skill-definitions"
    registry = SkillRegistry()
    documents = {}
    for path in source_root.rglob("SKILL.md"):
        doc = registry._parse(path.read_text(encoding="utf-8"), path, "brain" if "brain" in path.parts else "atomic")
        documents[doc.name] = doc
    missing = sorted({child for doc in documents.values() for child in doc.children if child not in documents})
    assert not missing
    checks.append({"name": "skill_parse_and_dependencies", "count": len(documents), "missing": missing, "brain_children": list(documents["context-driven-simulation-brain"].children)})
    with tempfile.TemporaryDirectory(prefix="creek-contract-", dir=ROOT / "verification") as tmp:
        memory = FileMemoryStream(tmp, run_id=uuid4(), attempt_id=uuid4())
        memory.begin_step(1, server.iteration.now)
        server.memory_stream = memory
        def call(name, args):
            response = server.call(name, args)
            assert not response.get("isError"), response
            return json.loads(response["content"][0]["text"])
        entry = call("memory-stream-append", {"content": "下午在会议桌旁讨论项目", "kind": "plan"})
        found = call("memory-stream-search", {"query": "会议桌 项目", "limit": 3})
        other = memory.search(agent_key="agent-2", query="会议桌 项目", limit=3)
        checks.append({"name": "fresh_memory_and_isolation", "fresh_count": len(found), "other_agent_count": len(other), "retrieval_method": found[0].get("retrieval_method") if found else None})
        assert found and not other
        entry_id = entry.get("id") or entry.get("memory_id")
        call("memory-stream-invalidate", {"memory_id": entry_id, "reason": "测试约定取消"})
        after = call("memory-stream-search", {"query": "会议桌 项目", "limit": 3})
        assert not after
        checks.append({"name": "invalidated_memory_excluded", "active_results": len(after)})

        # Reuse the existing cache exactly as the application's credential probe does.
        import requests
        session = requests.Session()
        session.trust_env = False
        base = "http://127.0.0.1:8888"
        cache = json.loads((Path.home() / ".unsloth/studio/auth/agent_api_key.json").read_text(encoding="utf-8"))
        entry = cache.get("servers", {}).get(base, {})
        selected = None
        model_id = None
        for bucket in ("saved", "minted"):
            for key in entry.get(bucket, []):
                response = session.get(base + "/v1/models", headers={"Authorization": "Bearer " + key}, timeout=10)
                if response.ok:
                    selected = key
                    model_id = response.json()["data"][0]["id"]
                    break
            if selected:
                break
        assert selected and model_id
        registry_view = SimpleNamespace(get=lambda name: documents[name], normalize_name=registry.normalize_name)
        runtime = SkillRuntime(registry_view, base_url=base + "/v1", model=model_id, api_key=selected, mcp=server, provider="vllm", timeout=45, max_hops=8, max_tokens=768, temperature=0.2, enable_thinking=False, retry_attempts=1)
        context = server.iteration.as_dict()
        context["variables"] = variables
        result = runtime.run("context-driven-simulation-brain", "在这个隔离的协议测试场景里，按当前正在阅读的活动决定本轮唯一动作。不要声称这是完整校园实验。", context={"IterationContext": context})
        calls = [{"tool": row.get("tool"), "is_error": bool(row.get("is_error"))} for row in result.trace if row.get("event") == "mcp.call"]
        checks.append({"name": "real_model_with_actual_skill_runtime_and_mcp", "mcp_calls": calls, "selected_action": server.action.action_type if server.action else None, "world_committed": False, "scope": "isolated fixture, current activity only; not four-agent campus behavior"})
        assert server.action is not None
        rejected = server.call("world-act", {"action_type": "ACT", "predicate": "阅读", "object": "讲义"})
        checks.append({"name": "second_world_action_rejected", "rejected": rejected.get("isError", False)})
        assert rejected.get("isError")
    (ROOT / "verification/local-contracts.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
