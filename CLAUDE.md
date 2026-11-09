# CLAUDE.md: operating manual for this repo

This file is for Claude (Claude Code) when the owner asks you to **run**, **debug** or **change** pr-reviewer. Read it fully before acting. README.md is for users and DESIGN.md is the architecture; this file covers what you need to operate safely and cheaply.

## What this is

`python review.py <PR url>` reviews any GitHub PR in one shot. It runs the stages fetch → partial clone + worktree → understand → find (parallel lenses) → adversarial verify → synthesize, then saves a draft under `local/<id>/`. The human edits it with `python review.py view --id <id>` and publishes with `python review.py post --id <id>`, which deletes the draft. There are two LLM backends: the Claude Code CLI (`--claude-method`) and the APIs (`--api-method`, Anthropic or OpenAI-compatible).

## Owner context

- GitHub user: **Ami-sh**. The `gh` CLI is at `~/.local/bin/gh` (not on the default PATH, so `export PATH="$HOME/.local/bin:$PATH"`). Git auth goes through `gh auth git-credential`.
- System Python is 3.9 (`/usr/bin/python3`), with packages installed using `pip --user`. It prints a harmless urllib3 LibreSSL warning; use `python3 -W ignore` to silence it.
- There is no `claude` on PATH. The binary is bundled with the VS Code extension: `~/.vscode/extensions/anthropic.claude-code-<ver>-darwin-arm64/resources/native-binary/claude`. `config.find_claude_binary()` picks the newest one automatically.
- The owner has a **limited credit budget**. Default to `--critic-level low` for test runs unless they ask for more, and tell them before anything expensive (`high` runs Opus with 3 votes per finding).
- There are no API keys on this machine, so the API backend can only be tested with fakes (`tests/test_api_backend.py`).

## Running a review for the owner

```bash
cd ~/Desktop/pr-reviewer && export PATH="$HOME/.local/bin:$PATH"
python3 -W ignore review.py <PR_URL> [--prompt "..."] [--critic-level low|medium|high] [--claude-method]
python3 -W ignore review.py show --id <id>          # read the draft in the terminal
python3 -W ignore review.py view --id <id>          # blocks; serves the editor (run in background or let the user run it)
python3 -W ignore review.py post --id <id> --dry-run   # inspect the payload
python3 -W ignore review.py post --id <id>          # PUBLISHES to GitHub, so confirm with the owner first
```

- **Posting is outward-facing.** Never run `post` without `--dry-run` unless the owner explicitly asks you to post that review.
- `view` blocks with `serve_forever`. When running it yourself, use `run_in_background`, or use `--no-browser` and drive it with `curl` (`GET /api/review`, `POST /api/save`).
- Cost and timing are printed at the end of a run and stored in `review.stats`. Every LLM call is logged in `local/<id>/data/llm_calls/*.json`, which is the first place to look when a stage misbehaves.
- A run inside a Claude Code session works because the backend removes the `CLAUDECODE` environment variable.

## Where to change things

| Want to... | Edit |
|---|---|
| change what reviewers look for | `prreviewer/prompts/lenses/<lens>.md` (one lens) or `prompts/system.md` (all stages) |
| change stage instructions or output rules | `prompts/understand.md`, `find.md`, `verify.md`, `synthesize.md` (placeholders are `{{name}}`, rendered by `prompts.render`) |
| change lenses, votes, models, thresholds per level | `CRITIC_PROFILES` in `prreviewer/config.py` |
| change the output fields | `prreviewer/schema.py`: dataclass plus JSON Schema, then `templates/view.html` and the tests |
| change the context given to models | `prreviewer/context.py` (budgets in `CHAR_BUDGETS`) |
| change claude CLI flags or tool permissions | `prreviewer/backends/claude_code.py` (`_cmd`, `READ_ONLY_BASH`) |
| change API tool definitions | `prreviewer/backends/tools.py` |
| change dedupe, vote aggregation or verdict rules | `prreviewer/pipeline.py` (`dedupe`, `aggregate_votes`, `enforce_verdict`) |
| change how comments are anchored or posted | `prreviewer/post.py` and `prreviewer/diff.py` (`snap`, `commentable`) |
| change the editor UI | `prreviewer/templates/view.html` (vanilla JS; data injected at `/*__DATA__*/null`) |

## Invariants: do not break these

1. **The reviewed code is untrusted.** The default tool set is read-only (Read/Grep/Glob/LS plus an allowlist of read-only git and shell commands). Don't add write or exec tools without an opt-in flag.
2. **The reviewer stays isolated from the reviewed repo's Claude config:** keep `--setting-sources ""`, `--disable-slash-commands` and `--strict-mcp-config`. Don't switch to `--bare`, because it disables OAuth and breaks the no-API-key method.
3. **Line anchors use GitHub semantics:** `side=RIGHT` with new-file numbers for added and context lines, `side=LEFT` with old-file numbers for removed lines. The annotated diff's `R<n>`/`L<n>` tags must keep mapping one-to-one onto them.
4. **Unit tests are offline.** `python3 -W ignore -m pytest -q` must pass with no network, and takes under 1 second.
5. **`local/` is never committed** (it's in .gitignore). It contains the cache clones, which hold tokens in their remote URLs.
6. **The verifier must not see the finder's reasoning**, only the claim and its evidence. This keeps it independent; see the research notes.

## Verifying a change

1. `python3 -W ignore -m compileall -q prreviewer && python3 -W ignore -m pytest -q`
2. If you touched prompts, the pipeline or a backend, do a real run on the playground PR, which has seven planted problems:
   `python3 -W ignore review.py https://github.com/Ami-sh/pr-reviewer-playground/pull/2 --critic-level low` (about $0.30, about 45 s), then `show --id <id>`. Expect findings for `shell=True`, the hard-coded `"2D"`, the uncapped backoff, `<=` vs `<`, the removed notify, the skipped test and the weakened `90m` assertion. Delete the draft afterwards (`delete --id`), or post it only if the owner asks.
3. If you touched the editor, syntax-check its JS without a browser:
   `python3 -c "import re;s=open('prreviewer/templates/view.html').read();open('/tmp/v.js','w').write('function f(){'+re.findall(r'<script>(.*?)</script>',s,re.S)[0]+'}')" && /System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc /tmp/v.js`
   Then exercise the server through `serve(..., block=False)`, as in the test in CONTRIBUTING.

## Build history and references

- Built 2026-11-09. The end-to-end test on playground PR #2 (`--critic-level low`) found all seven planted issues, cost $0.29, and took 43 s. The post was verified: 4 inline comments landed, the comment cut in the editor was omitted, the own-PR REQUEST_CHANGES fell back to COMMENT, and the local draft was deleted.
- `docs/RESEARCH.md` lists the papers and tools the design draws on. Its links were written from memory and **not re-verified online** (the verification job was stopped to save credits). Check a link before quoting it.

## Commit conventions

Commit as the owner's git identity. End commit messages with the attribution trailer the harness specifies. Never commit `local/`.
