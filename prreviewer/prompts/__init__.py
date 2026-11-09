"""Prompt templates live next to this file as Markdown with `{{placeholders}}`.

load("find") -> template text; render(template, **vars) substitutes placeholders
(missing ones become empty strings so a template never crashes the pipeline).
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any, List

_DIR = os.path.dirname(os.path.abspath(__file__))
_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


@lru_cache(maxsize=None)
def load(name: str) -> str:
    path = os.path.join(_DIR, name + ".md")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def render(template: str, **vars: Any) -> str:
    def sub(m: "re.Match[str]") -> str:
        v = vars.get(m.group(1), "")
        return "" if v is None else str(v)

    return _PLACEHOLDER.sub(sub, template)


def available_lenses() -> List[str]:
    d = os.path.join(_DIR, "lenses")
    return sorted(f[:-3] for f in os.listdir(d) if f.endswith(".md"))


def lens_charter(lens: str) -> str:
    return load(os.path.join("lenses", lens))
