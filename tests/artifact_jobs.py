"""Await the explicit background job API in integration tests."""
import time


def wait_artifact(client, run_id, response, *, expected='SUCCEEDED'):
    response.raise_for_status()
    job_id = response.json()['job_id']
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = client.get(f'/api/studio/runs/{run_id}/artifact-jobs/{job_id}')
        assert result.status_code == 200, result.text
        if result.json()['status'] not in {'QUEUED', 'RUNNING'}:
            assert result.json()['status'] == expected, result.text
            return result
        time.sleep(.01)
    raise AssertionError('Artifact job did not finish')
