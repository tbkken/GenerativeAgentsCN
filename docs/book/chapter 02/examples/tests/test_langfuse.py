"""Simulated behavior + optional real SDK / in-memory exporter checks; no network."""
from contextlib import contextmanager, nullcontext
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import langfuse_trace as lesson
from common import read_json


class FakeTelemetry:
    def __init__(self, *, flush_error=False, score_error=False):
        self.names, self.scores = [], []
        self.flush_count = 0
        self.flush_error, self.score_error = flush_error, score_error

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        self.names.append(kwargs["name"])
        yield SimpleNamespace(update=lambda **_: None)

    def get_current_trace_id(self):
        return "1" * 32  # Deliberately fake; never uploaded or presented as a real trace.

    def create_score(self, **kwargs):
        if self.score_error:
            raise RuntimeError("simulated score failure")
        self.scores.append(kwargs)

    def flush(self):
        self.flush_count += 1
        if self.flush_error:
            raise RuntimeError("simulated flush failure")


def fake_response(*, valid=True, status="completed"):
    candidate = read_json(ROOT / "fixtures" / ("valid-schedule.json" if valid else "invalid-schedule.json"))
    return SimpleNamespace(status=status, output=[], output_text=json.dumps(candidate),
                           model_dump=lambda **_: {"status": status, "output": [], "output_text": json.dumps(candidate)})


