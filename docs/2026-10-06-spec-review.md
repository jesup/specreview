# spec-review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local web app that maps a single patch (Phabricator revision, jj revision, or patch file) onto the spec algorithm steps it implements, with a linked spec/diff view colored by mapping category.

**Architecture:** Python CLI ingests a patch into a normalized `Patch`, fetches spec steps via `webspec-index`, asks `claude -p --json-schema` for step-to-range links, validates and caches the result, then serves it from FastAPI to a static vanilla-JS three-pane UI. All cross-component data is pydantic models (JSON schema is the contract), so stack support, other ingest sources and a server port can be added later without touching the UI.

**Tech Stack:** Python 3.12+ (uv), FastAPI, uvicorn, pydantic v2, pytest, vanilla JS/CSS. External tools: `claude`, `webspec-index`, `jj`.

Spec: `docs/superpowers/specs/2026-10-06-spec-review-design.md`

## Conventions for the implementer
- App root: `./spec-review/` (new directory, own repo, `jj git init`). All paths below are relative to it.
- The user commits and describes changes themselves. Never run `jj commit`/`git commit`. Each task ends with a "Checkpoint" step: run the full test suite and stop; the user snapshots.
- Minimal comments in code. No emoji. No trailing whitespace. Temp files go in `~/.claude/tmp`, not `/tmp`.
- `webspec-index`, `claude` and `uv sync` need network or write outside the sandbox; run them with the sandbox disabled when the sandbox blocks them. `jj` in another repo needs `--ignore-working-copy` if the sandbox blocks the lock file.
- Verified tool facts (do not re-derive):
  - `webspec-index query SPEC#anchor --format json` returns `{spec, sha, anchor, url, title, type, content, navigation, outgoing_refs}`. `content` is markdown; algorithm steps are `1. ` items nested by 4-space indentation; links look like `[text](url)`.
  - `webspec-index exists REF` exits 0 if found, 1 if not.
  - `claude -p --output-format json --json-schema '<schema>'` prints one JSON object with the validated result in the `structured_output` key and `is_error` boolean.
  - `jj --no-pager diff --git -r REV` emits git-style diffs.

## File structure
```
pyproject.toml
README.md
src/specreview/
  __init__.py
  models.py        pydantic models (Patch, Step, SpecDoc, Link, ... Analysis)
  diffparse.py     unified git diff text -> Patch
  ingest.py        patch file / jj revision / Phabricator revision -> Patch
  spec.py          webspec-index wrapper; markdown -> Step list
  hints.py         // Step N:, // Not in spec, spec URLs from added lines
  claude.py        run_claude(): headless claude with JSON schema
  discovery.py     patch -> candidate spec refs
  mapper.py        Mapper protocol, ClaudeMapper, prompt rendering
  validate.py      deterministic checks/cleanup of mapper output
  coverage.py      step_status, line_status, summarize (pure functions)
  store.py         Cache and Overrides (on-disk JSON)
  pipeline.py      analyze(): orchestration
  server.py        FastAPI app and payload builder
  cli.py           argparse entry point
  static/index.html, app.js, style.css
tests/
  conftest.py, test_*.py
```

---

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`, `src/specreview/__init__.py`, `tests/__init__.py`, `tests/test_smoke.py`, `.gitignore`

- [ ] **Step 1: Create the project**

```bash
mkdir -p ~/src/spec-review/{src/specreview/static,tests/fixtures}
cd ~/src/spec-review-app
jj git init
```

`pyproject.toml`:
```toml
[project]
name = "specreview"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastapi>=0.115", "uvicorn>=0.30", "pydantic>=2.7"]

[project.scripts]
specreview = "specreview.cli:main"

