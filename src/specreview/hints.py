import re
from dataclasses import dataclass

from .models import Patch

_STEP = re.compile(r"//\s*Step\s+(\d+(?:\.\d+)*)\b")
_NOT = re.compile(r"//\s*Not in spec\b", re.I)
_URL = re.compile(r"https?://[^\s)>\"']+#[\w.:%/-]+")


@dataclass(frozen=True)
class Hint:
    file: str
    line: int
    step: str | None
    not_in_spec: bool


def _added(patch: Patch):
    for f in patch.files:
        for h in f.hunks:
            for ln in h.lines:
                if ln.kind == "add":
                    yield f.path, ln.new, ln.text


def extract_hints(patch: Patch) -> list[Hint]:
    out = []
    for path, n, text in _added(patch):
        step = _STEP.search(text)
        not_in = bool(_NOT.search(text))
        if step or not_in:
            out.append(Hint(path, n, step.group(1) if step else None, not_in))
    return out


def spec_urls(patch: Patch) -> list[str]:
    texts = [patch.description] + [t for _, _, t in _added(patch)]
    seen: dict[str, None] = {}
    for t in texts:
        for m in _URL.finditer(t):
            seen.setdefault(m.group(0).rstrip(".,;:"), None)
    return list(seen)