class LangfuseLessonChecks(unittest.TestCase):
    def test_default_fixture_does_not_construct_sdk_and_has_no_trace(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(lesson, "make_live_clients") as factory:
            code, record = lesson.run(Path(folder) / "new")
            factory.assert_not_called()
            self.assertEqual(code, 0)
            self.assertEqual(record["mode"], "offline_fixture")
            self.assertIsNone(record["trace_id"])
            self.assertEqual(record["logical_model_requests"], 0)
            self.assertFalse(record["telemetry"]["flush_attempted"])

    def test_invalid_fixture_preserved_without_success_schedule(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "new"
            code, record = lesson.run(target, fixture=ROOT / "fixtures" / "invalid-schedule.json")
            self.assertEqual((code, record["business_score"]), (1, 0))
            self.assertTrue((target / "candidate.json").is_file())
            self.assertFalse((target / "schedule.json").exists())
            self.assertFalse(read_json(target / "validation-report.json")["valid"])

    def test_bad_json_remains_not_evaluated(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "bad.json"
            source.write_text('{"x": 1e309}', encoding="utf-8")
            code, record = lesson.run(Path(folder) / "new", fixture=source)
            self.assertEqual(code, 2)
            self.assertEqual(record["business_status"], "not_evaluated")
            self.assertIsNone(record["business_score"])

    def test_live_missing_env_stops_before_sdk_or_output(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True), patch.object(lesson, "make_live_clients") as factory:
            target = Path(folder) / "new"
            with self.assertRaises(lesson.ConfigurationError):
                lesson.run(target, live=True)
            factory.assert_not_called()
            self.assertFalse(target.exists())

    def test_existing_directory_not_modified(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            old = target / "schedule.json"
            old.write_text("prior evidence", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                lesson.run(target)
            self.assertEqual(old.read_text(encoding="utf-8"), "prior evidence")

    def test_shared_session_keeps_separate_local_attempts(self):
        with tempfile.TemporaryDirectory() as folder:
            _, first = lesson.run(Path(folder) / "one", session_id="comparison-one")
            _, second = lesson.run(Path(folder) / "two", session_id="comparison-one")
            self.assertEqual(first["session_id"], second["session_id"])
            self.assertNotEqual(first["example_id"], second["example_id"])
            self.assertIn("validate_schedule.py", first["source_sha256"])
            self.assertEqual(first["source_sha256"], second["source_sha256"])

    def test_empty_session_rejected_without_output(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(lesson.ConfigurationError):
                lesson.run(Path(folder) / "new", session_id=" ")

    def simulated_live(self, telemetry, *, response=None, failure=None):
        requests = []
        def create(**kwargs):
            requests.append(kwargs)
            if failure:
                raise failure
            return response or fake_response()
        client = SimpleNamespace(responses=SimpleNamespace(create=create), close=lambda: None)
        settings = {name: "offline-placeholder" for name in lesson.ENV_NAMES}
        with tempfile.TemporaryDirectory() as folder, patch.object(lesson, "live_settings", return_value=settings), patch.object(lesson, "make_live_clients", return_value=(telemetry, client, lambda **_: nullcontext())), patch.object(lesson, "version", return_value="mock-version"):
            code, record = lesson.run(Path(folder) / "new", live=True)
            files = {p.name for p in (Path(folder) / "new").iterdir()}
        return code, record, requests, files

    def test_model_failure_flushes_without_score(self):
        telemetry = FakeTelemetry()
        code, record, requests, files = self.simulated_live(telemetry, failure=TimeoutError("simulated"))
        self.assertEqual((code, len(requests), telemetry.flush_count), (2, 1, 1))
        self.assertEqual(record["business_status"], "not_evaluated")
        self.assertEqual(telemetry.scores, [])
        self.assertNotIn("schedule.json", files)

    def test_incomplete_response_saved_without_business_score(self):
        telemetry = FakeTelemetry()
        code, record, _, files = self.simulated_live(telemetry, response=fake_response(status="incomplete"))
        self.assertEqual(code, 2)
        self.assertIn("model-response.json", files)
        self.assertNotIn("candidate.json", files)
        self.assertIsNone(record["business_score"])
        self.assertEqual(telemetry.flush_count, 1)

    def test_telemetry_failure_does_not_replace_business_result(self):
        telemetry = FakeTelemetry(flush_error=True, score_error=True)
        code, record, requests, files = self.simulated_live(telemetry)
        self.assertEqual((code, record["business_score"]), (0, 1))
        self.assertIn("schedule.json", files)
        self.assertFalse(record["telemetry"]["flush_returned"])
        self.assertFalse(record["telemetry"]["server_delivery_verified"])
        self.assertEqual(len(record["telemetry"]["errors"]), 2)
        self.assertEqual(requests[0]["text"]["format"]["strict"], True)

    def test_failed_business_creates_boolean_zero_score(self):
        telemetry = FakeTelemetry()
        code, record, _, files = self.simulated_live(telemetry, response=fake_response(valid=False))
        self.assertEqual(code, 1)
        self.assertEqual(telemetry.scores[0]["value"], 0)
        self.assertEqual(telemetry.scores[0]["name"], "business_valid")
        self.assertEqual(telemetry.scores[0]["trace_id"], record["trace_id"])
        self.assertNotIn("schedule.json", files)

    @unittest.skipUnless(importlib.util.find_spec("langfuse") and importlib.util.find_spec("openai"), "optional Langfuse/OpenAI SDKs not installed")
    def test_real_sdks_with_mock_http_and_memory_exporter(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENAI_", "LANGFUSE_", "OTEL_"))}
        result = subprocess.run([sys.executable, "-X", "utf8", __file__, "--sdk-child"],
                                capture_output=True, text=True, encoding="utf-8", env=env, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"generation_count": 1', result.stdout)


def sdk_contract():
    """Use actual instrumented Responses and OTel context, entirely in memory."""
    import httpx
    from langfuse import Langfuse, propagate_attributes
    from langfuse.openai import OpenAI
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    import socket

    requests = []
    def handler(request):
        body = json.loads(request.content)
        if request.url.path.endswith("/responses"):
            requests.append(body)
            assert "langfuse_public_key" not in body and "name" not in body
            text = json.dumps(read_json(ROOT / "fixtures" / "valid-schedule.json"))
            return httpx.Response(200, json={"id": "resp_simulated", "object": "response", "created_at": 1,
                "model": "offline-model", "status": "completed", "output": [{"id": "msg_simulated", "type": "message", "role": "assistant",
                "status": "completed", "content": [{"type": "output_text", "text": text, "annotations": []}]}],
                "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}})
        assert request.url.host == "langfuse.test"
        return httpx.Response(200, json={"successes": [{"id": item["id"], "status": 201} for item in body.get("batch", [])], "errors": []})

    settings = {"OPENAI_API_KEY": "offline-placeholder", "OPENAI_MODEL": "offline-model", "LANGFUSE_PUBLIC_KEY": "pk-offline-contract",
                "LANGFUSE_SECRET_KEY": "sk-offline-contract", "LANGFUSE_BASE_URL": "https://langfuse.test"}
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    with patch.dict(os.environ, settings), patch.object(socket.socket, "connect", side_effect=AssertionError("No network allowed")):
        telemetry = Langfuse(public_key=settings["LANGFUSE_PUBLIC_KEY"], secret_key=settings["LANGFUSE_SECRET_KEY"],
            base_url=settings["LANGFUSE_BASE_URL"], tracer_provider=provider, span_exporter=exporter,
            httpx_client=httpx.Client(transport=httpx.MockTransport(handler)))
        client = OpenAI(api_key=settings["OPENAI_API_KEY"], base_url="https://openai.test/v1", timeout=45, max_retries=0,
                        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        with tempfile.TemporaryDirectory() as folder, patch.object(lesson, "make_live_clients", return_value=(telemetry, client, propagate_attributes)):
            code, record = lesson.run(Path(folder) / "new", live=True, session_id="sdk-contract-session")
        assert code == 0, record
        spans = exporter.get_finished_spans()
        generations = [s for s in spans if s.attributes.get("langfuse.observation.type") == "generation"]
        roots = [s for s in spans if s.name == "qinghe-schedule-attempt"]
        assert len(roots) == len(generations) == len(requests) == 1, [(s.name, dict(s.attributes)) for s in spans]
        root, generation = roots[0], generations[0]
        assert generation.parent.span_id == root.context.span_id
        assert f"{root.context.trace_id:032x}" == record["trace_id"]
        for span in spans:
            assert span.attributes["session.id"] == "sdk-contract-session", (span.name, dict(span.attributes))
            assert span.attributes["langfuse.trace.metadata.example_id"] == record["example_id"], (span.name, dict(span.attributes))
        assert {s.name for s in spans} >= {"load-materials", "validate-schedule", "save-artifacts"}
        assert client.max_retries == 0
        assert record["telemetry"]["flush_returned"] and not record["telemetry"]["server_delivery_verified"]
        print(json.dumps({"mode": "mock_http_memory_exporter", "generation_count": len(generations), "span_count": len(spans)}))
        telemetry.shutdown()


if __name__ == "__main__":
    sdk_contract() if "--sdk-child" in sys.argv else unittest.main()
