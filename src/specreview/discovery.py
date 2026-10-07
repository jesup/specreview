from .claude import run_claude
from .hints import spec_urls
from .models import Patch
from .spec import spec_exists

REFS_SCHEMA = {
    "type": "object",
    "properties": {"refs": {"type": "array", "items": {"type": "string"}}},
    "required": ["refs"],
}

_PROMPT = """Identify the web specification algorithms or definitions that this patch implements.
Return refs in webspec-index form, SPEC#anchor, for example WEBTRANSPORT#dom-webtransportsendstream-getstats
or HTML#navigate. Use `webspec-index search` and `webspec-index anchors` through the shell if you
can; otherwise use your knowledge. Return only refs you believe exist; return an empty list if
the patch does not implement a specification.

Commit message:
{description}

Patch:
{patch}
"""


def _render(patch: Patch, limit: int = 30000) -> str:
    out = []
    for f in patch.files:
        out.append(f"--- {f.path}")
        for h in f.hunks:
            out.extend(l.text for l in h.lines if l.kind == "add")
    return "\n".join(out)[:limit]


def discover_refs(patch, *, exists=spec_exists, ask=run_claude, repo=None, model=None):
    found = [u for u in spec_urls(patch) if exists(u)]
    if found:
        return found
    prompt = _PROMPT.format(description=patch.description, patch=_render(patch))
    answer = ask(prompt, REFS_SCHEMA, cwd=repo, model=model)
    return [r for r in answer["refs"] if exists(r)]
