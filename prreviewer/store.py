"""Local draft storage.

local/
  <id>/
    data/
      meta.json            id, created_at, pr url, settings, status
      pr.json              PRInfo
      diff.patch           raw unified diff from GitHub
      diff.json            parsed diff (for the GUI)
      understanding.json   stage 1 output
      findings_raw.json    stage 2 output (all lenses, before verification)
      verification.json    stage 3 votes
      review.json          THE editable review (what `view` edits and `post` posts)
      review.original.json pristine copy of review.json
      llm_calls/           one .json per backend call (prompt, response, cost) for debugging
    html/
      index.html           the viewer/editor (self-contained)
    repo/                  git worktree of the PR head (removed after review unless --keep-repo)
  _cache/repos/...         shared partial clones
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .ids import is_valid_id
from .schema import Review


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class ReviewStore:
    def __init__(self, local_dir: str):
        self.local_dir = local_dir
        os.makedirs(local_dir, exist_ok=True)

    # ----------------------------------------------------------------- paths
    def dir(self, rid: str) -> str:
        if not is_valid_id(rid):
            raise ValueError(f"invalid review id: {rid!r}")
        return os.path.join(self.local_dir, rid)

    def data_dir(self, rid: str) -> str:
        return os.path.join(self.dir(rid), "data")

    def html_dir(self, rid: str) -> str:
        return os.path.join(self.dir(rid), "html")

    def repo_dir(self, rid: str) -> str:
        return os.path.join(self.dir(rid), "repo")

    def calls_dir(self, rid: str) -> str:
        return os.path.join(self.data_dir(rid), "llm_calls")

    def exists(self, rid: str) -> bool:
        return os.path.isfile(os.path.join(self.data_dir(rid), "meta.json"))

    def create(self, rid: str) -> None:
        os.makedirs(self.data_dir(rid), exist_ok=True)
        os.makedirs(self.html_dir(rid), exist_ok=True)
        os.makedirs(self.calls_dir(rid), exist_ok=True)

    # ------------------------------------------------------------------ io
    def write_json(self, rid: str, name: str, obj: Any) -> str:
        path = os.path.join(self.data_dir(rid), name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, ensure_ascii=False, default=_default)
        os.replace(tmp, path)
        return path

    def read_json(self, rid: str, name: str, default: Any = None) -> Any:
        path = os.path.join(self.data_dir(rid), name)
        if not os.path.isfile(path):
            return default
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def write_text(self, rid: str, name: str, text: str) -> str:
        path = os.path.join(self.data_dir(rid), name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def read_text(self, rid: str, name: str) -> Optional[str]:
        path = os.path.join(self.data_dir(rid), name)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def write_html(self, rid: str, html: str) -> str:
        path = os.path.join(self.html_dir(rid), "index.html")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
        return path

    def save_call(self, rid: str, name: str, payload: Dict[str, Any]) -> None:
        os.makedirs(self.calls_dir(rid), exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:120]
        path = os.path.join(self.calls_dir(rid), safe + ".json")
        n = 1
        while os.path.exists(path):
            n += 1
            path = os.path.join(self.calls_dir(rid), f"{safe}.{n}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False, default=_default)

    # --------------------------------------------------------------- review
    def save_review(self, review: Review, original: bool = False) -> None:
        self.write_json(review.id, "review.json", review.to_dict())
        if original:
            self.write_json(review.id, "review.original.json", review.to_dict())
        meta = self.read_json(review.id, "meta.json", {}) or {}
        meta.update({"id": review.id, "updated_at": now_iso(), "verdict": review.verdict, "findings": len(review.active_findings())})
        self.write_json(review.id, "meta.json", meta)

    def load_review(self, rid: str) -> Review:
        d = self.read_json(rid, "review.json")
        if d is None:
            raise FileNotFoundError(f"no review.json for id {rid!r} in {self.local_dir}")
        return Review.from_dict(d)

    def list(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not os.path.isdir(self.local_dir):
            return out
        for name in sorted(os.listdir(self.local_dir)):
            if name.startswith("_") or name.startswith("."):
                continue
            meta_path = os.path.join(self.local_dir, name, "data", "meta.json")
            if os.path.isfile(meta_path):
                try:
                    with open(meta_path, encoding="utf-8") as fh:
                        meta = json.load(fh)
                except (OSError, ValueError):
                    meta = {"id": name, "status": "corrupt"}
                meta.setdefault("id", name)
                out.append(meta)
        out.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return out

    def delete(self, rid: str, keep_repo_cache: bool = True) -> None:
        d = self.dir(rid)
        # detach the worktree cleanly first (so the cache clone's bookkeeping stays sane)
        repo = self.repo_dir(rid)
        if os.path.isdir(repo):
            try:
                import subprocess

                git_dir = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=repo, capture_output=True, text=True)
                common = git_dir.stdout.strip()
                if common:
                    subprocess.run(["git", "--git-dir", common, "worktree", "remove", "--force", repo], capture_output=True)
            except Exception:
                pass
        shutil.rmtree(d, ignore_errors=True)

    def init_meta(self, rid: str, pr_url: str, settings: Dict[str, Any]) -> None:
        self.write_json(rid, "meta.json", {"id": rid, "pr_url": pr_url, "created_at": now_iso(), "settings": settings, "status": "running"})

    def set_status(self, rid: str, status: str, **extra: Any) -> None:
        meta = self.read_json(rid, "meta.json", {}) or {}
        meta["status"] = status
        meta.update(extra)
        self.write_json(rid, "meta.json", meta)


def _default(o: Any) -> Any:
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if isinstance(o, set):
        return sorted(o)
    return str(o)
