"""Backend factory."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from ..config import Settings
from .base import Backend, LLMResult  # noqa: F401


def make_backend(settings: Settings, call_logger: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> Backend:
    method = settings.resolve_method()
    profile = settings.profile
    model = settings.resolve_model(method)
    if method == "claude":
        from .claude_code import ClaudeCodeBackend

        return ClaudeCodeBackend(
            model=model,
            effort=profile.claude_effort,
            binary=settings.claude_bin,
            max_cost_usd=settings.max_cost_usd,
            max_turns=profile.max_tool_turns,
            allow_exec=settings.allow_exec,
            call_logger=call_logger,
            verbose=settings.verbose,
        )
    from .api import APIBackend

    return APIBackend(
        model=model,
        provider=settings.resolve_provider(),
        max_turns=profile.max_tool_turns,
        allow_exec=settings.allow_exec,
        call_logger=call_logger,
        verbose=settings.verbose,
    )
