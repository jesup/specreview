from typing import Protocol

from .claude import run_claude
from .hints import Hint
from .models import MapperOutput, Patch, SpecDoc

PROMPT_VERSION = "2"

INSTRUCTIONS = """You are reviewing a code patch against the specification steps it implements.
Produce a careful, honest mapping between spec steps and diff line ranges.

Rules:
- A step may map to several, non-contiguous ranges. A range may belong to several steps.
- Ranges use the line numbers printed in the diff below. Use side "new" for added or context
  lines (the number printed on the left of + and blank lines) and side "old" only for removed
  lines (marked "(old)"). start and end are inclusive and must exist in the diff.
- Copy step_id values exactly as written in brackets in the step list.
- kind: "implements" (the code does what the step says), "partial" (only part of the step, or
  only some cases), "structural" (the step describes concurrency/queuing/ordering that the
  code satisfies by structure rather than by particular lines; ranges may be empty),
  "deviates" (the code contradicts, reorders, or weakens the step).
- confidence is 0..1. Use below 0.5 when you are guessing. Do not inflate it.
- If a step has no implementation in this patch, emit no link for it. Do not invent links.
- Every added line that is not part of a link must be covered by an "unmapped" entry with a
  reason: "plumbing" (IPC, glue, plumbing between processes or layers), "error-path"
  (defensive or failure handling not in the spec), "test" (tests, expectations), or
  "unexplained" (you cannot tell why the code is there). Prefer "unexplained" over guessing.
- rationale and note are one short sentence.
- findings: concrete review notes: unimplemented steps that this patch should cover,
  deviations, ordering differences, stale or wrong step numbers in comments, behavior the spec
  does not allow. Severity "error" only for clear spec violations.
- The author's comments of the form "Step N" or "Not in spec" are claims, not facts. Verify
  them against the spec text; report mismatches as findings.
- You may use Read, Grep and Glob on the repository to understand context (callers, IDL,
  headers) when the diff alone is ambiguous. Do not modify anything.
"""


class Mapper(Protocol):
    def map(self, patch: Patch, docs: list[SpecDoc], hints: list[Hint]) -> MapperOutput: ...


def render_patch(patch: Patch) -> str:
    out = []
    for f in patch.files:
        out.append(f"FILE {f.path}")
        for h in f.hunks:
            out.append(h.header)
            for ln in h.lines:
                if ln.kind == "add":
                    out.append(f"{ln.new:>5} + {ln.text}")
                elif ln.kind == "del":
                    out.append(f"{ln.old:>5}(old) - {ln.text}")
                else:
                    out.append(f"{ln.new:>5}   {ln.text}")
    return "\n".join(out)


def render_steps(docs: list[SpecDoc]) -> str:
    out = []
    for d in docs:
        out.append(f"SPEC {d.ref} ({d.title}) {d.url}")
        for s in d.steps:
            out.append(f"{'  ' * s.depth}[{s.id}] {s.text}")
    return "\n".join(out)


def _render_hints(hints: list[Hint]) -> str:
    if not hints:
        return "(none)"
    lines = []
    for h in hints:
        what = f"claims Step {h.step}" if h.step else "says Not in spec"
        lines.append(f"- {h.file}:{h.line} {what}")
    return "\n".join(lines)


def build_prompt(patch: Patch, docs: list[SpecDoc], hints: list[Hint]) -> str:
    return (
        f"{INSTRUCTIONS}\n"
        f"Commit message:\n{patch.description or '(none)'}\n\n"
        f"Spec steps:\n{render_steps(docs)}\n\n"
        f"Author comment claims to verify:\n{_render_hints(hints)}\n\n"
        f"Diff:\n{render_patch(patch)}\n"
    )


class ClaudeMapper:
    def __init__(self, runner=run_claude, repo: str | None = None, model: str | None = None):
        self.runner = runner
        self.repo = repo
        self.model = model

    def map(self, patch: Patch, docs: list[SpecDoc], hints: list[Hint]) -> MapperOutput:
        raw = self.runner(
            build_prompt(patch, docs, hints),
            MapperOutput.model_json_schema(),
            cwd=self.repo,
            model=self.model,
        )
        out = MapperOutput.model_validate(raw)
        stamp = {"patch_id": patch.id}
        return MapperOutput(
            links=[x.model_copy(update=stamp) for x in out.links],
            unmapped=[x.model_copy(update=stamp) for x in out.unmapped],
            findings=[x.model_copy(update=stamp) for x in out.findings],
        )
