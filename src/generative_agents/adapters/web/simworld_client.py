"""Host-side connection options shared by the scene and character proxies."""
import os

from fastapi import HTTPException

from generative_agents.adapters.service_config import scene_url


def simworld_connection():
    try:
        url = scene_url()
    except (ValueError, OSError) as exc:
        raise HTTPException(503, "无法读取 SimWorld 服务地址，请检查主机服务配置与 GA_SIMWORLD_URL") from exc
    token = os.environ.get("GA_SIMWORLD_TOKEN")
    return {
        "base_url": url.rstrip("/") + "/v1/",
        "headers": {"Authorization": "Bearer " + token} if token else {},
        "follow_redirects": False,
    }
