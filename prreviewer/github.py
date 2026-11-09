"""GitHub REST client: fetch everything about a PR, and post a review.

Auth: $GITHUB_TOKEN / $GH_TOKEN, else `gh auth token` (the gh CLI's keyring).
Only the handful of endpoints we need; see DESIGN.md "GitHub integration".
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from typing import Any, Dict, Iterable, List, Optional

import requests

from . import log
from .schema import PRInfo
from .urls import PRRef

_ISSUE_LINK_RE = re.compile(
    r"\b(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)\s*:?\s*"
    r"(?:(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+))?#(?P<num>\d+)",
    re.IGNORECASE,
)
_ISSUE_URL_RE = re.compile(r"https?://[^/\s]+/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/issues/(?P<num>\d+)")


class GitHubError(RuntimeError):
    pass


def discover_token(host: str = "github.com") -> Optional[str]:
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        tok = os.environ.get(var)
        if tok:
            return tok
    gh = shutil.which("gh") or os.path.expanduser("~/.local/bin/gh")
    if os.path.isfile(gh):
        try:
            out = subprocess.run([gh, "auth", "token", "--hostname", host], capture_output=True, text=True, timeout=20)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return None


class GitHubClient:
    def __init__(self, ref: PRRef, token: Optional[str] = None):
        self.ref = ref
        self.token = token or discover_token(ref.host)
        self.session = requests.Session()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "pr-reviewer/0.1",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        else:
            log.warn("no GitHub token found (set GITHUB_TOKEN or run `gh auth login`); private repos and posting will fail")
        self.session.headers.update(headers)
        self.api = ref.api_base

    # ------------------------------------------------------------------ http
    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.api}{path}"

    def _request(self, method: str, path: str, accept: Optional[str] = None, **kw) -> requests.Response:
        headers = dict(kw.pop("headers", {}) or {})
        if accept:
            headers["Accept"] = accept
        for attempt in range(4):
            resp = self.session.request(method, self._url(path), headers=headers, timeout=60, **kw)
            if resp.status_code in (403, 429) and "rate limit" in resp.text.lower():
                reset = resp.headers.get("X-RateLimit-Reset")
                wait = max(5, min(90, int(reset) - int(time.time()))) if reset and reset.isdigit() else 10 * (attempt + 1)
                log.warn(f"GitHub rate limited; sleeping {wait}s")
                time.sleep(wait)
                continue
            if resp.status_code >= 500 and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            return resp
        return resp

    def get_json(self, path: str, **kw) -> Any:
        resp = self._request("GET", path, **kw)
        if resp.status_code >= 400:
            raise GitHubError(f"GET {path} -> {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def get_paginated(self, path: str, per_page: int = 100, max_pages: int = 50) -> List[Any]:
        out: List[Any] = []
        url: Optional[str] = f"{self._url(path)}{'&' if '?' in path else '?'}per_page={per_page}"
        pages = 0
        while url and pages < max_pages:
            resp = self._request("GET", url)
            if resp.status_code >= 400:
                raise GitHubError(f"GET {url} -> {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            if isinstance(data, list):
                out.extend(data)
            else:
                out.append(data)
            url = resp.links.get("next", {}).get("url")
            pages += 1
        return out

    # -------------------------------------------------------------------- PR
    def fetch_pr(self) -> PRInfo:
        r = self.ref
        base = f"/repos/{r.owner}/{r.repo}/pulls/{r.number}"
        pr = self.get_json(base)
        log.debug(f"fetched PR #{r.number}: {pr.get('title')!r}")

        files = self._safe(lambda: self.get_paginated(f"{base}/files"), [])
        review_comments = self._safe(lambda: self.get_paginated(f"{base}/comments"), [])
        issue_comments = self._safe(lambda: self.get_paginated(f"/repos/{r.owner}/{r.repo}/issues/{r.number}/comments"), [])
        reviews = self._safe(lambda: self.get_paginated(f"{base}/reviews"), [])
        commits = self._safe(lambda: self.get_paginated(f"{base}/commits"), [])
        head_sha = pr["head"]["sha"]
        ci = self._safe(lambda: self.fetch_ci(head_sha), {})
        body = pr.get("body") or ""
        linked = self._safe(lambda: self.fetch_linked_issues(body), [])

        head_repo = pr.get("head", {}).get("repo") or {}
        base_repo = pr.get("base", {}).get("repo") or {}
        return PRInfo(
            host=r.host,
            owner=r.owner,
            repo=r.repo,
            number=r.number,
            url=pr.get("html_url") or r.html_url,
            title=pr.get("title") or "",
            body=body,
            author=(pr.get("user") or {}).get("login", ""),
            draft=bool(pr.get("draft")),
            state=pr.get("state", ""),
            base_ref=pr["base"]["ref"],
            base_sha=pr["base"]["sha"],
            head_ref=pr["head"]["ref"],
            head_sha=head_sha,
            head_repo_clone_url=head_repo.get("clone_url") or "",
            base_repo_clone_url=base_repo.get("clone_url") or f"https://{r.host}/{r.owner}/{r.repo}.git",
            head_repo_full_name=head_repo.get("full_name") or "",
            base_repo_full_name=base_repo.get("full_name") or r.full_name,
            mergeable=pr.get("mergeable"),
            changed_files=int(pr.get("changed_files") or len(files)),
            additions=int(pr.get("additions") or 0),
            deletions=int(pr.get("deletions") or 0),
            commits=int(pr.get("commits") or len(commits)),
            labels=[l.get("name", "") for l in pr.get("labels") or []],
            linked_issues=linked,
            existing_review_comments=[_slim_review_comment(c) for c in review_comments],
            existing_issue_comments=[_slim_issue_comment(c) for c in issue_comments],
            existing_reviews=[_slim_review(c) for c in reviews],
            ci_status=ci,
            commit_messages=[(c.get("commit") or {}).get("message", "") for c in commits],
            files=[_slim_file(f) for f in files],
        )

    def fetch_diff(self) -> str:
        """Unified diff as GitHub renders it (merge-base...head).

        Falls back to concatenating per-file patches when the diff endpoint
        refuses (PRs with >300 files or >20k lines).
        """
        r = self.ref
        resp = self._request("GET", f"/repos/{r.owner}/{r.repo}/pulls/{r.number}", accept="application/vnd.github.v3.diff")
        if resp.status_code < 400 and resp.text.strip():
            return resp.text
        log.warn(f"diff endpoint returned {resp.status_code}; assembling from per-file patches")
        files = self.get_paginated(f"/repos/{r.owner}/{r.repo}/pulls/{r.number}/files")
        return assemble_diff_from_files(files)

    def fetch_ci(self, sha: str) -> Dict[str, Any]:
        r = self.ref
        out: Dict[str, Any] = {}
        try:
            cr = self.get_json(f"/repos/{r.owner}/{r.repo}/commits/{sha}/check-runs?per_page=100")
            out["check_runs"] = [
                {"name": c.get("name"), "status": c.get("status"), "conclusion": c.get("conclusion"), "url": c.get("html_url")}
                for c in cr.get("check_runs", [])
            ]
        except GitHubError:
            pass
        try:
            st = self.get_json(f"/repos/{r.owner}/{r.repo}/commits/{sha}/status")
            out["combined_status"] = st.get("state")
            out["statuses"] = [
                {"context": s.get("context"), "state": s.get("state"), "description": s.get("description")} for s in st.get("statuses", [])
            ]
        except GitHubError:
            pass
        return out

    def fetch_linked_issues(self, body: str) -> List[Dict[str, Any]]:
        r = self.ref
        seen = set()
        issues: List[Dict[str, Any]] = []
        for m in list(_ISSUE_LINK_RE.finditer(body)) + list(_ISSUE_URL_RE.finditer(body)):
            owner = m.group("owner") or r.owner
            repo = m.group("repo") or r.repo
            num = int(m.group("num"))
            key = (owner.lower(), repo.lower(), num)
            if key in seen:
                continue
            seen.add(key)
            try:
                iss = self.get_json(f"/repos/{owner}/{repo}/issues/{num}")
            except GitHubError:
                continue
            if "pull_request" in iss:
                continue
            issues.append(
                {
                    "number": num,
                    "repo": f"{owner}/{repo}",
                    "title": iss.get("title", ""),
                    "body": (iss.get("body") or "")[:6000],
                    "state": iss.get("state"),
                    "labels": [l.get("name") for l in iss.get("labels") or []],
                    "url": iss.get("html_url"),
                }
            )
            if len(issues) >= 5:
                break
        return issues

    def fetch_file_at(self, path: str, ref: str) -> Optional[str]:
        r = self.ref
        resp = self._request("GET", f"/repos/{r.owner}/{r.repo}/contents/{path}?ref={ref}", accept="application/vnd.github.raw+json")
        if resp.status_code >= 400:
            return None
        return resp.text

    def whoami(self) -> Optional[str]:
        try:
            return self.get_json("/user").get("login")
        except GitHubError:
            return None

    # ----------------------------------------------------------------- post
    def post_review(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = self.ref
        resp = self._request("POST", f"/repos/{r.owner}/{r.repo}/pulls/{r.number}/reviews", json=payload)
        if resp.status_code >= 400:
            raise GitHubError(f"POST review -> {resp.status_code}: {resp.text[:1000]}")
        return resp.json()

    def post_issue_comment(self, body: str) -> Dict[str, Any]:
        r = self.ref
        resp = self._request("POST", f"/repos/{r.owner}/{r.repo}/issues/{r.number}/comments", json={"body": body})
        if resp.status_code >= 400:
            raise GitHubError(f"POST comment -> {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _safe(fn, default):
        try:
            return fn()
        except GitHubError as e:
            log.debug(f"optional fetch failed: {e}")
            return default


def assemble_diff_from_files(files: Iterable[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for f in files:
        patch = f.get("patch")
        name = f.get("filename")
        prev = f.get("previous_filename") or name
        status = f.get("status")
        header = [f"diff --git a/{prev} b/{name}"]
        if status == "added":
            header.append("new file mode 100644")
            header.append("--- /dev/null")
            header.append(f"+++ b/{name}")
        elif status == "removed":
            header.append("deleted file mode 100644")
            header.append(f"--- a/{prev}")
            header.append("+++ /dev/null")
        else:
            if status == "renamed":
                header.append(f"rename from {prev}")
                header.append(f"rename to {name}")
            header.append(f"--- a/{prev}")
            header.append(f"+++ b/{name}")
        if patch:
            parts.append("\n".join(header) + "\n" + patch.rstrip("\n") + "\n")
        else:
            parts.append("\n".join(header) + "\n")  # binary / too large: no hunks
    return "".join(parts)


def _slim_file(f: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "filename": f.get("filename"),
        "previous_filename": f.get("previous_filename"),
        "status": f.get("status"),
        "additions": f.get("additions"),
        "deletions": f.get("deletions"),
        "changes": f.get("changes"),
        "has_patch": bool(f.get("patch")),
    }


def _slim_review_comment(c: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": c.get("id"),
        "user": (c.get("user") or {}).get("login"),
        "path": c.get("path"),
        "line": c.get("line"),
        "side": c.get("side"),
        "body": (c.get("body") or "")[:2000],
        "created_at": c.get("created_at"),
        "in_reply_to_id": c.get("in_reply_to_id"),
    }


def _slim_issue_comment(c: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": c.get("id"),
        "user": (c.get("user") or {}).get("login"),
        "body": (c.get("body") or "")[:2000],
        "created_at": c.get("created_at"),
    }


def _slim_review(c: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": c.get("id"),
        "user": (c.get("user") or {}).get("login"),
        "state": c.get("state"),
        "body": (c.get("body") or "")[:2000],
        "submitted_at": c.get("submitted_at"),
    }
