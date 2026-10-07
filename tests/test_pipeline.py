import pytest

from specreview.models import Link, MapperOutput, Range
from specreview.pipeline import NoSpecs, analyze
from specreview.store import Cache


class FakeMapper:
    def __init__(self, out):
        self.out, self.calls = out, 0

    def map(self, patch, docs, hints):
        self.calls += 1
        return self.out


def good_output():
    return MapperOutput(
        links=[Link(patch_id="p1", step_id="S#alg:1", kind="implements", confidence=0.9,
                    rationale="r", ranges=[Range(file="a/b.cpp", start=2, end=2)])],
        unmapped=[], findings=[],
    )


def test_analyze_uses_cache(tmp_path, patch, doc):
    m = FakeMapper(good_output())
    cache = Cache(tmp_path)
    kw = dict(mapper=m, cache=cache, fetch=lambda r: doc, discover=lambda p: ["S#alg"])
    a1 = analyze(patch, [], **kw)
    a2 = analyze(patch, [], **kw)
    assert m.calls == 1
    assert a1 == a2
    assert a1.links[0].step_id == "S#alg:1"
    analyze(patch, [], force=True, **kw)
    assert m.calls == 2


def test_explicit_refs_skip_discovery(tmp_path, patch, doc):
    def boom(p):
        raise AssertionError("discovery must not run")

    a = analyze(patch, ["S#alg"], mapper=FakeMapper(good_output()), cache=Cache(tmp_path),
                fetch=lambda r: doc, discover=boom)
    assert a.docs[0].ref == "S#alg"


def test_no_specs(tmp_path, patch):
    with pytest.raises(NoSpecs):
        analyze(patch, [], mapper=FakeMapper(good_output()), cache=Cache(tmp_path),
                fetch=lambda r: None, discover=lambda p: [])


def test_validation_warnings_recorded(tmp_path, patch, doc):
    out = good_output()
    out.links.append(Link(step_id="S#alg:99", kind="implements", confidence=1,
                          rationale="r", ranges=[Range(file="a/b.cpp", start=2, end=2)]))
    a = analyze(patch, ["S#alg"], mapper=FakeMapper(out), cache=Cache(tmp_path),
                fetch=lambda r: doc, discover=lambda p: [])
    assert len(a.links) == 1
    assert any("S#alg:99" in w for w in a.warnings)
