from specreview.coverage import line_key, line_status, step_status, summarize
from specreview.models import Link, Range, Unmapped


def test_step_status(analysis):
    st = step_status(analysis.steps(), analysis.links)
    assert st == {
        "S#alg:1": "implements",
        "S#alg:2": "unimplemented",
        "S#alg:2.1": "unimplemented",
        "S#alg:3": "unimplemented",
    }


def test_parent_without_direct_link_is_structural_when_child_linked(analysis):
    link = Link(
        step_id="S#alg:2.1", kind="partial", confidence=0.5, rationale="r",
        ranges=[Range(file="a/b.cpp", start=3, end=3)],
    )
    st = step_status(analysis.steps(), analysis.links + [link])
    assert st["S#alg:2.1"] == "partial"
    assert st["S#alg:2"] == "structural"


def test_worst_kind_wins(analysis):
    extra = Link(
        step_id="S#alg:1", kind="deviates", confidence=0.7, rationale="r",
        ranges=[Range(file="a/b.cpp", start=3, end=3)],
    )
    assert step_status(analysis.steps(), analysis.links + [extra])["S#alg:1"] == "deviates"


def test_line_status(patch, analysis):
    ls = line_status(patch, analysis.links, [])
    assert ls[line_key("a/b.cpp", "new", 2)] == ["implements"]
    assert ls[line_key("a/b.cpp", "new", 3)] == ["not-in-spec:unexplained"]
    assert line_key("a/b.cpp", "old", 2) not in ls


def test_unmapped_reason_is_not_unexplained(patch, analysis):
    um = Unmapped(
        ranges=[Range(file="a/b.cpp", start=3, end=3)], reason="plumbing", note="ipc"
    )
    ls = line_status(patch, analysis.links, [um])
    assert ls[line_key("a/b.cpp", "new", 3)] == ["not-in-spec:plumbing"]


def test_summarize(patch, analysis):
    st = step_status(analysis.steps(), analysis.links)
    ls = line_status(patch, analysis.links, [])
    s = summarize(st, ls, patch)
    assert s["steps"] == {"implements": 1, "unimplemented": 3}
    assert s["added_lines"] == 2
    assert s["unexplained_lines"] == 1
