from specreview.hints import Hint
from specreview.models import Finding, Link, MapperOutput, Range, Unmapped
from specreview.validate import validate


def link(step, start, end, kind="implements", conf=0.8, file="a/b.cpp", side="new"):
    return Link(
        step_id=step, kind=kind, confidence=conf, rationale="r",
        ranges=[Range(file=file, side=side, start=start, end=end)],
    )


def run(patch, doc, links=(), unmapped=(), findings=(), hints=()):
    out = MapperOutput(links=list(links), unmapped=list(unmapped), findings=list(findings))
    return validate(patch, [doc], out, list(hints))


def test_unknown_step_dropped(patch, doc):
    out, w = run(patch, doc, [link("S#alg:99", 2, 2)])
    assert out.links == []
    assert any("S#alg:99" in x for x in w)


def test_range_trimmed_to_diff_lines(patch, doc):
    out, _ = run(patch, doc, [link("S#alg:1", 0, 3)])
    r = out.links[0].ranges[0]
    assert (r.start, r.end) == (1, 3)


def test_range_entirely_outside_dropped(patch, doc):
    out, w = run(patch, doc, [link("S#alg:1", 50, 60), link("S#alg:1", 2, 2, file="nope.cpp")])
    assert out.links == []
    assert len(w) >= 2


def test_structural_may_have_no_ranges(patch, doc):
    l = Link(step_id="S#alg:2", kind="structural", confidence=0.6, rationale="r", ranges=[])
    out, _ = run(patch, doc, [l])
    assert len(out.links) == 1


def test_old_side_validated_against_old_lines(patch, doc):
    out, _ = run(patch, doc, [link("S#alg:1", 2, 2, side="old")])
    assert len(out.links) == 1
    out, _ = run(patch, doc, [link("S#alg:1", 4, 4, side="old")])
    assert out.links == []


def test_confidence_clamped(patch, doc):
    out, _ = run(patch, doc, [link("S#alg:1", 2, 2, conf=7)])
    assert out.links[0].confidence == 1.0


def test_unmapped_and_findings_cleaned(patch, doc):
    um = Unmapped(ranges=[Range(file="a/b.cpp", start=3, end=99)], reason="plumbing", note="n")
    f = Finding(text="t", severity="warn", step_id="S#alg:99")
    out, _ = run(patch, doc, unmapped=[um], findings=[f])
    assert out.unmapped[0].ranges[0].end == 4
    assert out.findings[0].step_id is None


def test_hint_step_mismatch_warns(patch, doc):
    hints = [Hint("a/b.cpp", 2, "2", False)]
    _, w = run(patch, doc, [link("S#alg:1", 2, 3)], hints=hints)
    assert any("Step 2" in x and "a/b.cpp:2" in x for x in w)


def test_hint_agreement_is_silent(patch, doc):
    hints = [Hint("a/b.cpp", 2, "1", False)]
    _, w = run(patch, doc, [link("S#alg:1", 2, 3)], hints=hints)
    assert w == []


def test_hint_nonexistent_step_warns(patch, doc):
    hints = [Hint("a/b.cpp", 2, "9.9", False)]
    _, w = run(patch, doc, hints=hints)
    assert any("does not exist" in x for x in w)
