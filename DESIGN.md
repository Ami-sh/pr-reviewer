# Design

This document explains how pr-reviewer is built and why. The references behind each decision are in [docs/RESEARCH.md](docs/RESEARCH.md).

## Goals and non-goals

**Goals**

1. **One command, any public or private GitHub PR**, draft or ready.
2. **Independence.** The reviewer shares no context with whoever wrote the PR, human or agent. It treats the PR description, commit messages and comments as untrusted claims, and it checks for reward hacking and test gaming specifically.
3. **Understanding before judging.** The reviewer works in a real checkout of the PR head, reads the repo's conventions, and explores callers and tests with tools.
4. **Precision.** Fewer, verified, well-anchored comments beat many speculative ones.
5. **Human in the loop at the end.** Every review is a draft. A human edits it, then posts it.
6. **Two interchangeable backends:** the Claude Code CLI (no API key) and the LLM APIs.

**Non-goals.** There is no back-and-forth loop with the author: the review is one shot, then the PR gets fixed, and that's the end. There is no hosted bot or webhook, and no auto-approval.

## Module map

| Module | Responsibility |
|---|---|
| `review.py` | entry point that puts the project on `sys.path` and calls `prreviewer.cli.main` |
| `prreviewer/cli.py` | argparse commands: `review` (default), `view`, `post`, `list`, `show`, `delete`, `doctor` |
| `prreviewer/config.py` | `Settings`, environment variables, **critic profiles**, discovery of the `claude` binary |
| `prreviewer/urls.py` | PR URL parsing (github.com, GHE, `owner/repo#N`, `/files` suffixes) |
| `prreviewer/github.py` | REST client: PR, files, diff, comments, reviews, commits, CI, linked issues; `post_review` |
| `prreviewer/repo.py` | partial clone cache, `pull/N/head` fetch, isolated worktree per review, git queries |
| `prreviewer/diff.py` | unified-diff parser, `L<n>`/`R<n>` annotation, commentable-line set, `snap()`, changed symbols |
| `prreviewer/context.py` | builds the **context pack** within character budgets |
| `prreviewer/prompts/*.md` | system, understand, find, verify and synthesize templates, plus `lenses/*.md` charters |
| `prreviewer/backends/` | `base.py` (interface, JSON helpers), `claude_code.py`, `api.py`, `tools.py` (read-only toolbox) |
| `prreviewer/pipeline.py` | stage orchestration, normalisation, dedupe, vote aggregation, thresholds, verdict rules |
| `prreviewer/store.py` | `local/<id>/` layout and atomic JSON writes |
| `prreviewer/render.py` + `templates/view.html` | self-contained HTML editor with the review and diff embedded as JSON |
| `prreviewer/server.py` | `127.0.0.1` server for the editor (`GET /`, `GET /api/review`, `POST /api/save`) |
| `prreviewer/post.py` | turns a stored review into a GitHub review payload (anchor validation, fallbacks) and posts it |
| `prreviewer/schema.py` | dataclasses (`PRInfo`, `Finding`, `Understanding`, `Review`) and the JSON Schemas given to the LLM |

## Stage by stage

### 0. Fetch and checkout

- `GET /repos/{o}/{r}/pulls/{n}` (metadata, `head.sha`, `base.sha`, draft), `/files`, `/comments`, `/issues/{n}/comments`, `/reviews`, `/commits`, check-runs and combined status for the head SHA, plus linked issues parsed from the body (`Fixes #N`, `closes owner/repo#N`, issue URLs; up to 5).
- The diff comes from `Accept: application/vnd.github.v3.diff`. For huge PRs, where that endpoint refuses, the diff is assembled from the per-file `patch` fields. If that is also empty, it falls back to the local three-dot `git diff merge-base..head`.
- The repo is a **partial clone** (`--filter=blob:none --no-checkout`) cached in `local/_cache/repos/...`. This gives full history for `git log`/`blame` without downloading every blob. Each run fetches `+refs/pull/N/head`, which also works for PRs from forks, and the base branch, then runs `git worktree add --detach local/<id>/repo <head_sha>`. Every review gets its own worktree, so parallel reviews never collide. The worktree is removed at the end of the run unless you pass `--keep-repo`.
- Tokens come from `$GITHUB_TOKEN` / `$GH_TOKEN`, or from `gh auth token`.

### Context pack (`context.py`)

The same pack goes to every stage, under per-depth character budgets (`short`/`normal`/`deep`). It contains:

