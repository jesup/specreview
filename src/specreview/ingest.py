import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from .diffparse import parse_diff, whole_file
from .models import Patch

PHAB = "https://phabricator.services.mozilla.com"


_DIFF = re.compile(r"^(?:diff --git |@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@)", re.M)


class IngestError(Exception):
    pass


def looks_like_diff(text: str) -> bool:
    return bool(_DIFF.search(text))


def _from_text(text: str, path: str, patch_id: str) -> Patch:
    if not text.strip():
        raise IngestError("empty file")
    if "\0" in text:
        raise IngestError(f"{path}: binary file")
    if looks_like_diff(text):
        patch = parse_diff(text, patch_id)
        if not patch.files:
            raise IngestError(f"{path}: no files found in the diff")
        return patch
    return whole_file(text, path, patch_id)


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
        return _from_text(sys.stdin.read(), "stdin", "stdin")
    p = Path(path)
    if not p.is_file():
        raise IngestError(f"no such file: {path}")
    return _from_text(p.read_text(errors="replace"), path, p.stem)


def from_jj(rev: str, repo: str | None) -> Patch:
    diff = _jj(["diff", "--git", "-r", rev], repo)
    cid = _jj(["log", "-r", rev, "--no-graph", "-T", "change_id.short()"], repo).strip()
    desc = _jj(["log", "-r", rev, "--no-graph", "-T", "description"], repo)
    return parse_diff(diff, cid, desc)


def from_phab(rev: str) -> Patch:
    patch = parse_diff(_http_get(f"{PHAB}/{rev}?download=true"), rev)
    if not patch.files:
        raise IngestError(f"{rev}: no diff returned (revision not public?)")
    return patch


def load(source: str, repo: str | None = None) -> Patch:
    if re.fullmatch(r"D\d+", source):
        return from_phab(source)
    if source.startswith("jj:"):
        return from_jj(source[3:], repo)
    return from_file(source)
