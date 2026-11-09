"""Data model for pr-reviewer.

Everything that flows between pipeline stages, the local store, the HTML GUI and
GitHub is defined here as plain dataclasses + the JSON Schemas we hand to the LLM
backends for structured output.  Keep this file dependency-free.

Vocabulary
----------
Severity   critical > high > medium > low > nit
Category   bug | reward_hacking | test_gap | security | design | performance |
           docs | style | question | praise
Side       "RIGHT" = the new file (added/context lines), "LEFT" = the old file
           (removed lines) — exactly the GitHub review-comment API semantics.
Verdict    APPROVE | COMMENT | REQUEST_CHANGES (GitHub review "event").
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

SEVERITIES = ["critical", "high", "medium", "low", "nit"]
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}
CATEGORIES = [
    "bug",
    "reward_hacking",
    "test_gap",
    "security",
    "design",
    "performance",
    "docs",
    "style",
    "question",
    "praise",
]
VERDICTS = ["APPROVE", "COMMENT", "REQUEST_CHANGES"]
SIDES = ["LEFT", "RIGHT"]


def _asdict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {k: _asdict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_asdict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _asdict(v) for k, v in obj.items()}
    return obj


class _Base:
    def to_dict(self) -> Dict[str, Any]:
        return _asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# --------------------------------------------------------------------------- PR


@dataclass
class PRInfo(_Base):
    """Everything we fetched about the PR (stored as data/pr.json)."""

    host: str
    owner: str
    repo: str
    number: int
    url: str
    title: str
    body: str
    author: str
    draft: bool
    state: str
    base_ref: str
    base_sha: str
    head_ref: str
    head_sha: str
    head_repo_clone_url: str
    base_repo_clone_url: str
    head_repo_full_name: str
    base_repo_full_name: str
    mergeable: Optional[bool]
    changed_files: int
    additions: int
    deletions: int
    commits: int
    labels: List[str] = field(default_factory=list)
    linked_issues: List[Dict[str, Any]] = field(default_factory=list)
    existing_review_comments: List[Dict[str, Any]] = field(default_factory=list)
    existing_issue_comments: List[Dict[str, Any]] = field(default_factory=list)
    existing_reviews: List[Dict[str, Any]] = field(default_factory=list)
    ci_status: Dict[str, Any] = field(default_factory=dict)
    commit_messages: List[str] = field(default_factory=list)
    files: List[Dict[str, Any]] = field(default_factory=list)  # from /pulls/N/files

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PRInfo":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


# --------------------------------------------------------------------- findings


@dataclass
class Finding(_Base):
    """A single review finding (one potential inline comment)."""

    id: str
    lens: str
    title: str
    severity: str  # one of SEVERITIES
    category: str  # one of CATEGORIES
    confidence: float  # 0..1, the finder's own estimate
    path: str
    line: Optional[int]  # anchor line (end of range) on `side`; None => file-level
    start_line: Optional[int]  # first line of a multi-line range (same side), optional
    side: str  # LEFT | RIGHT
    evidence: str  # quoted code / concrete observation from the repo
    explanation: str  # why this is a problem (or question)
    suggestion: str  # what to do instead (prose)
    suggested_code: Optional[str] = None  # replacement for the line range → ```suggestion
    blocking: bool = False
    # verification (filled by the verify stage)
    verification: Dict[str, Any] = field(default_factory=dict)
    # GUI state
    status: str = "active"  # active | cut
    # final wording decided by the synthesize stage (falls back to explanation)
    body: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Finding":
        known = {f.name for f in dataclasses.fields(cls)}
        d = {k: v for k, v in d.items() if k in known}
        d.setdefault("verification", {})
        d.setdefault("status", "active")
        d.setdefault("body", "")
        d.setdefault("suggested_code", None)
        d.setdefault("blocking", False)
        d.setdefault("start_line", None)
        return cls(**d)

    def severity_rank(self) -> int:
        return SEVERITY_RANK.get(self.severity, len(SEVERITIES))

    def render_body(self) -> str:
        """Markdown body for GitHub (Conventional-Comments-ish)."""
        text = self.body.strip() or self.explanation.strip()
        parts = [f"**{self.severity}** · {self.category.replace('_', ' ')}: {self.title}".strip()]
        if text:
            parts.append(text)
        if self.suggestion.strip() and self.suggestion.strip() not in text:
            parts.append(f"**Suggestion:** {self.suggestion.strip()}")
        if self.suggested_code is not None and self.suggested_code.strip() != "" and self.line:
            parts.append("```suggestion\n" + self.suggested_code.rstrip("\n") + "\n```")
        return "\n\n".join(parts)


@dataclass
class Understanding(_Base):
    """Output of the *understand* stage."""

    repo_summary: str
    languages: List[str]
    conventions: str
    pr_claims: str
    change_summary: str
    risk_areas: List[str]
    files_to_read: List[str]
    notes_for_reviewers: str

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Understanding":
        known = {f.name for f in dataclasses.fields(cls)}
        d = {k: v for k, v in d.items() if k in known}
        for k in ("languages", "risk_areas", "files_to_read"):
            d.setdefault(k, [])
        for k in ("repo_summary", "conventions", "pr_claims", "change_summary", "notes_for_reviewers"):
            d.setdefault(k, "")
        return cls(**d)


@dataclass
class Review(_Base):
    """The stored, editable review (data/review.json)."""

    id: str
    created_at: str
    pr: Dict[str, Any]  # subset of PRInfo (url, owner, repo, number, title, head_sha...)
    settings: Dict[str, Any]
    verdict: str  # APPROVE | COMMENT | REQUEST_CHANGES
    summary: str  # markdown, the review body
    findings: List[Finding]
    understanding: Dict[str, Any] = field(default_factory=dict)
    stats: Dict[str, Any] = field(default_factory=dict)
    posted: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Review":
        d = dict(d)
        d["findings"] = [Finding.from_dict(f) for f in d.get("findings", [])]
        known = {f.name for f in dataclasses.fields(cls)}
        d = {k: v for k, v in d.items() if k in known}
        d.setdefault("understanding", {})
        d.setdefault("stats", {})
        d.setdefault("posted", None)
        return cls(**d)

    def active_findings(self) -> List[Finding]:
        return [f for f in self.findings if f.status == "active"]


# ------------------------------------------------------------------ JSON schemas
# These are given to the LLM backends (`claude --json-schema` / forced tool use).

FINDING_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "<= 80 chars, specific, no fluff"},
        "severity": {"type": "string", "enum": SEVERITIES},
        "category": {"type": "string", "enum": CATEGORIES},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "path": {"type": "string", "description": "repo-relative path exactly as in the diff"},
        "side": {"type": "string", "enum": SIDES, "description": "RIGHT for new/context lines, LEFT for removed lines"},
        "line": {"type": ["integer", "null"], "description": "anchor line number on that side (last line of the range); null for file-level"},
        "start_line": {"type": ["integer", "null"], "description": "first line of a multi-line range, or null"},
        "evidence": {"type": "string", "description": "the exact code you are pointing at and any other concrete observation (quote it)"},
        "explanation": {"type": "string", "description": "why this is a problem; the failure scenario; be concrete"},
        "suggestion": {"type": "string", "description": "what to do instead (prose). Empty string if none."},
        "suggested_code": {"type": ["string", "null"], "description": "optional drop-in replacement for exactly the lines start_line..line (same indentation), else null"},
        "blocking": {"type": "boolean"},
    },
    "required": [
        "title",
        "severity",
        "category",
        "confidence",
        "path",
        "side",
        "line",
        "start_line",
        "evidence",
        "explanation",
        "suggestion",
        "suggested_code",
        "blocking",
    ],
}

FINDINGS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {"type": "array", "items": FINDING_SCHEMA},
        "what_is_good": {"type": "array", "items": {"type": "string"}, "description": "0-3 short, specific positives"},
        "coverage_notes": {"type": "string", "description": "what you checked and what you could not check"},
    },
    "required": ["findings", "what_is_good", "coverage_notes"],
}

UNDERSTANDING_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "repo_summary": {"type": "string"},
        "languages": {"type": "array", "items": {"type": "string"}},
        "conventions": {"type": "string", "description": "coding/testing/review conventions found (CLAUDE.md, CONTRIBUTING, linters, test layout)"},
        "pr_claims": {"type": "string", "description": "what the PR title/body/linked issue CLAIM is being done"},
        "change_summary": {"type": "string", "description": "what the diff ACTUALLY does, file by file, in your own words"},
        "risk_areas": {"type": "array", "items": {"type": "string"}},
        "files_to_read": {"type": "array", "items": {"type": "string"}, "description": "non-diff files a careful reviewer should open (callers, tests, configs)"},
        "notes_for_reviewers": {"type": "string"},
    },
    "required": [
        "repo_summary",
        "languages",
        "conventions",
        "pr_claims",
        "change_summary",
        "risk_areas",
        "files_to_read",
        "notes_for_reviewers",
    ],
}

VERIFY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["confirmed", "refuted", "uncertain"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string", "description": "what you checked in the repo and why the finding holds or not"},
        "corrected_severity": {"type": ["string", "null"], "enum": SEVERITIES + [None]},
        "corrected_path": {"type": ["string", "null"]},
        "corrected_line": {"type": ["integer", "null"]},
        "corrected_side": {"type": ["string", "null"], "enum": SIDES + [None]},
        "improved_explanation": {"type": ["string", "null"], "description": "a tighter, more accurate explanation if the original was imprecise, else null"},
    },
    "required": [
        "verdict",
        "confidence",
        "reasoning",
        "corrected_severity",
        "corrected_path",
        "corrected_line",
        "corrected_side",
        "improved_explanation",
    ],
}

SYNTHESIS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": VERDICTS},
        "summary_markdown": {"type": "string", "description": "the review body: what the PR does, overall assessment, key issues, positives. Markdown."},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "id of the input finding"},
                    "keep": {"type": "boolean", "description": "false to drop (duplicate / not worth a comment)"},
                    "merged_into": {"type": ["string", "null"], "description": "id of another finding this duplicates, else null"},
                    "severity": {"type": "string", "enum": SEVERITIES},
                    "body": {"type": "string", "description": "final inline comment text (markdown). Specific, actionable, polite, no preamble."},
                },
                "required": ["id", "keep", "merged_into", "severity", "body"],
            },
        },
    },
    "required": ["verdict", "summary_markdown", "findings"],
}
