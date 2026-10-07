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
