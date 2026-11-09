"""Offline tests of the API backend's tool loop using fake SDK clients (no keys, no network)."""
import json
import sys
import types
from types import SimpleNamespace as NS

import pytest

from prreviewer.backends.api import SUBMIT_TOOL, APIBackend

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


class _Block(NS):
    def model_dump(self):
        return {k: v for k, v in self.__dict__.items()}


def _install_fake_anthropic(monkeypatch, script):
    calls = []

    class Messages:
        def create(self, **kw):
            calls.append(kw)
            return script.pop(0)

    class Anthropic:
        def __init__(self, **kw):
            self.messages = Messages()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Anthropic))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    return calls


def test_anthropic_loop_uses_tools_then_submits(monkeypatch, tmp_path):
    (tmp_path / "hello.py").write_text("x = 42\n")
    usage = NS(input_tokens=100, output_tokens=10)
    script = [
        NS(usage=usage, content=[_Block(type="tool_use", id="t1", name="read_file", input={"path": "hello.py"})]),
        NS(usage=usage, content=[_Block(type="tool_use", id="t2", name=SUBMIT_TOOL, input={"answer": "42"})]),
    ]
    calls = _install_fake_anthropic(monkeypatch, script)
    be = APIBackend(model="claude-sonnet-5-5", provider="anthropic", max_turns=5)
    res = be.run(stage="t", system="sys", prompt="p", schema=SCHEMA, cwd=str(tmp_path))
    assert res.ok and res.data == {"answer": "42"}
    assert res.turns == 2 and res.input_tokens == 200 and res.cost_usd > 0
    msgs = calls[1]["messages"]  # same list object the backend keeps appending to
    tool_result = next(c for m in msgs if m["role"] == "user" and isinstance(m["content"], list) for c in m["content"] if c.get("type") == "tool_result")
    assert tool_result["tool_use_id"] == "t1" and "x = 42" in tool_result["content"]
    assert any(t["name"] == SUBMIT_TOOL for t in calls[0]["tools"])


def test_anthropic_forces_submit_after_budget(monkeypatch, tmp_path):
    usage = NS(input_tokens=1, output_tokens=1)
    explore = lambda i: NS(usage=usage, content=[_Block(type="tool_use", id=f"t{i}", name="list_files", input={})])
    script = [explore(0), explore(1), NS(usage=usage, content=[_Block(type="tool_use", id="s", name=SUBMIT_TOOL, input={"answer": "done"})])]
    calls = _install_fake_anthropic(monkeypatch, script)
    res = APIBackend(model="m", provider="anthropic", max_turns=2).run(stage="t", system="s", prompt="p", schema=SCHEMA, cwd=str(tmp_path))
    assert res.data == {"answer": "done"}
    assert calls[-1]["tool_choice"] == {"type": "tool", "name": SUBMIT_TOOL}


def test_openai_loop(monkeypatch, tmp_path):
    (tmp_path / "a.txt").write_text("hi\n")

    def msg(tool_calls=None, content=None):
        m = NS(tool_calls=tool_calls, content=content)
        m.model_dump = lambda exclude_none=True: {"role": "assistant", "content": content}
        return NS(choices=[NS(message=m)], usage=NS(prompt_tokens=5, completion_tokens=2))

    fn = lambda name, args, i: NS(id=f"c{i}", function=NS(name=name, arguments=json.dumps(args)))
    script = [msg([fn("read_file", {"path": "a.txt"}, 1)]), msg([fn(SUBMIT_TOOL, {"answer": "hi"}, 2)])]
    calls = []

    class Completions:
        def create(self, **kw):
            calls.append(kw)
            return script.pop(0)

    class OpenAI:
        def __init__(self, **kw):
            self.chat = NS(completions=Completions())

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=OpenAI))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    res = APIBackend(model="gpt-5", provider="openai", max_turns=5).run(stage="t", system="s", prompt="p", schema=SCHEMA, cwd=str(tmp_path))
    assert res.ok and res.data == {"answer": "hi"}
    tool_msg = next(m for m in calls[1]["messages"] if m.get("role") == "tool")
    assert tool_msg["tool_call_id"] == "c1" and "hi" in tool_msg["content"]


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        APIBackend(model="m", provider="anthropic")
