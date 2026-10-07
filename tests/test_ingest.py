import io
import subprocess

import pytest

from specreview import ingest


def test_from_file(tmp_path, diff_text):
    f = tmp_path / "my.patch"
    f.write_text(diff_text)
    p = ingest.load(str(f))
    assert p.id == "my"
    assert p.files[0].path == "a/b.cpp"


def test_phab_url(monkeypatch, diff_text):
    seen = {}

    def fake_fetch(url):
        seen["url"] = url
        return diff_text

    monkeypatch.setattr(ingest, "_http_get", fake_fetch)
    p = ingest.load("D123")
    assert p.id == "D123"
    assert seen["url"] == "https://phabricator.services.mozilla.com/D123?download=true"


def test_phab_no_files(monkeypatch):
    monkeypatch.setattr(ingest, "_http_get", lambda url: "<html>login</html>")
    with pytest.raises(ingest.IngestError):
        ingest.load("D123")


def test_jj(monkeypatch, diff_text):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        out = {"diff": diff_text, "change_id.short()": "abc", "description": "Bug 1: x"}
        if "diff" in cmd:
            return subprocess.CompletedProcess(cmd, 0, out["diff"], "")
        tmpl = cmd[cmd.index("-T") + 1]
        return subprocess.CompletedProcess(cmd, 0, out[tmpl], "")

    monkeypatch.setattr(ingest.subprocess, "run", fake_run)
    p = ingest.load("jj:@-", repo="/some/repo")
    assert p.id == "abc"
    assert p.description == "Bug 1: x"
    assert all(c[0] == "jj" and "--ignore-working-copy" in c for c in calls)


def test_missing_file():
    with pytest.raises(ingest.IngestError):
        ingest.load("/no/such/file.patch")


SOURCE = "int a;\nint b;\n\nint c;\n"


def test_source_file_is_whole_file_patch(tmp_path):
    f = tmp_path / "File.cpp"
    f.write_text(SOURCE)
    p = ingest.load(str(f))
    assert p.id == "File" and p.description == ""
    assert [x.path for x in p.files] == [str(f)]
    (h,) = p.files[0].hunks
    assert h.header == "@@ -0,0 +1,4 @@"
    assert [(l.kind, l.old, l.new, l.text) for l in h.lines] == [
        ("add", None, 1, "int a;"),
        ("add", None, 2, "int b;"),
        ("add", None, 3, ""),
        ("add", None, 4, "int c;"),
    ]


def test_relative_path_kept(tmp_path, monkeypatch):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "x.cpp").write_text("int a;\n")
    monkeypatch.chdir(tmp_path)
    assert ingest.load("d/x.cpp").files[0].path == "d/x.cpp"


def test_no_trailing_newline(tmp_path):
    f = tmp_path / "x.cpp"
    f.write_text("a\nb")
    assert len(ingest.load(str(f)).files[0].hunks[0].lines) == 2


def test_stdin_source(monkeypatch):
    monkeypatch.setattr(ingest.sys, "stdin", io.StringIO(SOURCE))
    p = ingest.load("-")
    assert p.id == "stdin" and p.files[0].path == "stdin"
    assert len(p.files[0].hunks[0].lines) == 4


def test_stdin_diff(monkeypatch, diff_text):
    monkeypatch.setattr(ingest.sys, "stdin", io.StringIO(diff_text))
    assert ingest.load("-").files[0].path == "a/b.cpp"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("diff --git a/x b/x\n", True),
        ("junk\n@@ -1,2 +3,4 @@ fn\n", True),
        ("@@ -1 +1 @@\n", True),
        ("int a;\n", False),
        ("// @@ -1,2 +3,4 @@\n", False),
        ("a diff --git b\n", False),
    ],
)
def test_looks_like_diff(text, expected):
    assert ingest.looks_like_diff(text) is expected


def test_diff_without_files_is_error(tmp_path):
    f = tmp_path / "x.patch"
    f.write_text("@@ -1,2 +1,2 @@\n a\n b\n")
    with pytest.raises(ingest.IngestError, match="no files"):
        ingest.load(str(f))


def test_empty_file(tmp_path):
    f = tmp_path / "x.cpp"
    f.write_text("")
    with pytest.raises(ingest.IngestError, match="empty file"):
        ingest.load(str(f))


def test_binary_file(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"ab\x00cd\n")
    with pytest.raises(ingest.IngestError, match="binary"):
        ingest.load(str(f))
