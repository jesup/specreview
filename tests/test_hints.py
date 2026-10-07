from specreview.diffparse import parse_diff
from specreview.hints import Hint, extract_hints, spec_urls

TEXT = """diff --git a/f.cpp b/f.cpp
--- a/f.cpp
+++ b/f.cpp
@@ -1,1 +1,6 @@
 ctx
+// https://w3c.github.io/webtransport/#dom-webtransportsendstream-getstats.
+// Step 2.2.1: Let stats be a new object.
+  // Not in spec: IPC actor is gone.
+  x = 1;  // see https://github.com/foo/bar/issues/1#issuecomment-2
+// Step 3: Return p.
"""


def test_hints():
    p = parse_diff(TEXT, "p")
    assert extract_hints(p) == [
        Hint(file="f.cpp", line=3, step="2.2.1", not_in_spec=False),
        Hint(file="f.cpp", line=4, step=None, not_in_spec=True),
        Hint(file="f.cpp", line=6, step="3", not_in_spec=False),
    ]


def test_spec_urls_include_description_and_dedupe():
    p = parse_diff(TEXT, "p", "see https://w3c.github.io/webtransport/#dom-webtransportsendstream-getstats")
    assert spec_urls(p) == [
        "https://w3c.github.io/webtransport/#dom-webtransportsendstream-getstats",
        "https://github.com/foo/bar/issues/1#issuecomment-2",
    ]
