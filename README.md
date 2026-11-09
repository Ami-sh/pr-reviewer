# pr-reviewer

**An independent, one-shot LLM reviewer for any GitHub pull request.** Give it a PR link. It checks out the repository at the PR's head commit, learns how the repo is built, reviews the change through several independent "lenses", tries to refute its own findings, and writes a draft review. You then edit the draft in a browser and post it to GitHub with one command.

```bash
python review.py https://github.com/OWNER/REPO/pull/123     # → review id: repo-123-a1b2
python review.py view --id repo-123-a1b2                    # edit in the browser
python review.py post --id repo-123-a1b2                    # post to GitHub, delete the draft
```

See **[HOW_TO_USE.md](HOW_TO_USE.md)** for the one-page cheat sheet.

## Why

Coding agents optimise for "tests pass" and "task done". A reviewer that shares the author's context, or trusts its PR description, inherits its blind spots. pr-reviewer is deliberately **independent**:

- It never sees the authoring session. It sees only the code, the repo and the PR as published, and it treats the PR description as **untrusted claims to verify**.
- One lens is dedicated to **reward hacking and test gaming**: skipped, weakened or deleted tests, hard-coded expected values, special-casing a bug report's exact input, disabled CI, broad `except` blocks, claimed work missing from the diff.
- It **reads the repository** (conventions files such as CLAUDE.md, AGENTS.md and CONTRIBUTING, callers of changed symbols, related tests, git history) before it judges anything.
- At `medium` and `high` critic levels, every finding faces a separate, adversarial **verifier** that tries to refute it. Findings that get refuted are dropped. Precision matters more than volume.

## Two pipelines

| | Claude Code method (`--claude-method`) | API method (`--api-method`) |
|---|---|---|
| LLM | your local `claude` CLI login (no API key) | Anthropic API, or any OpenAI-compatible API (OpenAI, OpenRouter, vLLM, Ollama...) |
| Repo exploration | Claude Code's Read/Grep/Glob plus read-only `git` via Bash | built-in read-only tools: `read_file`, `grep`, `list_files`, `git_log`, `git_blame`, `git_show` |
| Structured output | `claude -p --output-format json --json-schema ...` | forced `submit_answer` tool call |
| Status | tested end-to-end | tested offline with fake SDK clients; not yet run against a live API |

## Pipeline

```
fetch PR + diff + issues + CI + prior comments (GitHub REST)
  → partial clone + isolated git worktree at the PR head
  → context pack (conventions, tree, annotated diff, changed files, callers, history)
  → 1. understand   repo summary, conventions, what the PR CLAIMS vs what it DOES
  → 2. find         N independent lenses in parallel (correctness, intent/reward-hacking, tests, security, design, perf, docs/compat)
  → 3. verify       K adversarial verifier votes per finding (refute-first)
  → 4. synthesize   dedupe, final wording, verdict (APPROVE / COMMENT / REQUEST_CHANGES)
  → local/<id>/  (data/*.json + html/index.html)  → view/edit → post
```

## Install

Requires Python 3.9+, `git`, and either the [`claude` CLI](https://code.claude.com) or an API key.

```bash
git clone https://github.com/Ami-sh/pr-reviewer && cd pr-reviewer
pip install -r requirements.txt
gh auth login            # or export GITHUB_TOKEN=ghp_...
python review.py doctor
```

The tool finds the `claude` binary on your `PATH`, in `~/.claude/local`, or bundled inside the VS Code / Cursor extension. You can also set `PR_REVIEWER_CLAUDE_BIN`.

## The editor

`python review.py view --id <id>` starts a server on `127.0.0.1` and opens a page in your browser. On that page you can:

- edit the summary, the verdict, and each comment's body, suggestion and suggested code, severity, category and anchor
- **Cut** a comment so it isn't posted, or **Restore** it
- click any line number in the diff to add your own comment there
- Save with the button or ⌘/Ctrl+S; saving writes `local/<id>/data/review.json`

## Storage layout

```
local/
  <id>/
    data/   meta.json pr.json diff.patch diff.json understanding.json findings_raw.json
            verification.json review.json review.original.json llm_calls/*.json
    html/   index.html            (self-contained; also works opened as a plain file)
  _cache/repos/<host>/<owner>/<repo>   shared partial clones
```

`post` deletes `local/<id>/` after a successful post (keep it with `--keep`). The `local/` directory is git-ignored.

## Docs

- [HOW_TO_USE.md](HOW_TO_USE.md): commands and flags on one page
- [DESIGN.md](DESIGN.md): architecture, every stage, critic levels, anchoring and posting rules, security model
- [docs/RESEARCH.md](docs/RESEARCH.md): papers and tools this is based on, and what we took from each
- [CONTRIBUTING.md](CONTRIBUTING.md): dev setup, tests, how to add a lens or a backend
- [CLAUDE.md](CLAUDE.md): operating manual for Claude when running or changing this repo

## License

MIT
