"""Chapter 2 helpers. No SDK import or network access occurs on import."""
from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" if (ROOT / "data").is_dir() else ROOT.parent / "references"


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_json(text: str) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"Non-finite JSON number: {value}")
    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"JSON number exceeds finite range: {value}")
        return parsed
    return json.loads(text, object_pairs_hook=_unique_pairs, parse_constant=reject_constant, parse_float=finite_float)


def read_json(path: str | Path) -> Any:
    return parse_json(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def read_csv(name: str, data_dir: Path = DATA) -> list[dict[str, str]]:
    with (data_dir / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_schema() -> dict[str, Any]:
    path = ROOT / "schemas" / "schedule.schema.json"
    if not path.is_file():
        path = ROOT.parent / "assets" / "schedule.schema.json"
    return read_json(path)


def load_materials() -> str:
    names = ("brief.md", "rooms.csv", "rules.md", "requests.csv", "availability.json")
    return "\n\n".join(f"<document name={name!r}>\n{(DATA / name).read_text(encoding='utf-8')}\n</document>" for name in names)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"请先设置环境变量 {name}；不要把密钥写入教材文件。")
    return value


def make_client() -> Any:
    # Explicitly disable SDK retries so request counts in this tutorial are observable.
    from openai import OpenAI
    return OpenAI(api_key=require_env("OPENAI_API_KEY"), timeout=45.0, max_retries=0)


def save_response(response: Any, path: str | Path) -> None:
    write_json(path, response.model_dump(mode="json"))


def checked_response_text(response: Any) -> str:
    if getattr(response, "status", None) != "completed":
        raise RuntimeError(f"响应未完成：{getattr(response, 'status', None)}；检查保存的响应，不把部分输出当结果。")
    for item in response.output:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "refusal":
                raise RuntimeError(f"模型拒绝：{getattr(part, 'refusal', '')}")
    value = getattr(response, "output_text", "")
    if not value.strip():
        raise RuntimeError("响应中没有可用正文。")
    return value
