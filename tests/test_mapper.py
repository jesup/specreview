from specreview.hints import Hint
from specreview.mapper import ClaudeMapper, build_prompt, render_patch, render_steps


def test_render_patch_numbers_lines(patch):
    text = render_patch(patch)
    assert "FILE a/b.cpp" in text
    assert "    2 + new1" in text
    assert "    3 + new2" in text
    assert "    2(old) - old" in text
    assert "    1   keep" in text


def test_render_steps_indents_by_depth(doc):
    text = render_steps([doc])
    assert "[S#alg:1] Let p be a new promise" in text
    assert "  [S#alg:2.1] Gather stats" in text
    assert "\n[S#alg:2] Run in parallel" in text


def test_prompt_contains_hints_and_instructions(patch, doc):
    prompt = build_prompt(patch, [doc], [Hint("a/b.cpp", 2, "2.2.1", False)])
    assert "a/b.cpp:2 claims Step 2.2.1" in prompt
    assert "Not in spec" in prompt
    assert "desc" in prompt


def test_map_fills_patch_id_and_parses(patch, doc):
    captured = {}

    def fake(prompt, schema, **kw):
        captured.update(prompt=prompt, schema=schema, kw=kw)
        return {
            "links": [{
                "step_id": "S#alg:1", "kind": "implements", "confidence": 0.9,
                "rationale": "r", "ranges": [{"file": "a/b.cpp", "start": 2, "end": 2}],
            }],
            "unmapped": [],
            "findings": [{"text": "t", "severity": "info"}],
        }

    out = ClaudeMapper(runner=fake, repo="/r", model="m").map(patch, [doc], [])
    assert out.links[0].patch_id == "p1"
    assert out.findings[0].patch_id == "p1"
    assert captured["kw"] == {"cwd": "/r", "model": "m"}
    assert "links" in captured["schema"]["properties"]