[dependency-groups]
dev = ["pytest>=8", "httpx>=0.27"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/specreview"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`.gitignore`:
```
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
```

`src/specreview/__init__.py` and `tests/__init__.py`: empty files.

`tests/test_smoke.py`:
```python
def test_import():
    import specreview
    assert specreview
```

- [ ] **Step 2: Install and run**

Run: `uv sync && uv run pytest -v`
Expected: `tests/test_smoke.py::test_import PASSED`

- [ ] **Step 3: Capture real fixtures for later manual testing**

```bash
cd ~/src/<repo>
jj --ignore-working-copy --no-pager diff --git -r kmsrpxorkwly > ~/src/spec-review/tests/fixtures/getstats.patch
jj --ignore-working-copy --no-pager diff --git -r pzylxrtqzsop > ~/src/spec-review/tests/fixtures/writer.patch
cd ~/src/spec-review
webspec-index query WEBTRANSPORT#dom-webtransportsendstream-getstats --format json > tests/fixtures/getstats_spec.json
wc -l tests/fixtures/*
```
Expected: non-empty `getstats.patch` (~170 lines), `writer.patch`, and `getstats_spec.json`.

- [ ] **Step 4: Checkpoint** (user commits)

---

### Task 2: Data models

**Files:**
- Create: `src/specreview/models.py`, `tests/conftest.py`, `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:
```python
from specreview.models import Analysis, Link, MapperOutput, Range, Step


def test_step_id_and_json_roundtrip():
    s = Step(spec="S", anchor="alg", number="2.1", text="t", parent="S#alg:2")
    assert s.id == "S#alg:2.1"
    assert s.model_dump()["id"] == "S#alg:2.1"


def test_patch_digest_ignores_id(patch):
    other = patch.model_copy(update={"id": "other"})
    assert patch.digest() == other.digest()


def test_mapper_output_schema_has_links():
    schema = MapperOutput.model_json_schema()
    assert "links" in schema["properties"]


def test_analysis_steps(analysis):
    assert [s.number for s in analysis.steps()] == ["1", "2", "2.1", "3"]
    assert isinstance(analysis.links[0], Link)
    assert isinstance(analysis.links[0].ranges[0], Range)
    assert isinstance(analysis, Analysis)
```

`tests/conftest.py`:
```python
import pytest

from specreview.diffparse import parse_diff
from specreview.models import Analysis, Link, Range, SpecDoc, Step

DIFF = """diff --git a/a/b.cpp b/a/b.cpp
index 1..2 100644
--- a/a/b.cpp
+++ b/a/b.cpp
@@ -1,3 +1,4 @@ void f()
 keep
-old
+new1
+new2
 tail
"""


@pytest.fixture
def diff_text():
    return DIFF


@pytest.fixture
def patch():
    return parse_diff(DIFF, "p1", "desc")


@pytest.fixture
def doc():
    def step(number, text, parent=None):
        return Step(spec="S", anchor="alg", number=number, text=text, parent=parent)

    return SpecDoc(
        ref="S#alg",
        sha="sha1",
        title="alg",
        url="https://example.test/#alg",
        steps=[
            step("1", "Let p be a new promise"),
            step("2", "Run in parallel"),
            step("2.1", "Gather stats", "S#alg:2"),
            step("3", "Return p"),
        ],
    )


@pytest.fixture
def analysis(doc):
    link = Link(
        patch_id="p1",
        step_id="S#alg:1",
        ranges=[Range(file="a/b.cpp", start=2, end=2)],
        kind="implements",
        confidence=0.9,
        rationale="creates the promise",
    )
    return Analysis(patch_id="p1", docs=[doc], links=[link])
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_models.py -v`
Expected: collection error, `ModuleNotFoundError: specreview.diffparse` (conftest imports it; fine, proceed).

- [ ] **Step 3: Implement models**

`src/specreview/models.py`:
```python
import hashlib
from typing import Literal

from pydantic import BaseModel, Field, computed_field

LinkKind = Literal["implements", "partial", "structural", "deviates"]
UnmappedReason = Literal["plumbing", "error-path", "test", "unexplained"]
Side = Literal["new", "old"]


class DiffLine(BaseModel):
    kind: Literal["add", "del", "ctx"]
    old: int | None
    new: int | None
    text: str


class Hunk(BaseModel):
    header: str
    lines: list[DiffLine]


class FileDiff(BaseModel):
    path: str
    hunks: list[Hunk]


class Patch(BaseModel):
    id: str
    parent_ids: list[str] = Field(default_factory=list)
    description: str = ""
    files: list[FileDiff]

    def digest(self) -> str:
        data = self.model_dump_json(exclude={"id", "parent_ids"})
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def file(self, path: str) -> FileDiff | None:
        return next((f for f in self.files if f.path == path), None)


class Step(BaseModel):
    spec: str
    anchor: str
    number: str
    text: str
    parent: str | None = None

    @computed_field
    @property
    def id(self) -> str:
        return f"{self.spec}#{self.anchor}:{self.number}"


class SpecDoc(BaseModel):
    ref: str
    sha: str
    title: str
    url: str
    steps: list[Step]


class Range(BaseModel):
    file: str
    side: Side = "new"
    start: int
    end: int


class Link(BaseModel):
    patch_id: str = ""
    step_id: str
    ranges: list[Range]
    kind: LinkKind
    confidence: float
    rationale: str


class Unmapped(BaseModel):
    patch_id: str = ""
    ranges: list[Range]
    reason: UnmappedReason
    note: str


class Finding(BaseModel):
    patch_id: str = ""
    text: str
    severity: Literal["info", "warn", "error"]
    step_id: str | None = None
    range: Range | None = None


class MapperOutput(BaseModel):
    links: list[Link]
    unmapped: list[Unmapped]
    findings: list[Finding]


class Analysis(BaseModel):
    patch_id: str
    docs: list[SpecDoc]
    links: list[Link] = Field(default_factory=list)
    unmapped: list[Unmapped] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def steps(self) -> list[Step]:
        return [s for d in self.docs for s in d.steps]
```

- [ ] **Step 4: Checkpoint.** Models tests run after Task 3 (conftest needs `diffparse`).

---

### Task 3: Diff parsing

**Files:**
- Create: `src/specreview/diffparse.py`, `tests/test_diffparse.py`

- [ ] **Step 1: Write the failing test**

`tests/test_diffparse.py`:
```python
from specreview.diffparse import parse_diff


def test_parse_lines_and_numbers(patch):
    assert [f.path for f in patch.files] == ["a/b.cpp"]
    lines = patch.files[0].hunks[0].lines
    got = [(l.kind, l.old, l.new, l.text) for l in lines]
    assert got == [
        ("ctx", 1, 1, "keep"),
        ("del", 2, None, "old"),
        ("add", None, 2, "new1"),
        ("add", None, 3, "new2"),
        ("ctx", 3, 4, "tail"),
    ]
    assert patch.description == "desc"


def test_new_file_and_multiple_files():
    text = """diff --git a/x.h b/x.h
new file mode 100644
--- /dev/null
+++ b/x.h
@@ -0,0 +1,2 @@
+a
+b
diff --git a/y.cpp b/y.cpp
--- a/y.cpp
+++ b/y.cpp
@@ -5 +5 @@
-p
\\ No newline at end of file
+q
"""
    p = parse_diff(text, "p")
    assert [f.path for f in p.files] == ["x.h", "y.cpp"]
    assert [l.new for l in p.files[0].hunks[0].lines] == [1, 2]
    y = p.files[1].hunks[0].lines
    assert [(l.kind, l.old, l.new) for l in y] == [("del", 5, None), ("add", None, 5)]


def test_content_that_looks_like_headers_stays_in_hunk():
    text = """diff --git a/z b/z
--- a/z
+++ b/z
@@ -1,2 +1,2 @@
--- not a header
+++ also content
"""
    p = parse_diff(text, "p")
    assert len(p.files[0].hunks[0].lines) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_diffparse.py -v`
Expected: FAIL, `ModuleNotFoundError: specreview.diffparse`.

- [ ] **Step 3: Implement**

`src/specreview/diffparse.py`:
```python
import re

from .models import DiffLine, FileDiff, Hunk, Patch

_FILE = re.compile(r"^diff --git a/(.+) b/(.+)$")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def _count(s: str | None) -> int:
    return 1 if s is None else int(s)


def parse_diff(text: str, patch_id: str, description: str = "") -> Patch:
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    hunk: Hunk | None = None
    old = new = old_left = new_left = 0

    for raw in text.splitlines():
        if hunk is not None and (old_left > 0 or new_left > 0):
            c, body = raw[:1], raw[1:]
            if c == "\\":
                continue
            if c == "+":
                hunk.lines.append(DiffLine(kind="add", old=None, new=new, text=body))
                new += 1
                new_left -= 1
            elif c == "-":
                hunk.lines.append(DiffLine(kind="del", old=old, new=None, text=body))
                old += 1
                old_left -= 1
            else:
                hunk.lines.append(DiffLine(kind="ctx", old=old, new=new, text=body))
                old += 1
                new += 1
                old_left -= 1
                new_left -= 1
            continue

        m = _FILE.match(raw)
        if m:
            cur = FileDiff(path=m.group(2), hunks=[])
            files.append(cur)
            hunk = None
            continue
        if cur is None:
            continue
        if raw.startswith("+++ b/"):
            cur.path = raw[6:]
            continue
        m = _HUNK.match(raw)
        if m:
            old, new = int(m.group(1)), int(m.group(3))
            old_left, new_left = _count(m.group(2)), _count(m.group(4))
            hunk = Hunk(header=raw, lines=[])
            cur.hunks.append(hunk)

    return Patch(id=patch_id, description=description, files=files)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_diffparse.py tests/test_models.py -v`
Expected: all PASS.

- [ ] **Step 5: Parse the real fixtures**

Run: `uv run python -c "from pathlib import Path; from specreview.diffparse import parse_diff; p=parse_diff(Path('tests/fixtures/getstats.patch').read_text(),'x'); print([(f.path,sum(len(h.lines) for h in f.hunks)) for f in p.files])"`
Expected: 3 files, the `.cpp` one with ~80 lines.

- [ ] **Step 6: Checkpoint** (user commits)

---

### Task 4: Ingest (patch file, jj, Phabricator)

**Files:**
- Create: `src/specreview/ingest.py`, `tests/test_ingest.py`

- [ ] **Step 1: Write the failing test**

`tests/test_ingest.py`:
```python
import subprocess

import pytest

from specreview import ingest


def test_from_file(tmp_path, diff_text):
    f = tmp_path / "my.patch"
    f.write_text(diff_text)
    p = ingest.load(str(f))
    assert p.id == "my"
    assert p.files[0].path == "a/b.cpp"


def test_phab_url(monkeypatch, diff_text):
    seen = {}

    def fake_fetch(url):
        seen["url"] = url
        return diff_text

    monkeypatch.setattr(ingest, "_http_get", fake_fetch)
    p = ingest.load("D123")
    assert p.id == "D123"
    assert seen["url"] == "https://phabricator.services.mozilla.com/D123?download=true"


def test_jj(monkeypatch, diff_text):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        out = {"diff": diff_text, "change_id.short()": "abc", "description": "Bug 1: x"}
        if "diff" in cmd:
            return subprocess.CompletedProcess(cmd, 0, out["diff"], "")
        tmpl = cmd[cmd.index("-T") + 1]
        return subprocess.CompletedProcess(cmd, 0, out[tmpl], "")

    monkeypatch.setattr(ingest.subprocess, "run", fake_run)
    p = ingest.load("jj:@-", repo="/some/repo")
    assert p.id == "abc"
    assert p.description == "Bug 1: x"
    assert all(c[0] == "jj" and "--ignore-working-copy" in c for c in calls)


def test_missing_file():
    with pytest.raises(ingest.IngestError):
        ingest.load("/no/such/file.patch")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ingest.py -v`
Expected: FAIL, import error.

- [ ] **Step 3: Implement**

`src/specreview/ingest.py`:
```python
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from .diffparse import parse_diff
from .models import Patch

PHAB = "https://phabricator.services.mozilla.com"


class IngestError(Exception):
    pass


def _http_get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def _jj(args: list[str], repo: str | None) -> str:
    cmd = ["jj", "--no-pager", "--ignore-working-copy", *args]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=repo)
    if p.returncode != 0:
        raise IngestError(p.stderr.strip())
    return p.stdout


def from_file(path: str) -> Patch:
    if path == "-":
        return parse_diff(sys.stdin.read(), "stdin")
    p = Path(path)
    if not p.is_file():
        raise IngestError(f"no such file: {path}")
    return parse_diff(p.read_text(errors="replace"), p.stem)


def from_jj(rev: str, repo: str | None) -> Patch:
    diff = _jj(["diff", "--git", "-r", rev], repo)
    cid = _jj(["log", "-r", rev, "--no-graph", "-T", "change_id.short()"], repo).strip()
    desc = _jj(["log", "-r", rev, "--no-graph", "-T", "description"], repo)
    return parse_diff(diff, cid, desc)


def from_phab(rev: str) -> Patch:
    return parse_diff(_http_get(f"{PHAB}/{rev}?download=true"), rev)


def load(source: str, repo: str | None = None) -> Patch:
    if re.fullmatch(r"D\d+", source):
        return from_phab(source)
    if source.startswith("jj:"):
        return from_jj(source[3:], repo)
    return from_file(source)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_ingest.py -v`
Expected: all PASS.

- [ ] **Step 5: Manually verify Phabricator fetch**

Pick a public revision (ask the user for one if none is known) and run:
`uv run python -c "from specreview.ingest import load; p=load('D<N>'); print(len(p.files), p.files[0].path)"`
Expected: a file count and path. If the response is an HTML login page (non-public revision), `parse_diff` returns zero files: add the check below to `from_phab` and use the existing `moz-phab`/`../review` auth in a follow-up if needed:

```python
    if not patch.files:
        raise IngestError(f"{rev}: no diff returned (revision not public?)")
```
Rewrite `from_phab` as `patch = parse_diff(...)`, the check, `return patch`.

- [ ] **Step 6: Checkpoint** (user commits)

---

### Task 5: Spec steps (webspec-index wrapper)

**Files:**
- Create: `src/specreview/spec.py`, `tests/test_spec.py`

- [ ] **Step 1: Write the failing test**

`tests/test_spec.py`:
```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_spec.py -v`
Expected: FAIL, import error.

- [ ] **Step 3: Implement**

`src/specreview/spec.py`:
```python
import json
import re
import subprocess

from .models import SpecDoc, Step

_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_TAIL_LINK = re.compile(r"\]\(https?://[^)]*\)")
_ITEM = re.compile(r"^(\s*)(\d+)\.\s+(.*)$")


class SpecError(Exception):
    pass


def _clean(text: str) -> str:
    text = _LINK.sub(r"\1", text)
    text = _TAIL_LINK.sub("]", text)
    return re.sub(r"\s+", " ", text.replace("*", "").replace("`", "")).strip()


def parse_steps(spec: str, anchor: str, content: str) -> list[Step]:
    steps: list[Step] = []
    texts: list[str] = []
    stack: list[str] = []
    for line in content.splitlines():
        m = _ITEM.match(line)
        if m:
            depth = len(m.group(1).replace("\t", "    ")) // 4
            del stack[depth:]
            stack.append(m.group(2))
            parent = ".".join(stack[:-1]) or None
            steps.append(
                Step(
                    spec=spec,
                    anchor=anchor,
                    number=".".join(stack),
                    text="",
                    parent=f"{spec}#{anchor}:{parent}" if parent else None,
                )
            )
            texts.append(m.group(3))
        elif steps and line.strip() and line[0] in " \t":
            texts[-1] += " " + line.strip()
    if not steps:
        return [Step(spec=spec, anchor=anchor, number="0", text=_clean(content))]
    return [s.model_copy(update={"text": _clean(t)}) for s, t in zip(steps, texts)]


