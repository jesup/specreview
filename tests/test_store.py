from specreview.store import Cache, NullCache, Overrides, link_key


def test_cache_roundtrip(tmp_path, patch, doc, analysis):
    c = Cache(tmp_path)
    key = c.key(patch, [doc], "v1", None)
    assert c.get(key) is None
    c.put(key, analysis)
    assert c.get(key) == analysis


def test_cache_key_changes(tmp_path, patch, doc):
    c = Cache(tmp_path)
    base = c.key(patch, [doc], "v1", None)
    assert base != c.key(patch, [doc], "v2", None)
    assert base != c.key(patch, [doc], "v1", "opus")
    assert base != c.key(patch, [doc.model_copy(update={"sha": "other"})], "v1", None)


def test_overrides(tmp_path, patch, analysis):
    o = Overrides(tmp_path, patch)
    k = link_key(analysis.links[0])
    assert o.all() == {}
    o.set(k, "wrong")
    assert Overrides(tmp_path, patch).all() == {k: "wrong"}
    o.set(k, None)
    assert o.all() == {}


def test_null_cache_never_stores(patch, doc, analysis):
    c = NullCache()
    key = c.key(patch, [doc], "v1", None)
    c.put(key, analysis)
    assert c.get(key) is None


def _result(step_id="S#alg:1"):
    from specreview.models import ComplianceResult

    return ComplianceResult(step_id=step_id, verdict="partial", summary="s")


def test_compliance_roundtrip(tmp_path, patch, doc):
    c = Cache(tmp_path)
    key = c.compliance_key(patch, doc, "S#alg:1", None)
    assert c.get_compliance(key) is None
    c.put_compliance(key, _result())
    assert c.get_compliance(key) == _result()
    assert (tmp_path / "compliance" / f"{key}.json").is_file()


def test_compliance_key_changes(tmp_path, patch, doc):
    c = Cache(tmp_path)
    base = c.compliance_key(patch, doc, "S#alg:1", None)
    assert base == c.compliance_key(patch.model_copy(update={"id": "x", "source": "D1"}), doc, "S#alg:1", None)
    assert base != c.compliance_key(patch, doc, "S#alg:2", None)
    assert base != c.compliance_key(patch, doc, "S#alg:1", "opus")
    assert base != c.compliance_key(patch, doc.model_copy(update={"sha": "o"}), "S#alg:1", None)
    assert base != c.compliance_key(patch, doc.model_copy(update={"ref": "T#alg"}), "S#alg:1", None)
    other = patch.model_copy(update={"description": "different"})
    assert base != c.compliance_key(other, doc, "S#alg:1", None)


def test_null_cache_never_stores_compliance(patch, doc):
    c = NullCache()
    key = c.compliance_key(patch, doc, "S#alg:1", None)
    c.put_compliance(key, _result())
    assert c.get_compliance(key) is None


def test_discovery_roundtrip_and_keying(tmp_path, patch):
    c = Cache(tmp_path)
    key = c.discovery_key(patch, None)
    assert c.get_refs(key) is None
    c.put_refs(key, ["A#b", "C#d"])
    assert c.get_refs(key) == ["A#b", "C#d"]
    assert key != c.discovery_key(patch, "opus")
    changed = patch.model_copy(deep=True)
    changed.files[0].hunks[0].lines[2].text = "x"
    assert key != c.discovery_key(changed, None)


def test_null_cache_discovery(patch):
    from specreview.store import NullCache

    n = NullCache()
    n.put_refs(n.discovery_key(patch, None), ["A#b"])
    assert n.get_refs(n.discovery_key(patch, None)) is None
