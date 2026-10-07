from specreview.discovery import discover_refs


def test_urls_that_exist_are_used(patch):
    p = patch.model_copy(update={"description": "see https://w3c.github.io/webtransport/#alg"})
    asked = []
    refs = discover_refs(
        p,
        exists=lambda r: "webtransport" in r,
        ask=lambda *a, **k: asked.append(1) or {"refs": []},
    )
    assert refs == ["https://w3c.github.io/webtransport/#alg"]
    assert asked == []


def test_falls_back_to_claude_and_filters(patch):
    refs = discover_refs(
        patch,
        exists=lambda r: r == "WEBTRANSPORT#good",
        ask=lambda prompt, schema, **k: {"refs": ["WEBTRANSPORT#good", "WEBTRANSPORT#bad"]},
    )
    assert refs == ["WEBTRANSPORT#good"]
