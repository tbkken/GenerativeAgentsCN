"""Generator identities shared by scheduling and immutable builders."""

REPLAY_GENERATOR_VERSION = "ga-replay-v2"
REPORT_GENERATOR_VERSION = "ga-report-v1"

GENERATOR_VERSIONS = {
    "BUILD_REPLAY": REPLAY_GENERATOR_VERSION,
    "BUILD_REPORT": REPORT_GENERATOR_VERSION,
    "RESULT_BUNDLE": "ga-result-bundle-v1",
    "FILTERED_MEMORIES": "ga-memory-export-v1",
    "FILTERED_CONVERSATIONS": "ga-conversation-export-v1",
    "CHECKPOINT_BUNDLE": "ga-checkpoint-bundle-v1",
}