- **PR header:** title, author, draft flag, refs, size, labels, the description wrapped in `<pr_description>` and explicitly labelled UNTRUSTED, linked issues, commit subjects, CI state, and existing reviews and comments (so we don't repeat them).
- **Conventions:** CLAUDE.md, AGENTS.md, .cursorrules, CONTRIBUTING, the PR template, README, and build/lint configs, in that priority order.
- **Tree:** directory counts at depth ≤ 3, plus the full listing of every directory the PR touches.
- **Annotated diff:** every line tagged `R<n>` (new-file line, side RIGHT) or `L<n>` (old-file line, side LEFT). This maps one-to-one onto GitHub's `(side, line)`, so the model can cite exact anchors. The idea comes from PR-Agent's `__new hunk__`/`__old hunk__` format.
- **Changed files:** the full new content of files up to 400 lines, otherwise ±25 lines around each hunk, all with line numbers.
- **Related code:** likely test files for each changed file (matched by name), and `git grep` callers of the changed symbols. Symbols come from hunk headers and from `def`/`class`/`function`/`fn`/`func` lines.
- **History:** `git log` of the touched paths.
- The user's `--prompt`, inserted as highest-priority `<reviewer_instructions>`.

### 1. Understand

A single call. The model explores with tools and returns an `Understanding`: repo summary, languages, conventions (quoted with their source), **pr_claims** (what the PR says it does), **change_summary** (what the diff actually does), risk areas, files to read, and notes for reviewers. Separating claims from reality up front is what feeds the reward-hacking lens later.

### 2. Find (lenses)

Each lens is an independent call with its own charter (`prompts/lenses/*.md`) and the same context and understanding. The lenses run in parallel threads (`--parallel`, default 4).

| Lens | Charter |
|---|---|
| `correctness` | logic, boundaries, state, contracts with callers, API misuse, removed code |
| `intent_and_reward_hacking` | claims vs diff, symptom patching, stubs, test gaming, CI tampering, scope creep |
| `tests` | is every behaviour change tested, and would the tests fail without it? Changed tests compared against the base version |
| `security` | source-to-sink tracing, authN/Z, secrets, supply chain, CI `pull_request_target` |
| `design` | fit with the repo's patterns, layering, complexity, reinventing existing helpers |
| `performance` | algorithmic and I/O regressions on realistic hot paths only |
| `docs_and_compat` | public surface changes, defaults, migrations, missing docs and changelog |

Normalisation (`pipeline._normalise_finding`):

- enum casing is fixed, and `"R12"`-style line strings are parsed
- paths are mapped onto real diff paths (`_match_path` strips `a/` and `b/`, and resolves suffix and basename matches)
- each anchor is snapped to the nearest commentable line within 5 lines
- invalid `start_line` values are dropped

Then `dedupe()` merges findings that point at the same place with similar titles or explanations (difflib ratio > 0.6). It records `also_found_by` and keeps the maximum confidence.

### 3. Verify (adversarial)

For each finding, the verifier runs `verify_votes` independent calls. Each call sees **only the claim and the evidence**, never the finder's reasoning or chain of thought, so it isn't anchored. It is told to *try to refute* the finding. Each call returns `confirmed` / `refuted` / `uncertain`, its own confidence, and optional corrections (severity, path, line, side, a better explanation).

Aggregation (`aggregate_votes`):

- More refutes than confirms means the finding is dropped.
- Otherwise confidence = `0.3 × finder + 0.7 × mean(verifier)`.
- A correction is applied only when a majority of votes propose it.

After verification, findings are filtered by the profile's `min_severity` and `min_confidence`. Critical findings are never dropped for low confidence.

### 4. Synthesize

A single call that receives the surviving findings plus their verification summaries. It returns `keep`/`merged_into` for each finding, its final `body`, an adjusted severity, and `summary_markdown` with this fixed structure: what the PR does vs. what it claims, the verdict, key issues, also worth addressing, what's good, and review scope.

`enforce_verdict()` then applies hard rules the model can't override:

- any critical finding means REQUEST_CHANGES
- a blocking high finding means REQUEST_CHANGES (except on draft PRs, which get COMMENT)
- APPROVE with remaining high or medium findings is downgraded to COMMENT

## Critic levels (`config.CRITIC_PROFILES`)

| | low | medium (default) | high |
|---|---|---|---|
| Lenses | correctness, intent_and_reward_hacking | + tests, security, design | all 7 |
| Verifier votes per finding | 0 (skip) | 1 | 3 (majority) |
| Minimum severity kept | medium | low | nit |
| Minimum confidence | 0.6 | 0.5 | 0.4 |
| Claude Code model / effort | sonnet / low | sonnet / medium | opus / high |
| API model (Anthropic / OpenAI) | claude-sonnet-5-5 / gpt-5-mini | claude-sonnet-5-5 / gpt-5 | claude-opus-5-5 / gpt-5 |
| Tool turns per call (API) | 12 | 25 | 40 |
| Context depth | short | normal | deep |
| Max findings per lens | 6 | 10 | 15 |

Approximate LLM calls per run: `2 + lenses + findings × votes`.

## Backends

Both backends implement `Backend.run(stage, system, prompt, schema, cwd, label) -> LLMResult`.

### Claude Code (`backends/claude_code.py`)

```
claude -p --output-format json --json-schema <schema> --system-prompt <system.md>
       --model <alias> --effort <level> --permission-mode dontAsk --permission-prompts none
       --no-session-persistence --disable-slash-commands --setting-sources "" --strict-mcp-config
       --max-budget-usd <n> --tools Read,Grep,Glob,LS,Bash
       --allowedTools Read Grep Glob LS "Bash(git log:*)" "Bash(git show:*)" ... (read-only allowlist)
       --disallowedTools Edit Write MultiEdit NotebookEdit WebFetch WebSearch Agent Task
```

- The prompt goes in on stdin, and `cwd` is the PR worktree. The answer is read from `structured_output` in the JSON envelope, falling back to parsing JSON out of `result`. Cost, turns and tokens are read from the envelope as well.
- `--setting-sources ""` and `--disable-slash-commands` keep the reviewed repo's `.claude/settings.json`, hooks and commands out of the run. A malicious PR must not be able to run hooks or rewrite the reviewer's rules. We don't use `--bare` because it disables OAuth login, and the whole point of this method is to need no API key. Note that Claude Code may still auto-load the repo's CLAUDE.md into context. That's fine for reading conventions, and the system prompt says the human's instructions take priority.
- `CLAUDECODE` is removed from the environment so the tool can run inside a Claude Code session.
- `--allow-exec` replaces the Bash allowlist with unrestricted Bash, so the reviewer can run the test suite. Use it only on code you trust.

### API (`backends/api.py`)

- **Anthropic:** `client.messages.create(..., tools=[read-only tools..., submit_answer], tool_choice=auto)`. Once `max_turns` is reached, it forces `tool_choice={"type":"tool","name":"submit_answer"}`. The `input_schema` of `submit_answer` is the stage's JSON Schema.
- **OpenAI-compatible:** the same loop using function tools and `tool_choice={"type":"function",...}`. Set `OPENAI_BASE_URL` for other providers.
- Tools (`backends/tools.py`) are confined to the checkout (path traversal is rejected), are read-only, and truncate output at 24k chars. `run_command` exists only with `--allow-exec`.
- Cost is estimated from a small price table, because the API doesn't return cost.

Every call, from either backend, is logged to `local/<id>/data/llm_calls/<label>.json` (prompt, system, answer, cost, errors) for debugging.

## Robustness

- Schema answers go through `coerce_to_schema`, which fills missing required fields and fixes enum casing and numeric strings, and then through a minimal validator. More than 3 problems counts as a failure, and the call is retried once.
- A failed lens or verifier never kills the run. Its findings are simply missing, or the vote counts as `uncertain`.
- If synthesis fails, a deterministic summary is built from the findings.

## Posting (`post.py`)

- Each active finding becomes a comment `{path, line, side, body}`, plus `start_line`/`start_side` when the range lies in the same hunk. Anchors are validated against the stored diff and snapped up to ±3 lines. Anything that can't be anchored goes into the review body under "Other notes".
- The comment body is formatted as `**severity** · category: title`, then the text, then a **Suggestion:** line, then a ```` ```suggestion ```` block when there is suggested code and a range.
- GitHub rejects APPROVE and REQUEST_CHANGES on your own PR, so in that case the tool posts COMMENT and prints a warning.
- If GitHub returns 422 (an anchor isn't in the diff, usually because the PR was pushed to after the review), the tool retries once with every comment moved into the body.
- On success, `local/<id>/` is deleted unless you pass `--keep`.

## Security model

- The reviewed code is **untrusted**. By default the reviewer can only read files and run read-only git commands. It never executes the PR's code unless you pass `--allow-exec`.
- The PR description and comments are wrapped and labelled as untrusted, to resist prompt injection. Findings still go through verification and human editing before anything is posted.
- The editor server binds to `127.0.0.1` only, and `POST /api/save` validates the payload and the id.
- Tokens are injected into the clone URL only for HTTPS, and are never written to `local/` JSON. The cache's remote URL does contain the token, but it lives in git config under `local/_cache`, which is git-ignored.

## Known limitations and ideas

- Symbol extraction uses regexes, not tree-sitter, so callers are found by grep and can be noisy for short names.
- Suggestion blocks written by the model sometimes replace a different number of lines than their range covers. Check them in the editor before posting.
- There is no incremental re-review (by design: one shot).
- The API pipeline has only been tested against fake clients, because no keys were available while building it.
- Possible future work: tree-sitter repo map (as in Aider), a judge-calibration eval set, GitLab/Bitbucket support, and SARIF output.