def fetch_spec(ref: str) -> SpecDoc:
    p = subprocess.run(
        ["webspec-index", "query", ref, "--format", "json"],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        raise SpecError(f"{ref}: {p.stderr.strip() or p.stdout.strip()}")
    d = json.loads(p.stdout)
    return SpecDoc(
        ref=f"{d['spec']}#{d['anchor']}",
        sha=d["sha"],
        title=d["title"],
        url=d["url"],
        steps=parse_steps(d["spec"], d["anchor"], d["content"]),
    )


def spec_exists(ref: str) -> bool:
    p = subprocess.run(["webspec-index", "exists", ref], capture_output=True, text=True)
    return p.returncode == 0
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_spec.py -v`
Expected: all PASS. If `by["1"].text` still shows `[[[Transport]]]` garbage that's acceptable; the `https` assertion must hold.

- [ ] **Step 5: Live check**

Run: `uv run python -c "from specreview.spec import fetch_spec; d=fetch_spec('WEBTRANSPORT#dom-webtransportsendstream-getstats'); [print(s.id, s.text) for s in d.steps]"`
Expected: the nine getStats steps (sandbox disabled for `webspec-index`).

- [ ] **Step 6: Checkpoint** (user commits)

---

### Task 6: Comment hints and spec URL extraction

**Files:**
- Create: `src/specreview/hints.py`, `tests/test_hints.py`

- [ ] **Step 1: Write the failing test**

`tests/test_hints.py`:
```python
from specreview.diffparse import parse_diff
from specreview.hints import Hint, extract_hints, spec_urls

TEXT = """diff --git a/f.cpp b/f.cpp
--- a/f.cpp
+++ b/f.cpp
@@ -1,1 +1,6 @@
 ctx
+// https://w3c.github.io/webtransport/#dom-webtransportsendstream-getstats.
+// Step 2.2.1: Let stats be a new object.
+  // Not in spec: IPC actor is gone.
+  x = 1;  // see https://github.com/foo/bar/issues/1#issuecomment-2
+// Step 3: Return p.
"""


def test_hints():
    p = parse_diff(TEXT, "p")
    assert extract_hints(p) == [
        Hint(file="f.cpp", line=3, step="2.2.1", not_in_spec=False),
        Hint(file="f.cpp", line=4, step=None, not_in_spec=True),
        Hint(file="f.cpp", line=6, step="3", not_in_spec=False),
    ]


def test_spec_urls_include_description_and_dedupe():
    p = parse_diff(TEXT, "p", "see https://w3c.github.io/webtransport/#dom-webtransportsendstream-getstats")
    assert spec_urls(p) == [
        "https://w3c.github.io/webtransport/#dom-webtransportsendstream-getstats",
        "https://github.com/foo/bar/issues/1#issuecomment-2",
    ]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_hints.py -v`
Expected: FAIL, import error.

- [ ] **Step 3: Implement**

`src/specreview/hints.py`:
```python
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_hints.py -v`
Expected: all PASS.

- [ ] **Step 5: Checkpoint** (user commits)

---

### Task 7: Headless Claude runner and spec discovery

**Files:**
- Create: `src/specreview/claude.py`, `src/specreview/discovery.py`, `tests/test_claude.py`, `tests/test_discovery.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_claude.py`:
```python
import json
import subprocess

import pytest

from specreview import claude


def _proc(stdout, rc=0, stderr=""):
    return subprocess.CompletedProcess([], rc, stdout, stderr)


def test_run_claude_returns_structured_output(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return _proc(json.dumps({"is_error": False, "structured_output": {"x": 1}}))

    monkeypatch.setattr(claude.subprocess, "run", fake_run)
    out = claude.run_claude("hello", {"type": "object"}, cwd="/r", model="sonnet")
    assert out == {"x": 1}
    assert seen["kw"]["input"] == "hello"
    assert seen["kw"]["cwd"] == "/r"
    assert "--json-schema" in seen["cmd"] and "--model" in seen["cmd"]


def test_run_claude_errors(monkeypatch):
    monkeypatch.setattr(claude.subprocess, "run", lambda c, **k: _proc("", 1, "boom"))
    with pytest.raises(claude.ClaudeError, match="boom"):
        claude.run_claude("p", {})
    monkeypatch.setattr(
        claude.subprocess, "run",
        lambda c, **k: _proc(json.dumps({"is_error": True, "result": "bad"})),
    )
    with pytest.raises(claude.ClaudeError):
        claude.run_claude("p", {})
```

`tests/test_discovery.py`:
```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_claude.py tests/test_discovery.py -v`
Expected: FAIL, import errors.

- [ ] **Step 3: Implement**

`src/specreview/claude.py`:
```python
import json
import subprocess


class ClaudeError(Exception):
    pass


def run_claude(
    prompt: str,
    schema: dict,
    *,
    cwd: str | None = None,
    model: str | None = None,
    timeout: int = 1800,
) -> dict:
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--allowedTools", "Read,Grep,Glob",
    ]
    if model:
        cmd += ["--model", model]
    p = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True, cwd=cwd, timeout=timeout
    )
    if p.returncode != 0:
        raise ClaudeError(p.stderr.strip() or p.stdout[:500])
    data = json.loads(p.stdout)
    if data.get("is_error") or "structured_output" not in data:
        raise ClaudeError(str(data.get("result", data))[:500])
    return data["structured_output"]
```

`src/specreview/discovery.py`:
```python
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_claude.py tests/test_discovery.py -v`
Expected: all PASS.

- [ ] **Step 5: Smoke test the real runner (cheap model)**

Run: `uv run python -c "from specreview.claude import run_claude; print(run_claude('Reply x=1', {'type':'object','properties':{'x':{'type':'integer'}},'required':['x']}, model='haiku'))"`
Expected: `{'x': 1}` (sandbox disabled).

- [ ] **Step 6: Checkpoint** (user commits)

---

### Task 8: Coverage computation

**Files:**
- Create: `src/specreview/coverage.py`, `tests/test_coverage.py`

- [ ] **Step 1: Write the failing test**

`tests/test_coverage.py`:
```python
from specreview.coverage import line_key, line_status, step_status, summarize
from specreview.models import Link, Range, Unmapped


def test_step_status(analysis):
    st = step_status(analysis.steps(), analysis.links)
    assert st == {
        "S#alg:1": "implements",
        "S#alg:2": "unimplemented",
        "S#alg:2.1": "unimplemented",
        "S#alg:3": "unimplemented",
    }


def test_parent_without_direct_link_is_structural_when_child_linked(analysis):
    link = Link(
        step_id="S#alg:2.1", kind="partial", confidence=0.5, rationale="r",
        ranges=[Range(file="a/b.cpp", start=3, end=3)],
    )
    st = step_status(analysis.steps(), analysis.links + [link])
    assert st["S#alg:2.1"] == "partial"
    assert st["S#alg:2"] == "structural"


def test_worst_kind_wins(analysis):
    extra = Link(
        step_id="S#alg:1", kind="deviates", confidence=0.7, rationale="r",
        ranges=[Range(file="a/b.cpp", start=3, end=3)],
    )
    assert step_status(analysis.steps(), analysis.links + [extra])["S#alg:1"] == "deviates"


def test_line_status(patch, analysis):
    ls = line_status(patch, analysis.links, [])
    assert ls[line_key("a/b.cpp", "new", 2)] == ["implements"]
    assert ls[line_key("a/b.cpp", "new", 3)] == ["not-in-spec:unexplained"]
    assert line_key("a/b.cpp", "old", 2) not in ls


def test_unmapped_reason_is_not_unexplained(patch, analysis):
    um = Unmapped(
        ranges=[Range(file="a/b.cpp", start=3, end=3)], reason="plumbing", note="ipc"
    )
    ls = line_status(patch, analysis.links, [um])
    assert ls[line_key("a/b.cpp", "new", 3)] == ["not-in-spec:plumbing"]


def test_summarize(patch, analysis):
    st = step_status(analysis.steps(), analysis.links)
    ls = line_status(patch, analysis.links, [])
    s = summarize(st, ls, patch)
    assert s["steps"] == {"implements": 1, "unimplemented": 3}
    assert s["added_lines"] == 2
    assert s["unexplained_lines"] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_coverage.py -v`
Expected: FAIL, import error.

- [ ] **Step 3: Implement**

`src/specreview/coverage.py`:
```python
from collections import Counter

from .models import Link, Patch, Step, Unmapped

SEVERITY = ["deviates", "partial", "implements", "structural"]
UNEXPLAINED = "not-in-spec:unexplained"


def line_key(file: str, side: str, n: int) -> str:
    return f"{file}:{'n' if side == 'new' else 'o'}{n}"


def step_status(steps: list[Step], links: list[Link]) -> dict[str, str]:
    direct: dict[str, list[str]] = {}
    for link in links:
        direct.setdefault(link.step_id, []).append(link.kind)
    children: dict[str, list[str]] = {}
    for s in steps:
        if s.parent:
            children.setdefault(s.parent, []).append(s.id)
    status: dict[str, str] = {}
    for s in reversed(steps):
        kinds = direct.get(s.id)
        if kinds:
            status[s.id] = min(kinds, key=SEVERITY.index)
        elif any(status[c] != "unimplemented" for c in children.get(s.id, [])):
            status[s.id] = "structural"
        else:
            status[s.id] = "unimplemented"
    return {s.id: status[s.id] for s in steps}


