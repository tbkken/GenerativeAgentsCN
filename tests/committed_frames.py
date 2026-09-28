"""Construct immutable frame evidence for synthetic, non-executing fixtures."""
import gzip
import hashlib
import json

from generative_agents.ga_protocol.packages.io import atomic_write_json


def write_frame(root, result):
    step = result["step_no"]
    data = gzip.compress(json.dumps({"schema_version": 1, "result": result}, sort_keys=True,
                                    separators=(",", ":")).encode(), mtime=0)
    path = root / "frames" / f"step-{step:06d}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    atomic_write_json(root / "commits" / f"step-{step:06d}.json", {
        "schema_version": 1, **{key: result.get(key) for key in ("run_id", "attempt_id", "step_no", "virtual_time")},
        "frame": f"frames/{path.name}", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
    })
    return path
