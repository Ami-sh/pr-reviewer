"""Clone / fetch the PR head into a local cache and expose read-only git helpers.

Strategy (see DESIGN.md "Repository checkout"):
  1. `git clone --filter=blob:none --no-checkout <base repo>` into
     local/_cache/repos/<host>/<owner>/<repo>  (partial clone: full history for
     `git log`/`git blame`, blobs fetched lazily, so it is fast even for big repos).
  2. `git fetch origin pull/<n>/head` (works for forks too) and the base branch.
  3. `git worktree add <local>/<id>/repo <head_sha>` — each review gets its own
     detached worktree so parallel reviews never collide.
Falls back to a plain `--depth=1` clone when the server does not support
partial clones.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Dict, List, Optional

from . import log
from .schema import PRInfo


class GitError(RuntimeError):
    pass


def _git(args: List[str], cwd: Optional[str] = None, check: bool = True, timeout: int = 600, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    full_env.setdefault("GIT_TERMINAL_PROMPT", "0")
    if env:
        full_env.update(env)
    proc = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=full_env)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()[:800]}")
    return proc


def _authed_url(clone_url: str, token: Optional[str]) -> str:
    """Inject the token for private repos (https only)."""
    if token and clone_url.startswith("https://") and "@" not in clone_url.split("//", 1)[1].split("/", 1)[0]:
        return clone_url.replace("https://", f"https://x-access-token:{token}@", 1)
    return clone_url


class RepoCheckout:
    def __init__(self, pr: PRInfo, local_dir: str, review_id: str, token: Optional[str] = None):
        self.pr = pr
        self.local_dir = local_dir
        self.review_id = review_id
        self.token = token
        self.cache_dir = os.path.join(local_dir, "_cache", "repos", pr.host, pr.owner, pr.repo)
        self.worktree = os.path.join(local_dir, review_id, "repo")

    # ------------------------------------------------------------ prepare
    def prepare(self) -> str:
        """Ensure the cache clone exists, fetch the PR head + base, create the worktree. Returns the worktree path."""
        os.makedirs(os.path.dirname(self.cache_dir), exist_ok=True)
        url = _authed_url(self.pr.base_repo_clone_url, self.token)
        if not os.path.isdir(os.path.join(self.cache_dir, ".git")):
            log.info(f"cloning {self.pr.base_repo_full_name} (partial clone) ...")
            shutil.rmtree(self.cache_dir, ignore_errors=True)
            try:
                _git(["clone", "--filter=blob:none", "--no-checkout", "--quiet", url, self.cache_dir])
            except GitError as e:
                log.warn(f"partial clone failed ({e}); falling back to shallow clone")
                shutil.rmtree(self.cache_dir, ignore_errors=True)
                _git(["clone", "--depth=50", "--no-checkout", "--quiet", url, self.cache_dir])
        else:
            # keep the remote url fresh (token may have changed)
            _git(["remote", "set-url", "origin", url], cwd=self.cache_dir, check=False)

        n = self.pr.number
        log.info(f"fetching pull/{n}/head and base branch {self.pr.base_ref} ...")
        _git(["fetch", "--quiet", "origin", f"+refs/pull/{n}/head:refs/remotes/pr/{n}"], cwd=self.cache_dir)
        _git(["fetch", "--quiet", "origin", f"+refs/heads/{self.pr.base_ref}:refs/remotes/origin/{self.pr.base_ref}"], cwd=self.cache_dir, check=False)
        # make sure the exact head sha is present (the ref may have moved since we fetched PR metadata)
        if _git(["cat-file", "-e", f"{self.pr.head_sha}^{{commit}}"], cwd=self.cache_dir, check=False).returncode != 0:
            _git(["fetch", "--quiet", "origin", self.pr.head_sha], cwd=self.cache_dir, check=False)
        if _git(["cat-file", "-e", f"{self.pr.base_sha}^{{commit}}"], cwd=self.cache_dir, check=False).returncode != 0:
            _git(["fetch", "--quiet", "origin", self.pr.base_sha], cwd=self.cache_dir, check=False)

        head = self.pr.head_sha
        if _git(["cat-file", "-e", f"{head}^{{commit}}"], cwd=self.cache_dir, check=False).returncode != 0:
            head = f"refs/remotes/pr/{n}"
            log.warn(f"head sha {self.pr.head_sha[:10]} not reachable; using {head}")

        if os.path.isdir(self.worktree):
            _git(["worktree", "remove", "--force", self.worktree], cwd=self.cache_dir, check=False)
            shutil.rmtree(self.worktree, ignore_errors=True)
        _git(["worktree", "prune"], cwd=self.cache_dir, check=False)
        os.makedirs(os.path.dirname(self.worktree), exist_ok=True)
        log.info("checking out PR head into an isolated worktree ...")
        _git(["worktree", "add", "--detach", "--quiet", self.worktree, head], cwd=self.cache_dir)
        return self.worktree

    def remove_worktree(self) -> None:
        if os.path.isdir(self.worktree):
            _git(["worktree", "remove", "--force", self.worktree], cwd=self.cache_dir, check=False)
            shutil.rmtree(self.worktree, ignore_errors=True)
        _git(["worktree", "prune"], cwd=self.cache_dir, check=False)

    # ------------------------------------------------------------ queries
    def merge_base(self) -> Optional[str]:
        p = _git(["merge-base", self.pr.base_sha, self.pr.head_sha], cwd=self.worktree, check=False)
        return p.stdout.strip() or None

    def local_diff(self) -> Optional[str]:
        """Three-dot diff (what GitHub shows). None if history is too shallow."""
        mb = self.merge_base()
        if not mb:
            return None
        p = _git(["diff", "--no-color", "--no-ext-diff", "-U3", "--find-renames", mb, self.pr.head_sha], cwd=self.worktree, check=False)
        return p.stdout if p.returncode == 0 else None

    def file_tree(self, max_entries: int = 400) -> List[str]:
        p = _git(["ls-files"], cwd=self.worktree, check=False)
        files = p.stdout.splitlines() if p.returncode == 0 else []
        return files[:max_entries] + ([f"... ({len(files) - max_entries} more files)"] if len(files) > max_entries else [])

    def all_files(self) -> List[str]:
        p = _git(["ls-files"], cwd=self.worktree, check=False)
        return p.stdout.splitlines() if p.returncode == 0 else []

    def read_file(self, path: str, max_bytes: int = 200_000) -> Optional[str]:
        full = os.path.normpath(os.path.join(self.worktree, path))
        if not full.startswith(os.path.normpath(self.worktree) + os.sep) or not os.path.isfile(full):
            return None
        try:
            with open(full, "rb") as fh:
                data = fh.read(max_bytes + 1)
        except OSError:
            return None
        if b"\0" in data[:8000]:
            return None  # binary
        text = data[:max_bytes].decode("utf-8", errors="replace")
        if len(data) > max_bytes:
            text += "\n... [truncated] ..."
        return text

    def recent_log(self, paths: Optional[List[str]] = None, n: int = 15) -> str:
        args = ["log", f"-n{n}", "--no-color", "--date=short", "--format=%h %ad %an: %s"]
        if paths:
            args += ["--"] + paths
        p = _git(args, cwd=self.worktree, check=False)
        return p.stdout.strip()

    def grep(self, pattern: str, paths: Optional[List[str]] = None, max_lines: int = 200, fixed: bool = False) -> str:
        args = ["grep", "-n", "-I", "--no-color"]
        if fixed:
            args.append("-F")
        else:
            args.append("-E")
        args += ["-e", pattern, "--"]
        if paths:
            args += paths
        p = _git(args, cwd=self.worktree, check=False)
        lines = p.stdout.splitlines()
        if len(lines) > max_lines:
            lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more matches)"]
        return "\n".join(lines)

    def blame(self, path: str, start: int, end: int) -> str:
        p = _git(["blame", "--date=short", "-L", f"{start},{end}", "--", path], cwd=self.worktree, check=False)
        return p.stdout if p.returncode == 0 else p.stderr

    def show(self, rev: str, path: Optional[str] = None, max_chars: int = 60_000) -> str:
        args = ["show", "--no-color", "--stat" if path is None else rev + ":" + path] if path else ["show", "--no-color", "--stat", rev]
        if path:
            args = ["show", "--no-color", f"{rev}:{path}"]
        p = _git(args, cwd=self.worktree, check=False)
        out = p.stdout if p.returncode == 0 else p.stderr
        return out[:max_chars]

    def detect_conventions_files(self) -> Dict[str, str]:
        """Return contents of the files that encode repo conventions."""
        names = [
            "CLAUDE.md",
            "AGENTS.md",
            ".cursorrules",
            "CONTRIBUTING.md",
            "CONTRIBUTING.rst",
            "CODE_OF_CONDUCT.md",
            "README.md",
            "README.rst",
            "README",
            "docs/CONTRIBUTING.md",
            ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/pull_request_template.md",
            "pyproject.toml",
            "setup.cfg",
            "package.json",
            "Cargo.toml",
            "go.mod",
            "Makefile",
            ".pre-commit-config.yaml",
            "tox.ini",
            ".flake8",
            "ruff.toml",
            ".eslintrc.json",
            ".eslintrc.js",
            "eslint.config.js",
        ]
        out: Dict[str, str] = {}
        for n in names:
            t = self.read_file(n, max_bytes=20_000)
            if t:
                out[n] = t
        return out

    def test_files_for(self, path: str) -> List[str]:
        """Guess which test files cover `path` by name similarity."""
        base = os.path.splitext(os.path.basename(path))[0]
        if not base or base.startswith("test"):
            return []
        rx = re.compile(r"(^|/)(test_%s|%s_test|%s\.test|%s\.spec|test%s)\.[A-Za-z]+$" % tuple([re.escape(base)] * 5), re.IGNORECASE)
        return [f for f in self.all_files() if rx.search(f)][:10]