def line_status(patch: Patch, links: list[Link], unmapped: list[Unmapped]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}

    def mark(ranges, tag):
        for r in ranges:
            for n in range(r.start, r.end + 1):
                tags = out.setdefault(line_key(r.file, r.side, n), [])
                if tag not in tags:
                    tags.append(tag)

    for link in links:
        mark(link.ranges, link.kind)
    for u in unmapped:
        mark(u.ranges, f"not-in-spec:{u.reason}")
    for f in patch.files:
        for h in f.hunks:
            for ln in h.lines:
                if ln.kind == "add":
                    out.setdefault(line_key(f.path, "new", ln.new), [UNEXPLAINED])
    return out


def summarize(step_st: dict[str, str], line_st: dict[str, list[str]], patch: Patch) -> dict:
    added = [
        line_key(f.path, "new", ln.new)
        for f in patch.files
        for h in f.hunks
        for ln in h.lines
        if ln.kind == "add"
    ]
    return {
        "steps": dict(Counter(step_st.values())),
        "added_lines": len(added),
        "unexplained_lines": sum(1 for k in added if line_st.get(k) == [UNEXPLAINED]),
    }
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_coverage.py -v`
Expected: all PASS.

- [ ] **Step 5: Checkpoint** (user commits)

---

### Task 9: Validation of mapper output

**Files:**
- Create: `src/specreview/validate.py`, `tests/test_validate.py`

- [ ] **Step 1: Write the failing test**

`tests/test_validate.py`:
```python
from specreview.hints import Hint
from specreview.models import Finding, Link, MapperOutput, Range, Unmapped
from specreview.validate import validate


def link(step, start, end, kind="implements", conf=0.8, file="a/b.cpp", side="new"):
    return Link(
        step_id=step, kind=kind, confidence=conf, rationale="r",
        ranges=[Range(file=file, side=side, start=start, end=end)],
    )


def run(patch, doc, links=(), unmapped=(), findings=(), hints=()):
    out = MapperOutput(links=list(links), unmapped=list(unmapped), findings=list(findings))
    return validate(patch, [doc], out, list(hints))


def test_unknown_step_dropped(patch, doc):
    out, w = run(patch, doc, [link("S#alg:99", 2, 2)])
    assert out.links == []
    assert any("S#alg:99" in x for x in w)


def test_range_trimmed_to_diff_lines(patch, doc):
    out, _ = run(patch, doc, [link("S#alg:1", 0, 3)])
    r = out.links[0].ranges[0]
    assert (r.start, r.end) == (1, 3)


def test_range_entirely_outside_dropped(patch, doc):
    out, w = run(patch, doc, [link("S#alg:1", 50, 60), link("S#alg:1", 2, 2, file="nope.cpp")])
    assert out.links == []
    assert len(w) >= 2


def test_structural_may_have_no_ranges(patch, doc):
    l = Link(step_id="S#alg:2", kind="structural", confidence=0.6, rationale="r", ranges=[])
    out, _ = run(patch, doc, [l])
    assert len(out.links) == 1


def test_old_side_validated_against_old_lines(patch, doc):
    out, _ = run(patch, doc, [link("S#alg:1", 2, 2, side="old")])
    assert len(out.links) == 1
    out, _ = run(patch, doc, [link("S#alg:1", 4, 4, side="old")])
    assert out.links == []


def test_confidence_clamped(patch, doc):
    out, _ = run(patch, doc, [link("S#alg:1", 2, 2, conf=7)])
    assert out.links[0].confidence == 1.0


def test_unmapped_and_findings_cleaned(patch, doc):
    um = Unmapped(ranges=[Range(file="a/b.cpp", start=3, end=99)], reason="plumbing", note="n")
    f = Finding(text="t", severity="warn", step_id="S#alg:99")
    out, _ = run(patch, doc, unmapped=[um], findings=[f])
    assert out.unmapped[0].ranges[0].end == 3
    assert out.findings[0].step_id is None


def test_hint_step_mismatch_warns(patch, doc):
    hints = [Hint("a/b.cpp", 2, "2", False)]
    _, w = run(patch, doc, [link("S#alg:1", 2, 3)], hints=hints)
    assert any("Step 2" in x and "a/b.cpp:2" in x for x in w)


def test_hint_agreement_is_silent(patch, doc):
    hints = [Hint("a/b.cpp", 2, "1", False)]
    _, w = run(patch, doc, [link("S#alg:1", 2, 3)], hints=hints)
    assert w == []


def test_hint_nonexistent_step_warns(patch, doc):
    hints = [Hint("a/b.cpp", 2, "9.9", False)]
    _, w = run(patch, doc, hints=hints)
    assert any("does not exist" in x for x in w)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_validate.py -v`
Expected: FAIL, import error.

- [ ] **Step 3: Implement**

`src/specreview/validate.py`:
```python
from .hints import Hint
from .models import MapperOutput, Patch, Range, SpecDoc

HINT_WINDOW = 3


def _index(patch: Patch) -> dict[tuple[str, str], set[int]]:
    idx: dict[tuple[str, str], set[int]] = {}
    for f in patch.files:
        new = idx.setdefault((f.path, "new"), set())
        old = idx.setdefault((f.path, "old"), set())
        for h in f.hunks:
            for ln in h.lines:
                if ln.new is not None:
                    new.add(ln.new)
                if ln.old is not None:
                    old.add(ln.old)
    return idx


def _trim(idx, r: Range) -> Range | None:
    lines = idx.get((r.file, r.side))
    if lines is None:
        return None
    s, e = r.start, r.end
    while s <= e and s not in lines:
        s += 1
    while e >= s and e not in lines:
        e -= 1
    return None if s > e else r.model_copy(update={"start": s, "end": e})


def _trim_all(idx, ranges, warnings, what):
    kept = [t for r in ranges if (t := _trim(idx, r))]
    if len(kept) < len(ranges):
        warnings.append(f"{what}: dropped {len(ranges) - len(kept)} range(s) outside the diff")
    return kept


def _check_hints(links, hints: list[Hint], numbers: set[str]) -> list[str]:
    warnings = []
    for h in hints:
        if h.step is None:
            continue
        where = f"{h.file}:{h.line}"
        if h.step not in numbers:
            warnings.append(f"{where}: comment says Step {h.step}, which does not exist in the loaded specs")
            continue
        covering = [
            l for l in links
            if any(
                r.file == h.file and r.side == "new"
                and r.start <= h.line + HINT_WINDOW and r.end >= h.line
                for r in l.ranges
            )
        ]
        if covering and all(l.step_id.rsplit(":", 1)[1] != h.step for l in covering):
            mapped = ", ".join(sorted({l.step_id.rsplit(":", 1)[1] for l in covering}))
            warnings.append(f"{where}: comment says Step {h.step} but the code maps to Step {mapped}")
    return warnings


def validate(patch: Patch, docs: list[SpecDoc], out: MapperOutput, hints: list[Hint]):
    warnings: list[str] = []
    idx = _index(patch)
    steps = [s for d in docs for s in d.steps]
    known = {s.id for s in steps}
    numbers = {s.number for s in steps}

    links = []
    for link in out.links:
        if link.step_id not in known:
            warnings.append(f"dropped link to unknown step {link.step_id}")
            continue
        ranges = _trim_all(idx, link.ranges, warnings, link.step_id)
        if not ranges and link.kind != "structural":
            warnings.append(f"{link.step_id}: dropped link with no valid ranges")
            continue
        links.append(
            link.model_copy(
                update={"ranges": ranges, "confidence": min(1.0, max(0.0, link.confidence))}
            )
        )

    unmapped = []
    for u in out.unmapped:
        ranges = _trim_all(idx, u.ranges, warnings, "unmapped")
        if ranges:
            unmapped.append(u.model_copy(update={"ranges": ranges}))

    findings = []
    for f in out.findings:
        update = {}
        if f.step_id is not None and f.step_id not in known:
            update["step_id"] = None
        if f.range is not None:
            update["range"] = _trim(idx, f.range)
        findings.append(f.model_copy(update=update))

    warnings += _check_hints(links, hints, numbers)
    return MapperOutput(links=links, unmapped=unmapped, findings=findings), warnings
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_validate.py -v`
Expected: all PASS.

- [ ] **Step 5: Checkpoint** (user commits)

---

### Task 10: The Claude mapper

**Files:**
- Create: `src/specreview/mapper.py`, `tests/test_mapper.py`

- [ ] **Step 1: Write the failing test**

`tests/test_mapper.py`:
```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_mapper.py -v`
Expected: FAIL, import error.

- [ ] **Step 3: Implement**

`src/specreview/mapper.py`:
```python
from typing import Protocol

from .claude import run_claude
from .hints import Hint
from .models import MapperOutput, Patch, SpecDoc

PROMPT_VERSION = "1"

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
            depth = s.number.count(".")
            out.append(f"{'  ' * depth}[{s.id}] {s.text}")
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_mapper.py -v`
Expected: all PASS.

- [ ] **Step 5: Verify the real schema is accepted by `claude --json-schema`**

`MapperOutput.model_json_schema()` contains `$defs`/`$ref`. Check with a tiny prompt and the real schema:

Run: `uv run python -c "from specreview.claude import run_claude; from specreview.models import MapperOutput; print(run_claude('Return empty lists for links, unmapped and findings.', MapperOutput.model_json_schema(), model='haiku'))"`
Expected: `{'links': [], 'unmapped': [], 'findings': []}`.
If `claude` rejects or ignores `$ref`, add a `_inline_refs(schema)` helper in `mapper.py` that resolves `$defs` references recursively and use it in `ClaudeMapper.map`; add a test asserting `"$ref" not in json.dumps(schema)`.

- [ ] **Step 6: Checkpoint** (user commits)

---

### Task 11: Cache, overrides, and pipeline

**Files:**
- Create: `src/specreview/store.py`, `src/specreview/pipeline.py`, `tests/test_store.py`, `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_store.py`:
```python
from specreview.store import Cache, Overrides, link_key


