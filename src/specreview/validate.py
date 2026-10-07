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
