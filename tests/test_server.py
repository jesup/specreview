from fastapi.testclient import TestClient

from specreview.server import ReviewState, create_app
from specreview.store import Overrides


def make(tmp_path, patch, analysis, rerun=None):
    state = ReviewState(patch, analysis, Overrides(tmp_path, patch), rerun or (lambda refs: analysis))
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
