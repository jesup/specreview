import json
from pathlib import Path

from specreview import annotations
from specreview.cli import EXAMPLE_PATCH
from specreview.annotations import AnnotationMapper, annotation_refs, parse_annotations
from specreview.coverage import step_status
from specreview.diffparse import parse_diff
from specreview.models import Range, SpecDoc
from specreview.pipeline import analyze
from specreview.spec import parse_steps
from specreview.store import NullCache

URL = "https://example.test/#alg"


def make_patch(*hunks, path="a/b.cpp"):
    out = [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}"]
    start = 1
    for lines in hunks:
        old = sum(1 for ln in lines if ln[0] != "+")
        new = sum(1 for ln in lines if ln[0] != "-")
        out.append(f"@@ -{start},{old} +{start},{new} @@")
        out += lines
        start += new + 10
    return parse_diff("\n".join(out) + "\n", "p1")


def block(*body, url=URL):
    return [f"+// <spec> {url}", *body, "+// </spec>"]


def test_multi_step_block():
    p = make_patch(
        block(
            "+// Step 1: Let p be a new promise",
            "+",
            "+int a;",
            "+int b;",
            "+",
            "+// Step 2: Run in parallel",
            "+int c;",
        )
    )
    r = parse_annotations(p)
    assert r.diagnostics == []
    assert [(m.url, m.number, m.text) for m in r.marks] == [
        (URL, "1", "Let p be a new promise"),
        (URL, "2", "Run in parallel"),
    ]
    assert [m.range for m in r.marks] == [
        Range(file="a/b.cpp", start=4, end=5),
        Range(file="a/b.cpp", start=8, end=8),
    ]
    assert [m.line for m in r.marks] == [2, 7]


def test_comment_prefixes():
    p = make_patch(
        [
            "+# <spec> " + URL,
            "+# Step 1: hash style",
            "+x = 1",
            "+# </spec>",
            "+/*",
            "+ * <spec> " + URL,
            "+ * Step 2: star style",
            "+ */",
            "+int c;",
            "+// </spec>",
        ]
    )
    r = parse_annotations(p)
    assert [(m.number, m.text) for m in r.marks] == [("1", "hash style"), ("2", "star style")]
    assert r.marks[0].range.start == 3


def test_continuation_comment_lines_join_text():
    p = make_patch(
        block(
            "+// Step 1: Let p be",
            "+// a new promise.",
            "+int a;",
        )
    )
    m = parse_annotations(p).marks[0]
    assert m.text == "Let p be a new promise."
    assert m.range == Range(file="a/b.cpp", start=4, end=4)


def test_hunks_carry_spec_url_but_not_step_state():
    p = make_patch(
        [f"+// <spec> {URL}", "+// Step 1: one", "+int a;"],
        ["+int stray;", "+// Step 2: two", "+int b;", "+// </spec>"],
    )
    r = parse_annotations(p)
    assert r.diagnostics == []
    assert [(m.url, m.number) for m in r.marks] == [(URL, "1"), (URL, "2")]
    assert r.marks[0].range.end == 3
    assert r.marks[1].range == Range(file="a/b.cpp", start=16, end=16)


def test_close_without_opener_is_silent():
    r = parse_annotations(make_patch(["+int a;", "+// </spec>"]))
    assert r.diagnostics == [] and r.marks == []


def test_unclosed_block_is_silent():
    r = parse_annotations(make_patch([f"+// <spec> {URL}", "+// Step 1: one", "+int a;"]))
    assert len(r.marks) == 1 and r.marks[0].range.end == 3
    assert r.diagnostics == []


def test_step_without_any_spec_warns():
    r = parse_annotations(make_patch(["+// Step 1: one", "+int a;"]))
    assert r.marks == []
    assert [d.text for d in r.diagnostics] == ["cannot determine the spec for Step 1"]
    assert r.diagnostics[0].line == 1