def test_cache_roundtrip(tmp_path, patch, doc, analysis):
    c = Cache(tmp_path)
    key = c.key(patch, [doc], "v1", None)
    assert c.get(key) is None
    c.put(key, analysis)
    assert c.get(key) == analysis


def test_cache_key_changes(tmp_path, patch, doc):
    c = Cache(tmp_path)
    base = c.key(patch, [doc], "v1", None)
    assert base != c.key(patch, [doc], "v2", None)
    assert base != c.key(patch, [doc], "v1", "opus")
    assert base != c.key(patch, [doc.model_copy(update={"sha": "other"})], "v1", None)


def test_overrides(tmp_path, patch, analysis):
    o = Overrides(tmp_path, patch)
    k = link_key(analysis.links[0])
    assert o.all() == {}
    o.set(k, "wrong")
    assert Overrides(tmp_path, patch).all() == {k: "wrong"}
    o.set(k, None)
    assert o.all() == {}
```

`tests/test_pipeline.py`:
```python
import pytest

from specreview.models import Link, MapperOutput, Range
from specreview.pipeline import NoSpecs, analyze
from specreview.store import Cache


class FakeMapper:
    def __init__(self, out):
        self.out, self.calls = out, 0

    def map(self, patch, docs, hints):
        self.calls += 1
        return self.out


def good_output():
    return MapperOutput(
        links=[Link(patch_id="p1", step_id="S#alg:1", kind="implements", confidence=0.9,
                    rationale="r", ranges=[Range(file="a/b.cpp", start=2, end=2)])],
        unmapped=[], findings=[],
    )


def test_analyze_uses_cache(tmp_path, patch, doc):
    m = FakeMapper(good_output())
    cache = Cache(tmp_path)
    kw = dict(mapper=m, cache=cache, fetch=lambda r: doc, discover=lambda p: ["S#alg"])
    a1 = analyze(patch, [], **kw)
    a2 = analyze(patch, [], **kw)
    assert m.calls == 1
    assert a1 == a2
    assert a1.links[0].step_id == "S#alg:1"
    analyze(patch, [], force=True, **kw)
    assert m.calls == 2


def test_explicit_refs_skip_discovery(tmp_path, patch, doc):
    def boom(p):
        raise AssertionError("discovery must not run")

    a = analyze(patch, ["S#alg"], mapper=FakeMapper(good_output()), cache=Cache(tmp_path),
                fetch=lambda r: doc, discover=boom)
    assert a.docs[0].ref == "S#alg"


def test_no_specs(tmp_path, patch):
    with pytest.raises(NoSpecs):
        analyze(patch, [], mapper=FakeMapper(good_output()), cache=Cache(tmp_path),
                fetch=lambda r: None, discover=lambda p: [])


def test_validation_warnings_recorded(tmp_path, patch, doc):
    out = good_output()
    out.links.append(Link(step_id="S#alg:99", kind="implements", confidence=1,
                          rationale="r", ranges=[Range(file="a/b.cpp", start=2, end=2)]))
    a = analyze(patch, ["S#alg"], mapper=FakeMapper(out), cache=Cache(tmp_path),
                fetch=lambda r: doc, discover=lambda p: [])
    assert len(a.links) == 1
    assert any("S#alg:99" in w for w in a.warnings)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_store.py tests/test_pipeline.py -v`
Expected: FAIL, import errors.

- [ ] **Step 3: Implement**

`src/specreview/store.py`:
```python
import hashlib
import json
import os
from pathlib import Path

from .models import Analysis, Link, Patch, SpecDoc


def default_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "specreview"


def link_key(link: Link) -> str:
    data = link.step_id + link.model_dump_json(include={"ranges"})
    return hashlib.sha1(data.encode()).hexdigest()[:12]


class Cache:
    def __init__(self, root: Path | None = None):
        self.dir = (root or default_root()) / "analysis"

    def key(self, patch: Patch, docs: list[SpecDoc], prompt_version: str, model: str | None) -> str:
        parts = [patch.digest(), prompt_version, model or ""]
        parts += sorted(f"{d.ref}@{d.sha}" for d in docs)
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]

    def get(self, key: str) -> Analysis | None:
        p = self.dir / f"{key}.json"
        return Analysis.model_validate_json(p.read_text()) if p.exists() else None

    def put(self, key: str, analysis: Analysis) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{key}.json").write_text(analysis.model_dump_json())


class Overrides:
    def __init__(self, root: Path | None, patch: Patch):
        self.path = (root or default_root()) / "overrides" / f"{patch.digest()}.json"

    def all(self) -> dict[str, str]:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def set(self, key: str, verdict: str | None) -> None:
        data = self.all()
        if verdict is None:
            data.pop(key, None)
        else:
            data[key] = verdict
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data))
```

`src/specreview/pipeline.py`:
```python
from .hints import extract_hints
from .mapper import PROMPT_VERSION
from .models import Analysis
from .store import Cache
from .validate import validate


class NoSpecs(Exception):
    pass


def analyze(
    patch,
    refs,
    *,
    mapper,
    cache: Cache,
    fetch,
    discover,
    model: str | None = None,
    force: bool = False,
) -> Analysis:
    refs = list(refs) or discover(patch)
    if not refs:
        raise NoSpecs("no specification found for this patch; pass --spec SPEC#anchor")
    docs = [fetch(r) for r in refs]
    key = cache.key(patch, docs, PROMPT_VERSION, model)
    if not force and (hit := cache.get(key)):
        return hit
    hints = extract_hints(patch)
    out, warnings = validate(patch, docs, mapper.map(patch, docs, hints), hints)
    analysis = Analysis(
        patch_id=patch.id,
        docs=docs,
        links=out.links,
        unmapped=out.unmapped,
        findings=out.findings,
        warnings=warnings,
    )
    cache.put(key, analysis)
    return analysis
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_store.py tests/test_pipeline.py -v`
Expected: all PASS.

- [ ] **Step 5: Checkpoint** (user commits)

---

### Task 12: Server and CLI

**Files:**
- Create: `src/specreview/server.py`, `src/specreview/cli.py`, `tests/test_server.py`, `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_server.py`:
```python
from fastapi.testclient import TestClient

from specreview.server import ReviewState, create_app
from specreview.store import Overrides


def make(tmp_path, patch, analysis, rerun=None):
    state = ReviewState(patch, analysis, Overrides(tmp_path, patch), rerun or (lambda refs: analysis))
    return TestClient(create_app(state)), state


def test_review_payload(tmp_path, patch, analysis):
    client, _ = make(tmp_path, patch, analysis)
    d = client.get("/api/review").json()
    assert d["patch"]["id"] == "p1"
    assert d["step_status"]["S#alg:1"] == "implements"
    assert d["line_status"]["a/b.cpp:n3"] == ["not-in-spec:unexplained"]
    assert d["summary"]["added_lines"] == 2
    assert len(d["link_keys"]) == len(d["analysis"]["links"])
    assert d["overrides"] == {}


def test_override_roundtrip(tmp_path, patch, analysis):
    client, _ = make(tmp_path, patch, analysis)
    key = client.get("/api/review").json()["link_keys"][0]
    assert client.put(f"/api/overrides/{key}", json={"verdict": "wrong"}).status_code == 200
    assert client.get("/api/review").json()["overrides"] == {key: "wrong"}
    client.put(f"/api/overrides/{key}", json={"verdict": None})
    assert client.get("/api/review").json()["overrides"] == {}


def test_bad_verdict_rejected(tmp_path, patch, analysis):
    client, _ = make(tmp_path, patch, analysis)
    assert client.put("/api/overrides/abc", json={"verdict": "meh"}).status_code == 422


def test_rerun_replaces_analysis(tmp_path, patch, analysis):
    seen = []
    new = analysis.model_copy(update={"warnings": ["rerun"]})

    def rerun(refs):
        seen.append(refs)
        return new

    client, _ = make(tmp_path, patch, analysis, rerun)
    d = client.post("/api/rerun", json={"specs": ["S#alg"]}).json()
    assert seen == [["S#alg"]]
    assert d["analysis"]["warnings"] == ["rerun"]


def test_serves_index(tmp_path, patch, analysis):
    client, _ = make(tmp_path, patch, analysis)
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text
```

`tests/test_cli.py`:
```python
from specreview.cli import parse_args


def test_parse_args():
    a = parse_args(["D123", "--spec", "A#b", "--spec", "C#d", "--repo", "/r", "--port", "9"])
    assert a.source == "D123"
    assert a.spec == ["A#b", "C#d"]
    assert a.repo == "/r" and a.port == 9
    assert a.refresh is False and a.no_open is False


def test_defaults():
    a = parse_args(["jj:@-"])
    assert a.spec == [] and a.port == 8765 and a.model is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_server.py tests/test_cli.py -v`
Expected: FAIL, import errors.

- [ ] **Step 3: Implement server**

`src/specreview/server.py`:
```python
from pathlib import Path
from typing import Callable, Literal

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .coverage import line_status, step_status, summarize
from .models import Analysis, Patch
from .store import Overrides, link_key

