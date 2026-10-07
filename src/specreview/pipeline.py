from datetime import datetime, timezone

from .hints import extract_hints
from .mapper import PROMPT_VERSION
from .models import Analysis
from .store import Cache
from .validate import validate


NO_SPECS = "no specification found for this patch; pass --spec SPEC#anchor"


class NoSpecs(Exception):
    pass


def _discover(patch, cache: Cache, discover, model, force) -> list[str]:
    key = cache.discovery_key(patch, model)
    if not force and (hit := cache.get_refs(key)):
        return hit
    refs = discover(patch)
    if refs:
        cache.put_refs(key, refs)
    return refs


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
    no_specs_message: str = NO_SPECS,
) -> Analysis:
    refs = list(refs) or _discover(patch, cache, discover, model, force)
    if not refs:
        raise NoSpecs(no_specs_message)
    docs = [fetch(r) for r in refs]
    key = cache.key(patch, docs, PROMPT_VERSION, model)
    if not force and (hit := cache.get(key)):
        return hit.model_copy(update={"cached": True})
    hints = extract_hints(patch)
    out, warnings = validate(patch, docs, mapper.map(patch, docs, hints), hints)
    analysis = Analysis(
        patch_id=patch.id,
        docs=docs,
        links=out.links,
        unmapped=out.unmapped,
        findings=out.findings,
        warnings=warnings,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    cache.put(key, analysis)
    return analysis
