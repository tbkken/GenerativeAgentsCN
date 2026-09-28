"""Offline behavior checks for chapter multimodal and parallel review examples."""
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import multi_agent
import multimodal
from common import read_json


class FakeResponse:
    def __init__(self, status="completed", text="教学回答"):
        self.status = status
        self.output_text = text
        self.output = []

    def model_dump(self, **_):
        return {"status": self.status, "output_text": self.output_text, "output": []}


class ExtensionChecks(unittest.TestCase):
    def test_business_failure_never_starts_paid_review(self):
        schedule = read_json(ROOT / "fixtures" / "invalid-schedule.json")
        with tempfile.TemporaryDirectory() as folder, patch.object(multi_agent, "make_client") as factory:
            destination = Path(folder)
            with self.assertRaisesRegex(ValueError, "未通过业务校验"):
                multi_agent.run(schedule, destination)
            factory.assert_not_called()
            self.assertFalse(read_json(destination / "deterministic-report.json")["valid"])
            self.assertFalse((destination / "team-report.md").exists())

    def test_three_isolated_reviews_include_actual_draft(self):
        calls = []
        clients = []

        def factory():
            def create(**request):
                calls.append(request)
                return FakeResponse()
            client = SimpleNamespace(responses=SimpleNamespace(create=create))
            clients.append(client)
            return client

        with tempfile.TemporaryDirectory() as folder, patch.object(multi_agent, "make_client", side_effect=factory), patch.object(multi_agent, "require_env", return_value="offline-placeholder"):
            destination = Path(folder)
            multi_agent.run(read_json(ROOT / "fixtures" / "valid-schedule.json"), destination, "未核验的说法：已经发送通知")
            self.assertEqual(len(calls), 3)
            self.assertEqual(len({id(x) for x in clients}), 3)
            for request in calls:
                self.assertIn("已经发送通知", request["input"])
                self.assertIn("<unverified_draft_plan>", request["input"])
            self.assertNotIn("<review role=", calls[0]["input"])
            self.assertNotIn("<review role=", calls[1]["input"])
            self.assertIn("<review role=", calls[2]["input"])
            self.assertTrue((destination / "team-report.md").is_file())

    def test_incomplete_review_does_not_synthesize_success(self):
        calls = []

        def factory():
            def create(**request):
                calls.append(request)
                return FakeResponse(status="incomplete")
            return SimpleNamespace(responses=SimpleNamespace(create=create))

        with tempfile.TemporaryDirectory() as folder, patch.object(multi_agent, "make_client", side_effect=factory), patch.object(multi_agent, "require_env", return_value="offline-placeholder"):
            destination = Path(folder)
            with self.assertRaisesRegex(RuntimeError, "响应未完成"):
                multi_agent.run(read_json(ROOT / "fixtures" / "valid-schedule.json"), destination)
            self.assertLessEqual(len(calls), 2)
            self.assertFalse((destination / "team-report.md").exists())

    def test_image_payload_and_incomplete_output_boundary(self):
        calls = []

        def create(**request):
            calls.append(request)
            return FakeResponse(status="incomplete")

        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        with tempfile.TemporaryDirectory() as folder, patch.object(multimodal, "make_client", return_value=client), patch.object(multimodal, "require_env", return_value="offline-model"):
            destination = Path(folder)
            with self.assertRaisesRegex(RuntimeError, "响应未完成"):
                multimodal.describe_image(ROOT / "data" / "reference-layout.png", destination)
            part = calls[0]["input"][0]["content"][1]
            self.assertEqual(part["type"], "input_image")
            self.assertTrue(part["image_url"].startswith("data:image/png;base64,iVBOR"))
            self.assertTrue((destination / "image-response.json").is_file())
            self.assertFalse((destination / "image-observations.md").exists())


if __name__ == "__main__":
    unittest.main()
