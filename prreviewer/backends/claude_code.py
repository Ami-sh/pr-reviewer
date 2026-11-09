"""Claude Code backend: shell out to `claude -p` with structured output.

Each stage is one headless invocation run *inside the PR checkout* so Claude
can use its own Read/Grep/Glob tools (and read-only git via Bash) to explore the
repository.  No API key is needed — it uses whatever login the `claude` CLI has.

Flags we rely on (Claude Code >= 2.0):
  -p / --print                 non-interactive
  --output-format json         one JSON envelope on stdout; `structured_output`
                               holds the schema-validated answer
  --json-schema <schema>       forces structured output
  --tools <list>               restrict the built-in toolset
  --allowedTools <patterns>    pre-approve tool calls (e.g. "Bash(git log:*)")
  --permission-mode dontAsk    never prompt; anything not pre-approved is denied
  --permission-prompts none    (belt and braces for SDK hosts)
  --system-prompt <text>       replace the default system prompt
  --model / --effort           model alias (sonnet/opus/haiku/fable) and effort
  --max-budget-usd <n>         hard cost cap per call
  --no-session-persistence     do not litter ~/.claude with sessions
  --bare                       skip hooks, plugins, CLAUDE.md auto-discovery of
                               the *reviewed* repo (we feed context ourselves;
                               a malicious PR must not be able to inject
                               instructions through CLAUDE.md/hooks). NOTE: --bare
                               also disables OAuth; we therefore use
                               --setting-sources "" + --disable-slash-commands
                               instead, which keeps login working.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Dict, List, Optional

from .. import log
from ..config import find_claude_binary
from .base import Backend, LLMResult, coerce_to_schema, extract_json_object, validate_against_schema

READ_ONLY_TOOLS = ["Read", "Grep", "Glob", "LS", "Bash"]
READ_ONLY_BASH = [
    "Bash(git log:*)",
    "Bash(git show:*)",
    "Bash(git blame:*)",
    "Bash(git diff:*)",
    "Bash(git grep:*)",
    "Bash(git ls-files:*)",
    "Bash(git status:*)",
    "Bash(git rev-parse:*)",
    "Bash(git branch:*)",
    "Bash(git cat-file:*)",
    "Bash(git merge-base:*)",
    "Bash(ls:*)",
    "Bash(wc:*)",
    "Bash(cat:*)",
    "Bash(head:*)",
    "Bash(tail:*)",
    "Bash(find:*)",
    "Bash(rg:*)",
    "Bash(grep:*)",
    "Bash(tree:*)",
]


class ClaudeCodeBackend(Backend):
    name = "claude-code"

    def __init__(self, model: str, effort: str = "medium", binary: Optional[str] = None, max_cost_usd: float = 3.0, timeout_s: int = 1800, **kw):
        super().__init__(model=model, **kw)
        self.effort = effort
        self.binary = binary or find_claude_binary()
        if not self.binary:
            raise RuntimeError(
                "Claude Code CLI not found. Install it (https://code.claude.com) or set PR_REVIEWER_CLAUDE_BIN, "
                "or use the API backend with ANTHROPIC_API_KEY / OPENAI_API_KEY."
            )
        self.max_cost_usd = max_cost_usd
        self.timeout_s = timeout_s

    def describe(self) -> str:
        return f"claude-code:{self.model} (effort={self.effort}, bin={self.binary})"

    def _cmd(self, system: str, schema: Dict[str, Any]) -> List[str]:
        cmd = [
            self.binary,
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema),
            "--system-prompt",
            system,
            "--model",
            self.model,
            "--effort",
            self.effort,
            "--permission-mode",
            "dontAsk",
            "--permission-prompts",
            "none",
            "--no-session-persistence",
            "--disable-slash-commands",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--max-budget-usd",
            f"{self.max_cost_usd:.2f}",
        ]
        if self.allow_exec:
            cmd += ["--tools", "Read,Grep,Glob,LS,Bash", "--allowedTools", "Read", "Grep", "Glob", "LS", "Bash"]
        else:
            cmd += ["--tools", ",".join(READ_ONLY_TOOLS), "--allowedTools", "Read", "Grep", "Glob", "LS"] + READ_ONLY_BASH
            cmd += ["--disallowedTools", "Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch", "Agent", "Task"]
        return cmd

    def run(self, *, stage: str, system: str, prompt: str, schema: Dict[str, Any], cwd: str, label: str = "") -> LLMResult:
        cmd = self._cmd(system, schema)
        env = dict(os.environ)
        env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
        env.setdefault("DISABLE_AUTOUPDATER", "1")
        env.pop("CLAUDECODE", None)  # allow nesting when pr-reviewer itself runs inside Claude Code
        t0 = time.time()
        log.debug(f"[{label or stage}] claude -p ({self.model}/{self.effort}) in {cwd}")
        try:
            proc = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True, timeout=self.timeout_s, env=env)
        except subprocess.TimeoutExpired:
            return LLMResult(data=None, error=f"claude timed out after {self.timeout_s}s", duration_s=time.time() - t0, model=self.model)
        dur = time.time() - t0
        stdout, stderr = proc.stdout, proc.stderr
        envelope: Optional[Dict[str, Any]] = None
        try:
            envelope = json.loads(stdout)
        except ValueError:
            # stream of JSON lines or junk before the envelope: take the last {...}
            for line in reversed(stdout.splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        envelope = json.loads(line)
                        break
                    except ValueError:
                        continue
        result = LLMResult(data=None, duration_s=dur, model=self.model, raw_text=stdout[-20000:])
        if envelope is None:
            result.error = f"claude exited {proc.returncode} with no JSON output: {stderr.strip()[-800:] or stdout[-800:]}"
            self._log_call(label or stage, {"cmd": cmd[:3], "cwd": cwd, "prompt": prompt, "stdout": stdout[-20000:], "stderr": stderr[-4000:], "error": result.error})
            return result
        if isinstance(envelope, list):
            envelope = next((e for e in reversed(envelope) if isinstance(e, dict) and e.get("type") == "result"), envelope[-1] if envelope else {})
        result.cost_usd = float(envelope.get("total_cost_usd") or 0.0)
        usage = envelope.get("usage") or {}
        result.input_tokens = int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0)
        result.output_tokens = int(usage.get("output_tokens") or 0)
        result.turns = int(envelope.get("num_turns") or 0)
        data = envelope.get("structured_output")
        if data is None:
            text = envelope.get("result") or ""
            data = extract_json_object(text if isinstance(text, str) else json.dumps(text))
        if envelope.get("is_error") and data is None:
            result.error = f"claude error: {str(envelope.get('result'))[:800]}"
        if data is not None:
            coerce_to_schema(data, schema)
            problems = validate_against_schema(data, schema)
            if problems:
                log.debug(f"[{label or stage}] schema problems: {problems[:5]}")
                result.error = None if len(problems) <= 3 else "structured output failed validation: " + "; ".join(problems[:5])
            result.data = data if result.error is None else None
        elif result.error is None:
            result.error = f"no structured output (subtype={envelope.get('subtype')}, stderr={stderr.strip()[-400:]})"
        if envelope.get("permission_denials"):
            result.transcript.append({"permission_denials": envelope.get("permission_denials")})
        self._log_call(
            label or stage,
            {
                "backend": self.name,
                "model": self.model,
                "effort": self.effort,
                "cwd": cwd,
                "system": system,
                "prompt": prompt,
                "cost_usd": result.cost_usd,
                "turns": result.turns,
                "duration_s": round(dur, 1),
                "error": result.error,
                "data": result.data,
                "permission_denials": envelope.get("permission_denials"),
                "stderr": stderr[-2000:],
            },
        )
        return result
