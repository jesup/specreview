import json
import subprocess

import pytest

from specreview import claude


def _proc(stdout, rc=0, stderr=""):
    return subprocess.CompletedProcess([], rc, stdout, stderr)


def test_run_claude_returns_structured_output(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return _proc(json.dumps({"is_error": False, "structured_output": {"x": 1}}))

    monkeypatch.setattr(claude.subprocess, "run", fake_run)
    out = claude.run_claude("hello", {"type": "object"}, cwd="/r", model="sonnet")
    assert out == {"x": 1}
    assert seen["kw"]["input"] == "hello"
    assert seen["kw"]["cwd"] == "/r"
    assert "--json-schema" in seen["cmd"] and "--model" in seen["cmd"]


def test_run_claude_errors(monkeypatch):
    monkeypatch.setattr(claude.subprocess, "run", lambda c, **k: _proc("", 1, "boom"))
    with pytest.raises(claude.ClaudeError, match="boom"):
        claude.run_claude("p", {})
    monkeypatch.setattr(
        claude.subprocess, "run",
        lambda c, **k: _proc(json.dumps({"is_error": True, "result": "bad"})),
    )
    with pytest.raises(claude.ClaudeError):
        claude.run_claude("p", {})
