import json
import tempfile
from pathlib import Path

import uvicorn

from specreview.diffparse import parse_diff
from specreview.models import Analysis, Finding, Link, Range, SpecDoc, Unmapped
from specreview.server import ReviewState, create_app
from specreview.spec import parse_steps
from specreview.store import Overrides

HERE = Path(__file__).parent
CPP = "dom/webtransport/api/WebTransportSendStream.cpp"
HDR = "dom/webtransport/api/WebTransportSendStream.h"
INI = "testing/web-platform/meta/webtransport/stream-stats.https.any.js.ini"


def doc(name="getstats_spec.json") -> SpecDoc:
    d = json.loads((HERE / name).read_text())
    return SpecDoc(
        ref=f"{d['spec']}#{d['anchor']}",
        sha=d["sha"],
        title=d["title"],
        anchor=d["anchor"],
        url=d["url"],
        steps=parse_steps(d["spec"], d["anchor"], d["content"]),
    )


def analysis(patch, d: SpecDoc, ietf: SpecDoc) -> Analysis:
    def link(number, kind, confidence, rationale, *spans):
        return Link(
            patch_id=patch.id,
            step_id=f"{d.ref}:{number}",
            ranges=[Range(file=CPP, start=a, end=b) for a, b in spans],
            kind=kind,
            confidence=confidence,
            rationale=rationale,
        )

    links = [
        link("1", "partial", 0.55, "Reads the transport via mTransport but tolerates a null transport.", (179, 179)),
        link("2", "implements", 0.95, "Creates the promise.", (175, 177)),
        link("3", "structural", 0.8, "SendGetStreamStats runs the work asynchronously via IPC.", (203, 204)),
        link("3.1", "partial", 0.4, "Gathering is delegated to the networking process.", (203, 203), (211, 212)),
        link("3.2", "structural", 0.7, "The IPC response callback stands in for the network task.", (205, 205)),
        link("3.2.1", "implements", 0.9, "Creates the stats dictionary.", (208, 208)),
        link(
            "3.2.2", "deviates", 0.75,
            "Clamps the values and falls back to cached ones instead of exposing gatheredStats directly.",
            (213, 219), (224, 225),
        ),
        link("3.2.3", "implements", 0.95, "Resolves the promise with stats.", (234, 234)),
        Link(
            patch_id=patch.id,
            step_id=f"{ietf.ref}:6.3-5",
            ranges=[Range(file=CPP, start=234, end=234)],
            kind="partial",
            confidence=0.6,
            rationale="Demo link into a prose-style IETF section.",
        ),
    ]
    unmapped = [
        Unmapped(
            patch_id=patch.id,
            ranges=[Range(file=CPP, start=180, end=190)],
            reason="error-path",
            note="Resolves with cached stats when there is no IPC actor.",
        ),
        Unmapped(
            patch_id=patch.id,
            ranges=[Range(file=CPP, start=236, end=243), Range(file=HDR, start=86, end=87), Range(file=HDR, start=211, end=212)],
            reason="plumbing",
            note="IPC rejection handler and cached-stat accessors and members.",
        ),
        Unmapped(
            patch_id=patch.id,
            ranges=[Range(file=INI, side="old", start=2, end=13)],
            reason="test",
            note="Removes expected-FAIL annotations for the now-passing getStats tests.",
        ),
    ]
    findings = [
        Finding(patch_id=patch.id, text="Step 4 (return p) is not mentioned by the code comments.", severity="info", step_id=f"{d.ref}:4"),
        Finding(patch_id=patch.id, text="Clamping deviates from the spec algorithm.", severity="warn", step_id=f"{d.ref}:3.2.2"),
    ]
    return Analysis(patch_id=patch.id, docs=[d, ietf], links=links, unmapped=unmapped, findings=findings, warnings=["demo data"])


def main():
    patch = parse_diff((HERE / "getstats.patch").read_text(), "demo")
    result = analysis(patch, doc(), doc("ietf_wt_stop_sending.json"))
    root = Path(tempfile.mkdtemp(prefix="specreview-demo-"))
    state = ReviewState(patch, result, Overrides(root, patch), lambda refs: result)
    uvicorn.run(create_app(state), host="127.0.0.1", port=8766)


if __name__ == "__main__":
    main()
