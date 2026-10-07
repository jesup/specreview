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


def whole_file(text: str, path: str, patch_id: str) -> Patch:
    lines = [
        DiffLine(kind="add", old=None, new=i, text=t)
        for i, t in enumerate(text.splitlines(), 1)
    ]
    hunk = Hunk(header=f"@@ -0,0 +1,{len(lines)} @@", lines=lines)
    return Patch(id=patch_id, description="", files=[FileDiff(path=path, hunks=[hunk])])
