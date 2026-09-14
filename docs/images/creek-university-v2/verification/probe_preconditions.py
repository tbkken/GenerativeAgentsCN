"""Read-only service/protocol probes; never log or persist authentication values.

Does not create Studio resources, execute world actions, or start a Run.
The synthetic tool below tests model protocol only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import time

import requests


ROOT = Path(__file__).resolve().parents[1]
REPORT = {"checked_at": datetime.now(timezone.utc).isoformat(), "scope": "read-only APIs and synthetic model tool protocol", "checks": []}


def record(name, **fields):
    row = {"name": name, **fields}
    REPORT["checks"].append(row)
    print(json.dumps(row, ensure_ascii=True), flush=True)
    (ROOT / "verification" / "preconditions.json").write_text(
        json.dumps(REPORT, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main():
    studio = "http://127.0.0.1:8000"
    chat = "http://127.0.0.1:8888"
    embedding = "http://127.0.0.1:5002"
    session = requests.Session()
    session.trust_env = False
    response = session.get(studio + "/api/studio/health", timeout=10)
    record("studio_health", status=response.status_code, result=response.json())
    schema = session.get(studio + "/openapi.json", timeout=10).json()
    route = "/api/studio/resources/skills/{skill_name}/run"
    record("skill_run_route_registered", present=route in schema.get("paths", {}))
    response = session.post(studio + "/api/studio/resources/skills/context-driven-simulation-brain/run", json={"input_text": "验证路由是否存在"}, timeout=10)
    record("skill_run_route_response", status=response.status_code)
    for resource in ("maps", "agents", "model-presets"):
        response = session.get(studio + "/api/studio/resources/" + resource, timeout=10)
        entries = response.json().get("items", [])
        record("studio_" + resource, status=response.status_code, count=len(entries), names=[item.get("name") for item in entries])
    response = session.get(embedding + "/v1/models", timeout=10)
    models = [item["id"] for item in response.json().get("data", [])]
    record("embedding_models", status=response.status_code, models=models)
    if models:
        begin = time.monotonic()
        response = session.post(embedding + "/v1/embeddings", json={"model": models[0], "input": ["林晨约定下午在会议桌旁和赵悦讨论项目。", "今天下午的项目讨论安排在哪里？"]}, timeout=45)
        data = response.json().get("data", [])
        vectors = [item.get("embedding", []) for item in data]
        record("embedding_request", status=response.status_code, seconds=round(time.monotonic()-begin, 3), vector_count=len(vectors), dimensions=[len(v) for v in vectors])
    response = session.get(chat + "/v1/models", timeout=10)
    record("chat_without_auth", status=response.status_code)
    cache = Path.home() / ".unsloth/studio/auth/agent_api_key.json"
    document = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    entry = document.get("servers", {}).get(chat, {})
    headers = None
    for bucket in ("saved", "minted"):
        for key in entry.get(bucket, []):
            if not isinstance(key, str) or not key:
                continue
            candidate = {"Authorization": "Bearer " + key}
            response = session.get(chat + "/v1/models", headers=candidate, timeout=10)
            if response.ok:
                headers = candidate
                break
        if headers:
            break
    if not headers:
        record("chat_cached_auth", success=False)
        return
    chat_models = [item["id"] for item in response.json().get("data", [])]
    record("chat_cached_auth", success=True, models=chat_models)
    if not chat_models:
        return
    tool = {"type": "function", "function": {"name": "report_probe_value", "description": "Return the exact requested test value. This is a synthetic protocol probe, not a world action.", "parameters": {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"], "additionalProperties": False}}}
    for expected in (17, 29, 43):
        messages = [{"role": "system", "content": "这是工具协议测试。使用提供的工具，不能用普通文字冒充工具调用。不要输出思考过程。"}, {"role": "user", "content": f"请调用 report_probe_value，把 value 设为 {expected}。"}]
        begin = time.monotonic()
        response = session.post(chat + "/v1/chat/completions", headers=headers, json={"model": chat_models[0], "messages": messages, "tools": [tool], "temperature": 0.2, "max_tokens": 512, "chat_template_kwargs": {"enable_thinking": False}}, timeout=55)
        payload = response.json()
        message = (payload.get("choices") or [{}])[0].get("message", {})
        calls = message.get("tool_calls") or []
        valid = False
        if len(calls) == 1:
            function = calls[0].get("function", {})
            try:
                args = json.loads(function.get("arguments", "{}"))
                valid = function.get("name") == "report_probe_value" and args == {"value": expected}
            except (TypeError, ValueError):
                pass
        record("chat_tool_call", expected=expected, status=response.status_code, seconds=round(time.monotonic()-begin, 3), valid=valid, call_count=len(calls), finish_reason=(payload.get("choices") or [{}])[0].get("finish_reason"))
        if valid and expected == 17:
            messages.extend([message, {"role": "tool", "tool_call_id": calls[0]["id"], "content": '{"received":17}'}, {"role": "user", "content": "工具已返回。请只说：已收到17，不再调用工具。"}])
            begin = time.monotonic()
            response = session.post(chat + "/v1/chat/completions", headers=headers, json={"model": chat_models[0], "messages": messages, "tools": [tool], "temperature": 0, "max_tokens": 128, "chat_template_kwargs": {"enable_thinking": False}}, timeout=55)
            message = (response.json().get("choices") or [{}])[0].get("message", {})
            record("chat_tool_result_continuation", status=response.status_code, seconds=round(time.monotonic()-begin, 3), valid="17" in str(message.get("content", "")) and not message.get("tool_calls"), output=message.get("content"))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Do not expose request headers, server payloads, or credential values.
        record("probe_error", error_type=type(error).__name__)
        raise SystemExit(1)