def test_context_lines_are_visible_and_deleted_ignored():
    p = make_patch(
        [
            f" // <spec> {URL}",
            " // Step 1: one",
            "-old",
            "+new",
            " // </spec>",
        ]
    )
    r = parse_annotations(p)
    assert r.marks[0].range == Range(file="a/b.cpp", start=3, end=3)
    assert r.diagnostics == []


def test_step_in_later_hunk_without_opener_uses_earlier_url():
    p = make_patch(
        [f"+// <spec> {URL}", "+// Step 1: one", "+int a;"],
        [" int x;", "+// Step 2: two", "+int b;", " }"],
    )
    r = parse_annotations(p)
    assert r.diagnostics == []
    assert [(m.url, m.number) for m in r.marks] == [(URL, "1"), (URL, "2")]


def test_step_after_close_uses_last_file_url():
    p = make_patch(block("+// Step 1: one", "+int a;") + ["+// Step 2: two", "+int b;"])
    r = parse_annotations(p)
    assert r.diagnostics == []
    assert [(m.url, m.number) for m in r.marks] == [(URL, "1"), (URL, "2")]


def two_files(first, second):
    p = make_patch(first, path="a.cpp")
    p.files += make_patch(second, path="b.cpp").files
    return p


def test_step_resolves_from_single_patch_url():
    p = two_files(["+// Step 2: two", "+int b;"], block("+// Step 1: one", "+int a;"))
    r = parse_annotations(p)
    assert r.diagnostics == []
    assert sorted((m.url, m.number) for m in r.marks) == [(URL, "1"), (URL, "2")]


def test_step_resolves_from_default_url():
    r = parse_annotations(make_patch(["+// Step 1: one", "+int a;"]), default_url=URL)
    assert r.diagnostics == []
    assert [(m.url, m.number) for m in r.marks] == [(URL, "1")]


def test_default_url_beats_patch_urls():
    other = "https://e.test/#other"
    p = two_files(
        ["+// Step 2: two", "+int b;"],
        block("+// Step 1: one", "+int a;", url=other) + block("+// Step 3: x", "+int c;", url=URL),
    )
    r = parse_annotations(p, default_url=URL)
    assert {(m.number): m.url for m in r.marks}["2"] == URL


def test_step_with_ambiguous_spec_warns_and_is_skipped():
    p = two_files(
        ["+int a;", "+// Step 2: two", "+int b;"],
        block("+// Step 1: one", "+int a;", url="https://e.test/#a")
        + block("+// Step 1: one", "+int a;", url="https://e.test/#b"),
    )
    r = parse_annotations(p)
    assert [m.number for m in r.marks] == ["1", "1"]
    [d] = r.diagnostics
    assert d.text == "cannot determine the spec for Step 2"
    assert (d.file, d.line, d.severity) == ("a.cpp", 2, "warn")


def test_range_trimmed_to_added_lines():
    p = make_patch(
        [
            f"+// <spec> {URL}",
            "+// Step 1: one",
            " ctxA",
            " ",
            "+add1",
            " ctxMid",
            "+add2",
            " ctxB",
            "+// </spec>",
        ]
    )
    assert parse_annotations(p).marks[0].range == Range(file="a/b.cpp", start=5, end=7)


def test_context_only_step_has_no_range_and_no_warning():
    p = make_patch([f" // <spec> {URL}", " // Step 1: one", " int a;", " // </spec>"])
    r = parse_annotations(p)
    assert r.marks[0].range is None and not r.marks[0].no_code
    assert r.diagnostics == []


def test_no_code_only_when_terminated():
    ended = parse_annotations(make_patch(block("+// Step 1: one", "+")))
    assert ended.marks[0].no_code
    stepped = parse_annotations(
        make_patch(block("+// Step 1: one", "+", "+// Step 2: two", "+int a;"))
    )
    assert stepped.marks[0].no_code and not stepped.marks[1].no_code
    runs_off = parse_annotations(make_patch([f"+// <spec> {URL}", "+// Step 1: one", "+"]))
    assert not runs_off.marks[0].no_code


