import pytest

from specreview import cli
from specreview.cli import parse_args


def test_parse_args():
    a = parse_args(["D123", "--spec", "A#b", "--spec", "C#d", "--repo", "/r", "--port", "9"])
    assert a.source == "D123"
    assert a.spec == ["A#b", "C#d"]
    assert a.repo == "/r" and a.port == 9
    assert a.refresh is False and a.no_open is False


def test_defaults():
    a = parse_args(["jj:@-"])
    assert a.spec == [] and a.port == 8765 and a.model is None


def test_main_wiring(monkeypatch, patch, analysis):
    calls = []

    def fake_analyze(p, refs, **kw):
        calls.append((p, list(refs), kw["force"]))
        return analysis

    served = {}
    monkeypatch.setattr(cli.ingest, "load", lambda source, repo: patch)
    monkeypatch.setattr(cli, "analyze", fake_analyze)
    monkeypatch.setattr(cli, "ClaudeMapper", lambda **kw: object())
    monkeypatch.setattr(cli, "create_app", lambda state: served.setdefault("state", state))
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: None)

    assert cli.main(["D1", "--spec", "A#b", "--no-open", "--refresh"]) == 0
    assert calls == [(patch, ["A#b"], True)]
    served["state"].rerun(["C#d"])
    assert calls[-1] == (patch, ["C#d"], True)


def test_manual_flags():
    assert parse_args(["x"]).manual is False
    assert parse_args(["x", "--manual"]).manual is True
    assert parse_args(["x", "--no-ai"]).manual is True


def test_main_wiring_manual(monkeypatch, patch, analysis):
    seen = {}

    def fake_analyze(p, refs, **kw):
        seen.update(kw, refs=list(refs))
        return analysis

    def boom(**kw):
        raise AssertionError("Claude must not be used in manual mode")

    served = {}
    monkeypatch.setattr(cli.ingest, "load", lambda source, repo: patch)
    monkeypatch.setattr(cli, "analyze", fake_analyze)
    monkeypatch.setattr(cli, "ClaudeMapper", boom)
    monkeypatch.setattr(cli, "discover_refs", boom)
    monkeypatch.setattr(cli, "create_app", lambda state: served.setdefault("state", state))
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: None)

    assert cli.main(["D1", "--manual", "--no-open", "--model", "opus"]) == 0
    assert isinstance(seen["mapper"], cli.AnnotationMapper)
    assert isinstance(seen["cache"], cli.NullCache)
    assert seen["discover"] is cli.annotation_refs
    assert seen["model"] is None
    assert seen["refs"] == []
    assert "<spec>" in seen["no_specs_message"]
    served["state"].rerun(["C#d"])
    assert seen["refs"] == ["C#d"] and seen["force"] is True


def test_source_or_example_required(capsys):
    with pytest.raises(SystemExit):
        parse_args([])
    assert "--example" in capsys.readouterr().err


def test_example_flag_implies_manual():
    a = parse_args(["--example"])
    assert a.manual is True
    assert a.source == str(cli.EXAMPLE_PATCH)
    assert cli.EXAMPLE_PATCH.is_file()


def test_main_example(monkeypatch, patch, analysis):
    seen = {}

    def fake_load(source, repo):
        seen["source"] = source
        return patch

    def fake_analyze(p, refs, **kw):
        seen["mapper"] = kw["mapper"]
        return analysis

    def boom(**kw):
        raise AssertionError("Claude must not be used for the example")

    monkeypatch.setattr(cli.ingest, "load", fake_load)
    monkeypatch.setattr(cli, "analyze", fake_analyze)
    monkeypatch.setattr(cli, "ClaudeMapper", boom)
    monkeypatch.setattr(cli, "create_app", lambda state: state)
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: None)

    assert cli.main(["--example", "--no-open"]) == 0
    assert seen["source"] == str(cli.EXAMPLE_PATCH)
    assert isinstance(seen["mapper"], cli.AnnotationMapper)


def test_main_single_spec_is_default_url(monkeypatch, patch, analysis):
    seen = {}
    monkeypatch.setattr(cli.ingest, "load", lambda source, repo: patch)
    monkeypatch.setattr(cli, "analyze", lambda p, refs, **kw: seen.update(m=kw["mapper"]) or analysis)
    monkeypatch.setattr(cli, "create_app", lambda state: state)
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: None)

    assert cli.main(["x", "--manual", "--spec", "A#b", "--no-open"]) == 0
    assert seen["m"].default_url == "A#b"
    assert cli.main(["x", "--manual", "--spec", "A#b", "--spec", "C#d", "--no-open"]) == 0
    assert seen["m"].default_url is None


