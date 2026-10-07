from specreview.diffparse import parse_diff


def test_parse_lines_and_numbers(patch):
    assert [f.path for f in patch.files] == ["a/b.cpp"]
    lines = patch.files[0].hunks[0].lines
    got = [(l.kind, l.old, l.new, l.text) for l in lines]
    assert got == [
        ("ctx", 1, 1, "keep"),
        ("del", 2, None, "old"),
        ("add", None, 2, "new1"),
        ("add", None, 3, "new2"),
        ("ctx", 3, 4, "tail"),
    ]
    assert patch.description == "desc"


def test_new_file_and_multiple_files():
    text = """diff --git a/x.h b/x.h
new file mode 100644
--- /dev/null
+++ b/x.h
@@ -0,0 +1,2 @@
+a
+b
diff --git a/y.cpp b/y.cpp
--- a/y.cpp
+++ b/y.cpp
@@ -5 +5 @@
-p
\\ No newline at end of file
+q
"""
    p = parse_diff(text, "p")
    assert [f.path for f in p.files] == ["x.h", "y.cpp"]
    assert [l.new for l in p.files[0].hunks[0].lines] == [1, 2]
    y = p.files[1].hunks[0].lines
    assert [(l.kind, l.old, l.new) for l in y] == [("del", 5, None), ("add", None, 5)]


def test_content_that_looks_like_headers_stays_in_hunk():
    text = """diff --git a/z b/z
--- a/z
+++ b/z
@@ -1,2 +1,2 @@
--- not a header
+++ also content
"""
    p = parse_diff(text, "p")
    assert len(p.files[0].hunks[0].lines) == 2


def test_whole_file():
    from specreview.diffparse import whole_file

    p = whole_file("a\nb\n", "p/x.cpp", "x")
    assert p.id == "x"
    (h,) = p.files[0].hunks
    assert h.header == "@@ -0,0 +1,2 @@"
    assert [l.new for l in h.lines] == [1, 2]
    assert all(l.kind == "add" and l.old is None for l in h.lines)
