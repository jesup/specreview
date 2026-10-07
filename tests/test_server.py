from fastapi.testclient import TestClient

from specreview.claude import ClaudeError
from specreview.models import ComplianceResult
from specreview.server import ReviewState, create_app
from specreview.store import Overrides


def make(tmp_path, patch, analysis, rerun=None, **kw):
    state = ReviewState(patch, analysis, Overrides(tmp_path, patch), rerun or (lambda refs: analysis), **kw)
    return TestClient(create_app(state)), state


def test_review_payload(tmp_path, patch, analysis):
    client, _ = make(tmp_path, patch, analysis)
    d = client.get("/api/review").json()
    assert d["patch"]["id"] == "p1"
    assert d["step_status"]["S#alg:1"] == "implements"
    assert d["line_status"]["a/b.cpp:n3"] == ["not-in-spec:unexplained"]
    assert d["summary"]["added_lines"] == 2
    assert len(d["link_keys"]) == len(d["analysis"]["links"])
    assert d["overrides"] == {}


def test_override_roundtrip(tmp_path, patch, analysis):
    client, _ = make(tmp_path, patch, analysis)
    key = client.get("/api/review").json()["link_keys"][0]
    assert client.put(f"/api/overrides/{key}", json={"verdict": "wrong"}).status_code == 200
    assert client.get("/api/review").json()["overrides"] == {key: "wrong"}
    client.put(f"/api/overrides/{key}", json={"verdict": None})
    assert client.get("/api/review").json()["overrides"] == {}


def test_bad_verdict_rejected(tmp_path, patch, analysis):
    client, _ = make(tmp_path, patch, analysis)
    assert client.put("/api/overrides/abc", json={"verdict": "meh"}).status_code == 422


def test_rerun_replaces_analysis(tmp_path, patch, analysis):
    seen = []
    new = analysis.model_copy(update={"warnings": ["rerun"]})

    def rerun(refs):
        seen.append(refs)
        return new

    client, _ = make(tmp_path, patch, analysis, rerun)
    d = client.post("/api/rerun", json={"specs": ["S#alg"]}).json()
    assert seen == [["S#alg"]]
    assert d["analysis"]["warnings"] == ["rerun"]


def test_serves_index(tmp_path, patch, analysis):
    client, _ = make(tmp_path, patch, analysis)
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text


def result(step_id="S#alg:1"):
    return ComplianceResult(step_id=step_id, verdict="compliant", summary="fine")


def test_payload_ai_flags(tmp_path, patch, analysis):
    client, _ = make(tmp_path, patch, analysis)
    d = client.get("/api/review").json()
    assert d["ai_enabled"] is False and d["checks"] == {}
    client, _ = make(tmp_path, patch, analysis, ai_enabled=True)
    assert client.get("/api/review").json()["ai_enabled"] is True


def test_check_manual_rejected(tmp_path, patch, analysis):
    client, _ = make(tmp_path, patch, analysis, checker=lambda s, f: result(s))
    r = client.post("/api/check", json={"step_id": "S#alg:1"})
    assert r.status_code == 409


def test_check_unknown_step(tmp_path, patch, analysis):
    client, _ = make(tmp_path, patch, analysis, checker=lambda s, f: result(s), ai_enabled=True)
    assert client.post("/api/check", json={"step_id": "S#alg:99"}).status_code == 404


def test_check_success_and_force(tmp_path, patch, analysis):
    calls = []

    def checker(step_id, force):
        calls.append((step_id, force))
        return result(step_id)

    client, _ = make(tmp_path, patch, analysis, checker=checker, ai_enabled=True)
    r = client.post("/api/check", json={"step_id": "S#alg:1"})
    assert r.status_code == 200
    assert r.json()["verdict"] == "compliant" and r.json()["step_id"] == "S#alg:1"
    client.post("/api/check", json={"step_id": "S#alg:2", "force": True})
    assert calls == [("S#alg:1", False), ("S#alg:2", True)]


def test_check_claude_error_is_502(tmp_path, patch, analysis):
    def checker(step_id, force):
        raise ClaudeError("it broke")

    client, _ = make(tmp_path, patch, analysis, checker=checker, ai_enabled=True)
    r = client.post("/api/check", json={"step_id": "S#alg:1"})
    assert r.status_code == 502 and "it broke" in r.json()["detail"]


def test_payload_checks_from_cache(tmp_path, patch, analysis):
    client, _ = make(
        tmp_path, patch, analysis, ai_enabled=True,
        cached_checks=lambda: {"S#alg:1": result()},
    )
    d = client.get("/api/review").json()
    assert d["checks"]["S#alg:1"]["summary"] == "fine"


def test_check_uses_current_analysis(tmp_path, patch, analysis):
    new = analysis.model_copy(update={"docs": []})
    client, _ = make(tmp_path, patch, analysis, rerun=lambda refs: new,
                     checker=lambda s, f: result(s), ai_enabled=True)
    client.post("/api/rerun", json={"specs": ["S#alg"]})
    assert client.post("/api/check", json={"step_id": "S#alg:1"}).status_code == 404
