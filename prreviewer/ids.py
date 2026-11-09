"""Review ids: `<repo>-<pr>-<4 hex>` — short enough to type, unique enough to not collide."""
from __future__ import annotations

import os
import re
import secrets


def make_review_id(repo: str, number: int, local_dir: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", repo.lower()).strip("-")[:24] or "repo"
    for _ in range(50):
        rid = f"{slug}-{number}-{secrets.token_hex(2)}"
        if not os.path.exists(os.path.join(local_dir, rid)):
            return rid
    raise RuntimeError("could not allocate a unique review id")


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")


def is_valid_id(rid: str) -> bool:
    return bool(_ID_RE.match(rid)) and not rid.startswith("_") and ".." not in rid
