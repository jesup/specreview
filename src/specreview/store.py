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


class NullCache(Cache):
    def __init__(self):
        pass

    def key(self, patch: Patch, docs: list[SpecDoc], prompt_version: str, model: str | None) -> str:
        return ""

    def get(self, key: str) -> Analysis | None:
        return None

    def put(self, key: str, analysis: Analysis) -> None:
        pass


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
