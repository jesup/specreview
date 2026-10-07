import re

from pydantic import ValidationError

from .claude import ClaudeError, run_claude
from .mapper import render_patch
from .models import ComplianceResult, Link, Patch, SpecDoc, Step

COMPLIANCE_PROMPT_VERSION = "1"

PHAB = "https://phabricator.services.mozilla.com"

COMPLIANCE_TOOLS = [
    "Read", "Grep", "Glob",
    "Bash(searchfox-cli:*)",
    "Bash(webspec-index:*)",
    "Bash(jj log:*)", "Bash(jj show:*)", "Bash(jj diff:*)", "Bash(jj file show:*)",
    "WebFetch(domain:phabricator.services.mozilla.com)",
    "WebFetch(domain:mozphab-phabhost-cdn.devsvcprod.mozaws.net)",
]

INSTRUCTIONS = """You are checking whether the code in a patch satisfies ONE entry of a specification.
The entry is marked TARGET in the spec text below. The neighboring entries are context only:
judge ONLY the target entry.

Rules:
- Give a verdict: "compliant" (the code does what the entry requires, in all cases the entry
  covers), "partial" (only some of it, or only some cases), "non-compliant" (the code
  contradicts, reorders or weakens the entry), or "unclear".
- Use "unclear" when the context available to you cannot decide the question, and say in the
  summary what is missing. Never speculate beyond the evidence you actually saw.
- summary is a short paragraph.
- issues: concrete problems with file and line when possible. For code in the patch use the
  line numbers printed in the diff below (new-side numbers for added and context lines).
  Severity "error" only for clear violations of the entry.
- evidence: the lines (file, line, note) that support your verdict, for or against.
- context_used: short strings naming the sources you consulted beyond the patch and spec text
  (for example "local repo", "searchfox", "phabricator stack", "jj stack"). Empty if none.
- step_id in the output is the id of the target entry.
- Do not modify anything: you only have read-only tools.
"""

REPO_GUIDANCE = """Context: the local repository at {repo} is your working directory. Use Read, Grep and Glob
on it for surrounding code, callers, headers and IDL. The working copy may not contain the
patch's changes: the patch text below is authoritative for changed code; the repository is for
everything around it."""

SEARCHFOX_GUIDANCE = """Context: no local repository is available. Searchfox contains the code already merged
upstream but does not contain this patch itself: the patch text below is authoritative for
changed code. Use the searchfox-cli tool (via Bash) for surrounding code, callers, headers and IDL:
  searchfox-cli --define 'Class::Method'
  searchfox-cli --define Class
  searchfox-cli -q text --path dir
  searchfox-cli --id Name -l 50 --cpp"""

PHAB_GUIDANCE = """Stack: this patch is Phabricator revision {rev}. If what you need may live in another patch of
its stack (parents or children), you may inspect the other revisions on {phab} and fetch their
diffs; a revision's raw diff is at {phab}/D<N>?download=true (this may redirect to a CDN host,
which you may also fetch)."""

JJ_GUIDANCE = """Stack: this patch is the jj revision {rev}. You may use read-only jj commands (jj log,
jj show, jj diff, jj file show) to see its ancestors and descendants in the stack; pass
--ignore-working-copy and --no-pager after the subcommand."""


def _guidance(patch: Patch, repo: str | None) -> str:
    parts = [REPO_GUIDANCE.format(repo=repo) if repo else SEARCHFOX_GUIDANCE]
    if re.fullmatch(r"D\d+", patch.source):
        parts.append(PHAB_GUIDANCE.format(rev=patch.source, phab=PHAB))
    elif patch.source.startswith("jj:") and repo:
        parts.append(JJ_GUIDANCE.format(rev=patch.source))
    return "\n\n".join(parts)


def _render_doc(doc: SpecDoc, target: Step) -> str:
    out = [f"SPEC {doc.ref} ({doc.title}) {doc.url}"]
    for s in doc.steps:
        line = f"{'  ' * s.depth}[{s.id}] {s.text}"
        out.append(f">>> {line}   <-- TARGET" if s.id == target.id else f"    {line}")
    return "\n".join(out)


def _render_links(links: list[Link]) -> str:
    if not links:
        return "(no code in this patch is mapped to this entry)"
    out = []
    for l in links:
        spans = ", ".join(f"{r.file}:{r.side} {r.start}-{r.end}" for r in l.ranges) or "(no lines)"
        out.append(f"- {l.kind}, confidence {l.confidence}: {l.rationale}\n  ranges: {spans}")
    return "\n".join(out)


def build_prompt(patch: Patch, doc: SpecDoc, step: Step, links: list[Link], repo: str | None) -> str:
    return (
        f"{INSTRUCTIONS}\n"
        f"{_guidance(patch, repo)}\n\n"
        f"Patch source: {patch.source or '(unknown)'}\n"
        f"Commit message:\n{patch.description or '(none)'}\n\n"
        f"Spec text:\n{_render_doc(doc, step)}\n\n"
        f"Target entry [{step.id}]:\n{step.text}\n\n"
        f"Code mapped to the target entry (by the author or by an earlier analysis; may be wrong):\n"
        f"{_render_links(links)}\n\n"
        f"Diff:\n{render_patch(patch)}\n"
    )


class ComplianceChecker:
    def __init__(self, runner=run_claude, repo: str | None = None, model: str | None = None):
        self.runner = runner
        self.repo = repo
        self.model = model

    def check(self, patch: Patch, doc: SpecDoc, step: Step, links: list[Link]) -> ComplianceResult:
        raw = self.runner(
            build_prompt(patch, doc, step, links, self.repo),
            ComplianceResult.model_json_schema(),
            cwd=self.repo,
            model=self.model,
            tools=COMPLIANCE_TOOLS,
        )
        try:
            result = ComplianceResult.model_validate(raw)
        except ValidationError as e:
            raise ClaudeError(f"invalid compliance result: {e}") from e
        return result.model_copy(update={"step_id": step.id})
