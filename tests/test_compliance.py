import pytest

from specreview import compliance
from specreview.claude import ClaudeError
from specreview.compliance import COMPLIANCE_TOOLS, ComplianceChecker, build_prompt
from specreview.mapper import render_patch
from specreview.models import ComplianceResult


@pytest.fixture
def step(doc):
    return next(s for s in doc.steps if s.number == "2.1")


def prompt(patch, doc, step, links=(), repo=None, source=""):
    return build_prompt(patch.model_copy(update={"source": source}), doc, step, list(links), repo)


def test_prompt_core_sections(patch, doc, step, analysis):
    p = prompt(patch, doc, step, analysis.links)
    assert render_patch(patch) in p
    assert "Let p be a new promise" in p and "Return p" in p
    assert "Gather stats" in p
    assert "desc" in p
    assert COMPLIANCE_TOOLS and "do not modify" in p.lower()
    assert "ONLY" in p and "unclear" in p


def test_prompt_marks_target(patch, doc, step):
    lines = prompt(patch, doc, step).splitlines()
    marked = [l for l in lines if l.startswith(">>>")]
    assert len(marked) == 1 and "[S#alg:2.1]" in marked[0]
    assert sum("TARGET" in l for l in lines if "[S#alg:" in l) == 1


def test_prompt_mapped_ranges(patch, doc, analysis):
    step = next(s for s in doc.steps if s.number == "1")
    p = prompt(patch, doc, step, analysis.links)
    assert "a/b.cpp" in p and "2-2" in p
    assert "implements" in p and "0.9" in p and "creates the promise" in p
    assert "no code in this patch is mapped" in prompt(patch, doc, step)


def test_prompt_no_repo_uses_searchfox(patch, doc, step):
    p = prompt(patch, doc, step)
    assert "searchfox-cli --define 'Class::Method'" in p
    assert "searchfox-cli --define Class" in p
    assert "searchfox-cli -q text --path dir" in p
    assert "searchfox-cli --id Name -l 50 --cpp" in p
    assert "does not contain this patch" in p
    assert "working copy" not in p


def test_prompt_repo_guidance(patch, doc, step):
    p = prompt(patch, doc, step, repo="/the/repo")
    assert "/the/repo" in p
    assert "working copy may not contain" in p
    assert "authoritative" in p
    assert "searchfox-cli --define 'Class::Method'" not in p


def test_prompt_phabricator_stack(patch, doc, step):
    p = prompt(patch, doc, step, source="D327733")
    assert "D327733" in p
    assert "https://phabricator.services.mozilla.com" in p
    assert "/D<N>?download=true" in p
    assert "jj log" not in p


def test_prompt_jj_stack_needs_repo(patch, doc, step):
    with_repo = prompt(patch, doc, step, repo="/r", source="jj:@-")
    assert "jj:@-" in with_repo
    for cmd in ("jj log", "jj show", "jj diff", "jj file show"):
        assert cmd in with_repo
    assert "--ignore-working-copy" in with_repo
    assert "jj log" not in prompt(patch, doc, step, source="jj:@-")


def test_prompt_plain_source_has_no_stack_guidance(patch, doc, step):
    p = prompt(patch, doc, step, repo="/r", source="x.patch")
    assert "/D<N>" not in p and "jj log" not in p


def test_tools_are_read_only():
    assert COMPLIANCE_TOOLS[:3] == ["Read", "Grep", "Glob"]
    assert "Bash(searchfox-cli:*)" in COMPLIANCE_TOOLS
    assert "Bash(webspec-index:*)" in COMPLIANCE_TOOLS
    for c in ("log", "show", "diff", "file show"):
        assert f"Bash(jj {c}:*)" in COMPLIANCE_TOOLS
    assert "WebFetch(domain:phabricator.services.mozilla.com)" in COMPLIANCE_TOOLS
    assert not any(t.startswith(("Edit", "Write")) for t in COMPLIANCE_TOOLS)


RAW = {
    "step_id": "model-made-this-up",
    "verdict": "partial",
    "summary": "Only handles one case.",
    "issues": [{"text": "misses null", "severity": "warn", "file": "a/b.cpp", "line": 3}],
    "evidence": [{"file": "a/b.cpp", "line": 2, "note": "creates it"}],
    "context_used": ["local repo"],
}


def test_check_stamps_and_passes_options(patch, doc, step, analysis):
    seen = {}

    def runner(prompt, schema, **kw):
        seen.update(prompt=prompt, schema=schema, kw=kw)
        return dict(RAW)

    c = ComplianceChecker(runner=runner, repo="/r", model="haiku")
    r = c.check(patch, doc, step, analysis.links)
    assert isinstance(r, ComplianceResult)
    assert r.step_id == step.id
    assert r.verdict == "partial" and r.issues[0].line == 3
    assert r.evidence[0].note == "creates it" and r.context_used == ["local repo"]
    assert seen["kw"] == {"cwd": "/r", "model": "haiku", "tools": COMPLIANCE_TOOLS}
    assert seen["schema"] == ComplianceResult.model_json_schema()
    assert "working copy may not contain" in seen["prompt"]


def test_check_invalid_output_is_claude_error(patch, doc, step):
    c = ComplianceChecker(runner=lambda p, s, **kw: {"verdict": "nonsense"})
    with pytest.raises(ClaudeError):
        c.check(patch, doc, step, [])


def test_check_defaults(patch, doc, step):
    assert ComplianceChecker().repo is None
    assert compliance.COMPLIANCE_PROMPT_VERSION == "1"
