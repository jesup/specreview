import argparse
import sys
import threading
import webbrowser
from functools import partial
from pathlib import Path

import uvicorn

from . import ingest
from .annotations import AnnotationMapper, annotation_refs
from .compliance import ComplianceChecker
from .discovery import discover_refs
from .mapper import ClaudeMapper
from .pipeline import NoSpecs, analyze
from .server import ReviewState, create_app
from .spec import fetch_spec
from .store import Cache, NullCache, Overrides

EXAMPLES = Path(__file__).parent / "examples"
EXAMPLE_PATCH = EXAMPLES / "manual_example.patch"
EXAMPLE_FILE = EXAMPLES / "manual_example.cpp"


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="specreview", description="Review a patch against the spec it implements")
    p.add_argument("source", nargs="?", help="D12345, jj:<revset>, patch or source file, or - for stdin")
    p.add_argument("--spec", action="append", default=[], help="SPEC#anchor or URL; repeatable")
    p.add_argument("--repo", help="repository for jj and for Claude's context")
    p.add_argument("--model", help="claude model alias")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--refresh", action="store_true", help="ignore cached results and replace them")
    p.add_argument("--no-cache", action="store_true", help="neither read nor write the cache")
    p.add_argument("--manual", "--no-ai", dest="manual", action="store_true", help="map from <spec> annotations in the patch, without Claude")
    p.add_argument("--example", action="store_true", help="show the bundled manual-mode example (implies --manual)")
    p.add_argument("--example-file", action="store_true", help="show the bundled whole-file manual-mode example (implies --manual)")
    p.add_argument("--no-open", action="store_true")
    args = p.parse_args(argv)
    if args.example or args.example_file:
        args.manual = True
        example = EXAMPLE_FILE if args.example_file else EXAMPLE_PATCH
        args.source = args.source or str(example)
    elif args.source is None:
        p.error("a source, --example or --example-file is required")
    return args


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def compliance_hooks(state, checker, cache, model):
    def locate(step_id):
        doc = next(d for d in state.analysis.docs if any(s.id == step_id for s in d.steps))
        step = next(s for s in doc.steps if s.id == step_id)
        return doc, step

    def check(step_id, force):
        doc, step = locate(step_id)
        key = cache.compliance_key(state.patch, doc, step_id, model)
        if not force and (hit := cache.get_compliance(key)):
            return hit
        links = [l for l in state.analysis.links if l.step_id == step_id]
        result = checker.check(state.patch, doc, step, links)
        cache.put_compliance(key, result)
        return result

    def cached():
        found = {}
        for doc in state.analysis.docs:
            for s in doc.steps:
                if hit := cache.get_compliance(cache.compliance_key(state.patch, doc, s.id, model)):
                    found[s.id] = hit
        return found

    return check, cached


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

    if args.manual:
        opts = dict(
            mapper=AnnotationMapper(default_url=args.spec[0] if len(args.spec) == 1 else None),
            cache=NullCache(),
            discover=annotation_refs,
            model=None,
            no_specs_message="no specification found; add <spec> URL blocks to the patch or pass --spec",
        )
    else:
        opts = dict(
            mapper=ClaudeMapper(repo=args.repo, model=args.model),
            cache=NullCache() if args.no_cache else Cache(),
            discover=partial(discover_refs, repo=args.repo, model=args.model),
            model=args.model,
        )
    run = partial(analyze, patch, fetch=fetch_spec, **opts)

    def rerun(refs):
        log(f"analyzing against {', '.join(refs)} ...")
        return run(refs, force=True)

    try:
        log(
            "manual mode (no AI)"
            if args.manual
            else "analyzing (this can take a few minutes on first run) ..."
        )
        analysis = run(args.spec, force=args.refresh)
    except NoSpecs as e:
        log(f"error: {e}")
        return 1

    state = ReviewState(patch, analysis, Overrides(None, patch), rerun)
    if not args.manual:
        checker = ComplianceChecker(repo=args.repo, model=args.model)
        state.checker, state.cached_checks = compliance_hooks(state, checker, opts["cache"], args.model)
        state.ai_enabled = True
    app = create_app(state)
    url = f"http://127.0.0.1:{args.port}/"
    log(f"serving {url}")
    if not args.no_open:
        threading.Timer(1.0, webbrowser.open, args=[url]).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0
