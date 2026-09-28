"""Optional SDK contract checks. HTTP is mocked; MCP is in memory."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common import load_schema, read_json
from function_calling import run_agent

try:
    import httpx
    from openai import OpenAI
except ImportError:
    httpx = None
    OpenAI = None

try:
    from mcp import Client as MCPClient
    from mcp_server import mcp
except ImportError:
    MCPClient = None
    mcp = None


def envelope(output, name="resp_test"):
    return {"id": name, "object": "response", "created_at": 1, "status": "completed",
            "model": "offline-test-model", "output": output, "parallel_tool_calls": False,
            "tools": [], "tool_choice": "auto", "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}


@unittest.skipIf(OpenAI is None or httpx is None, "optional openai/httpx SDKs not installed")
class OpenAIContracts(unittest.TestCase):
    def test_real_sdk_serializes_tool_output_and_preserved_reasoning(self):
        fixture = read_json(ROOT / "fixtures" / "valid-schedule.json")
        requests = []

        def handler(request):
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                return httpx.Response(200, json=envelope([
                    {"type": "reasoning", "id": "rs_test", "summary": [], "encrypted_content": "offline-placeholder"},
                    {"type": "function_call", "id": "fc_test", "call_id": "call_test", "name": "validate_schedule", "arguments": json.dumps({"schedule": fixture}), "status": "completed"},
                ]))
            return httpx.Response(200, json=envelope([
                {"type": "message", "id": "msg_test", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "排期草案通过，未预约。", "annotations": []}]},
            ], "resp_final"))

        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            with OpenAI(api_key="offline-test-key-not-valid", http_client=http_client, max_retries=0) as client:
                result = run_agent(client, "offline-test-model")
        self.assertEqual(result.schedule, fixture)
        self.assertEqual(requests[1]["input"][1]["encrypted_content"], "offline-placeholder")
        self.assertEqual(requests[1]["input"][-1]["call_id"], "call_test")
        self.assertFalse(requests[0]["store"])

    def test_responses_structured_output_payload(self):
        fixture = read_json(ROOT / "fixtures" / "valid-schedule.json")
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=envelope([
                {"type": "message", "id": "msg_schema", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": json.dumps(fixture), "annotations": []}]},
            ]))

        with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
            with OpenAI(api_key="offline-test-key-not-valid", http_client=http_client, max_retries=0) as client:
                response = client.responses.create(model="offline-test-model", input="test", text={"format": {"type": "json_schema", "name": "open_day_schedule", "schema": load_schema(), "strict": True}}, store=False)
        self.assertEqual(json.loads(response.output_text), fixture)
        self.assertEqual(requests[0]["text"]["format"]["type"], "json_schema")
        self.assertNotIn("response_format", requests[0])


@unittest.skipIf(MCPClient is None, "optional mcp v2 SDK not installed")
class MCPContracts(unittest.TestCase):
    def test_discovery_and_read_only_tools(self):
        async def exercise():
            async with MCPClient(mcp) as client:
                listed = await client.list_tools()
                self.assertEqual({tool.name for tool in listed.tools}, {"get_room_availability", "validate_schedule"})
                found = await client.call_tool("get_room_availability", {"room_id": "craft", "date": "2026-10-17"})
                self.assertEqual(found.structured_content["capacity"], 12)
                result = await client.call_tool("validate_schedule", {"schedule": read_json(ROOT / "fixtures" / "valid-schedule.json")})
                self.assertTrue(result.structured_content["valid"])
                result = await client.call_tool("validate_schedule", {"schedule": read_json(ROOT / "fixtures" / "invalid-schedule.json")})
                self.assertFalse(result.structured_content["valid"])
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
