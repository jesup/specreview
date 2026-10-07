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
    assert a2.model_copy(update={"cached": False}) == a1
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


def kw_for(tmp_path, doc, mapper, discover):
    return dict(mapper=mapper, cache=Cache(tmp_path), fetch=lambda r: doc, discover=discover)


def test_hit_is_marked_cached_and_fresh_has_timestamp(tmp_path, patch, doc):
    kw = kw_for(tmp_path, doc, FakeMapper(good_output()), lambda p: ["S#alg"])
    fresh = analyze(patch, [], **kw)
    hit = analyze(patch, [], **kw)
    assert fresh.cached is False and fresh.created_at
    assert hit.cached is True and hit.created_at == fresh.created_at


def test_discovery_is_cached_and_force_rediscovers(tmp_path, patch, doc):
    found = []

    def discover(p):
        found.append(1)
        return ["S#alg"]

    kw = kw_for(tmp_path, doc, FakeMapper(good_output()), discover)
    analyze(patch, [], **kw)
    analyze(patch, [], **kw)
    assert len(found) == 1
    analyze(patch, [], force=True, **kw)
    assert len(found) == 2


def test_discovery_cache_follows_source_hash(tmp_path, patch, doc):
    found = []
    kw = kw_for(tmp_path, doc, FakeMapper(good_output()), lambda p: found.append(1) or ["S#alg"])
    analyze(patch, [], **kw)
    changed = patch.model_copy(deep=True)
    changed.files[0].hunks[0].lines[2].text = "different"
    analyze(changed, [], **kw)
    assert len(found) == 2


def test_empty_discovery_is_not_cached(tmp_path, patch, doc):
    results = iter([[], ["S#alg"]])
    kw = kw_for(tmp_path, doc, FakeMapper(good_output()), lambda p: next(results))
    with pytest.raises(NoSpecs):
        analyze(patch, [], **kw)
    assert analyze(patch, [], **kw).docs[0].ref == "S#alg"


def test_source_or_spec_change_invalidates_mapping(tmp_path, patch, doc):
    m = FakeMapper(good_output())
    kw = kw_for(tmp_path, doc, m, lambda p: ["S#alg"])
    analyze(patch, [], **kw)
    changed = patch.model_copy(deep=True)
    changed.files[0].hunks[0].lines[2].text = "different"
    analyze(changed, [], **kw)
    assert m.calls == 2
    newer = doc.model_copy(update={"sha": "sha2"})
    analyze(patch, [], **{**kw, "fetch": lambda r: newer})
    assert m.calls == 3
    analyze(patch, [], **kw)
    assert m.calls == 3


def test_null_cache_never_hits(patch, doc):
    from specreview.store import NullCache

    m = FakeMapper(good_output())
    kw = dict(mapper=m, cache=NullCache(), fetch=lambda r: doc, discover=lambda p: ["S#alg"])
    analyze(patch, [], **kw)
    analyze(patch, [], **kw)
    assert m.calls == 2
