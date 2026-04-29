"""Tests for scripts/probe.py CLI shim (Topic Probe Tier 1).

AC-C8-1 through AC-C8-3 per docs/specs/topic-probe-2026-04-29.md §8 Cycle 8.
"""
from __future__ import annotations

import json
import re
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _free_port() -> int:
    """Pick a free localhost port for the mock backend."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _MockProbeHandler(BaseHTTPRequestHandler):
    """Stub /api/probes endpoint that streams a deterministic SSE sequence."""

    # Silence default stderr access logs from BaseHTTPRequestHandler.
    def log_message(self, format, *args):  # noqa: A002 — stdlib signature
        return

    def do_POST(self):  # noqa: N802 — stdlib signature
        if self.path != "/api/probes":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except json.JSONDecodeError:
            payload = {}

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        topic = payload.get("topic", "unknown")
        n_prompts = payload.get("n_prompts", 1)

        # Echo topic so the test can assert it travelled through.
        self._write_sse({"event": "probe_started", "topic": topic})

        # Per-prompt completion events that match the script's parser
        # (must include both "current" and "intent_label").
        for i in range(1, n_prompts + 1):
            self._write_sse({
                "current": i,
                "intent_label": "audit",
                "overall_score": 7.25,
            })

        # Final report event.
        self._write_sse({
            "final_report": (
                f"# Probe report\n\nTopic: {topic}\nPrompts: {n_prompts}\n"
            ),
        })
        self.wfile.flush()

    def _write_sse(self, obj: dict) -> None:
        line = f"data: {json.dumps(obj)}\n\n"
        self.wfile.write(line.encode("utf-8"))
        self.wfile.flush()


@pytest.fixture
def mock_probe_post(monkeypatch):
    """Spin up a localhost stub backend and patch scripts/probe.py to point at it.

    The CLI runs in a subprocess so monkeypatch can't reach into it. We pass
    the override URL via the ``PROBE_API_BASE`` environment variable — the
    GREEN-phase script reads it (falling back to ``http://localhost:8000``).
    """
    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), _MockProbeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Tiny delay so the server is accepting before the subprocess opens a stream.
    time.sleep(0.05)

    monkeypatch.setenv("PROBE_API_BASE", f"http://127.0.0.1:{port}")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


class TestProbeCLIShim:
    def test_preset_lookup_translates_to_post_probes(
        self, monkeypatch, mock_probe_post,
    ):
        """AC-C8-1: `python scripts/probe.py <preset>` POSTs to /api/probes with topic=preset_name."""
        result = subprocess.run(
            ["python", "scripts/probe.py", "cycle-19-optimization-audit"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env={**dict(__import__("os").environ), "PROBE_API_BASE": mock_probe_post},
            timeout=30,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
        assert "topic" in result.stdout or "probe_completed" in result.stdout or "cycle-19" in result.stdout

    def test_existing_prompt_sets_dict_unchanged(self):
        """AC-C8-2: scripts/validate_taxonomy_emergence.py::PROMPT_SETS still importable + cycle-19 key present."""
        spec_path = REPO_ROOT / "scripts" / "validate_taxonomy_emergence.py"
        assert spec_path.exists()
        content = spec_path.read_text()
        assert "PROMPT_SETS" in content
        assert "cycle-19" in content

    def test_output_format_chain_runner_friendly(
        self, monkeypatch, mock_probe_post,
    ):
        """AC-C8-3: stdout has per-prompt completion lines + final markdown report."""
        result = subprocess.run(
            ["python", "scripts/probe.py", "cycle-19-optimization-audit"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env={**dict(__import__("os").environ), "PROBE_API_BASE": mock_probe_post},
            timeout=30,
        )
        completion_pat = re.compile(r"\[\d+/\d+\] \w+ overall=\d+\.\d+")
        assert completion_pat.search(result.stdout) is not None, (
            f"stdout missing chain-runner completion lines:\n{result.stdout}"
        )
