"""Optional paid image-input or transcription exercise. No calls on import."""
from __future__ import annotations

import argparse
import base64
from pathlib import Path
import sys

from common import ROOT, checked_response_text, make_client, require_env, save_response


def describe_image(path: Path, destination: Path) -> None:
    # Restrict this teaching input to the two formats we construct correctly.
    media_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
    if path.suffix.lower() not in media_types:
        raise ValueError("本例接受 PNG 或 JPEG；先按官方支持格式准备图片。")
    payload = path.read_bytes()
    if not payload:
        raise ValueError("图片为空")
    image_url = f"data:{media_types[path.suffix.lower()]};base64," + base64.b64encode(payload).decode("ascii")
    client = make_client()
    response = client.responses.create(
        model=require_env("OPENAI_MODEL"),
        max_output_tokens=8192, store=False,
        instructions="你是教学资料核对员。只描述可见信息，区分图片观察与无法从图中确定的事实。",
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": "列出图中的房间和相对位置，说明哪些预约事实、容量和可通行信息无法仅凭此图确认。"},
            {"type": "input_image", "image_url": image_url},
        ]}],
    )
    save_response(response, destination / "image-response.json")
    body = checked_response_text(response)
    (destination / "image-observations.md").write_text(body + "\n", encoding="utf-8")
    print(body)


def transcribe(path: Path, destination: Path) -> None:
    client = make_client()
    with path.open("rb") as audio_file:
        result = client.audio.transcriptions.create(
            model=require_env("OPENAI_TRANSCRIPTION_MODEL"), file=audio_file,
        )
    save_response(result, destination / "transcription-response.json")
    text = getattr(result, "text", "")
    if not text.strip():
        raise ValueError("转写没有正文；检查响应，不推断为录音没有需求。")
    (destination / "transcript.txt").write_text(text + "\n", encoding="utf-8")
    print(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["image", "transcribe"])
    parser.add_argument("file", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output" / "multimodal")
    args = parser.parse_args()
    try:
        if not args.file.is_file():
            raise ValueError(f"找不到输入文件：{args.file}")
        if args.output_dir.exists() and any(args.output_dir.iterdir()):
            raise ValueError("输出目录已有内容；使用 --output-dir 指向新的练习目录以保留证据。")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        if args.mode == "image":
            describe_image(args.file, args.output_dir)
        else:
            transcribe(args.file, args.output_dir)
        return 0
    except Exception as exc:
        # The SDK may include remote response details; never dump environment variables.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
