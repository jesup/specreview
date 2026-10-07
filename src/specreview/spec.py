import json
import re
import subprocess

from .models import SpecDoc, Step

_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_TAIL_LINK = re.compile(r"\]\(https?://[^)]*\)")
_PILCROW = re.compile(r"\[\u00b6\]\([^)#]*#([^)]*)\)")
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>~])")
_ITEM = re.compile(r"^(\s*)(\d+)\.\s+(.*)$")
_LABEL = re.compile(r"^([^.:]{1,40}:)(\s|$)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[])")
TITLE_MAX = 70


class SpecError(Exception):
    pass


def _clean(text: str) -> str:
    text = _PILCROW.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _TAIL_LINK.sub("]", text)
    text = re.sub(r"(?<!\\)[*`]", "", text)
    return re.sub(r"\s+", " ", _ESCAPE.sub(r"\1", text)).strip()


def _title(text: str) -> str:
    first = _SENTENCE_END.split(text, maxsplit=1)[0]
    if len(first) <= TITLE_MAX:
        return first
    cut = first[:TITLE_MAX]
    if first[TITLE_MAX] != " " and " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:") + "\u2026"


def _parse_list(spec: str, anchor: str, content: str) -> list[Step]:
    steps: list[Step] = []
    texts: list[str] = []
    stack: list[str] = []
    lists = 0
    prefix = ""
    group = ""
    intro = ""
    for line in content.splitlines():
        m = _ITEM.match(line)
        if m:
            depth = len(m.group(1).replace("\t", "    ")) // 4
            top = depth == 0
            if top and (not stack or int(m.group(2)) <= int(stack[0])):
                lists += 1
                prefix = f"L{lists}/" if lists > 1 else ""
                group = _title(_clean(intro)) if intro else ""
            del stack[depth:]
            stack.append(m.group(2))
            parent = ".".join(stack[:-1]) or None
            steps.append(
                Step(
                    spec=spec,
                    anchor=anchor,
                    number=prefix + ".".join(stack),
                    text="",
                    parent=f"{spec}#{anchor}:{prefix}{parent}" if parent else None,
                    depth=len(stack) - 1,
                    group=group,
                )
            )
            texts.append(m.group(3))
        elif steps and line.strip() and line[0] in " \t":
            texts[-1] += " " + line.strip()
        elif line.strip():
            intro = line
    out = []
    for s, t in zip(steps, texts):
        text = _clean(t)
        out.append(s.model_copy(update={"text": text, "title": _title(text)}))
    return out


def _prose_step(spec: str, anchor: str, number: str, target: str | None, blocks: list[str]) -> Step:
    cleaned = [c for c in map(_clean, blocks) if c]
    text = " ".join(cleaned)
    label = _LABEL.match(cleaned[0]) if cleaned else None
    title = label.group(1) if label else _title(cleaned[-1] if cleaned else "")
    return Step(spec=spec, anchor=anchor, number=number, text=text, title=title, target=target)


def _parse_prose(spec: str, anchor: str, content: str) -> list[Step]:
    blocks = [b for b in re.split(r"\n\s*\n", content) if b.strip()]
    steps: list[Step] = []
    pending: list[str] = []
    for block in blocks:
        pending.append(block)
        m = _PILCROW.search(block)
        if m:
            frag = m.group(1)
            number = frag.removeprefix("section-")
            steps.append(_prose_step(spec, anchor, number, frag, pending))
            pending = []
    if steps:
        if pending:
            last = steps[-1]
            joined = " ".join(filter(None, [last.text, *map(_clean, pending)]))
            steps[-1] = last.model_copy(update={"text": joined})
        return steps
    if len(blocks) <= 1:
        return [_prose_step(spec, anchor, "0", None, blocks)]
    return [_prose_step(spec, anchor, str(i), None, [b]) for i, b in enumerate(blocks, 1)]


def parse_steps(spec: str, anchor: str, content: str) -> list[Step]:
    return _parse_list(spec, anchor, content) or _parse_prose(spec, anchor, content)


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
        anchor=d["anchor"],
        url=d["url"],
        steps=parse_steps(d["spec"], d["anchor"], d["content"] or d["title"]),
    )


def spec_exists(ref: str) -> bool:
    p = subprocess.run(["webspec-index", "exists", ref], capture_output=True, text=True)
    return p.returncode == 0
