"""Replay reduces committed object facts, including the window baseline."""
import json
import subprocess
from pathlib import Path
from generative_agents.ga_replay.reader import ReplayReader


def test_replay_window_reduces_committed_object_state_events():
    reader = ReplayReader.__new__(ReplayReader)
    facts = [("signal-1", {"state": "RED"}), ("signal-1", {"state": "GREEN"}), ("gate-1", {"open": True})]
    reader.iter_steps = lambda **kwargs: iter([
        {"domain_events": [{"event_type": "GAME_OBJECT_STATE_CHANGED", "payload": {
            "structured_payload": {"object_key": key, "after": state}}}]} for key, state in facts
    ])
    assert reader.state_at(7)["object_states"] == {"signal-1": {"state": "GREEN"}, "gate-1": {"open": True}}


def test_replay_player_applies_window_baseline_and_current_step_event():
    root = Path(__file__).resolve().parents[2]
    player = root / 'src' / 'generative_agents' / 'adapters' / 'web' / 'static' / 'replay/replay-player.js'
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
