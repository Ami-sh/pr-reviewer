"""Parse GitHub pull-request URLs (github.com and GitHub Enterprise)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

_PR_PATH = re.compile(r"^/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)(?:/.*)?$")
_SHORT = re.compile(r"^(?P<owner>[^/\s]+)/(?P<repo>[^/#\s]+)#(?P<number>\d+)$")


@dataclass(frozen=True)
class PRRef:
    host: str  # e.g. github.com
    owner: str
    repo: str
    number: int

    @property
    def api_base(self) -> str:
        if self.host == "github.com":
            return "https://api.github.com"
        return f"https://{self.host}/api/v3"

    @property
    def html_url(self) -> str:
        return f"https://{self.host}/{self.owner}/{self.repo}/pull/{self.number}"

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


def parse_pr_url(text: str) -> PRRef:
    """Accepts:
      https://github.com/owner/repo/pull/123
      https://github.com/owner/repo/pull/123/files#diff-...
      github.com/owner/repo/pull/123
      owner/repo#123
      https://ghe.example.com/owner/repo/pull/7
    """
    text = text.strip()
    m = _SHORT.match(text)
    if m:
        return PRRef("github.com", m.group("owner"), m.group("repo"), int(m.group("number")))
    if "://" not in text:
        text = "https://" + text
    u = urlparse(text)
    if not u.netloc:
        raise ValueError(f"not a pull-request URL: {text!r}")
    m = _PR_PATH.match(u.path)
    if not m:
        raise ValueError(f"not a pull-request URL (expected .../<owner>/<repo>/pull/<n>): {text!r}")
    repo = m.group("repo")
    if repo.endswith(".git"):
        repo = repo[:-4]
    return PRRef(u.netloc.lower(), m.group("owner"), repo, int(m.group("number")))