def test_annotation_refs_distinct_in_order():
    p = make_patch(
        block("+// Step 1: a", "+x;", url="https://e.test/#b"),
        block("+// Step 1: a", "+x;", url="https://e.test/#a"),
        block("+// Step 2: a", "+x;", url="https://e.test/#b"),
    )
    assert annotation_refs(p) == ["https://e.test/#b", "https://e.test/#a"]
    assert annotation_refs(make_patch(["+int a;"])) == []


def run(p, doc, default_url=None):
    calls = []

    def fetch(ref):
        calls.append(ref)
        return doc

    out = AnnotationMapper(fetch=fetch, default_url=default_url).map(p, [doc], [])
    return out, calls


def test_mapper_merges_repeated_step(doc):
    p = make_patch(
        block("+// Step 1: Let p be a new promise", "+int a;", "+int b;"),
        block("+// Step 1: Let p be a new promise", "+int c;"),
        block("+// Step 3: Return p", "+return p;"),
    )
    out, calls = run(p, doc)
    assert calls == [URL]
    assert [(l.step_id, l.kind, l.confidence, l.rationale, l.patch_id) for l in out.links] == [
        ("S#alg:1", "implements", 1.0, "author annotation (Step 1)", "p1"),
        ("S#alg:3", "implements", 1.0, "author annotation (Step 3)", "p1"),
    ]
    assert out.links[0].ranges == [
        Range(file="a/b.cpp", start=3, end=4),
        Range(file="a/b.cpp", start=18, end=18),
    ]
    assert out.unmapped == []
    assert out.findings == []


def test_mapper_unknown_step(doc):
    p = make_patch(block("+// Step 9: Something", "+int a;"))
    out, _ = run(p, doc)
    assert out.links == []
    [f] = out.findings
    assert f.severity == "warn" and "Step 9" in f.text
    assert f.range == Range(file="a/b.cpp", start=2, end=2)


def test_mapper_step_without_code(doc):
    out, _ = run(make_patch(block("+// Step 1: Let p be a new promise")), doc)
    assert out.links == []
    [f] = out.findings
    assert f.severity == "warn" and f.step_id == "S#alg:1"


def test_mapper_text_differs(doc):
    p = make_patch(block("+// Step 1: Frobnicate the widget quickly", "+int a;"))
    out, _ = run(p, doc)
    assert len(out.links) == 1
    [f] = out.findings
    assert f.severity == "info"
    assert f.text == "comment text differs from spec step 1"
    assert f.step_id == "S#alg:1"


def test_mapper_text_close_enough_and_empty(doc):
    p = make_patch(
        block(
            "+// Step 1: Let a promise be created",
            "+int a;",
            "+// Step 2:",
            "+int b;",
        )
    )
    out, _ = run(p, doc)
    assert out.findings == []
    assert len(out.links) == 2


def test_mapper_snapshot_url_info(doc):
    url = "https://foo.spec.whatwg.org/commit-snapshots/abc123/#foo"
    p = make_patch(
        block("+// Step 1: Let p be a new promise", "+int a;", url=url),
        block("+// Step 2: Run in parallel", "+int b;", url=url),
    )
    out, _ = run(p, doc)
    [f] = out.findings
    assert f.severity == "info"
    assert url in f.text and "latest indexed" in f.text


def test_mapper_reports_parse_diagnostics(doc):
    out, _ = run(make_patch(["+// Step 1: one", "+int a;"]), doc)
    [f] = out.findings
    assert f.severity == "warn" and f.range == Range(file="a/b.cpp", start=1, end=1)


def test_mapper_uses_default_url(doc):
    out, calls = run(make_patch(["+// Step 1: Let p be a new promise", "+int a;"]), doc, URL)
    assert calls == [URL] and out.findings == []
    assert [l.step_id for l in out.links] == ["S#alg:1"]


def test_mapper_context_only_step_is_silent(doc):
    p = make_patch([f" // <spec> {URL}", " // Step 1: Let p be a new promise", " int a;"])
    out, _ = run(p, doc)
    assert out.links == [] and out.findings == []