STATIC = Path(__file__).parent / "static"


class ReviewState:
    def __init__(self, patch: Patch, analysis: Analysis, overrides: Overrides,
                 rerun: Callable[[list[str]], Analysis]):
        self.patch = patch
        self.analysis = analysis
        self.overrides = overrides
        self.rerun = rerun


class RerunBody(BaseModel):
    specs: list[str]


class VerdictBody(BaseModel):
    verdict: Literal["ok", "wrong"] | None


def build_payload(state: ReviewState) -> dict:
    a = state.analysis
    st = step_status(a.steps(), a.links)
    ls = line_status(state.patch, a.links, a.unmapped)
    return {
        "patch": state.patch.model_dump(),
        "analysis": a.model_dump(),
        "step_status": st,
        "line_status": ls,
        "summary": summarize(st, ls, state.patch),
        "overrides": state.overrides.all(),
        "link_keys": [link_key(l) for l in a.links],
    }


def create_app(state: ReviewState) -> FastAPI:
    app = FastAPI()

    @app.get("/api/review")
    def review():
        return build_payload(state)

    @app.post("/api/rerun")
    def rerun(body: RerunBody):
        state.analysis = state.rerun(body.specs)
        return build_payload(state)

    @app.put("/api/overrides/{key}")
    def override(key: str, body: VerdictBody):
        state.overrides.set(key, body.verdict)
        return {"ok": True}

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
```

Create a placeholder `src/specreview/static/index.html` containing `<!doctype html><html><body></body></html>` so `test_serves_index` passes now; Task 13 replaces it.

- [ ] **Step 4: Implement CLI**

`src/specreview/cli.py`:
```python
import argparse
import sys
import threading
import webbrowser
from functools import partial

import uvicorn