def test_example_file_flag_implies_manual():
    a = parse_args(["--example-file"])
    assert a.manual is True
    assert a.source == str(cli.EXAMPLE_FILE)
    assert cli.EXAMPLE_FILE.is_file()
    assert parse_args(["--example"]).source == str(cli.EXAMPLE_PATCH)


@pytest.fixture
def real_cache(monkeypatch, tmp_path):
    real = cli.Cache
    monkeypatch.setattr(cli, "Cache", lambda: real(tmp_path))


def test_wiring_manual_disables_ai(monkeypatch, patch, analysis):
    served = {}
    monkeypatch.setattr(cli.ingest, "load", lambda source, repo: patch)
    monkeypatch.setattr(cli, "analyze", lambda p, refs, **kw: analysis)
    monkeypatch.setattr(cli, "create_app", lambda state: served.setdefault("state", state))
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: None)
    assert cli.main(["x", "--manual", "--no-open"]) == 0
    assert served["state"].ai_enabled is False
    assert served["state"].checker is None


def test_wiring_ai_checker(monkeypatch, tmp_path, patch, analysis, real_cache):
    from specreview.models import ComplianceResult

    served = {}
    made = {}
    runs = []

    class FakeChecker:
        def __init__(self, **kw):
            made.update(kw)

        def check(self, p, doc, step, links):
            runs.append((p, doc.ref, step.id, [l.step_id for l in links]))
            return ComplianceResult(step_id=step.id, verdict="compliant", summary="ok")

    monkeypatch.setattr(cli.ingest, "load", lambda source, repo: patch)
    monkeypatch.setattr(cli, "analyze", lambda p, refs, **kw: analysis)
    monkeypatch.setattr(cli, "ClaudeMapper", lambda **kw: object())
    monkeypatch.setattr(cli, "ComplianceChecker", FakeChecker)
    monkeypatch.setattr(cli, "create_app", lambda state: served.setdefault("state", state))
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: None)

    assert cli.main(["x", "--repo", "/r", "--model", "haiku", "--no-open"]) == 0
    state = served["state"]
    assert state.ai_enabled is True
    assert made == {"repo": "/r", "model": "haiku"}
    assert state.cached_checks() == {}

    r = state.checker("S#alg:1", False)
    assert r.verdict == "compliant"
    assert runs == [(patch, "S#alg", "S#alg:1", ["S#alg:1"])]
    state.checker("S#alg:1", False)
    assert len(runs) == 1
    state.checker("S#alg:1", True)
    assert len(runs) == 2
    assert list(state.cached_checks()) == ["S#alg:1"]


def test_checker_follows_rerun_analysis(monkeypatch, patch, analysis, doc, real_cache):
    from specreview.models import ComplianceResult

    served = {}
    seen = []
    new = analysis.model_copy(update={"links": []})

    class FakeChecker:
        def __init__(self, **kw):
            pass

        def check(self, p, d, step, links):
            seen.append(links)
            return ComplianceResult(step_id=step.id, verdict="unclear", summary="s")

    analyses = iter([analysis, new])
    monkeypatch.setattr(cli.ingest, "load", lambda source, repo: patch)
    monkeypatch.setattr(cli, "analyze", lambda p, refs, **kw: next(analyses))
    monkeypatch.setattr(cli, "ClaudeMapper", lambda **kw: object())
    monkeypatch.setattr(cli, "ComplianceChecker", FakeChecker)
    monkeypatch.setattr(cli, "create_app", lambda state: served.setdefault("state", state))
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: None)
    assert cli.main(["x", "--no-open"]) == 0
    state = served["state"]
    state.checker("S#alg:1", False)
    assert len(seen[-1]) == 1
    state.analysis = state.rerun(["S#alg"])
    state.checker("S#alg:1", True)
    assert seen[-1] == []


def test_no_cache_flag():
    assert parse_args(["x"]).no_cache is False
    assert parse_args(["x", "--no-cache"]).no_cache is True


def test_main_no_cache_uses_null_cache(monkeypatch, patch, analysis):
    seen = {}

    def fake_analyze(p, refs, **kw):
        seen.update(kw)
        return analysis

    monkeypatch.setattr(cli.ingest, "load", lambda source, repo: patch)
    monkeypatch.setattr(cli, "analyze", fake_analyze)
    monkeypatch.setattr(cli, "ClaudeMapper", lambda **kw: object())
    monkeypatch.setattr(cli, "create_app", lambda state: object())
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: None)
    assert cli.main(["D1", "--spec", "A#b", "--no-open", "--no-cache"]) == 0
    assert isinstance(seen["cache"], cli.NullCache)
    assert cli.main(["D1", "--spec", "A#b", "--no-open"]) == 0
    assert not isinstance(seen["cache"], cli.NullCache)