def test_end_to_end_fixture(monkeypatch):
    d = json.loads(Path("tests/fixtures/getstats_spec.json").read_text())
    sdoc = SpecDoc(
        ref=f"{d['spec']}#{d['anchor']}", sha=d["sha"], title=d["title"],
        anchor=d["anchor"], url=d["url"],
        steps=parse_steps(d["spec"], d["anchor"], d["content"]),
    )
    monkeypatch.setattr(annotations, "fetch_spec", lambda ref: sdoc)
    patch = parse_diff(EXAMPLE_PATCH.read_text(), "m1")
    a = analyze(
        patch, [], mapper=AnnotationMapper(), cache=NullCache(), fetch=lambda r: sdoc,
        discover=annotation_refs,
    )
    f = "dom/webtransport/api/WebTransportSendStream.cpp"
    by = {l.step_id.rsplit(":", 1)[1]: l for l in a.links}
    assert sorted(by) == ["1", "2", "3.1", "3.2.1", "3.2.3", "4"]
    assert a.warnings == []
    assert all(l.kind == "implements" and l.confidence == 1.0 for l in a.links)
    assert {k: [(r.start, r.end) for r in v.ranges] for k, v in by.items()} == EXPECTED
    assert all(r.file == f for v in by.values() for r in v.ranges)
    st = step_status(a.steps(), a.links)
    pre = f"{sdoc.ref}:"
    assert st[pre + "3.2.2"] == "unimplemented"
    assert st[pre + "3.2"] == "structural"
    assert st[pre + "1"] == "implements"
    assert a.warnings == []
    assert [x for x in a.findings if x.severity != "info"] == []


EXPECTED = {
    "1": [(177, 177)],
    "2": [(181, 184)],
    "3.1": [(188, 188)],
    "3.2.1": [(191, 194)],
    "3.2.3": [(221, 221), (294, 295)],
    "4": [(254, 256)],
}


def _getstats_doc():
    d = json.loads(Path("tests/fixtures/getstats_spec.json").read_text())
    return SpecDoc(
        ref=f"{d['spec']}#{d['anchor']}", sha=d["sha"], title=d["title"],
        anchor=d["anchor"], url=d["url"],
        steps=parse_steps(d["spec"], d["anchor"], d["content"]),
    )


def _whole_file_links(monkeypatch, path):
    from specreview import ingest

    sdoc = _getstats_doc()
    monkeypatch.setattr(annotations, "fetch_spec", lambda ref: sdoc)
    a = analyze(
        ingest.load(str(path)), [], mapper=AnnotationMapper(), cache=NullCache(),
        fetch=lambda r: sdoc, discover=annotation_refs,
    )
    return a, {l.step_id.rsplit(":", 1)[1]: l for l in a.links}


def test_end_to_end_whole_file(monkeypatch, tmp_path):
    src = tmp_path / "S.cpp"
    src.write_text(
        "int pre;\n"
        "// <spec> https://w3c.github.io/webtransport/#dom-webtransportsendstream-getstats\n"
        "// Step 1: Let transport be this.[[Transport]].\n"
        "int a;\n"
        "\n"
        "// </spec>\n"
        "int mid;\n"
        "// Step 2: Let p be a new promise.\n"
        "int b;\n"
        "int c;\n"
    )
    a, by = _whole_file_links(monkeypatch, src)
    assert sorted(by) == ["1", "2"]
    assert [(r.start, r.end) for r in by["1"].ranges] == [(4, 4)]
    assert [(r.start, r.end) for r in by["2"].ranges] == [(9, 10)]
    assert all(r.file == str(src) for l in a.links for r in l.ranges)
    assert a.warnings == []


def test_end_to_end_example_file(monkeypatch):
    from specreview.cli import EXAMPLE_FILE

    a, by = _whole_file_links(monkeypatch, EXAMPLE_FILE)
    assert sorted(by) == ["1", "2", "3.1", "3.2.1", "3.2.3", "4"]
    assert a.warnings == []
    assert [x for x in a.findings if x.severity != "info"] == []
    assert [(r.start, r.end) for r in by["3.2.3"].ranges] == [(41, 45)]
