import json
import subprocess
from pathlib import Path

import pytest

from specreview import spec

CONTENT = (
    "\n\n**`getStats()`**\n\nWhen called, the user agent MUST run these steps:\n\n"
    "1. Let *transport* be [this](https://webidl.spec.whatwg.org/#this).`[[[Transport]]](https://x/#slot)`.\n"
    "2. Let *p* be a new promise.\n"
    "3. Run the following steps [in parallel](https://html.spec.whatwg.org/#in-parallel):\n\n"
    "    1. Let *gathered* be the [list](https://infra/#list) of stats.\n"
    "    2. [Queue a network task](https://x/#q) with *transport* to run the following steps:\n\n"
    "        1. Let *stats* be a new object.\n"
    "        2. For each member of *stats*,\n          set it to the entry.\n"
    "        3. Resolve *p* with *stats*.\n"
    "4. Return *p*."
)


def test_parse_steps_numbers_parents_text():
    steps = spec.parse_steps("W", "gs", CONTENT)
    assert [s.number for s in steps] == [
        "1", "2", "3", "3.1", "3.2", "3.2.1", "3.2.2", "3.2.3", "4",
    ]
    by = {s.number: s for s in steps}
    assert by["3.2.1"].parent == "W#gs:3.2"
    assert by["3"].parent is None
    assert by["3"].text == "Run the following steps in parallel:"
    assert by["3.2.2"].text == "For each member of stats, set it to the entry."
    assert "https" not in by["1"].text


def test_numbered_depth_and_title():
    steps = {s.number: s for s in spec.parse_steps("W", "gs", CONTENT)}
    assert [steps[n].depth for n in ["1", "3", "3.1", "3.2", "3.2.1", "4"]] == [0, 0, 1, 1, 2, 0]
    assert steps["3"].title == "Run the following steps in parallel:"
    assert all(s.target is None for s in steps.values())


def test_title_truncated_at_word_boundary():
    long = "Let x be " + "word " * 30 + "end."
    (s,) = spec.parse_steps("W", "a", f"1. {long}\n")
    assert len(s.title) <= 71
    assert s.title.endswith("\u2026")
    assert not s.title[:-1].endswith(" ")
    assert long.startswith(s.title[:-1])


def test_title_first_sentence():
    (s,) = spec.parse_steps("W", "a", "1. Do the thing. Then do another thing.\n")
    assert s.title == "Do the thing."


def ietf_steps():
    payload = json.loads(Path("tests/fixtures/ietf_wt_stop_sending.json").read_text())
    return spec.parse_steps(payload["spec"], payload["anchor"], payload["content"])


def test_prose_items_numbers_and_targets():
    steps = ietf_steps()
    assert [s.number for s in steps] == [
        "6.3-1", "6.3-3", "6.3-4.2.1", "6.3-4.4.1", "6.3-5", "6.3-6",
    ]
    assert [s.target for s in steps] == [f"section-{s.number}" for s in steps]
    assert all(s.depth == 0 and s.parent is None for s in steps)
    assert steps[0].id == "draft-ietf-webtrans-http2#name-wt_stop_sending-capsule:6.3-1"


def test_prose_titles():
    steps = ietf_steps()
    assert steps[2].title == "Stream ID:"
    assert steps[3].title == "Application Protocol Error Code:"
    assert steps[1].title == "The WT_STOP_SENDING capsule defines the following fields:"
    assert steps[0].title.startswith("An HTTP capsule [HTTP-DATAGRAM] called")
    assert all(len(s.title) <= 71 for s in steps)


def test_prose_cleaning():
    steps = ietf_steps()
    for s in steps:
        assert "\u00b6" not in s.text
        assert "](" not in s.text
        assert "\\" not in s.text
        assert "http" not in s.text
    assert "[HTTP-DATAGRAM]" in steps[0].text
    assert "WT_STOP_SENDING" in steps[0].text
    assert "Type (i) = 0x190B4D3A," in steps[1].text
    assert "Figure 4: WT_STOP_SENDING Capsule Format" in steps[1].text
    assert steps[2].text.startswith("Stream ID: A variable-length integer")


def test_prose_trailing_blocks_join_last_item():
    steps = spec.parse_steps("W", "a", "First.[\u00b6](https://x/#section-1-1)\n\nTrailing note.\n")
    assert [s.number for s in steps] == ["1-1"]
    assert steps[0].text == "First. Trailing note."


def test_prose_without_pilcrow_splits_on_blank_lines():
    steps = spec.parse_steps("W", "a", "One.\n\nTwo.\n\nThree.")
    assert [(s.number, s.target, s.text) for s in steps] == [
        ("1", None, "One."), ("2", None, "Two."), ("3", None, "Three."),
    ]


def test_escapes_unescaped():
    (s,) = spec.parse_steps("W", "a", "1. See \\[RFC\\_1\\] and a\\*b.\n")
    assert s.text == "See [RFC_1] and a*b."


def test_no_list_gives_single_step():
    steps = spec.parse_steps("W", "x", "Some prose definition.")
    assert [(s.number, s.text) for s in steps] == [("0", "Some prose definition.")]


def test_fetch_spec(monkeypatch):
    payload = json.loads(Path("tests/fixtures/getstats_spec.json").read_text())

    def fake_run(cmd, **kw):
        assert cmd[:2] == ["webspec-index", "query"]
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

    monkeypatch.setattr(spec.subprocess, "run", fake_run)
    doc = spec.fetch_spec("WEBTRANSPORT#dom-webtransportsendstream-getstats")
    assert doc.ref == "WEBTRANSPORT#dom-webtransportsendstream-getstats"
    assert doc.sha.startswith("hash:")
    assert [s.number for s in doc.steps][:3] == ["1", "2", "3"]
    assert doc.anchor == "dom-webtransportsendstream-getstats"


def test_fetch_null_content(monkeypatch):
    payload = json.loads(Path("tests/fixtures/getstats_spec.json").read_text())
    payload.update(content=None, title="[[Slot]]")
    monkeypatch.setattr(
        spec.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, json.dumps(payload), ""),
    )
    doc = spec.fetch_spec("X#y")
    assert [(s.number, s.text) for s in doc.steps] == [("0", "[[Slot]]")]


def test_fetch_error(monkeypatch):
    monkeypatch.setattr(
        spec.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "not found"),
    )
    with pytest.raises(spec.SpecError):
        spec.fetch_spec("X#y")


def test_exists(monkeypatch):
    monkeypatch.setattr(
        spec.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0 if "good" in cmd[-1] else 1, "", ""),
    )
    assert spec.spec_exists("good")
    assert not spec.spec_exists("bad")
