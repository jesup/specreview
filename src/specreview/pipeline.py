from .hints import extract_hints
from .mapper import PROMPT_VERSION
from .models import Analysis
from .store import Cache
from .validate import validate


NO_SPECS = "no specification found for this patch; pass --spec SPEC#anchor"


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
    no_specs_message: str = NO_SPECS,
) -> Analysis:
    refs = list(refs) or discover(patch)
    if not refs:
        raise NoSpecs(no_specs_message)
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
