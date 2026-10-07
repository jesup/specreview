import re
from dataclasses import dataclass, field

from .hints import Hint
from .models import Finding, Link, MapperOutput, Patch, Range, SpecDoc
from .spec import fetch_spec

_PREFIX = r"^\s*(?P<p>//|#|/?\*+)\s*"
_OPEN = re.compile(_PREFIX + r"<spec>\s+(?P<url>\S+)")
_CLOSE = re.compile(_PREFIX + r"</spec>")
_STEP = re.compile(_PREFIX + r"Step\s+(?P<n>\S+?):\s*(?P<t>.*?)\s*(?:\*/)?\s*$")
_CONT = re.compile(r"^\s*(?P<p>//|#|\*+(?!/))\s*(?P<t>.*?)\s*(?:\*/)?\s*$")
_COMMENT_END = re.compile(r"^\s*\*+/\s*$")
_WORD = re.compile(r"[a-z0-9]+")
SNAPSHOT = "commit-snapshots"


@dataclass
class StepMark:
    url: str | None
    number: str
    text: str
    file: str
    line: int
    range: Range | None
    no_code: bool = False


@dataclass
class Diagnostic:
    text: str
    file: str
    line: int
    severity: str = "warn"


@dataclass
class Parsed:
    marks: list[StepMark] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)


class _File:
    def __init__(self, path: str, out: Parsed):
        self.path, self.out = path, out
        self.last_url: str | None = None


class _Hunk:
    def __init__(self, file: _File):
        self.file, self.out = file, file.out
        self.url: str | None = None
        self.step: StepMark | None = None
        self.code: list[tuple[int, str, bool]] = []
        self.cont: str | None = None

    def finish_step(self, terminated: bool = False) -> None:
        if self.step is None:
            return
        added = [i for i, (_, t, is_add) in enumerate(self.code) if is_add and t.strip()]
        if added:
            first, last = self.code[added[0]][0], self.code[added[-1]][0]
            self.step.range = Range(file=self.file.path, start=first, end=last)
        elif terminated and not any(t.strip() for _, t, _ in self.code):
            self.step.no_code = True
        self.out.marks.append(self.step)
        self.step, self.code, self.cont = None, [], None

    def feed(self, n: int, text: str, is_add: bool) -> None:
        if self.cont is not None:
            if _COMMENT_END.match(text):
                return
            m = _CONT.match(text)
            if m and m["p"] == self.cont and m["t"] and not _is_marker(text):
                self.step.text = f"{self.step.text} {m['t']}".strip()
                return
            self.cont = None
        if m := _OPEN.match(text):
            self.finish_step(terminated=True)
            self.url = self.file.last_url = m["url"]
            if m["url"] not in self.out.urls:
                self.out.urls.append(m["url"])
        elif _CLOSE.match(text):
            self.finish_step(terminated=True)
            self.url = None
        elif m := _STEP.match(text):
            self.finish_step(terminated=True)
            url = self.url or self.file.last_url
            self.step = StepMark(url, m["n"], m["t"], self.file.path, n, None)
            self.cont = m["p"]
        elif self.step is not None:
            self.code.append((n, text, is_add))


def _is_marker(text: str) -> bool:
    return bool(_OPEN.match(text) or _CLOSE.match(text) or _STEP.match(text))


def parse_annotations(patch: Patch, default_url: str | None = None) -> Parsed:
    out = Parsed()
    for f in patch.files:
        file = _File(f.path, out)
        for h in f.hunks:
            state = _Hunk(file)
            for ln in h.lines:
                if ln.kind != "del":
                    state.feed(ln.new, ln.text, ln.kind == "add")
            state.finish_step()
    fallback = default_url or (out.urls[0] if len(out.urls) == 1 else None)
    marks = []
    for m in out.marks:
        m.url = m.url or fallback
        if m.url is None:
            out.diagnostics.append(
                Diagnostic(f"cannot determine the spec for Step {m.number}", m.file, m.line)
            )
        else:
            marks.append(m)
    out.marks = marks
    return out


def annotation_refs(patch: Patch) -> list[str]:
    return parse_annotations(patch).urls


def _differs(comment: str, step_text: str) -> bool:
    words = _WORD.findall(comment.lower())
    if not words:
        return False
    known = set(_WORD.findall(step_text.lower()))
    return sum(w in known for w in words) * 2 < len(words)


class AnnotationMapper:
    def __init__(self, fetch=fetch_spec, default_url: str | None = None):
        self.fetch = fetch
        self.default_url = default_url

    def map(self, patch: Patch, docs: list[SpecDoc], hints: list[Hint]) -> MapperOutput:
        parsed = parse_annotations(patch, self.default_url)
        findings: list[Finding] = []

        def finding(text, severity, step_id=None, file=None, line=None):
            rng = Range(file=file, start=line, end=line) if file else None
            findings.append(
                Finding(patch_id=patch.id, text=text, severity=severity, step_id=step_id, range=rng)
            )

        for url in parsed.urls:
            if SNAPSHOT in url:
                finding(
                    f"{url}: spec text comes from the latest indexed version, not the pinned "
                    "commit, so step numbers may differ",
                    "info",
                )
        for d in parsed.diagnostics:
            finding(d.text, d.severity, file=d.file, line=d.line)

        fetched: dict[str, dict] = {}
        ranges: dict[str, list[Range]] = {}
        for m in parsed.marks:
            if m.url not in fetched:
                fetched[m.url] = {s.number: s for s in self.fetch(m.url).steps}
            step = fetched[m.url].get(m.number)
            if step is None:
                finding(f"Step {m.number} does not exist in {m.url}", "warn", file=m.file, line=m.line)
                continue
            if m.range is None:
                if m.no_code:
                    finding(f"Step {m.number} annotation has no code", "warn", step.id, m.file, m.line)
                continue
            ranges.setdefault(step.id, []).append(m.range)
            if _differs(m.text, step.text):
                finding(
                    f"comment text differs from spec step {m.number}", "info", step.id, m.file, m.line
                )

        links = [
            Link(
                patch_id=patch.id,
                step_id=sid,
                ranges=rs,
                kind="implements",
                confidence=1.0,
                rationale=f"author annotation (Step {sid.rsplit(':', 1)[1]})",
            )
            for sid, rs in ranges.items()
        ]
        return MapperOutput(links=links, unmapped=[], findings=findings)
