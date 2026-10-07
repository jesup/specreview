from pathlib import Path
from typing import Callable, Literal

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .coverage import line_status, step_status, summarize
from .models import Analysis, Patch
from .store import Overrides, link_key

STATIC = Path(__file__).parent / "static"


class ReviewState:
    def __init__(self, patch: Patch, analysis: Analysis, overrides: Overrides,
                 rerun: Callable[[list[str]], Analysis]):
        self.patch = patch
        self.analysis = analysis
        self.overrides = overrides
        self.rerun = rerun


class RerunBody(BaseModel):
    specs: list[str]


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

    @app.put("/api/overrides/{key}")
    def override(key: str, body: VerdictBody):
        state.overrides.set(key, body.verdict)
        return {"ok": True}

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
