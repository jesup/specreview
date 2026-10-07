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
