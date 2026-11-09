"""Offline unit tests (no network, no LLM calls)."""
import json

import pytest

from prreviewer.backends.base import coerce_to_schema, extract_json_object, validate_against_schema
from prreviewer.backends.tools import ToolBox, _glob_to_regex
from prreviewer.diff import changed_symbols, parse_unified_diff
from prreviewer.pipeline import _match_path, aggregate_votes, dedupe, enforce_verdict
from prreviewer.post import build_payload
from prreviewer.render import render_review_html
from prreviewer.schema import FINDINGS_SCHEMA, Finding, Review
from prreviewer.server import validate_review_payload
from prreviewer.store import ReviewStore
from prreviewer.urls import parse_pr_url

DIFF = """diff --git a/pkg/mod.py b/pkg/mod.py
index 111..222 100644
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -1,5 +1,7 @@ import os
 import os
-def old(x):
-    return x
+def new(x):
+    if x is None:
+        return 0
+    return x

 VALUE = 1
@@ -20,3 +21,3 @@ def other():
     a = 1
-    b = 2
+    b = 3
     return a + b
diff --git a/new.txt b/new.txt
new file mode 100644
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+hello
+world
diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-x = 1
-y = 2
"""


def finding(**kw):
    base = dict(id="f1", lens="correctness", title="t", severity="medium", category="bug", confidence=0.8,
                path="pkg/mod.py", line=3, start_line=None, side="RIGHT", evidence="e", explanation="x", suggestion="s")
    base.update(kw)
    return Finding.from_dict(base)


# ---------------------------------------------------------------- urls
@pytest.mark.parametrize("text,expected", [
    ("https://github.com/a/b/pull/12", ("github.com", "a", "b", 12)),
    ("https://github.com/a/b/pull/12/files#diff-x", ("github.com", "a", "b", 12)),
    ("github.com/a/b.git/pull/3", ("github.com", "a", "b", 3)),
    ("a/b#7", ("github.com", "a", "b", 7)),
    ("https://ghe.corp.io/x/y/pull/9", ("ghe.corp.io", "x", "y", 9)),
])
def test_parse_pr_url(text, expected):
    r = parse_pr_url(text)
    assert (r.host, r.owner, r.repo, r.number) == expected


def test_parse_pr_url_rejects_non_pr():
    with pytest.raises(ValueError):
        parse_pr_url("https://github.com/a/b/issues/1")
    assert parse_pr_url("https://ghe.corp.io/x/y/pull/9").api_base == "https://ghe.corp.io/api/v3"


# ---------------------------------------------------------------- diff
def test_parse_diff_line_numbers_and_status():
    d = parse_unified_diff(DIFF)
    assert d.paths() == ["pkg/mod.py", "new.txt", "gone.py"]
    assert [f.status for f in d.files] == ["modified", "added", "removed"]
    f = d.get("pkg/mod.py")
    adds = [(l.new_no, l.text) for h in f.hunks for l in h.lines if l.kind == "add"]
    assert adds[0] == (2, "def new(x):") and adds[-1] == (22, "    b = 3")
    dels = [(l.old_no, l.text) for h in f.hunks for l in h.lines if l.kind == "del"]
    assert dels == [(2, "def old(x):"), (3, "    return x"), (21, "    b = 2")]
    assert d.stats() == {"files": 3, "additions": 7, "deletions": 5, "hunks": 4}


def test_commentable_and_snap():
    d = parse_unified_diff(DIFF)
    assert d.is_commentable("pkg/mod.py", "RIGHT", 2)
    assert d.is_commentable("pkg/mod.py", "LEFT", 21)
    assert d.is_commentable("pkg/mod.py", "RIGHT", 1)  # context line
    assert not d.is_commentable("pkg/mod.py", "RIGHT", 12)
    assert d.snap("pkg/mod.py", "RIGHT", 9) == ("RIGHT", 7)
    assert d.snap("pkg/mod.py", "RIGHT", 100) is None
    assert d.snap("gone.py", "RIGHT", 1) == ("LEFT", 1)  # falls back to the other side


def test_annotate_tags():
    text = parse_unified_diff(DIFF).annotate()
    assert "R2    + def new(x):" in text
    assert "L2    - def old(x):" in text
    assert "===== FILE: new.txt  (added) =====" in text


def test_changed_symbols():
    syms = changed_symbols(parse_unified_diff(DIFF))
    assert "new" in syms["pkg/mod.py"] and "old" in syms["pkg/mod.py"] and "other" in syms["pkg/mod.py"]


# ---------------------------------------------------------- json helpers
def test_extract_json_object():
    assert extract_json_object('blah ```json\n{"a": 1}\n``` tail') == {"a": 1}
    assert extract_json_object('prefix {"a": {"b": "}"}} suffix') == {"a": {"b": "}"}}
    assert extract_json_object("nothing") is None


def test_schema_validation_and_coercion():
    obj = {"findings": [{"title": "x", "severity": "HIGH", "line": "12"}], "what_is_good": []}
    coerce_to_schema(obj, FINDINGS_SCHEMA)
    assert obj["coverage_notes"] == ""
    assert obj["findings"][0]["severity"] == "high" and obj["findings"][0]["line"] == 12
    assert obj["findings"][0]["confidence"] == 0  # required field filled with a harmless default
    assert validate_against_schema(obj, FINDINGS_SCHEMA) == []
    assert any("missing" in p for p in validate_against_schema({"findings": []}, FINDINGS_SCHEMA))


