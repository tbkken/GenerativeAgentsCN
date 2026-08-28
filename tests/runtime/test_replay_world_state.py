"""Replay state reduction contracts for mutable Game Objects."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from generative_agents.services.replay import ReplayService


class _Session:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self, _statement):
        return iter(self.rows)


def test_replay_window_reduces_committed_object_state_events():
    rows = [
        SimpleNamespace(
            payload_json={
                "structured_payload": {
                    "object_key": "signal-1",
                    "after": {"state": "RED"},
                }
            }
        ),
        SimpleNamespace(
            payload_json={
                "structured_payload": {
                    "object_key": "signal-1",
                    "after": {"state": "GREEN"},
                }
            }
        ),
        SimpleNamespace(
            payload_json={
                "structured_payload": {
                    "object_key": "gate-1",
                    "after": {"open": True},
                }
            }
        ),
    ]

    state = ReplayService._world_state_before(
        _Session(rows),
        run_id="run-1",
        before_step=8,
    )

    assert state == {
        "signal-1": {"state": "GREEN"},
        "gate-1": {"open": True},
    }


def test_replay_player_applies_window_baseline_and_current_step_event():
    root = Path(__file__).resolve().parents[2]
    player = root / "generative_agents" / "web" / "static" / "replay-player.js"
    script = r"""
const { GAReplayPlayer } = require(process.argv[1]);
const instance = new GAReplayPlayer({});
const labels = [];
instance.windowSize = 100;
instance.worldStateBefore.set(101, {'signal-1': {state: 'RED'}});
instance.windows.set(101, [{
  step_no: 105,
  domain_events: [{
    event_type: 'GAME_OBJECT_STATE_CHANGED',
    payload: {structured_payload: {object_key: 'signal-1', after: {state: 'GREEN'}}},
  }],
}]);
instance.worldObjects.set('signal-1', {
  glyph: {setText(value) { labels.push(value); }},
  appearance: {emoji: 'signal'},
  initialState: {state: 'OFF'},
  state: {state: 'OFF'},
});
instance._renderWorldState(105);
process.stdout.write(JSON.stringify({state: instance.worldObjects.get('signal-1').state, labels}));
"""

    completed = subprocess.run(
        ["node", "-e", script, str(player)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["state"] == {"state": "GREEN"}
    assert result["labels"]
