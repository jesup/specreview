import hashlib
import json
import os
from pathlib import Path

from .compliance import COMPLIANCE_PROMPT_VERSION
from .discovery import DISCOVERY_PROMPT_VERSION
from .models import Analysis, ComplianceResult, Link, Patch, SpecDoc


def default_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "specreview"


def link_key(link: Link) -> str:
    data = link.step_id + link.model_dump_json(include={"ranges"})
    return hashlib.sha1(data.encode()).hexdigest()[:12]


class Cache:
    def __init__(self, root: Path | None = None):
        base = root or default_root()
        self.dir = base / "analysis"
        self.compliance_dir = base / "compliance"
        self.discovery_dir = base / "discovery"

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

    def discovery_key(self, patch: Patch, model: str | None) -> str:
        parts = [patch.digest(), DISCOVERY_PROMPT_VERSION, model or ""]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]

    def get_refs(self, key: str) -> list[str] | None:
        p = self.discovery_dir / f"{key}.json"
        return json.loads(p.read_text()) if p.exists() else None

    def put_refs(self, key: str, refs: list[str]) -> None:
        self.discovery_dir.mkdir(parents=True, exist_ok=True)
        (self.discovery_dir / f"{key}.json").write_text(json.dumps(refs))

    def compliance_key(self, patch: Patch, doc: SpecDoc, step_id: str, model: str | None) -> str:
        parts = [patch.digest(), f"{doc.ref}@{doc.sha}", step_id, COMPLIANCE_PROMPT_VERSION, model or ""]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]

    def get_compliance(self, key: str) -> ComplianceResult | None:
        p = self.compliance_dir / f"{key}.json"
        return ComplianceResult.model_validate_json(p.read_text()) if p.exists() else None

    def put_compliance(self, key: str, result: ComplianceResult) -> None:
        self.compliance_dir.mkdir(parents=True, exist_ok=True)
        (self.compliance_dir / f"{key}.json").write_text(result.model_dump_json())


class NullCache(Cache):
    def __init__(self):
        pass

    def key(self, patch: Patch, docs: list[SpecDoc], prompt_version: str, model: str | None) -> str:
        return ""

    def get(self, key: str) -> Analysis | None:
        return None

    def put(self, key: str, analysis: Analysis) -> None:
        pass

    def discovery_key(self, patch: Patch, model: str | None) -> str:
        return ""

    def get_refs(self, key: str) -> list[str] | None:
        return None

    def put_refs(self, key: str, refs: list[str]) -> None:
        pass

    def compliance_key(self, patch: Patch, doc: SpecDoc, step_id: str, model: str | None) -> str:
        return ""

    def get_compliance(self, key: str) -> ComplianceResult | None:
        return None

    def put_compliance(self, key: str, result: ComplianceResult) -> None:
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
