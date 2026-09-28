import gzip
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from generative_agents.ga_protocol.facts.commits import read_committed_frame, validate_committed_frames
from generative_agents.ga_protocol.packages.io import PackageError
from generative_agents.ga_runtime.engine.context import RunPaths
from generative_agents.ga_runtime.storage.frames import FrameStore
from generative_agents.ga_runtime.engine.results import StepResultBuilder


def test_frame_corruption_is_detected_without_projection(tmp_path):
    run_id = uuid4()
    paths = RunPaths.under(tmp_path, run_id)
    result = StepResultBuilder(run_id=run_id, attempt_id=uuid4(), step_no=1,
                               virtual_time=datetime(2026, 9, 22, tzinfo=timezone.utc)).freeze()
    stored = FrameStore(paths).write(result)
    assert not (paths.root / "projection.json").exists()
    assert read_committed_frame(paths.root, str(run_id), 1) == result.to_dict()
    document = json.loads(gzip.decompress(stored.path.read_bytes()))
    document["result"]["virtual_time"] = "2026-09-23T00:00:00+00:00"
    stored.path.write_bytes(gzip.compress(json.dumps(document).encode(), mtime=0))
    with pytest.raises(PackageError, match="hash mismatch"):
        validate_committed_frames(paths.root, str(run_id), 1)


def test_committed_frame_requires_durable_record_and_correct_identity(tmp_path):
    run_id = uuid4()
    paths = RunPaths.under(tmp_path, run_id)
    result = StepResultBuilder(run_id=run_id, attempt_id=uuid4(), step_no=1,
                               virtual_time=datetime(2026, 9, 22, tzinfo=timezone.utc)).freeze()
    FrameStore(paths).write(result)
    with pytest.raises(PackageError, match="identity mismatch"):
        read_committed_frame(paths.root, str(uuid4()), 1)
    (paths.root / "commits/step-000001.json").unlink()
    with pytest.raises((PackageError, FileNotFoundError)):
        read_committed_frame(paths.root, str(run_id), 1)
