"""Settings: CLI flags + environment variables + critic-level profiles.

Environment variables (all optional):
  GITHUB_TOKEN / GH_TOKEN         GitHub token (else we ask `gh auth token`)
  PR_REVIEWER_METHOD              auto | claude | api            (default: auto)
  PR_REVIEWER_CRITIC_LEVEL        low | medium | high            (default: medium)
  PR_REVIEWER_MODEL               model override for whichever backend is used
  PR_REVIEWER_PROVIDER            anthropic | openai             (API backend; default: inferred from keys)
  ANTHROPIC_API_KEY               enables the API backend with Anthropic
  OPENAI_API_KEY (+OPENAI_BASE_URL) enables the API backend with any OpenAI-compatible server
  PR_REVIEWER_CLAUDE_BIN          path to the `claude` CLI (else auto-discovered)
  PR_REVIEWER_LOCAL_DIR           where drafts live (default: <repo>/local)
  PR_REVIEWER_MAX_COST_USD        per-`claude -p` call budget (default 3.00)
  PR_REVIEWER_PARALLEL            max concurrent LLM calls (default 4)
"""
from __future__ import annotations

import glob
import os
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LOCAL_DIR = os.path.join(PROJECT_ROOT, "local")

# --------------------------------------------------------------- critic levels
# Each level decides: which review lenses run, how many independent verifier
# votes each finding must survive, the minimum severity we keep, and the default
# model tier.  See DESIGN.md "Critic levels".

ALL_LENSES = [
    "correctness",
    "intent_and_reward_hacking",
    "tests",
    "security",
    "design",
    "performance",
    "docs_and_compat",
]


@dataclass(frozen=True)
class CriticProfile:
    name: str
    lenses: List[str]
    verify_votes: int  # 0 = skip verification
    min_severity: str  # findings below this are dropped from the posted review
    min_confidence: float  # after verification
    claude_model: str  # alias for the Claude Code backend
    claude_effort: str
    api_model_anthropic: str
    api_model_openai: str
    max_tool_turns: int  # API backend agent loop cap per stage
    understand_depth: str  # short | normal | deep


CRITIC_PROFILES: Dict[str, CriticProfile] = {
    "low": CriticProfile(
        name="low",
        lenses=["correctness", "intent_and_reward_hacking"],
        verify_votes=0,
        min_severity="medium",
        min_confidence=0.6,
        claude_model="sonnet",
        claude_effort="low",
        api_model_anthropic="claude-sonnet-5-5",
        api_model_openai="gpt-5-mini",
        max_tool_turns=12,
        understand_depth="short",
    ),
    "medium": CriticProfile(
        name="medium",
        lenses=["correctness", "intent_and_reward_hacking", "tests", "security", "design"],
        verify_votes=1,
        min_severity="low",
        min_confidence=0.5,
        claude_model="sonnet",
        claude_effort="medium",
        api_model_anthropic="claude-sonnet-5-5",
        api_model_openai="gpt-5",
        max_tool_turns=25,
        understand_depth="normal",
    ),
    "high": CriticProfile(
        name="high",
        lenses=list(ALL_LENSES),
        verify_votes=3,
        min_severity="nit",
        min_confidence=0.4,
        claude_model="opus",
        claude_effort="high",
        api_model_anthropic="claude-opus-5-5",
        api_model_openai="gpt-5",
        max_tool_turns=40,
        understand_depth="deep",
    ),
}


def find_claude_binary() -> Optional[str]:
    """Locate the Claude Code CLI.

    Order: $PR_REVIEWER_CLAUDE_BIN, PATH, ~/.claude/local, the native binary
    bundled with the VS Code / Cursor / Windsurf extensions (newest version).
    """
    env = os.environ.get("PR_REVIEWER_CLAUDE_BIN")
    if env and os.path.isfile(env):
        return env
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    home = os.path.expanduser("~")
    candidates: List[str] = [
        os.path.join(home, ".claude", "local", "claude"),
        os.path.join(home, ".claude", "local", "bin", "claude"),
        os.path.join(home, ".local", "bin", "claude"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ]
    for ext_root in (".vscode", ".vscode-insiders", ".cursor", ".windsurf"):
        pattern = os.path.join(home, ext_root, "extensions", "anthropic.claude-code-*", "resources", "native-binary", "claude")
        found = sorted(glob.glob(pattern), key=_version_key)
        candidates.extend(reversed(found))  # newest first
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _version_key(path: str):
    import re

    m = re.search(r"claude-code-(\d+)\.(\d+)\.(\d+)", path)
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


@dataclass
class Settings:
    pr_url: str = ""
    user_prompt: str = ""
    method: str = "auto"  # auto | claude | api
    critic_level: str = "medium"
    model: Optional[str] = None
    provider: Optional[str] = None  # anthropic | openai (API backend)
    allow_exec: bool = False  # let the reviewer run tests/commands (claude backend only)
    open_browser: bool = False
    local_dir: str = DEFAULT_LOCAL_DIR
    max_cost_usd: float = 3.0
    parallel: int = 4
    verbose: bool = False
    claude_bin: Optional[str] = None
    extra: Dict[str, str] = field(default_factory=dict)

    # ---- derived -----------------------------------------------------------
    @property
    def profile(self) -> CriticProfile:
        return CRITIC_PROFILES[self.critic_level]

    def resolve_method(self) -> str:
        """auto → 'api' if an API key is present, else 'claude'."""
        if self.method in ("claude", "api"):
            return self.method
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"):
            return "api"
        return "claude"

    def resolve_provider(self) -> str:
        if self.provider:
            return self.provider
        env = os.environ.get("PR_REVIEWER_PROVIDER")
        if env:
            return env
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic"
        if os.environ.get("OPENAI_API_KEY"):
            return "openai"
        return "anthropic"

    def resolve_model(self, method: str) -> str:
        if self.model:
            return self.model
        env = os.environ.get("PR_REVIEWER_MODEL")
        if env:
            return env
        p = self.profile
        if method == "claude":
            return p.claude_model
        return p.api_model_anthropic if self.resolve_provider() == "anthropic" else p.api_model_openai

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        s = cls()
        s.method = os.environ.get("PR_REVIEWER_METHOD", s.method)
        s.critic_level = os.environ.get("PR_REVIEWER_CRITIC_LEVEL", s.critic_level)
        s.local_dir = os.environ.get("PR_REVIEWER_LOCAL_DIR", s.local_dir)
        s.max_cost_usd = float(os.environ.get("PR_REVIEWER_MAX_COST_USD", s.max_cost_usd))
        s.parallel = int(os.environ.get("PR_REVIEWER_PARALLEL", s.parallel))
        s.claude_bin = os.environ.get("PR_REVIEWER_CLAUDE_BIN") or None
        for k, v in overrides.items():
            if v is not None:
                setattr(s, k, v)
        if s.critic_level not in CRITIC_PROFILES:
            raise ValueError(f"critic level must be one of {list(CRITIC_PROFILES)}, got {s.critic_level!r}")
        return s