from . import ingest
from .discovery import discover_refs
from .mapper import ClaudeMapper
from .pipeline import NoSpecs, analyze
from .server import ReviewState, create_app
from .spec import fetch_spec
from .store import Cache, Overrides


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="specreview", description="Review a patch against the spec it implements")
    p.add_argument("source", help="D12345, jj:<revset>, patch file, or - for stdin")
    p.add_argument("--spec", action="append", default=[], help="SPEC#anchor or URL; repeatable")
    p.add_argument("--repo", help="repository for jj and for Claude's context")
    p.add_argument("--model", help="claude model alias")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--refresh", action="store_true", help="ignore cached analysis")
    p.add_argument("--no-open", action="store_true")
    return p.parse_args(argv)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        patch = ingest.load(args.source, args.repo)
    except ingest.IngestError as e:
        log(f"error: {e}")
        return 1
    if not patch.files:
        log("error: the patch contains no changes")
        return 1

    cache = Cache()
    run = partial(
        analyze,
        patch,
        mapper=ClaudeMapper(repo=args.repo, model=args.model),
        cache=cache,
        fetch=fetch_spec,
        discover=partial(discover_refs, repo=args.repo, model=args.model),
        model=args.model,
    )

    def rerun(refs):
        log(f"analyzing against {', '.join(refs)} ...")
        return run(refs, force=True)

    try:
        log("analyzing (this can take a few minutes on first run) ...")
        analysis = run(args.spec, force=args.refresh)
    except NoSpecs as e:
        log(f"error: {e}")
        return 1

    app = create_app(ReviewState(patch, analysis, Overrides(None, patch), rerun))
    url = f"http://127.0.0.1:{args.port}/"
    log(f"serving {url}")
    if not args.no_open:
        threading.Timer(1.0, webbrowser.open, args=[url]).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest -v`
Expected: all tests PASS across the suite.

- [ ] **Step 6: Checkpoint** (user commits)

---

### Task 13: Front end

**Files:**
- Replace: `src/specreview/static/index.html`
- Create: `src/specreview/static/style.css`, `src/specreview/static/app.js`
- Create: `tests/fixtures/make_demo.py` (dev helper that writes a demo payload server; deleted at the end of the task if the user doesn't want it kept)

The front end has no automated tests; verify manually against the demo server in Step 5 and the real run in Task 14.

- [ ] **Step 1: index.html**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>spec-review</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header id="bar">
  <strong id="title"></strong>
  <span id="summary"></span>
  <span class="sep"></span>
  <label><input type="checkbox" id="f-steps"> unimplemented steps</label>
  <label><input type="checkbox" id="f-code"> unexplained code</label>
  <label><input type="checkbox" id="f-low"> low confidence</label>
  <label><input type="checkbox" id="f-dev"> deviations</label>
  <span class="sep"></span>
  <span id="spec-chips"></span>
  <input id="add-spec" placeholder="SPEC#anchor or URL">
  <button id="rerun">Re-run</button>
  <span id="legend"></span>
</header>
<div id="notes" hidden></div>
<main>
  <section id="spec-pane"><div class="mm" id="spec-mm"></div><div id="spec" class="scroll"></div></section>
  <section id="diff-pane"><div class="mm" id="diff-mm"></div><div id="diff" class="scroll"></div></section>
</main>
<aside id="pop" hidden></aside>
<script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 2: style.css**

```css
:root {
  --implements: #2e9d4f;
  --partial: #e0a800;
  --structural: #3b82c4;
  --deviates: #e8590c;
  --unimplemented: #d6293e;
  --notinspec: #8a8f98;
  --bg: #fafafa;
  --line-h: 1.45;
}
* { box-sizing: border-box; }
body { margin: 0; font: 13px system-ui, sans-serif; background: var(--bg); height: 100vh; display: flex; flex-direction: column; }
#bar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; padding: 6px 10px; border-bottom: 1px solid #ccc; background: #fff; }
#bar .sep { width: 1px; height: 18px; background: #ccc; }
#summary { color: #555; }
.chip { background: #eef; border-radius: 10px; padding: 1px 8px; font-size: 12px; }
.chip button { border: 0; background: none; cursor: pointer; }
#legend span { margin-left: 8px; padding-left: 14px; position: relative; font-size: 11px; }
#legend span::before { content: ""; position: absolute; left: 0; top: 2px; width: 10px; height: 10px; background: var(--c); }
#legend span.hatch::before { background: repeating-linear-gradient(45deg, var(--c) 0 2px, transparent 2px 4px); }
#notes { padding: 4px 10px; background: #fff7e0; border-bottom: 1px solid #e6d9a8; max-height: 90px; overflow: auto; font-size: 12px; }
main { flex: 1; display: flex; min-height: 0; }
section { display: flex; min-width: 0; }
#spec-pane { flex: 0 0 38%; border-right: 1px solid #ccc; }
#diff-pane { flex: 1; }
.scroll { flex: 1; overflow: auto; }
.mm { width: 10px; display: flex; flex-direction: column; background: #eee; }
.mm div { flex: 1 1 0; min-height: 1px; cursor: pointer; }

.doc-head { position: sticky; top: 0; background: #e8ecf3; padding: 3px 8px; font-weight: 600; z-index: 1; }
.step { display: flex; gap: 6px; padding: 3px 8px 3px 4px; border-left: 5px solid var(--c, #ccc); cursor: pointer; }
.step:hover { background: #eef3ff; }
.step.sel { background: #dbe7ff; outline: 1px solid #5b8def; }
.step .num { color: #555; min-width: 3.2em; font-variant-numeric: tabular-nums; }
.step .badge { font-size: 10px; color: #fff; background: var(--c, #999); border-radius: 3px; padding: 0 4px; align-self: flex-start; }
.st-implements { --c: var(--implements); }
.st-partial { --c: var(--partial); }
.st-structural { --c: var(--structural); }
.st-deviates { --c: var(--deviates); }
.st-unimplemented { --c: var(--unimplemented); background: #fff0f0; }
.st-unimplemented .text { font-weight: 600; }

.file-head { position: sticky; top: 0; background: #e8ecf3; padding: 3px 8px; font-weight: 600; z-index: 1; }
.hunk-head { color: #777; background: #f0f0f0; padding: 1px 8px; font: 11px monospace; }
.line { display: flex; font: 12px/var(--line-h) monospace; white-space: pre; cursor: pointer; }
.line:hover { background: #eef3ff; }
.line.add { background: #eaffea; }
.line.del { background: #ffecec; }
.line.hit { outline: 2px solid #5b8def; outline-offset: -2px; background: #dbe7ff; }
.line.wrong { opacity: .55; }
.stripe { width: 6px; flex: none; background: transparent; }
.stripe.u { background: repeating-linear-gradient(45deg, var(--unimplemented) 0 2px, transparent 2px 5px); }
.stripe.n { background: repeating-linear-gradient(45deg, var(--notinspec) 0 2px, transparent 2px 5px); }
.ln { width: 4.5em; text-align: right; color: #999; padding-right: 6px; flex: none; user-select: none; }
.code { flex: 1; padding-right: 8px; }
.labels { color: #335; padding: 0 8px; font-size: 11px; flex: none; }
.dim { opacity: .25; }

#pop { position: fixed; right: 12px; bottom: 12px; width: 440px; max-height: 45vh; overflow: auto; background: #fff; border: 1px solid #999; border-radius: 6px; box-shadow: 0 4px 16px #0003; padding: 8px 10px; font-size: 12px; z-index: 5; }
#pop .item { border-top: 1px solid #eee; padding: 5px 0; }
#pop .item:first-child { border-top: 0; }
#pop .k { display: inline-block; color: #fff; font-size: 10px; border-radius: 3px; padding: 0 4px; background: var(--c); }
#pop button { margin-right: 4px; }
#pop button.on { font-weight: 700; outline: 1px solid #333; }
```

- [ ] **Step 3: app.js**

```js
const KIND_LABEL = {implements: "impl", partial: "part", structural: "struct", deviates: "DEV", unimplemented: "MISSING"};
const KIND_ORDER = ["deviates", "partial", "implements", "structural"];
const COLOR = {
  implements: "var(--implements)", partial: "var(--partial)", structural: "var(--structural)",
  deviates: "var(--deviates)", unimplemented: "var(--unimplemented)",
};
const LOW = 0.5;

let D, linksByStep, linksByLine, stepsById, stepEls, rowEls, hits = [], hitIdx = 0;

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};
const lk = (file, side, n) => `${file}:${side === "new" ? "n" : "o"}${n}`;
const stepNum = (id) => id.slice(id.lastIndexOf(":") + 1);
const $ = (id) => document.getElementById(id);

async function load() {
  D = await (await fetch("/api/review")).json();
  index();
  render();
}

function index() {
  linksByStep = {};
  linksByLine = {};
  stepsById = {};
  D.analysis.docs.forEach((d) => d.steps.forEach((s) => (stepsById[s.id] = s)));
  D.analysis.links.forEach((l, i) => {
    (linksByStep[l.step_id] ||= []).push(i);
    for (const r of l.ranges)
      for (let n = r.start; n <= r.end; n++) (linksByLine[lk(r.file, r.side, n)] ||= []).push(i);
  });
}

function render() {
  stepEls = {};
  rowEls = {};
  hits = [];
  $("title").textContent = D.patch.id;
  const s = D.summary;
  const counts = Object.entries(s.steps).map(([k, v]) => `${v} ${k}`).join(", ");
  $("summary").textContent = `${counts}; ${s.unexplained_lines}/${s.added_lines} added lines unexplained`;
  renderLegend();
  renderSpecs();
  renderNotes();
  renderSpecPane();
  renderDiffPane();
  applyFilters();
  closePop();
}

function renderLegend() {
  const lg = $("legend");
  lg.replaceChildren();
  for (const k of Object.keys(COLOR)) {
    const sp = el("span", "", KIND_LABEL[k]);
    sp.style.setProperty("--c", COLOR[k]);
    lg.append(sp);
  }
  const g = el("span", "hatch", "not in spec");
  g.style.setProperty("--c", "var(--notinspec)");
  const u = el("span", "hatch", "unexplained");
  u.style.setProperty("--c", "var(--unimplemented)");
  lg.append(g, u);
}

let specRefs = [];
function renderSpecs() {
  specRefs = D.analysis.docs.map((d) => d.ref);
  const box = $("spec-chips");
  box.replaceChildren();
  specRefs.forEach((ref) => {
    const c = el("span", "chip", ref + " ");
    const x = el("button", "", "x");
    x.title = "remove";
    x.onclick = () => {
      specRefs = specRefs.filter((r) => r !== ref);
      c.remove();
    };
    c.append(x);
    box.append(c);
  });
}

function renderNotes() {
  const items = [
    ...D.analysis.warnings.map((w) => `warning: ${w}`),
    ...D.analysis.findings.map((f) => `${f.severity}: ${f.text}`),
  ];
  const box = $("notes");
  box.hidden = items.length === 0;
  box.replaceChildren(...items.map((t) => el("div", "", t)));
}

function renderSpecPane() {
  const pane = $("spec");
  const mm = $("spec-mm");
  pane.replaceChildren();
  mm.replaceChildren();
  for (const d of D.analysis.docs) {
    pane.append(el("div", "doc-head", `${d.title}  (${d.ref})`));
    for (const s of d.steps) {
      const st = D.step_status[s.id];
      const row = el("div", `step st-${st}`);
      row.dataset.id = s.id;
      row.style.paddingLeft = `${4 + 16 * s.number.split(".").length - 16}px`;
      row.append(el("span", "num", s.number), el("span", "text", s.text), el("span", "badge", KIND_LABEL[st]));
      row.onclick = () => selectStep(s.id);
      pane.append(row);
      stepEls[s.id] = row;
      const tick = el("div");
      tick.style.background = COLOR[st];
      tick.onclick = () => row.scrollIntoView({block: "center"});
      mm.append(tick);
    }
  }
}

function kindsFor(key) {
  return D.line_status[key] || [];
}

function stripeFor(kinds) {
  const st = el("span", "stripe");
  const links = KIND_ORDER.filter((k) => kinds.includes(k));
  if (links.length) {
    const seg = 100 / links.length;
    st.style.background = `linear-gradient(to bottom, ${links
      .map((k, i) => `${COLOR[k]} ${i * seg}% ${(i + 1) * seg}%`)
      .join(",")})`;
  } else if (kinds.includes("not-in-spec:unexplained")) {
    st.classList.add("u");
  } else if (kinds.length) {
    st.classList.add("n");
  }
  return st;
}

function renderDiffPane() {
  const pane = $("diff");
  const mm = $("diff-mm");
  pane.replaceChildren();
  mm.replaceChildren();
  let order = 0;
  for (const f of D.patch.files) {
    pane.append(el("div", "file-head", f.path));
    for (const h of f.hunks) {
      pane.append(el("div", "hunk-head", h.header));
      for (const ln of h.lines) {
        const side = ln.kind === "del" ? "old" : "new";
        const n = side === "old" ? ln.old : ln.new;
        const key = lk(f.path, side, n);
        const kinds = ln.kind === "ctx" ? [] : kindsFor(key);
        const row = el("div", `line ${ln.kind}`);
        row.dataset.key = key;
        row.dataset.order = order++;
        const nums = [...new Set((linksByLine[key] || []).map((i) => stepNum(D.analysis.links[i].step_id)))];
        row.append(
          stripeFor(kinds),
          el("span", "ln", n),
          el("span", "code", (ln.kind === "add" ? "+" : ln.kind === "del" ? "-" : " ") + ln.text),
          el("span", "labels", nums.join(","))
        );
        row.onclick = () => selectLine(key);
        applyVerdict(row, key);
        pane.append(row);
        rowEls[key] = row;
        if (ln.kind !== "ctx") {
          const tick = el("div");
          const top = KIND_ORDER.find((k) => kinds.includes(k));
          tick.style.background = top ? COLOR[top] : kinds.includes("not-in-spec:unexplained") ? COLOR.unimplemented : "#bbb";
          tick.onclick = () => row.scrollIntoView({block: "center"});
          mm.append(tick);
        }
      }
    }
  }
}

function applyVerdict(row, key) {
  const idxs = linksByLine[key] || [];
  const allWrong = idxs.length > 0 && idxs.every((i) => D.overrides[D.link_keys[i]] === "wrong");
  row.classList.toggle("wrong", allWrong);
}

function clearSel() {
  document.querySelectorAll(".sel, .hit").forEach((e) => e.classList.remove("sel", "hit"));
}

function selectStep(id) {
  clearSel();
  stepEls[id].classList.add("sel");
  hits = [];
  for (const i of linksByStep[id] || [])
    for (const r of D.analysis.links[i].ranges)
      for (let n = r.start; n <= r.end; n++) {
        const row = rowEls[lk(r.file, r.side, n)];
        if (row && !hits.includes(row)) hits.push(row);
      }
  hits.sort((a, b) => a.dataset.order - b.dataset.order);
  hits.forEach((r) => r.classList.add("hit"));
  hitIdx = 0;
  if (hits[0]) hits[0].scrollIntoView({block: "center"});
  showStepPop(id);
}

function selectLine(key) {
  clearSel();
  rowEls[key].classList.add("hit");
  const idxs = linksByLine[key] || [];
  idxs.forEach((i) => stepEls[D.analysis.links[i].step_id]?.classList.add("sel"));
  const first = stepEls[idxs.length ? D.analysis.links[idxs[0]].step_id : ""];
  if (first) first.scrollIntoView({block: "center"});
  showLinePop(key, idxs);
}

function jump(delta) {
  if (!hits.length) return;
  hitIdx = (hitIdx + delta + hits.length) % hits.length;
  hits[hitIdx].scrollIntoView({block: "center"});
}

function linkItem(i, withVerdict) {
  const l = D.analysis.links[i];
  const key = D.link_keys[i];
  const box = el("div", "item");
  const k = el("span", "k", `${KIND_LABEL[l.kind]} ${Math.round(l.confidence * 100)}%`);
  k.style.setProperty("--c", COLOR[l.kind]);
  const step = stepsById[l.step_id];
  box.append(k, el("span", "", ` ${step ? step.number + " " + step.text : l.step_id}`), el("div", "", l.rationale));
  if (withVerdict) {
    const row = el("div");
    for (const v of ["ok", "wrong"]) {
      const b = el("button", D.overrides[key] === v ? "on" : "", v);
      b.onclick = async (e) => {
        e.stopPropagation();
        const next = D.overrides[key] === v ? null : v;
        await fetch(`/api/overrides/${key}`, {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({verdict: next}),
        });
        if (next) D.overrides[key] = next;
        else delete D.overrides[key];
        for (const r of Object.values(rowEls)) applyVerdict(r, r.dataset.key);
        row.replaceChildren(...verdictButtons());
      };
      row.append(b);
    }
    box.append(row);
  }
  return box;
}

function verdictButtons() {
  return [];
}

function showStepPop(id) {
  const s = stepsById[id];
  const pop = $("pop");
  pop.replaceChildren(el("div", "", `${s.number} ${s.text}`));
  const idxs = linksByStep[id] || [];
  if (!idxs.length) pop.append(el("div", "item", `No code maps to this step (${D.step_status[id]}).`));
  idxs.forEach((i) => pop.append(linkItem(i, true)));
  if (hits.length) pop.append(el("div", "item", `${hits.length} lines; n / p to step through`));
  pop.hidden = false;
}

function showLinePop(key, idxs) {
  const pop = $("pop");
  pop.replaceChildren();
  idxs.forEach((i) => pop.append(linkItem(i, true)));
  for (const u of D.analysis.unmapped)
    for (const r of u.ranges)
      if (lk(r.file, r.side, r.start) <= key && inRange(key, r)) {
        pop.append(el("div", "item", `not in spec (${u.reason}): ${u.note}`));
      }
  if (!pop.childNodes.length) pop.append(el("div", "", (kindsFor(key)[0] || "context line")));
  pop.hidden = false;
}

function inRange(key, r) {
  const m = key.match(/^(.*):([no])(\d+)$/);
  return m && m[1] === r.file && (m[2] === "n") === (r.side === "new") && +m[3] >= r.start && +m[3] <= r.end;
}

function closePop() {
  $("pop").hidden = true;
}

function applyFilters() {
  const f = {
    steps: $("f-steps").checked, code: $("f-code").checked,
    low: $("f-low").checked, dev: $("f-dev").checked,
  };
  const any = Object.values(f).some(Boolean);
  for (const [id, e] of Object.entries(stepEls)) {
    const st = D.step_status[id];
    const lows = (linksByStep[id] || []).some((i) => D.analysis.links[i].confidence < LOW);
    const match = (f.steps && st === "unimplemented") || (f.dev && st === "deviates") || (f.low && lows);
    e.classList.toggle("dim", any && !match);
  }
  for (const [key, e] of Object.entries(rowEls)) {
    if (e.classList.contains("ctx")) {
      e.classList.toggle("dim", any);
      continue;
    }
    const kinds = kindsFor(key);
    const idxs = linksByLine[key] || [];
    const match =
      (f.code && kinds.includes("not-in-spec:unexplained")) ||
      (f.dev && kinds.includes("deviates")) ||
      (f.low && idxs.some((i) => D.analysis.links[i].confidence < LOW));
    e.classList.toggle("dim", any && !match);
  }
}

document.querySelectorAll("#bar input[type=checkbox]").forEach((c) => (c.onchange = applyFilters));
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") return;
  if (e.key === "n") jump(1);
  else if (e.key === "p") jump(-1);
  else if (e.key === "Escape") closePop();
});

$("rerun").onclick = async () => {
  const extra = $("add-spec").value.trim();
  const refs = extra ? [...specRefs, extra] : specRefs;
  if (!refs.length) return;
  const btn = $("rerun");
  btn.disabled = true;
  btn.textContent = "Analyzing...";
  try {
    const r = await fetch("/api/rerun", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({specs: refs}),
    });
    if (!r.ok) throw new Error(await r.text());
    D = await r.json();
    $("add-spec").value = "";
    index();
    render();
  } catch (err) {
    alert(`re-run failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Re-run";
  }
};

load();
```

Simplify before saving: delete the unused `verdictButtons()` helper and its use. In `linkItem`'s button handler, after the PUT and local `D.overrides` update, re-render the button group in place:
```js
for (const b of row.children) b.classList.toggle("on", D.overrides[key] === b.textContent);
```
In `showLinePop`, replace the awkward condition `lk(r.file, r.side, r.start) <= key && inRange(key, r)` with just `inRange(key, r)`.

- [ ] **Step 4: Demo data helper**

`tests/fixtures/make_demo.py`: a script that builds a `ReviewState` from the real fixtures (`getstats.patch`, `getstats_spec.json`, and a hand-written `Analysis` with a few links, one `deviates`, one unmapped `plumbing`, and the rest of the added lines unexplained) and runs `uvicorn.run(create_app(state), port=8766)`. Run: `uv run python tests/fixtures/make_demo.py`. The hand-written links must use real line numbers from `getstats.patch`; read it first to choose them.

- [ ] **Step 5: Manual verification**

Open `http://127.0.0.1:8766/`. Check:
1. Spec steps 1–4 and nested 3.x steps appear with indentation; unimplemented steps are red with bold text and a MISSING badge.
2. Added diff lines show colored stripes; unexplained lines have the red hatch; "not in spec" lines gray hatch; a line covered by two kinds shows a two-color stripe.
3. Click a step: it highlights, the diff scrolls to the first hit, `n`/`p` cycle through hits, the popover lists links with rationale and confidence.
4. Click a diff line: its steps highlight in the spec pane, popover shows rationale; ok/wrong buttons persist across a page reload.
5. Each filter checkbox dims non-matching steps and lines; unchecking restores.
6. Minimap ticks scroll to the corresponding row on click.
7. Removing a spec chip and adding `SPEC#anchor` then Re-run (against the demo server `rerun` stub) updates the page without a reload.
Fix visual problems found; do not add features.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`
Expected: all PASS (`test_serves_index` now serves the real page).

- [ ] **Step 7: Checkpoint** (user commits)

---

### Task 14: End-to-end on real patches and README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Run on the clean case**

Run: `uv run specreview tests/fixtures/<patch> --repo ~/src/<repo> --spec <spec section> --no-open --port 8765 &> ~/.claude/tmp/e2e1.log`
(sandbox disabled: it runs `claude` and `webspec-index`; the first run takes minutes.)
Expected log: "serving http://127.0.0.1:8765/". Then `curl -s localhost:8765/api/review | uv run python -c "import json,sys; d=json.load(sys.stdin); print(d['summary']); print(d['analysis']['warnings']); [print(f) for f in d['analysis']['findings']]"`.

Sanity expectations from reading the patch by hand (the author numbered comments `Step 2.2.1`, but the spec's step is `3.2.1`):
- Steps 2, 3.1, 3.2.x, 4 are mostly `implements`/`partial`; step 1 (`Let transport`) may be unlinked or `partial`.
- `validate` warnings include comment/step number mismatches (`comment says Step 2.2.1 but the code maps to Step 3.2.1`).
- The `if (!child) {...}` fallback and the IPC-failure lambda are `not-in-spec` with reason `error-path`.
- The `.ini` expectation file hunks are `not-in-spec` with reason `test`.

- [ ] **Step 2: Judge the mapping quality**

Open the UI and review it as the user would. Record specific problems (wrong ranges, unexplained code that is obviously plumbing, missing steps). Adjust only `INSTRUCTIONS` in `mapper.py` (bump `PROMPT_VERSION` each change so the cache invalidates), re-run with `--refresh`, repeat at most three times. Report the remaining problems to the user rather than over-tuning.

- [ ] **Step 3: Run on the messy case with auto-detection**

Run: `uv run specreview tests/fixtures/<patch> --repo ~/src/<repo> --no-open --port 8765 &> ~/.claude/tmp/e2e2.log`
Expected: discovery finds WebTransport anchors from comment URLs or through the Claude fallback (it may find none: report that). Check that IPDL/`parent/` plumbing lands in `not-in-spec:plumbing` and the UI stays usable with many unexplained lines.

- [ ] **Step 4: Run via jj and Phabricator inputs**

Run: `uv run specreview jj:kmsrpxorkwly --repo ~/src/<repo> --no-open --port 8765` (cached result expected to be reused if the digest matches; confirm that no second Claude call is made by the faster startup) and one real `D<N>` revision if the user supplies one.

- [ ] **Step 5: Write README.md**

Sections, concise: what it is; install (`uv sync`); usage (`uv run specreview D12345|jj:REV|file.patch [--spec REF ...] [--repo PATH]`); the color/category legend; keyboard shortcuts (`n`/`p`, `Esc`); where caches and overrides live (`~/.cache/specreview`); how mapping works in four sentences; the extension points copied from the design doc ("Extension points" section) so a future implementer finds them: `Patch.parent_ids`, `Link.patch_id`, the `Mapper` protocol in `mapper.py`, pure `coverage.py`, `ingest.load` dispatch, `spec.fetch_spec`.

- [ ] **Step 6: Clean up and checkpoint**

Delete `tests/fixtures/make_demo.py` if the user does not want it kept, and any files under `~/.claude/tmp/e2e*.log`. Run `uv run pytest -v` one last time. Checkpoint (user commits).

---

## Self-review notes

**Spec coverage:**
- Local web app, three-pane layout, stripe colors, patterns plus labels, minimap strips, filters, click linking both ways, popover rationale/confidence: Task 13.
- Inputs (Phab, jj, patch file): Task 4. Auto-detect with override: Tasks 7 and 13 (chips, add, re-run).
- Author comments as verified hints: Tasks 6, 9, 10.
- `claude -p` headless with JSON schema: Tasks 7, 10. Caching keyed by patch hash, spec SHA, prompt version (and model): Task 11.
- Deterministic validation and coverage: Tasks 8, 9. Overrides sidecar: Tasks 11, 12, 13.
- Read-only v1, single patch: whole plan. Extension hooks (`parent_ids`, `patch_id`, `Mapper` protocol, pure coverage, ingest/spec dispatch): present in models and README (Task 14); not implemented, as agreed.

**Deliberate deviations from the design doc (flag to the user):**
- Mapping is one Claude call per patch with all selected spec anchors, not one call per algorithm/file group; chunking belongs behind the `Mapper` protocol when stack/cost work starts. This keeps unmapped-region computation consistent.
- Overrides are `ok`/`wrong` verdicts per link; "reassign a link" is not in v1.
- `Step.number` for specs without a numbered list is the single step `0` holding the whole definition text.

**Known risks:** Phabricator raw-diff fetch may need authentication for non-public revisions (Task 4 Step 5); `claude --json-schema` and `$ref` handling (Task 10 Step 5); mapping quality depends on the prompt and is tuned empirically in Task 14.
