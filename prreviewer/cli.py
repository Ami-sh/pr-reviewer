"""Command-line interface.

  python review.py <pr_url> [--prompt TEXT] [--claude-method|--api-method] [--critic-level low|medium|high]
  python review.py view --id <id>
  python review.py post --id <id> [--keep] [--dry-run]
  python review.py list | show --id <id> | delete --id <id> | doctor
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import __version__, log
from .config import CRITIC_PROFILES, Settings, find_claude_binary

SUBCOMMANDS = {"review", "view", "post", "list", "show", "delete", "doctor"}


def _add_id_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("id_pos", nargs="?", metavar="ID", help="review id (see `list`)")
    p.add_argument("--id", dest="id_opt", help="review id (same as the positional)")


def _resolve_id(ns: argparse.Namespace) -> str:
    rid = ns.id_opt or ns.id_pos
    if not rid:
        raise SystemExit("error: a review id is required (python review.py list)")
    return rid


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="review.py", description="Independent one-shot LLM pull-request reviewer.")
    p.add_argument("--version", action="version", version=f"pr-reviewer {__version__}")
    p.add_argument("--local-dir", "--local_dir", dest="local_dir", help="where drafts are stored (default: ./local)")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("review", help="review a PR and save a draft (default command)")
    r.add_argument("url", nargs="?", help="PR URL, e.g. https://github.com/owner/repo/pull/123 or owner/repo#123")
    r.add_argument("--url", "--pr", "--github-link", "--github_link", dest="url_opt", help="PR URL (same as the positional)")
    r.add_argument("--prompt", "-p", "--user-prompt", "--user_prompt", dest="user_prompt", default="", help="extra instructions / context / emphasis for this review")
    r.add_argument("--prompt-file", "--prompt_file", dest="prompt_file", help="read --prompt from a file")
    r.add_argument("--claude-method", "--claude_method", dest="method", action="store_const", const="claude", help="use the Claude Code CLI (no API key needed)")
    r.add_argument("--api-method", "--api_method", dest="method", action="store_const", const="api", help="use an LLM API (ANTHROPIC_API_KEY or OPENAI_API_KEY)")
    r.add_argument("--method", dest="method", choices=["auto", "claude", "api"], help="auto (default): api if a key is set, else claude")
    r.add_argument("--critic-level", "--critic_level", "-c", dest="critic_level", choices=list(CRITIC_PROFILES), help="how demanding the review is (default: medium)")
    r.add_argument("--model", help="model override (alias like sonnet/opus for claude; full id for api)")
    r.add_argument("--provider", choices=["anthropic", "openai"], help="API backend provider (default: inferred from keys)")
    r.add_argument("--allow-exec", "--allow_exec", dest="allow_exec", action="store_true", help="let the reviewer run commands/tests in the checkout (untrusted code!)")
    r.add_argument("--open", action="store_true", help="open the editor in a browser when done")
    r.add_argument("--keep-repo", "--keep_repo", dest="keep_repo", action="store_true", help="keep the checked-out worktree under local/<id>/repo")
    r.add_argument("--parallel", type=int, help="max concurrent LLM calls (default 4)")
    r.add_argument("--max-cost-usd", "--max_cost_usd", dest="max_cost_usd", type=float, help="per-call budget for the claude backend (default 3.0)")
    r.add_argument("-v", "--verbose", action="store_true")

    v = sub.add_parser("view", help="open the HTML editor for a draft")
    _add_id_arg(v)
    v.add_argument("--port", type=int, default=0)
    v.add_argument("--no-browser", "--no_browser", dest="no_browser", action="store_true")

    po = sub.add_parser("post", help="post a draft to GitHub as a PR review, then delete it locally")
    _add_id_arg(po)
    po.add_argument("--keep", action="store_true", help="keep the local draft after posting")
    po.add_argument("--dry-run", "--dry_run", dest="dry_run", action="store_true", help="print the payload, do not post")
    po.add_argument("--event", choices=["COMMENT", "REQUEST_CHANGES", "APPROVE"], help="override the verdict")
    po.add_argument("--no-footer", "--no_footer", dest="no_footer", action="store_true", help="omit the pr-reviewer footer")

    sub.add_parser("list", help="list local drafts")

    sh = sub.add_parser("show", help="print a draft as markdown (or --json)")
    _add_id_arg(sh)
    sh.add_argument("--json", action="store_true")

    d = sub.add_parser("delete", help="delete a local draft")
    _add_id_arg(d)

    sub.add_parser("doctor", help="check GitHub auth, the claude CLI and API keys")
    return p


def _preprocess_argv(argv: List[str]) -> List[str]:
    """Allow `review.py <url> ...` without the `review` word."""
    out = list(argv)
    # find first token that is not a global option
    i = 0
    while i < len(out) and out[i] in ("--local-dir", "--local_dir"):
        i += 2
    if i >= len(out):
        return out
    first = out[i]
    if first in SUBCOMMANDS or first in ("-h", "--help", "--version"):
        return out
    return out[:i] + ["review"] + out[i:]


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    ns = parser.parse_args(_preprocess_argv(argv))
    if ns.cmd is None:
        parser.print_help()
        return 2
    local_dir = ns.local_dir or os.environ.get("PR_REVIEWER_LOCAL_DIR") or None
    try:
        if ns.cmd == "review":
            return cmd_review(ns, local_dir)
        if ns.cmd == "view":
            return cmd_view(ns, local_dir)
        if ns.cmd == "post":
            return cmd_post(ns, local_dir)
        if ns.cmd == "list":
            return cmd_list(local_dir)
        if ns.cmd == "show":
            return cmd_show(ns, local_dir)
        if ns.cmd == "delete":
            return cmd_delete(ns, local_dir)
        if ns.cmd == "doctor":
            return cmd_doctor(local_dir)
    except KeyboardInterrupt:
        log.error("interrupted")
        return 130
    except Exception as e:  # keep the CLI friendly; -v shows the traceback
        if getattr(ns, "verbose", False):
            raise
        log.error(f"{type(e).__name__}: {e}")
        return 1
    return 0


# ------------------------------------------------------------------ commands


def cmd_review(ns: argparse.Namespace, local_dir: Optional[str]) -> int:
    url = ns.url_opt or ns.url
    if not url:
        raise SystemExit("error: a PR URL is required, e.g. python review.py https://github.com/owner/repo/pull/123")
    user_prompt = ns.user_prompt or ""
    if ns.prompt_file:
        with open(ns.prompt_file, encoding="utf-8") as fh:
            user_prompt = (user_prompt + "\n\n" + fh.read()).strip()
    log.set_verbose(bool(ns.verbose))
    settings = Settings.from_env(
        pr_url=url,
        user_prompt=user_prompt,
        method=ns.method,
        critic_level=ns.critic_level,
        model=ns.model,
        provider=ns.provider,
        allow_exec=bool(ns.allow_exec) or None,
        open_browser=bool(ns.open) or None,
        local_dir=local_dir,
        parallel=ns.parallel,
        max_cost_usd=ns.max_cost_usd,
        verbose=bool(ns.verbose) or None,
    )
    from .pipeline import run_review

    review = run_review(settings, keep_repo=bool(ns.keep_repo))
    active = review.active_findings()
    print()
    print(f"review id: {review.id}")
    print(f"verdict:   {review.verdict}   findings: {len(active)} active ({', '.join(f'{s}={sum(1 for f in active if f.severity == s)}' for s in ('critical', 'high', 'medium', 'low', 'nit') if any(f.severity == s for f in active)) or 'none'})")
    print(f"cost:      ${review.stats.get('cost_usd', 0):.2f} over {review.stats.get('calls', 0)} LLM call(s) in {review.stats.get('seconds_total', 0)}s")
    print()
    print(f"  view:   python review.py view --id {review.id}")
    print(f"  post:   python review.py post --id {review.id}")
    if ns.open:
        from .server import serve
        from .store import ReviewStore

        serve(ReviewStore(settings.local_dir), review.id, open_browser=True, block=True)
    return 0


def cmd_view(ns: argparse.Namespace, local_dir: Optional[str]) -> int:
    from .server import serve
    from .store import ReviewStore

    settings = Settings.from_env(local_dir=local_dir)
    serve(ReviewStore(settings.local_dir), _resolve_id(ns), port=ns.port, open_browser=not ns.no_browser, block=True)
    return 0


def cmd_post(ns: argparse.Namespace, local_dir: Optional[str]) -> int:
    from .post import post_review
    from .store import ReviewStore

    settings = Settings.from_env(local_dir=local_dir)
    result = post_review(ReviewStore(settings.local_dir), _resolve_id(ns), keep=ns.keep, dry_run=ns.dry_run, event_override=ns.event, footer=not ns.no_footer)
    if ns.dry_run:
        print(json.dumps(result["payload"], indent=2, ensure_ascii=False))
    else:
        print(f"posted {result['event']} with {result['inline']} inline comment(s): {result['html_url']}")
    return 0


def cmd_list(local_dir: Optional[str]) -> int:
    from .store import ReviewStore

    settings = Settings.from_env(local_dir=local_dir)
    rows = ReviewStore(settings.local_dir).list()
    if not rows:
        print(f"(no local drafts in {settings.local_dir})")
        return 0
    print(f"{'ID':32} {'STATUS':8} {'VERDICT':16} {'N':>3}  {'CREATED':20} PR")
    for m in rows:
        print(f"{m.get('id', ''):32} {m.get('status', ''):8} {m.get('verdict', '') or '':16} {str(m.get('findings', '') or ''):>3}  {str(m.get('created_at', ''))[:19]:20} {m.get('pr_url', '')}")
    return 0


def cmd_show(ns: argparse.Namespace, local_dir: Optional[str]) -> int:
    from .post import build_payload, load_diff
    from .store import ReviewStore

    settings = Settings.from_env(local_dir=local_dir)
    store = ReviewStore(settings.local_dir)
    rid = _resolve_id(ns)
    review = store.load_review(rid)
    if ns.json:
        print(review.to_json())
        return 0
    payload, inline, unanchored = build_payload(review, load_diff(store, rid), footer=False)
    print(f"# {review.pr.get('title')}  ({review.pr.get('url')})")
    print(f"id: {review.id}   verdict: {payload['event']}   inline: {len(inline)}   unanchored: {len(unanchored)}")
    print()
    print(payload["body"])
    print()
    for c in payload["comments"]:
        rng = f"{c['start_line']}-" if "start_line" in c else ""
        print(f"--- {c['path']}:{rng}{c['line']} ({c['side']})")
        print(c["body"])
        print()
    return 0


def cmd_delete(ns: argparse.Namespace, local_dir: Optional[str]) -> int:
    from .store import ReviewStore

    settings = Settings.from_env(local_dir=local_dir)
    store = ReviewStore(settings.local_dir)
    rid = _resolve_id(ns)
    if not store.exists(rid):
        raise SystemExit(f"error: no draft with id {rid!r}")
    store.delete(rid)
    print(f"deleted {rid}")
    return 0


def cmd_doctor(local_dir: Optional[str]) -> int:
    from .github import discover_token

    settings = Settings.from_env(local_dir=local_dir)
    ok = True
    tok = discover_token()
    print(f"GitHub token:      {'found' if tok else 'MISSING (set GITHUB_TOKEN or run `gh auth login`)'}")
    ok &= bool(tok)
    cb = settings.claude_bin or find_claude_binary()
    print(f"claude CLI:        {cb or 'not found (needed for --claude-method)'}")
    print(f"ANTHROPIC_API_KEY: {'set' if os.environ.get('ANTHROPIC_API_KEY') else 'not set'}")
    print(f"OPENAI_API_KEY:    {'set' if os.environ.get('OPENAI_API_KEY') else 'not set'}" + (f"  (base_url={os.environ['OPENAI_BASE_URL']})" if os.environ.get("OPENAI_BASE_URL") else ""))
    try:
        import anthropic  # noqa: F401

        print("anthropic sdk:     installed")
    except ImportError:
        print("anthropic sdk:     not installed (pip install anthropic) — only needed for --api-method with Anthropic")
    try:
        import openai  # noqa: F401

        print("openai sdk:        installed")
    except ImportError:
        print("openai sdk:        not installed (pip install openai) — only needed for --api-method with OpenAI-compatible APIs")
    method = settings.resolve_method()
    print(f"default method:    {method} (model {settings.resolve_model(method)})")
    print(f"local dir:         {settings.local_dir}")
    if method == "claude" and not cb:
        ok = False
    print("status:            " + ("OK" if ok else "problems found"))
    return 0 if ok else 1
