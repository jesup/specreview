from pathlib import Path
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .claude import ClaudeError
from .coverage import line_status, step_status, summarize
from .models import Analysis, ComplianceResult, Patch
from .store import Overrides, link_key

STATIC = Path(__file__).parent / "static"


class ReviewState:
    def __init__(self, patch: Patch, analysis: Analysis, overrides: Overrides,
                 rerun: Callable[[list[str]], Analysis],
                 checker: Callable[[str, bool], ComplianceResult] | None = None,
                 ai_enabled: bool = False,
                 cached_checks: Callable[[], dict[str, ComplianceResult]] | None = None):
        self.patch = patch
        self.analysis = analysis
        self.overrides = overrides
        self.rerun = rerun
        self.checker = checker
        self.ai_enabled = ai_enabled
        self.cached_checks = cached_checks


class RerunBody(BaseModel):
    specs: list[str]


class CheckBody(BaseModel):
    step_id: str
    force: bool = False


class VerdictBody(BaseModel):
    verdict: Literal["ok", "wrong"] | None


def build_payload(state: ReviewState) -> dict:
    a = state.analysis
    st = step_status(a.steps(), a.links)
    ls = line_status(state.patch, a.links, a.unmapped)
    return {
        "patch": state.patch.model_dump(),
        "analysis": a.model_dump(),
        "step_status": st,
        "line_status": ls,
        "summary": summarize(st, ls, state.patch),
        "overrides": state.overrides.all(),
        "link_keys": [link_key(l) for l in a.links],
        "ai_enabled": state.ai_enabled,
        "checks": {k: v.model_dump() for k, v in (state.cached_checks() if state.cached_checks else {}).items()},
    }


def create_app(state: ReviewState) -> FastAPI:
    app = FastAPI()

    @app.get("/api/review")
    def review():
        return build_payload(state)

    @app.post("/api/rerun")
    def rerun(body: RerunBody):
        state.analysis = state.rerun(body.specs)
        return build_payload(state)

    @app.post("/api/check")
    def check(body: CheckBody):
        if not state.ai_enabled or state.checker is None:
            raise HTTPException(409, "AI checks are not available in manual mode")
        if body.step_id not in {s.id for s in state.analysis.steps()}:
            raise HTTPException(404, f"unknown step {body.step_id}")
        try:
            return state.checker(body.step_id, body.force)
        except ClaudeError as e:
            raise HTTPException(502, str(e))

    @app.put("/api/overrides/{key}")
    def override(key: str, body: VerdictBody):
        state.overrides.set(key, body.verdict)
        return {"ok": True}

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
