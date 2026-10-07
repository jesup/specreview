import pytest

from specreview.diffparse import parse_diff
from specreview.models import Analysis, Link, Range, SpecDoc, Step

DIFF = """diff --git a/a/b.cpp b/a/b.cpp
index 1..2 100644
--- a/a/b.cpp
+++ b/a/b.cpp
@@ -1,3 +1,4 @@ void f()
 keep
-old
+new1
+new2
 tail
"""


@pytest.fixture
def diff_text():
    return DIFF


@pytest.fixture
def patch():
    return parse_diff(DIFF, "p1", "desc")


@pytest.fixture
def doc():
    def step(number, text, parent=None):
        return Step(
            spec="S", anchor="alg", number=number, text=text, parent=parent,
            title=text, depth=number.count("."),
        )

    return SpecDoc(
        ref="S#alg",
        sha="sha1",
        title="alg",
        anchor="alg",
        url="https://example.test/#alg",
        steps=[
            step("1", "Let p be a new promise"),
            step("2", "Run in parallel"),
            step("2.1", "Gather stats", "S#alg:2"),
            step("3", "Return p"),
        ],
    )


@pytest.fixture
def analysis(doc):
    link = Link(
        patch_id="p1",
        step_id="S#alg:1",
        ranges=[Range(file="a/b.cpp", start=2, end=2)],
        kind="implements",
        confidence=0.9,
        rationale="creates the promise",
    )
    return Analysis(patch_id="p1", docs=[doc], links=[link])