# ---------------------------------------------------------- pipeline bits
def test_match_path():
    paths = ["src/pkg/mod.py", "README.md"]
    assert _match_path("b/src/pkg/mod.py", paths) == "src/pkg/mod.py"
    assert _match_path("./pkg/mod.py", paths) == "src/pkg/mod.py"
    assert _match_path("mod.py", paths) == "src/pkg/mod.py"
    assert _match_path("other.py", paths) == "other.py"


def test_dedupe_merges_same_place():
    a = finding(id="a", lens="correctness", title="None input returns 0 silently", confidence=0.6)
    b = finding(id="b", lens="tests", title="None input returns 0 silently!", line=4, confidence=0.9)
    c = finding(id="c", title="Totally different", line=22)
    out = dedupe([a, b, c])
    assert len(out) == 2
    merged = next(f for f in out if f.line in (3, 4))
    assert merged.confidence == 0.9


def test_aggregate_votes():
    v = aggregate_votes([{"verdict": "refuted", "confidence": 0.2}, {"verdict": "refuted", "confidence": 0.1}, {"verdict": "confirmed", "confidence": 0.9}], 0.8)
    assert v["verdict"] == "refuted" and v["confidence"] <= 0.3
    v = aggregate_votes([{"verdict": "confirmed", "confidence": 0.9}], 0.5)
    assert v["verdict"] == "confirmed" and 0.7 < v["confidence"] < 0.9


def test_enforce_verdict():
    assert enforce_verdict("APPROVE", [finding(severity="critical")]) == "REQUEST_CHANGES"
    assert enforce_verdict("APPROVE", [finding(severity="medium")]) == "COMMENT"
    assert enforce_verdict("COMMENT", [finding(severity="high", blocking=True)]) == "REQUEST_CHANGES"
    assert enforce_verdict("COMMENT", [finding(severity="high", blocking=True)], draft=True) == "COMMENT"
    assert enforce_verdict("bogus", []) == "COMMENT"


# ---------------------------------------------------------- posting
def make_review(findings):
    return Review(id="repo-1-abcd", created_at="now", pr={"owner": "o", "repo": "r", "number": 1, "author": "me"},
                  settings={}, verdict="COMMENT", summary="Summary.", findings=findings)


def test_build_payload_anchors_snaps_and_moves():
    d = parse_unified_diff(DIFF)
    ok = finding(id="ok", line=4, start_line=2, suggested_code="    pass")
    snapped = finding(id="snap", line=9)
    off = finding(id="off", line=200)
    filelevel = finding(id="file", line=None)
    cut = finding(id="cut", status="cut")
    cross_hunk = finding(id="xh", line=22, start_line=3)
    payload, inline, unanchored = build_payload(make_review([ok, snapped, off, filelevel, cut, cross_hunk]), d, footer=False)
    by_body = {c["line"]: c for c in payload["comments"]}
    assert {f.id for f in inline} == {"ok", "snap", "xh"}
    assert {f.id for f in unanchored} == {"off", "file"}
    assert by_body[4]["start_line"] == 2 and "```suggestion" in by_body[4]["body"]
    assert 7 in by_body  # 9 snapped to 7
    assert "start_line" not in by_body[22]  # cross-hunk range dropped
    assert "Other notes" in payload["body"]
    assert payload["event"] == "COMMENT"


# ---------------------------------------------------------- store / gui
def test_store_roundtrip_and_html(tmp_path):
    store = ReviewStore(str(tmp_path))
    r = make_review([finding()])
    store.create(r.id)
    store.init_meta(r.id, "https://github.com/o/r/pull/1", {})
    store.save_review(r, original=True)
    assert store.exists(r.id) and store.list()[0]["id"] == r.id
    loaded = store.load_review(r.id)
    assert loaded.findings[0].title == "t"
    html = render_review_html(loaded, parse_unified_diff(DIFF).to_dict(), served=True)
    assert "/*__DATA__*/" not in html and '"served": true' in html
    store.delete(r.id)
    assert not store.exists(r.id)
    with pytest.raises(ValueError):
        store.dir("../etc")


def test_validate_review_payload():
    d = make_review([finding()]).to_dict()
    d["findings"][0]["line"] = "5"
    d["findings"][0]["side"] = "weird"
    r = validate_review_payload(json.loads(json.dumps(d)))
    assert r.findings[0].line == 5 and r.findings[0].side == "RIGHT"
    d["verdict"] = "MERGE"
    with pytest.raises(ValueError):
        validate_review_payload(d)


# ---------------------------------------------------------- tools
def test_toolbox_sandbox(tmp_path):
    (tmp_path / "a.py").write_text("print(1)\n")
    tb = ToolBox(str(tmp_path))
    assert "print(1)" in tb.call("read_file", {"path": "a.py"})
    assert "escapes" in tb.call("read_file", {"path": "../../etc/passwd"})
    assert "disabled" in tb.call("run_command", {"command": "ls"}) or "unknown" in tb.call("run_command", {"command": "ls"})
    assert {s["name"] for s in tb.specs()} >= {"read_file", "grep", "list_files", "git_log", "git_blame", "git_show"}
    assert "run_command" not in {s["name"] for s in tb.specs()}


def test_glob():
    rx = _glob_to_regex("src/**/*.py")
    assert rx.match("src/a/b/c.py") and rx.match("src/c.py") and not rx.match("lib/c.py")
