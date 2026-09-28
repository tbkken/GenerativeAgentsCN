"""Offline tests: fixtures, adversarial mutations, and a simulated Responses loop."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common import parse_json, read_json
from function_calling import dispatch_tool, run_agent
from generate_schedule import generate_schedule
from rag import build_chunks, cosine, rank_chunks
from validate_schedule import get_room_availability, validate_schedule


def function_call(name: str, arguments: object, call_id: str = "call_1") -> SimpleNamespace:
    return SimpleNamespace(type="function_call", name=name, arguments=json.dumps(arguments), call_id=call_id)


class Response:
    def __init__(self, output=None, text="", status="completed"):
        self.output = output or []
        self.output_text = text
        self.status = status

    def model_dump(self, **_):
        return {"output": [vars(item) for item in self.output], "output_text": self.output_text, "status": self.status}


class Client:
    def __init__(self, responses):
        self.queue = iter(responses)
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        return next(self.queue)


class BusinessChecks(unittest.TestCase):
    def setUp(self):
        self.valid = read_json(ROOT / "fixtures" / "valid-schedule.json")

    def codes(self, schedule):
        return {error["code"] for error in validate_schedule(schedule)["errors"]}

    def test_human_fixture_and_algorithm_baseline_are_valid(self):
        self.assertTrue(validate_schedule(self.valid)["valid"])
        self.assertTrue(validate_schedule(generate_schedule())["valid"])
        self.assertEqual(generate_schedule()["activities"][1]["end"], "2026-10-17T10:00:00+08:00")

    def test_invalid_fixture_has_three_business_errors(self):
        invalid = read_json(ROOT / "fixtures" / "invalid-schedule.json")
        self.assertEqual(self.codes(invalid), {"participants_mismatch", "capacity_exceeded", "booking_conflict"})

    def test_half_open_boundaries_are_valid(self):
        item = self.valid["activities"][1]
        item.update(start="2026-10-17T09:00:00+08:00", end="2026-10-17T10:00:00+08:00")
        self.assertTrue(validate_schedule(self.valid)["valid"])
        item.update(start="2026-10-17T10:30:00+08:00", end="2026-10-17T11:30:00+08:00")
        self.assertTrue(validate_schedule(self.valid)["valid"])

    def test_one_minute_booking_overlap_is_rejected(self):
        self.valid["activities"][1].update(start="2026-10-17T09:01:00+08:00", end="2026-10-17T10:01:00+08:00")
        self.assertIn("booking_conflict", self.codes(self.valid))

    def test_missing_and_duplicate_activities(self):
        self.valid["activities"][2] = deepcopy(self.valid["activities"][0])
        self.assertTrue({"missing_activity", "duplicate_activity", "activity_conflict"} <= self.codes(self.valid))

    def test_unknown_ids_and_bad_types_do_not_crash(self):
        for value in (None, [], {}, True, "not-an-id"):
            with self.subTest(value=value):
                changed = deepcopy(self.valid)
                changed["activities"][0]["activity_id"] = value
                changed["activities"][0]["room_id"] = value
                self.assertTrue({"unknown_activity", "unknown_room"} <= self.codes(changed))

    def test_boolean_and_string_participants_are_rejected(self):
        for value in (True, "24", 24.0, 0, -1):
            with self.subTest(value=value):
                self.valid["activities"][0]["participants"] = value
                self.assertIn("invalid_participants", self.codes(self.valid))

    def test_missing_extra_fields_and_wrong_root_type(self):
        del self.valid["activities"][0]["start"]
        self.valid["activities"][0]["booked"] = True
        self.assertTrue({"missing_field", "extra_field", "invalid_time"} <= self.codes(self.valid))
        for value in (None, [], "text"):
            self.assertIn("invalid_type", self.codes(value))

    def test_wrong_room_and_capacity(self):
        self.valid["activities"][0]["room_id"] = "craft"
        self.assertTrue({"room_not_allowed", "capacity_exceeded"} <= self.codes(self.valid))

    def test_duration_and_open_window(self):
        self.valid["activities"][0].update(start="2026-10-17T08:30:00+08:00", end="2026-10-17T10:00:00+08:00")
        self.assertTrue({"outside_opening", "outside_window", "duration_mismatch"} <= self.codes(self.valid))

    def test_date_timezone_and_reversed_interval(self):
        self.valid.update(date="2026-10-18", timezone="UTC")
        self.valid["activities"][0].update(start="2026-10-17T10:00:00+08:00", end="2026-10-17T09:00:00+08:00")
        self.assertTrue({"wrong_date", "wrong_timezone", "invalid_interval"} <= self.codes(self.valid))

    def test_time_offset_and_seconds_required(self):
        for value in ("2026-10-17T09:00:00", "2026-10-17T01:00:00Z", "2026-10-18T09:00:00+08:00", "2026-10-17T09:00:01+08:00", "2026-10-17T25:00:00+08:00"):
            self.valid["activities"][0]["start"] = value
            self.assertIn("invalid_time", self.codes(self.valid))

    def test_json_duplicate_keys_and_nonfinite_rejected(self):
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e309}', '{"x":-1e309}'):
            with self.assertRaises(ValueError):
                parse_json(text)

    def test_room_query_is_read_only_and_rejects_unknown(self):
        before = (ROOT / "data" / "availability.json").read_bytes()
        result = get_room_availability("craft", "2026-10-17")
        self.assertEqual(result["bookings"][0]["start"], "2026-10-17T10:00:00+08:00")
        self.assertEqual(before, (ROOT / "data" / "availability.json").read_bytes())
        with self.assertRaises(ValueError):
            get_room_availability("unknown", "2026-10-17")
        with self.assertRaises(ValueError):
            get_room_availability("craft", "2026-10-18")

    def test_cli_exit_codes_and_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            base = [sys.executable, "-X", "utf8", str(ROOT / "validate_schedule.py")]
            valid = subprocess.run(base + [str(ROOT / "fixtures" / "valid-schedule.json"), "--output", str(report)], capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(valid.returncode, 0)
            self.assertTrue(json.loads(valid.stdout)["valid"])
            self.assertTrue(read_json(report)["valid"])
            invalid = subprocess.run(base + [str(ROOT / "fixtures" / "invalid-schedule.json")], capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(invalid.returncode, 1)
            missing = subprocess.run(base + [str(Path(directory) / "missing.json")], capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(missing.returncode, 2)
            self.assertEqual(json.loads(missing.stderr)["error"], "input_or_io_error")

    def test_cli_never_overwrites_its_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "schedule.json"
            text = json.dumps(self.valid)
            path.write_text(text, encoding="utf-8")
            result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "validate_schedule.py"), str(path), "--output", str(path)], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_skill_bundle_is_self_contained_and_in_sync(self):
        skill = ROOT / "skills" / "open-day-planner"
        for name in ("common.py", "validate_schedule.py"):
            self.assertEqual((ROOT / name).read_bytes(), (skill / "scripts" / name).read_bytes())
        for name in ("brief.md", "rooms.csv", "rules.md", "requests.csv", "availability.json"):
            self.assertEqual((ROOT / "data" / name).read_bytes(), (skill / "references" / name).read_bytes())
        self.assertEqual((ROOT / "schemas" / "schedule.schema.json").read_bytes(), (skill / "assets" / "schedule.schema.json").read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-X", "utf8", str(skill / "scripts" / "validate_schedule.py"), str(ROOT / "fixtures" / "valid-schedule.json")], cwd=directory, capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)


class FunctionLoopChecks(unittest.TestCase):
    def setUp(self):
        self.valid = read_json(ROOT / "fixtures" / "valid-schedule.json")

    def test_preserves_reasoning_and_call_id(self):
        reasoning = SimpleNamespace(type="reasoning", id="reasoning_1", summary=[])
        client = Client([Response([reasoning, function_call("validate_schedule", {"schedule": self.valid})]), Response(text="草案可行，未预约。")])
        result = run_agent(client, "fake-model")
        self.assertEqual(result.schedule, self.valid)
        items = client.calls[1]["input"]
        self.assertEqual(items[1].type, "reasoning")
        self.assertEqual(items[2].call_id, "call_1")
        self.assertEqual(items[3]["call_id"], "call_1")
        self.assertTrue(json.loads(items[3]["output"])["result"]["valid"])

    def test_unknown_and_malformed_arguments_rejected(self):
        self.assertEqual(dispatch_tool("delete_files", {})["error"], "unknown_tool")
        self.assertEqual(dispatch_tool("get_room_availability", {"room_id": "craft", "date": "2026-10-17", "extra": True})["error"], "invalid_arguments")
        call = function_call("get_room_availability", {})
        call.arguments = "{malformed"
        client = Client([Response([call]), Response(text="无法完成")])
        with self.assertRaisesRegex(RuntimeError, "没有最后一次"):
            run_agent(client, "fake")
        self.assertEqual(json.loads(client.calls[1]["input"][-1]["output"])["error"], "invalid_json")

    def test_multiple_calls_are_processed_and_results_matched(self):
        calls = [function_call("get_room_availability", {"room_id": "craft", "date": "2026-10-17"}, "lookup"),
                 function_call("validate_schedule", {"schedule": self.valid}, "validate")]
        client = Client([Response(calls), Response(text="完成草案")])
        run_agent(client, "fake")
        outputs = [item for item in client.calls[1]["input"] if isinstance(item, dict) and item.get("type") == "function_call_output"]
        self.assertEqual([item["call_id"] for item in outputs], ["lookup", "validate"])

    def test_incomplete_and_refusal_stop_before_tool_execution(self):
        client = Client([Response([function_call("validate_schedule", {"schedule": self.valid})], status="incomplete")])
        with self.assertRaisesRegex(RuntimeError, "未完成"):
            run_agent(client, "fake")
        refusal = SimpleNamespace(type="message", content=[SimpleNamespace(type="refusal", refusal="拒绝")])
        with self.assertRaisesRegex(RuntimeError, "拒绝"):
            run_agent(Client([Response([refusal])]), "fake")

    def test_no_validation_no_delivery(self):
        with self.assertRaisesRegex(RuntimeError, "没有最后一次"):
            run_agent(Client([Response(text="我已经完成并检查。")]), "fake")

    def test_new_invalid_candidate_clears_previous_success(self):
        invalid = deepcopy(self.valid)
        invalid["activities"] = []
        client = Client([Response([function_call("validate_schedule", {"schedule": self.valid}, "a")]),
                         Response([function_call("validate_schedule", {"schedule": invalid}, "b")]), Response(text="完成")])
        with self.assertRaisesRegex(RuntimeError, "没有最后一次"):
            run_agent(client, "fake")

    def test_duplicate_call_id_rejected(self):
        client = Client([Response([function_call("get_room_availability", {"room_id": "craft", "date": "2026-10-17"}, "same")]),
                         Response([function_call("validate_schedule", {"schedule": self.valid}, "same")])])
        with self.assertRaisesRegex(RuntimeError, "call_id"):
            run_agent(client, "fake")

    def test_round_and_tool_budgets(self):
        query = function_call("get_room_availability", {"room_id": "craft", "date": "2026-10-17"})
        with self.assertRaisesRegex(RuntimeError, "轮数预算"):
            run_agent(Client([Response([query])]), "fake", max_rounds=1)
        with self.assertRaisesRegex(RuntimeError, "工具调用预算"):
            run_agent(Client([Response([query, function_call("validate_schedule", {"schedule": self.valid}, "2")])]), "fake", max_tool_calls=1)

    def test_repeated_calls_stop(self):
        responses = [Response([function_call("get_room_availability", {"room_id": "craft", "date": "2026-10-17"}, str(i))]) for i in range(3)]
        trace = []
        with self.assertRaisesRegex(RuntimeError, "无进展"):
            run_agent(Client(responses), "fake", trace=trace)
        self.assertEqual(trace[-1]["not_executed"], "no_progress_limit")
        self.assertEqual(sum("output" in item for item in trace), 2)


class RetrievalChecks(unittest.TestCase):
    def test_chunks_have_unique_stable_sources(self):
        chunks = build_chunks()
        self.assertEqual(len({item["chunk_id"] for item in chunks}), len(chunks))
        self.assertTrue(any(item["chunk_id"] == "availability.json#1" and "10:30" in item["text"] for item in chunks))

    def test_cosine_and_ranking(self):
        self.assertAlmostEqual(cosine([1, 0], [1, 0]), 1)
        self.assertAlmostEqual(cosine([1, 0], [0, 1]), 0)
        result = rank_chunks([{"chunk_id": "a", "text": "甲"}, {"chunk_id": "b", "text": "乙"}], [1, 0], [[0, 1], [1, 0]], 1)
        self.assertEqual(result[0]["chunk_id"], "b")
        for left, right in (([0, 0], [1, 1]), ([1], [1, 2]), ([float("nan")], [1])):
            with self.assertRaises(ValueError):
                cosine(left, right)


if __name__ == "__main__":
    unittest.main()
