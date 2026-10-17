"""Model waits must stop on cancellation and share one logical-call budget."""
from __future__ import annotations

import json
import ssl
import shutil
import threading
import time
from uuid import uuid4
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import certifi

from generative_agents.ga_runtime.engine.context import RunControl
from generative_agents.ga_runtime.models.gateway import LLMModel
from generative_agents.ga_runtime.lifecycle.control import FileRunControl
from generative_agents.ga_runtime.models.trace import ModelTraceEventType, ModelTraceStatus


class _Model(LLMModel):
    def setup(self, config):
        return None


@pytest.fixture
def delayed_provider():
    state = {"calls": 0, "delay": 0.8, "entered": threading.Event(),
             "disconnected": threading.Event(), "wait_for_disconnect": False, "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["calls"] += 1
            state["entered"].set()
            if state["wait_for_disconnect"]:
                self.connection.settimeout(2)
                remaining = self.connection.recv(1)
                state["remaining"] = repr(remaining)
                if remaining == b"":
                    state["disconnected"].set()
                return
            time.sleep(state["delay"])
            data = json.dumps({"choices": [{"message": {"content": "done"}}]}).encode()
            try:
                self.send_response(state["status"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    worker.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}/v1"
    yield state
    server.shutdown()
    server.server_close()
    worker.join(timeout=1)


def _model(provider, control, **config):
    return _Model({"model": "test", "base_url": provider["url"],
                   "timeout_seconds": 2, "retry_attempts": 3,
                   "retry_backoff_seconds": 0, **config}, control=control)


def test_cancelled_run_never_starts_another_http_request(delayed_provider):
    control = RunControl()
    control.request_cancel()
    model = _model(delayed_provider, control)
    with pytest.raises(Exception, match="(?i)cancel"):
        model.chat_completion([{"role": "user", "content": "test"}])
    assert delayed_provider["calls"] == 0


@pytest.mark.parametrize("file_control", [False, True])
def test_cancel_interrupts_inflight_http_and_stops_retry(delayed_provider, tmp_path, file_control):
    delayed_provider["wait_for_disconnect"] = True
    control = FileRunControl(tmp_path) if file_control else RunControl()
    writer = FileRunControl(tmp_path) if file_control else control
    model = _model(delayed_provider, control)

    def cancel_when_sent():
        assert delayed_provider["entered"].wait(2)
        writer.request_cancel()

    canceller = threading.Thread(target=cancel_when_sent, daemon=True)
    canceller.start()
    started = time.monotonic()
    with pytest.raises(Exception, match="(?i)cancel"):
        model.chat_completion([{"role": "user", "content": "test"}])
    assert time.monotonic() - started < 0.6
    assert delayed_provider["calls"] == 1
    assert delayed_provider["disconnected"].wait(0.5), delayed_provider.get("remaining", "recv still waiting")
    canceller.join(timeout=1)


def test_timeout_is_one_budget_not_multiplied_by_retries(delayed_provider):
    model = _model(delayed_provider, RunControl(), timeout_seconds=0.12)
    started = time.monotonic()
    with pytest.raises(Exception, match="(?i)timed? ?out|timeout|budget"):
        model.chat_completion([{"role": "user", "content": "test"}])
    assert delayed_provider["calls"] == 1
    assert time.monotonic() - started < 0.32


def test_slow_success_inside_budget_is_not_cut_short(delayed_provider):
    delayed_provider["delay"] = 0.15
    model = _model(delayed_provider, RunControl(), timeout_seconds=1)
    assert model.chat_completion([{"role": "user", "content": "test"}])["content"] == "done"
    assert delayed_provider["calls"] == 1


@pytest.mark.parametrize("status,expected_calls", [(401, 1), (429, 3), (503, 3)])
def test_only_transient_http_errors_are_retried(delayed_provider, status, expected_calls):
    delayed_provider.update(status=status, delay=0)
    model = _model(delayed_provider, RunControl())
    with pytest.raises(Exception):
        model.chat_completion([{"role": "user", "content": "test"}])
    assert delayed_provider["calls"] == expected_calls


def test_cancellation_is_aborted_in_trace_even_when_failsafe_exists(delayed_provider):
    class Recorder:
        run_id = uuid4()
        attempt_id = uuid4()
        events = []

        def append(self, event):
            self.events.append(event)

    control = RunControl()
    control.request_cancel()
    model = _model(delayed_provider, control)
    recorder = Recorder()
    model._recorder = recorder
    with pytest.raises(Exception, match="(?i)cancel"):
        model.completion("test", failsafe="must not be used")
    assert [event.event_type for event in recorder.events] == [ModelTraceEventType.LOGICAL_END]
    assert recorder.events[0].status == ModelTraceStatus.ABORTED
    assert recorder.events[0].payload["response"] is None
    assert delayed_provider["calls"] == 0


@pytest.mark.parametrize("ca_variable", ["SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"])
def test_proxy_bypass_keeps_environment_ca_configuration(delayed_provider, monkeypatch, tmp_path, ca_variable):
    import generative_agents.ga_runtime.models.transport as transport

    delayed_provider["delay"] = 0
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        monkeypatch.delenv(name, raising=False)
    ca_file = tmp_path / "custom-model-trust.pem"
    shutil.copyfile(certifi.where(), ca_file)
    expected = str(tmp_path if ca_variable == "SSL_CERT_DIR" else ca_file)
    monkeypatch.setenv(ca_variable, expected)
    monkeypatch.setattr(transport, "proxy_bypass", lambda _host: True)
    original_context = ssl.create_default_context
    loaded_ca_files = []

    def context(*args, **kwargs):
        loaded_ca_files.extend([kwargs.get("cafile"), kwargs.get("capath")])
        return original_context(*args, **kwargs)

    monkeypatch.setattr(ssl, "create_default_context", context)
    model = _model(delayed_provider, RunControl())
    assert model.chat_completion([{"role": "user", "content": "test"}])["content"] == "done"
    assert expected in loaded_ca_files
