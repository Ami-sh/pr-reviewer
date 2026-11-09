"""Backend interface shared by the Claude Code and API pipelines.

A backend answers ONE structured question per call: given a system prompt, a
user prompt, a JSON schema and a working directory (the PR checkout), return a
dict that validates against the schema.  The backend may use read-only tools to
explore the repository before answering; that is the whole point.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class LLMResult:
    data: Optional[Dict[str, Any]]
    raw_text: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0
    duration_s: float = 0.0
    model: str = ""
    error: Optional[str] = None
    transcript: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.data is not None and self.error is None


class Backend:
    name = "base"

    def __init__(self, model: str, max_turns: int = 25, allow_exec: bool = False, call_logger: Optional[Callable[[str, Dict[str, Any]], None]] = None, verbose: bool = False):
        self.model = model
        self.max_turns = max_turns
        self.allow_exec = allow_exec
        self.call_logger = call_logger
        self.verbose = verbose

    def run(self, *, stage: str, system: str, prompt: str, schema: Dict[str, Any], cwd: str, label: str = "") -> LLMResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def describe(self) -> str:
        return f"{self.name}:{self.model}"

    def _log_call(self, label: str, payload: Dict[str, Any]) -> None:
        if self.call_logger:
            try:
                self.call_logger(label, payload)
            except Exception:
                pass


# ------------------------------------------------------------ json helpers

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort: find the first JSON object in free text."""
    if not text:
        return None
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass
    m = _FENCE_RE.search(text)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    # scan for the outermost {...}
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def validate_against_schema(obj: Any, schema: Dict[str, Any], path: str = "$") -> List[str]:
    """Minimal JSON-schema validation (type / required / enum / items / properties).
    Returns a list of problems; empty means valid.  Enough to catch malformed
    LLM output without a jsonschema dependency."""
    problems: List[str] = []
    t = schema.get("type")
    types = t if isinstance(t, list) else ([t] if t else [])

    def is_type(o: Any, name: str) -> bool:
        return {
            "object": isinstance(o, dict),
            "array": isinstance(o, list),
            "string": isinstance(o, str),
            "number": isinstance(o, (int, float)) and not isinstance(o, bool),
            "integer": isinstance(o, int) and not isinstance(o, bool),
            "boolean": isinstance(o, bool),
            "null": o is None,
        }.get(name, True)

    if types and not any(is_type(obj, n) for n in types):
        problems.append(f"{path}: expected {types}, got {type(obj).__name__}")
        return problems
    if "enum" in schema and obj not in schema["enum"]:
        problems.append(f"{path}: {obj!r} not in enum {schema['enum']}")
    if isinstance(obj, dict):
        for req in schema.get("required", []):
            if req not in obj:
                problems.append(f"{path}.{req}: missing")
        for k, sub in schema.get("properties", {}).items():
            if k in obj:
                problems.extend(validate_against_schema(obj[k], sub, f"{path}.{k}"))
    if isinstance(obj, list) and "items" in schema:
        for i, it in enumerate(obj):
            problems.extend(validate_against_schema(it, schema["items"], f"{path}[{i}]"))
    return problems


def coerce_to_schema(obj: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """Fill missing required fields with harmless defaults and normalise enum
    casing so a slightly sloppy answer still flows through the pipeline."""
    if not isinstance(obj, dict):
        return obj
    props = schema.get("properties", {})
    for k, sub in props.items():
        if k not in obj:
            if k in schema.get("required", []):
                obj[k] = _default_for(sub)
            continue
        v = obj[k]
        if "enum" in sub and isinstance(v, str) and v not in sub["enum"]:
            lowered = {str(e).lower(): e for e in sub["enum"] if e is not None}
            if v.lower() in lowered:
                obj[k] = lowered[v.lower()]
        st = sub.get("type")
        sts = st if isinstance(st, list) else [st]
        if "integer" in sts and isinstance(v, str) and v.strip().lstrip("-").isdigit():
            obj[k] = int(v)
        if "number" in sts and isinstance(v, str):
            try:
                obj[k] = float(v)
            except ValueError:
                pass
        if "object" in sts and isinstance(v, dict):
            coerce_to_schema(v, sub)
        if "array" in sts and isinstance(v, list) and "items" in sub and sub["items"].get("type") == "object":
            for it in v:
                if isinstance(it, dict):
                    coerce_to_schema(it, sub["items"])
    return obj


def _default_for(sub: Dict[str, Any]) -> Any:
    t = sub.get("type")
    ts = t if isinstance(t, list) else [t]
    if "null" in ts:
        return None
    if "enum" in sub and sub["enum"]:
        return sub["enum"][0]
    if "string" in ts:
        return ""
    if "array" in ts:
        return []
    if "object" in ts:
        return {}
    if "boolean" in ts:
        return False
    if "number" in ts or "integer" in ts:
        return 0
    return None
