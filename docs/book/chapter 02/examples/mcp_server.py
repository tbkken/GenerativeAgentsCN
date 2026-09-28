"""Read-only MCP v2 teaching server. Default transport is local stdio."""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from mcp.server import MCPServer

from common import DATA
from validate_schedule import get_room_availability as lookup_room
from validate_schedule import validate_schedule as check_schedule

mcp = MCPServer("青禾开放日教学工具")


@mcp.tool()
def get_room_availability(room_id: str, date: str) -> dict[str, Any]:
    """只读查询虚构开放日房间；room_id=reading/craft/discussion，date=2026-10-17。"""
    return lookup_room(room_id, date)


@mcp.tool()
def validate_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    """只读检验完整排期，返回valid/errors；绝不创建真实预约或写入文件。"""
    return check_schedule(schedule)


@mcp.resource("open-day://rules")
def rules() -> str:
    """提供开放日规则；客户端是否展示资源取决于其支持。"""
    return (DATA / "rules.md").read_text(encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    if args.transport == "stdio":
        mcp.run()
    else:
        # Local learning endpoint; this is not a public authenticated deployment.
        mcp.run(transport="streamable-http", host="127.0.0.1", port=args.port)
