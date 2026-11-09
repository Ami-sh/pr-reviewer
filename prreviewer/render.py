"""Render the self-contained HTML viewer/editor for a review.

The page embeds the review JSON and the parsed diff; all behaviour is vanilla
JS inside templates/view.html.  When served by `review.py view` it saves via
POST /api/save; opened as a plain file it still works and offers a JSON
download instead.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from .schema import Review

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "view.html")


def render_review_html(review: Review, diff: Optional[Dict[str, Any]] = None, served: bool = False) -> str:
    with open(TEMPLATE_PATH, encoding="utf-8") as fh:
        tpl = fh.read()
    payload = {"review": review.to_dict(), "diff": diff or {"files": []}, "served": served}
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return tpl.replace("/*__DATA__*/null", data)


def write_review_html(store, rid: str, review: Review, diff: Optional[Dict[str, Any]], served: bool = False) -> str:
    html = render_review_html(review, diff, served=served)
    return store.write_html(rid, html)
