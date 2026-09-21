"""The wall-clock deadline that bounds a stalled model request.

Report: `enhancements/pha-request-stall-timeout-bug-report.md`. `timeout_s` is
httpx's PER-OPERATION timeout, and a gateway that trickles keep-alive bytes
resets the read timeout on every byte — so a response that never completes used
to sit indefinitely (measured stalls of ~6 h 30 and ~4 h 17). These tests run a
real socket server and assert pha's own `deadline_s` ends the attempt.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from personal_historical_archive.model_client import ModelClient, ModelError, ModelStall


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)
        handler = self.server.behaviour  # type: ignore[attr-defined]
        try:
            handler(self)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client abandoned the attempt; the close is expected

    def log_message(self, *args) -> None:  # keep pytest output clean
        pass


class _Server:
    """A one-behaviour HTTP server on a random local port."""

    def __init__(self, behaviour):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.behaviour = behaviour
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _blackhole(_h) -> None:
    """Accept the request and answer nothing, forever."""
    time.sleep(10)


def _trickle(h) -> None:
    """Keep the connection warm with bytes that never form a complete body."""
    h.send_response(200)
    h.send_header("Content-Type", "application/json")
    h.end_headers()
    for _ in range(400):
        try:
            h.wfile.write(b" ")
            h.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        time.sleep(0.02)


def _slow_but_honest(delay: float):
    def _h(h) -> None:
        time.sleep(delay)
        body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
        h.send_response(200)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)
        h.wfile.flush()
    return _h


def _good_then(h, state: dict) -> None:
    """First request trickles (stalls), later ones answer correctly."""
    state["calls"] += 1
    if state["calls"] == 1:
        _trickle(h)
        return
    body = json.dumps({"choices": [{"message": {"content": "second try"}}]}).encode()
    h.send_response(200)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)
    h.wfile.flush()


def test_keepalive_trickle_is_bounded_by_deadline():
    """THE case that defeats `timeout_s`: bytes arrive regularly (resetting the
    read timeout) but the response never completes — only pha's deadline ends
    it, and it is classified as a STALL (the provider, not the page)."""
    server = _Server(_trickle)
    try:
        client = ModelClient(server.url, timeout_s=30, retries=0, deadline_s=0.5)
        started = time.monotonic()
        with pytest.raises(ModelStall) as exc:
            client.chat_text("m", "prompt")
        elapsed = time.monotonic() - started
        assert "wall-clock deadline" in str(exc.value)
        assert elapsed < 3, f"the trickle outlived the deadline (took {elapsed:.1f}s)"
        client.close()
    finally:
        server.close()


def test_silent_connection_stays_bounded_by_the_read_timeout():
    """A blackhole (no byte at all) is the case `timeout_s` already bounds: it
    remains a normal error, not a stall."""
    server = _Server(_blackhole)
    try:
        client = ModelClient(server.url, timeout_s=0.5, retries=0, deadline_s=30)
        started = time.monotonic()
        with pytest.raises(ModelError) as exc:
            client.chat_text("m", "prompt")
        assert not isinstance(exc.value, ModelStall)  # the read timeout fired first
        assert time.monotonic() - started < 3
        client.close()
    finally:
        server.close()


def test_a_deadline_below_the_read_timeout_bounds_the_silence_too():
    """Whichever is smaller caps the request; a deadline smaller than the read
    timeout must not let a silent connection outlast it."""
    server = _Server(_blackhole)
    try:
        client = ModelClient(server.url, timeout_s=30, retries=0, deadline_s=0.5)
        started = time.monotonic()
        with pytest.raises(ModelError):
            client.chat_text("m", "prompt")
        assert time.monotonic() - started < 3
        client.close()
    finally:
        server.close()


def test_slow_but_honest_response_inside_the_deadline_succeeds():
    """A genuinely slow page that finishes inside the deadline still succeeds —
    the deadline must not break legitimate long generations."""
    server = _Server(_slow_but_honest(0.3))
    try:
        client = ModelClient(server.url, timeout_s=30, retries=0, deadline_s=5)
        assert client.chat_text("m", "prompt") == "ok"
        assert client.last_elapsed_s >= 0.25
        client.close()
    finally:
        server.close()


def test_retry_after_a_stalled_attempt_still_runs():
    """A stalled attempt is retryable: the next attempt can succeed, and the
    whole call stays inside `(retries + 1) x deadline` (+ backoff)."""
    state = {"calls": 0}
    server = _Server(lambda h: _good_then(h, state))
    try:
        client = ModelClient(server.url, timeout_s=30, retries=1, deadline_s=0.4)
        started = time.monotonic()
        assert client.chat_text("m", "prompt") == "second try"
        elapsed = time.monotonic() - started
        assert state["calls"] == 2, "the second attempt must have run"
        # 1 deadline-bounded stall + the 2s backoff + a quick success
        assert elapsed < (1 + 1) * 0.4 + 2 + 3
        client.close()
    finally:
        server.close()


def test_deadline_zero_disables_the_ceiling():
    """`deadline_s: 0` keeps the old per-operation behaviour (opt-out)."""
    server = _Server(_blackhole)
    try:
        client = ModelClient(server.url, timeout_s=0.5, retries=0, deadline_s=0)
        started = time.monotonic()
        with pytest.raises(ModelError) as exc:
            client.chat_text("m", "prompt")
        assert not isinstance(exc.value, ModelStall)
        assert time.monotonic() - started < 3  # the read timeout did the bounding
        client.close()
    finally:
        server.close()


def test_no_worker_thread_is_left_behind():
    """The deadline is enforced by streaming in the CALLER's thread, so an
    abandoned attempt cannot leak a worker (the endless-trickle case)."""
    server = _Server(_trickle)
    try:
        client = ModelClient(server.url, timeout_s=30, retries=0, deadline_s=0.4)
        with pytest.raises(ModelStall):
            client.chat_text("m", "prompt")
        assert not [t for t in threading.enumerate() if t.name == "pha-http"]
        client.close()
    finally:
        server.close()


def test_default_deadline_is_derived_from_timeout():
    """Without an explicit deadline_s the ceiling is derived, so the bug is
    fixed for every existing model stage with no config change."""
    c = ModelClient("http://example/v1", timeout_s=900)
    assert c.deadline_s == 1800
    c.close()
    c = ModelClient("http://example/v1", timeout_s=10)  # tiny -> floor applies
    assert c.deadline_s == 60
    c.close()
