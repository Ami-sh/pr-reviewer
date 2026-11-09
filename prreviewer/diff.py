"""Unified-diff parsing, line-number annotation, and comment anchoring.

GitHub anchors review comments by (path, side, line) where `line` is a line
number in the OLD file (side=LEFT, removed lines) or NEW file (side=RIGHT,
added or context lines) and the line must appear inside a diff hunk.  We parse
the diff once and expose:

  * DiffFile / Hunk / DiffLine with old/new numbers,
  * `commentable()`  -> set of (path, side, line) GitHub will accept,
  * `annotate()`     -> the diff re-rendered with `L<n>` / `R<n>` tags so the
                        model can cite exact anchors (PR-Agent style),
  * `snap()`         -> nearest valid anchor for a slightly-off line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_DIFF_GIT_RE = re.compile(r'^diff --git (?:"?a/(.+?)"?) (?:"?b/(.+?)"?)$')


@dataclass
class DiffLine:
    kind: str  # 'add' | 'del' | 'ctx' | 'meta'
    text: str  # without the leading +/-/space
    old_no: Optional[int]
    new_no: Optional[int]

    @property
    def side(self) -> str:
        return "LEFT" if self.kind == "del" else "RIGHT"

    @property
    def line(self) -> Optional[int]:
        return self.old_no if self.kind == "del" else self.new_no


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    header: str  # function context after @@
    lines: List[DiffLine] = field(default_factory=list)

    @property
    def new_end(self) -> int:
        return self.new_start + max(self.new_len, 1) - 1

    @property
    def old_end(self) -> int:
        return self.old_start + max(self.old_len, 1) - 1


@dataclass
class DiffFile:
    path: str  # new path (or old path if deleted)
    old_path: Optional[str]
    status: str  # added | removed | modified | renamed
    is_binary: bool = False
    hunks: List[Hunk] = field(default_factory=list)
    mode_change: Optional[str] = None

    @property
    def additions(self) -> int:
        return sum(1 for h in self.hunks for l in h.lines if l.kind == "add")

    @property
    def deletions(self) -> int:
        return sum(1 for h in self.hunks for l in h.lines if l.kind == "del")


class ParsedDiff:
    def __init__(self, files: List[DiffFile]):
        self.files = files
        self._by_path: Dict[str, DiffFile] = {f.path: f for f in files}
        self._commentable: Optional[Set[Tuple[str, str, int]]] = None

    def __iter__(self) -> Iterable[DiffFile]:
        return iter(self.files)

    def get(self, path: str) -> Optional[DiffFile]:
        return self._by_path.get(path)

    def paths(self) -> List[str]:
        return [f.path for f in self.files]

    # --------------------------------------------------------- anchoring
    def commentable(self) -> Set[Tuple[str, str, int]]:
        if self._commentable is None:
            s: Set[Tuple[str, str, int]] = set()
            for f in self.files:
                for h in f.hunks:
                    for l in h.lines:
                        if l.kind == "add" or l.kind == "ctx":
                            s.add((f.path, "RIGHT", l.new_no))
                        if l.kind == "del" or l.kind == "ctx":
                            s.add((f.path, "LEFT", l.old_no))
            self._commentable = s
        return self._commentable

    def is_commentable(self, path: str, side: str, line: Optional[int]) -> bool:
        return line is not None and (path, side, line) in self.commentable()

    def snap(self, path: str, side: str, line: Optional[int], radius: int = 3) -> Optional[Tuple[str, int]]:
        """Return (side, line) of the nearest commentable anchor within `radius`
        lines (preferring the requested side, then the other side at the same
        number), or None."""
        if line is None:
            return None
        c = self.commentable()
        if (path, side, line) in c:
            return side, line
        other = "LEFT" if side == "RIGHT" else "RIGHT"
        if (path, other, line) in c:
            return other, line
        for d in range(1, radius + 1):
            for cand in (line + d, line - d):
                if (path, side, cand) in c:
                    return side, cand
        return None

    def hunk_for(self, path: str, side: str, line: int) -> Optional[Hunk]:
        f = self.get(path)
        if not f:
            return None
        for h in f.hunks:
            for l in h.lines:
                if l.side == side and l.line == line:
                    return h
                if l.kind == "ctx" and side == "LEFT" and l.old_no == line:
                    return h
        return None

    # ---------------------------------------------------------- rendering
    def annotate(self, paths: Optional[Iterable[str]] = None, max_chars: Optional[int] = None) -> str:
        """Re-render the diff with explicit anchors.

        Each line is prefixed with a tag: `R123 +` (added line 123 in the new
        file), `R123  ` (context line, new-file numbering) or `L45 -` (removed
        line 45 in the old file).  To cite a line a reviewer uses path + the
        tag, which maps 1:1 to GitHub's (side, line).
        """
        want = set(paths) if paths is not None else None
        out: List[str] = []
        for f in self.files:
            if want is not None and f.path not in want:
                continue
            out.append(f"\n===== FILE: {f.path}  ({f.status}{', binary' if f.is_binary else ''}"
                       f"{', was ' + f.old_path if f.old_path and f.old_path != f.path else ''}) =====")
            if f.is_binary or not f.hunks:
                out.append("(no textual hunks)")
                continue
            for h in f.hunks:
                out.append(f"@@ -{h.old_start},{h.old_len} +{h.new_start},{h.new_len} @@{h.header}")
                for l in h.lines:
                    if l.kind == "add":
                        out.append(f"R{l.new_no:<5d}+ {l.text}")
                    elif l.kind == "del":
                        out.append(f"L{l.old_no:<5d}- {l.text}")
                    elif l.kind == "ctx":
                        out.append(f"R{l.new_no:<5d}  {l.text}")
                    else:
                        out.append(f"       {l.text}")
        text = "\n".join(out).lstrip("\n")
        if max_chars is not None and len(text) > max_chars:
            text = text[:max_chars] + f"\n\n... [diff truncated at {max_chars} chars; use tools to read the rest] ..."
        return text

    def context_snippet(self, path: str, side: str, line: int, before: int = 4, after: int = 4) -> str:
        """Annotated lines around an anchor (for the GUI and verifier prompts)."""
        f = self.get(path)
        if not f:
            return ""
        for h in f.hunks:
            idx = None
            for i, l in enumerate(h.lines):
                if (l.side == side and l.line == line) or (l.kind == "ctx" and l.old_no == line and side == "LEFT"):
                    idx = i
                    break
            if idx is None:
                continue
            lo, hi = max(0, idx - before), min(len(h.lines), idx + after + 1)
            rows = []
            for l in h.lines[lo:hi]:
                mark = ">>" if l is h.lines[idx] else "  "
                if l.kind == "add":
                    rows.append(f"{mark} R{l.new_no:<5d}+ {l.text}")
                elif l.kind == "del":
                    rows.append(f"{mark} L{l.old_no:<5d}- {l.text}")
                else:
                    rows.append(f"{mark} R{l.new_no:<5d}  {l.text}")
            return "\n".join(rows)
        return ""

    def stats(self) -> Dict[str, int]:
        return {
            "files": len(self.files),
            "additions": sum(f.additions for f in self.files),
            "deletions": sum(f.deletions for f in self.files),
            "hunks": sum(len(f.hunks) for f in self.files),
        }

    def to_dict(self) -> Dict:
        return {
            "files": [
                {
                    "path": f.path,
                    "old_path": f.old_path,
                    "status": f.status,
                    "is_binary": f.is_binary,
                    "hunks": [
                        {
                            "old_start": h.old_start,
                            "old_len": h.old_len,
                            "new_start": h.new_start,
                            "new_len": h.new_len,
                            "header": h.header,
                            "lines": [
                                {"kind": l.kind, "text": l.text, "old_no": l.old_no, "new_no": l.new_no} for l in h.lines
                            ],
                        }
                        for h in f.hunks
                    ],
                }
                for f in self.files
            ]
        }


def _unquote(p: str) -> str:
    p = p.strip()
    if p.startswith('"') and p.endswith('"'):
        p = p[1:-1]
    return p


def parse_unified_diff(text: str) -> ParsedDiff:
    files: List[DiffFile] = []
    cur: Optional[DiffFile] = None
    hunk: Optional[Hunk] = None
    old_no = new_no = 0
    old_left = new_left = 0  # remaining counts in the hunk

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        m = _DIFF_GIT_RE.match(raw)
        if m:
            a, b = _unquote(m.group(1)), _unquote(m.group(2))
            cur = DiffFile(path=b, old_path=a, status="modified")
            files.append(cur)
            hunk = None
            continue
        if cur is None:
            # diff may start without `diff --git` (e.g. assembled)
            if raw.startswith("--- "):
                cur = DiffFile(path="", old_path=None, status="modified")
                files.append(cur)
            else:
                continue
        if raw.startswith("new file mode"):
            cur.status = "added"
            continue
        if raw.startswith("deleted file mode"):
            cur.status = "removed"
            continue
        if raw.startswith("rename from "):
            cur.old_path = _unquote(raw[len("rename from "):])
            cur.status = "renamed"
            continue
        if raw.startswith("rename to "):
            cur.path = _unquote(raw[len("rename to "):])
            cur.status = "renamed"
            continue
        if raw.startswith("similarity index") or raw.startswith("index ") or raw.startswith("old mode") or raw.startswith("new mode"):
            if raw.startswith("old mode") or raw.startswith("new mode"):
                cur.mode_change = (cur.mode_change or "") + raw + ";"
            continue
        if raw.startswith("Binary files") or raw.startswith("GIT binary patch"):
            cur.is_binary = True
            continue
        if raw.startswith("--- "):
            p = raw[4:].split("\t")[0]
            if p == "/dev/null":
                cur.status = "added"
                cur.old_path = None
            else:
                p = _unquote(p)
                cur.old_path = p[2:] if p.startswith("a/") else p
            continue
        if raw.startswith("+++ "):
            p = raw[4:].split("\t")[0]
            if p == "/dev/null":
                cur.status = "removed"
                cur.path = cur.old_path or cur.path
            else:
                p = _unquote(p)
                cur.path = p[2:] if p.startswith("b/") else p
            continue
        hm = _HUNK_RE.match(raw)
        if hm:
            old_start = int(hm.group(1))
            old_len = int(hm.group(2)) if hm.group(2) is not None else 1
            new_start = int(hm.group(3))
            new_len = int(hm.group(4)) if hm.group(4) is not None else 1
            hunk = Hunk(old_start, old_len, new_start, new_len, hm.group(5) or "")
            cur.hunks.append(hunk)
            old_no, new_no = old_start, new_start
            old_left, new_left = old_len, new_len
            continue
        if hunk is None:
            continue
        if raw.startswith("\\ No newline at end of file"):
            hunk.lines.append(DiffLine("meta", raw, None, None))
            continue
        if raw.startswith("+"):
            hunk.lines.append(DiffLine("add", raw[1:], None, new_no))
            new_no += 1
            new_left -= 1
        elif raw.startswith("-"):
            hunk.lines.append(DiffLine("del", raw[1:], old_no, None))
            old_no += 1
            old_left -= 1
        elif raw.startswith(" ") or raw == "":
            hunk.lines.append(DiffLine("ctx", raw[1:] if raw else "", old_no, new_no))
            old_no += 1
            new_no += 1
            old_left -= 1
            new_left -= 1
        else:
            # unexpected content inside a hunk — tolerate
            hunk.lines.append(DiffLine("meta", raw, None, None))
        if old_left <= 0 and new_left <= 0:
            hunk = None  # hunk complete; anything else before next @@ is metadata
    # drop files with empty path (malformed)
    files = [f for f in files if f.path]
    return ParsedDiff(files)


# ------------------------------------------------- changed-symbol extraction

_DEF_RES = [
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)"),  # python
    re.compile(r"^\s*class\s+([A-Za-z_]\w*)"),
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"),  # js/ts
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\(|[A-Za-z_$][\w$]*\s*=>)"),
    re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)"),  # rust
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"),  # go
    re.compile(r"^\s*(?:public|private|protected|static|final|abstract|\s)*[\w<>\[\],\s]+\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*(?:throws[^{]*)?\{"),  # java/c#
]


def changed_symbols(diff: ParsedDiff) -> Dict[str, List[str]]:
    """Cheap, language-agnostic guess of which functions/classes a diff touches:
    names from hunk headers (git's function context) plus def/class lines that
    are added/removed."""
    out: Dict[str, List[str]] = {}
    for f in diff.files:
        names: List[str] = []
        for h in f.hunks:
            hdr = h.header.strip()
            if hdr:
                m = re.search(r"([A-Za-z_][\w$]*)\s*\(", hdr) or re.search(r"(?:class|def|func|fn)\s+([A-Za-z_][\w$]*)", hdr)
                if m:
                    names.append(m.group(1))
            for l in h.lines:
                if l.kind in ("add", "del"):
                    for rx in _DEF_RES:
                        m = rx.match(l.text)
                        if m:
                            names.append(m.group(1))
                            break
        seen = set()
        uniq = [n for n in names if not (n in seen or seen.add(n))]
        if uniq:
            out[f.path] = uniq
    return out
