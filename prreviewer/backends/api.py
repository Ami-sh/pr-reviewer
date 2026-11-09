"""API backend: Anthropic Messages API or any OpenAI-compatible chat API, with a
read-only tool loop over the PR checkout and a forced `submit` tool call for the
final structured answer.

Provider selection (config.Settings.resolve_provider):
  anthropic  -> ANTHROPIC_API_KEY  (+ optional ANTHROPIC_BASE_URL)
  openai     -> OPENAI_API_KEY     (+ optional OPENAI_BASE_URL; works for OpenAI,
                OpenRouter, Together, Groq, Ollama, vLLM, Gemini's compat endpoint ...)
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from .. import log
from .base import Backend, LLMResult, coerce_to_schema, extract_json_object, validate_against_schema
from .tools import ToolBox

SUBMIT_TOOL = "submit_answer"


def _submit_spec(schema: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": SUBMIT_TOOL,
        "description": "Submit your final answer. Call this exactly once, when you are done investigating. The input must match the schema exactly.",
        "input_schema": schema,
    }


# Rough list prices (USD per 1M tokens) so the stats are not blank; override via env.
_PRICES = {
    "claude-opus": (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (1.0, 5.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-5": (1.25, 10.0),
    "gpt-4o": (2.5, 10.0),
}


def _estimate_cost(model: str, inp: int, out: int) -> float:
    for k, (pi, po) in _PRICES.items():
        if model.startswith(k):
            return inp / 1e6 * pi + out / 1e6 * po
    return 0.0


class APIBackend(Backend):
    name = "api"

    def __init__(self, model: str, provider: str = "anthropic", max_tokens: int = 8000, **kw):
        super().__init__(model=model, **kw)
        self.provider = provider
        self.max_tokens = max_tokens
        if provider == "anthropic":
            try:
                import anthropic  # noqa: F401
            except ImportError as e:
                raise RuntimeError("pip install anthropic   (required for the Anthropic API backend)") from e
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError("ANTHROPIC_API_KEY is not set")
        elif provider == "openai":
            try:
                import openai  # noqa: F401
            except ImportError as e:
                raise RuntimeError("pip install openai   (required for the OpenAI-compatible API backend)") from e
            if not os.environ.get("OPENAI_API_KEY"):
                raise RuntimeError("OPENAI_API_KEY is not set")
        else:
            raise RuntimeError(f"unknown provider {provider!r} (anthropic|openai)")

    def describe(self) -> str:
        return f"api:{self.provider}:{self.model}"

    def run(self, *, stage: str, system: str, prompt: str, schema: Dict[str, Any], cwd: str, label: str = "") -> LLMResult:
        toolbox = ToolBox(cwd, allow_exec=self.allow_exec)
        t0 = time.time()
        if self.provider == "anthropic":
            res = self._run_anthropic(system, prompt, schema, toolbox, label or stage)
        else:
            res = self._run_openai(system, prompt, schema, toolbox, label or stage)
        res.duration_s = time.time() - t0
        res.model = self.model
        if res.data is not None:
            coerce_to_schema(res.data, schema)
            problems = validate_against_schema(res.data, schema)
            if len(problems) > 3:
                res.error = "structured output failed validation: " + "; ".join(problems[:5])
                res.data = None
        self._log_call(
            label or stage,
            {
                "backend": self.describe(),
                "cwd": cwd,
                "system": system,
                "prompt": prompt,
                "turns": res.turns,
                "cost_usd": res.cost_usd,
                "input_tokens": res.input_tokens,
                "output_tokens": res.output_tokens,
                "duration_s": round(res.duration_s, 1),
                "error": res.error,
                "data": res.data,
                "transcript": res.transcript[-60:],
            },
        )
        return res

    # ------------------------------------------------------------ anthropic
    def _run_anthropic(self, system: str, prompt: str, schema: Dict[str, Any], toolbox: ToolBox, label: str) -> LLMResult:
        import anthropic

        client = anthropic.Anthropic(base_url=os.environ.get("ANTHROPIC_BASE_URL") or None, max_retries=3)
        tools = toolbox.anthropic_tools() + [_submit_spec(schema)]
        messages: List[Dict[str, Any]] = [{"role": "user", "content": prompt}]
        res = LLMResult(data=None)
        for turn in range(self.max_turns + 2):
            force_submit = turn >= self.max_turns
            kwargs: Dict[str, Any] = dict(model=self.model, system=system, max_tokens=self.max_tokens, messages=messages, tools=tools)
            kwargs["tool_choice"] = {"type": "tool", "name": SUBMIT_TOOL} if force_submit else {"type": "auto"}
            if force_submit:
                messages.append({"role": "user", "content": "You have used your exploration budget. Submit your final answer now with submit_answer."})
            try:
                msg = client.messages.create(**kwargs)
            except Exception as e:  # network/API error
                res.error = f"anthropic error: {e}"
                return res
            res.turns += 1
            res.input_tokens += getattr(msg.usage, "input_tokens", 0) or 0
            res.output_tokens += getattr(msg.usage, "output_tokens", 0) or 0
            content = msg.content
            messages.append({"role": "assistant", "content": [c.model_dump() for c in content]})
            tool_uses = [c for c in content if c.type == "tool_use"]
            texts = [c.text for c in content if c.type == "text"]
            if texts:
                res.transcript.append({"assistant": "\n".join(texts)[:2000]})
            submitted = next((c for c in tool_uses if c.name == SUBMIT_TOOL), None)
            if submitted:
                res.data = submitted.input if isinstance(submitted.input, dict) else None
                res.raw_text = json.dumps(submitted.input)
                break
            if not tool_uses:
                # model answered in prose; try to salvage JSON, else nudge
                data = extract_json_object("\n".join(texts))
                if data:
                    res.data = data
                    break
                messages.append({"role": "user", "content": "Please call submit_answer with your final structured answer."})
                continue
            results = []
            for tu in tool_uses:
                out = toolbox.call(tu.name, tu.input or {})
                res.transcript.append({"tool": tu.name, "input": tu.input, "output": out[:1500]})
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out})
            messages.append({"role": "user", "content": results})
        if res.data is None and res.error is None:
            res.error = "model never submitted a structured answer"
        res.cost_usd = _estimate_cost(self.model, res.input_tokens, res.output_tokens)
        return res

    # --------------------------------------------------------------- openai
    def _run_openai(self, system: str, prompt: str, schema: Dict[str, Any], toolbox: ToolBox, label: str) -> LLMResult:
        import openai

        client = openai.OpenAI(base_url=os.environ.get("OPENAI_BASE_URL") or None, max_retries=3)
        tools = toolbox.openai_tools() + [
            {"type": "function", "function": {"name": SUBMIT_TOOL, "description": _submit_spec(schema)["description"], "parameters": schema}}
        ]
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        res = LLMResult(data=None)
        for turn in range(self.max_turns + 2):
            force_submit = turn >= self.max_turns
            if force_submit:
                messages.append({"role": "user", "content": "You have used your exploration budget. Call submit_answer now."})
            kwargs: Dict[str, Any] = dict(model=self.model, messages=messages, tools=tools)
            kwargs["tool_choice"] = {"type": "function", "function": {"name": SUBMIT_TOOL}} if force_submit else "auto"
            try:
                resp = client.chat.completions.create(**kwargs)
            except Exception as e:
                res.error = f"openai error: {e}"
                return res
            res.turns += 1
            if resp.usage:
                res.input_tokens += resp.usage.prompt_tokens or 0
                res.output_tokens += resp.usage.completion_tokens or 0
            choice = resp.choices[0]
            m = choice.message
            messages.append(m.model_dump(exclude_none=True))
            calls = m.tool_calls or []
            if m.content:
                res.transcript.append({"assistant": m.content[:2000]})
            submitted = next((c for c in calls if c.function.name == SUBMIT_TOOL), None)
            if submitted:
                try:
                    res.data = json.loads(submitted.function.arguments or "{}")
                except ValueError:
                    res.data = extract_json_object(submitted.function.arguments or "")
                res.raw_text = submitted.function.arguments or ""
                break
            if not calls:
                data = extract_json_object(m.content or "")
                if data:
                    res.data = data
                    break
                messages.append({"role": "user", "content": "Please call submit_answer with your final structured answer."})
                continue
            for c in calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except ValueError:
                    args = {}
                out = toolbox.call(c.function.name, args)
                res.transcript.append({"tool": c.function.name, "input": args, "output": out[:1500]})
                messages.append({"role": "tool", "tool_call_id": c.id, "content": out})
        if res.data is None and res.error is None:
            res.error = "model never submitted a structured answer"
        res.cost_usd = _estimate_cost(self.model, res.input_tokens, res.output_tokens)
        return res
