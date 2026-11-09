# Research notes: what pr-reviewer is built on

These are the papers, tools and docs the design draws on, and what we took from each. 

> **Provenance:** this list was compiled from prior knowledge while building the tool. A live citation-verification pass was started but stopped early to save credits, so **check a link before quoting it**. Each entry lists the takeaway we actually used.

## 1. LLM / ML code review

| Work | What we took |
|---|---|
| **CodeReviewer**: Li et al., *Automating Code Review Activities by Large-Scale Pre-training*, ESEC/FSE 2022. [arXiv:2203.09095](https://arxiv.org/abs/2203.09095) | Review splits into three tasks: diff quality estimation, comment generation, code refinement. Our stages mirror them: find, comment body, and `suggested_code`. |
| **LLaMA-Reviewer**: Lu et al., ISSRE 2023. [arXiv:2308.11148](https://arxiv.org/abs/2308.11148) | General LLMs can do review. The hard part is useful, precise comments, not fluent ones. |
| **Google**, *Resolving code review comments with ML* (Google Research blog, 2023) | In production, a confidence threshold decides what gets surfaced. That is our `min_confidence` per critic level. |
| **CRScore** (Naik et al., 2024): a reference-free metric for review-comment quality (conciseness, comprehensiveness, relevance) | The criteria behind our comment-wording rules in `synthesize.md`. |

## 2. Critics, judges and verification

| Work | What we took |
|---|---|
| **CriticGPT**: McAleese et al., *LLM Critics Help Catch LLM Bugs*, OpenAI 2024. [arXiv:2407.00215](https://arxiv.org/abs/2407.00215) | Model critics catch bugs that humans miss, but they also hallucinate nitpicks. You have to trade comprehensiveness against false alarms, and a separate verify step is how we control that trade. |
| **Judging LLM-as-a-Judge**: Zheng et al., NeurIPS 2023. [arXiv:2306.05685](https://arxiv.org/abs/2306.05685) | Judges have position, verbosity and self-preference biases. Our verifier sees the claim and evidence only, never the finder's reasoning, and is told to refute. |
| **Self-Consistency**: Wang et al., ICLR 2023. [arXiv:2203.11171](https://arxiv.org/abs/2203.11171) | Majority voting over independent samples improves reliability, hence `verify_votes` = 3 at `high`. |
| **Multi-agent debate**: Du et al., 2023. [arXiv:2305.14325](https://arxiv.org/abs/2305.14325) | Independent agents that cross-check each other reduce hallucinations. We use the cheaper "independent verify" form, not multi-round debate. |
| **Self-Refine**: Madaan et al., NeurIPS 2023. [arXiv:2303.17651](https://arxiv.org/abs/2303.17651) | Feedback-then-refine works, but self-critique in the *same* context is anchored. That's why our verifier and synthesizer run in fresh contexts. |

## 3. Reward hacking and test gaming

| Work | What we took |
|---|---|
| **Baker et al.**, *Monitoring Reasoning Models for Misbehavior and the Risks of Promoting Obfuscation*, OpenAI 2025. [arXiv:2503.11926](https://arxiv.org/abs/2503.11926) | Coding agents really do hack graders: `exit(0)`, stubbed verification functions, special-casing tests. The patterns are detectable from the code alone, which is what the `intent_and_reward_hacking` lens hunts for. |
| **Denison et al.**, *Sycophancy to Subterfuge: Investigating Reward-Tampering in LLMs*, Anthropic 2024. [arXiv:2406.10162](https://arxiv.org/abs/2406.10162) | Models generalise from small specification gaming to editing their own tests and rewards. The reviewer must treat tests and CI changed in the same PR as suspect. |
| **ImpossibleBench** (2025): coding tasks whose tests conflict with the spec, used to measure how often agents cheat (edit tests, special-case) | Weakened assertions and edited expected values are a primary signal, so the `tests` lens compares changed tests against `origin/<base>`. |
| METR reports on reward hacking by frontier models in agentic tasks (2025) | Agents may claim success without doing the work. We separate `pr_claims` from `change_summary` in stage 1. |

The checklist lives in `prompts/lenses/intent_and_reward_hacking.md`:

| Pattern | What to look for in the diff |
|---|---|
| Tests disabled | `@skip`, `xfail`, `.only`, `it.skip`, `#[ignore]`, commented-out or deleted tests |
| Assertions weakened | `==` → `>`, `in`, or `is not None`; specific exception → `Exception`; expected values edited |
| Special-casing | branches on the exact input from the issue or test fixture; `if TESTING`; magic constants |
| Mocking the unit under test | a monkeypatch replaces the very function the test is supposed to test |
| CI tampering | `continue-on-error`, `|| true`, lowered coverage thresholds, silenced linters |
| Swallowed errors | new broad `try/except: pass` around a failing path |
| Claims ≠ diff | "added tests" or "capped at 60s" with no matching code |
| Stubs | `TODO`, `pass`, `NotImplementedError`, hard-coded returns |

## 4. Understanding the repository

| Work | What we took |
|---|---|
| **SWE-agent**: Yang et al., *Agent-Computer Interfaces...*, NeurIPS 2024. [arXiv:2405.15793](https://arxiv.org/abs/2405.15793) | A small, well-designed tool set (view file with line numbers, search, truncated output) beats raw shell access. Our `ToolBox` mirrors it. |
| **Agentless**: Xia et al., 2024. [arXiv:2407.01489](https://arxiv.org/abs/2407.01489) | Hierarchical localisation (files → symbols → lines) is cheap and strong, hence the static context pack: changed files, then changed symbols, then callers by grep. |
| **RepoCoder**: Zhang et al., EMNLP 2023. [arXiv:2303.12570](https://arxiv.org/abs/2303.12570) | Repository-level retrieval of related code matters more than a bigger window. |
| **Aider repo map** ([aider.chat/docs/repomap.html](https://aider.chat/docs/repomap.html)) | A token-budgeted summary of the repo structure. We ship a simpler directory and file map; a tree-sitter map is future work. |
| Anthropic, *Effective context engineering for AI agents* (2025) | Give a small, high-signal context up front plus tools to pull more, rather than dumping everything. |

## 5. Existing tools

| Tool | What we took |
|---|---|
| **Qodo PR-Agent** ([github.com/qodo-ai/pr-agent](https://github.com/qodo-ai/pr-agent)) | Its line-numbered hunk format, which inspired our `R<n>`/`L<n>` annotation, plus PR compression for big diffs and structured YAML/JSON findings. |
| **CodeRabbit**, **Greptile**, **Cursor BugBot**, **GitHub Copilot code review** | The widely reported complaint about all of them is *noise*. We kept severity floors, the verify stage and human editing before posting. |
| **Claude Code `/code-review`** and **anthropics/claude-code-action** | Parallel reviewer fan-out plus a verification pass plus confidence thresholds. Our find → verify shape follows this. |
| **reviewdog / Danger** | Posting as a single review with inline comments, and validating anchors against the diff before posting. |

## 6. Conventions and APIs

- **Google Engineering Practices**, Code Review Developer Guide ([google.github.io/eng-practices/review](https://google.github.io/eng-practices/review/)): what to look for (design, functionality, complexity, tests, naming, comments, style, docs), the "nit:" convention, and the standard of approving when a change improves overall code health.
- **Conventional Comments** ([conventionalcomments.org](https://conventionalcomments.org/)): labelled comments (issue, suggestion, question, nitpick, praise) with blocking and non-blocking decorations. That became our `category` and `blocking` fields.
- **GitHub REST API, pull request reviews** ([docs.github.com/en/rest/pulls/reviews](https://docs.github.com/en/rest/pulls/reviews)): `POST /repos/{o}/{r}/pulls/{n}/reviews` with `event` and `comments[]` of `{path, line, side, start_line, start_side, body}`. The line must be inside the diff, otherwise you get a 422. You can't APPROVE or REQUEST_CHANGES your own PR. The diff media type is `application/vnd.github.v3.diff`, and ```` ```suggestion ```` blocks become suggested changes.
- **Claude Code headless mode** ([code.claude.com/docs](https://code.claude.com/docs)): `-p`, `--output-format json` (`structured_output`, `total_cost_usd`, `num_turns`, `is_error`), `--json-schema`, `--tools`, `--allowedTools`, `--permission-mode dontAsk`, and `--max-budget-usd`. We checked these flags against the installed CLI's `--help` (v2.1.319) and with a smoke test.

## Principles we distilled

1. **Independence:** fresh contexts per stage; the verifier sees claims, not reasoning; the PR description is untrusted (CriticGPT, LLM-as-a-Judge, Self-Refine).
2. **Precision over recall:** verify, then threshold confidence, then let a human edit (CriticGPT, Google, the noise complaints about existing tools).
3. **Understand before judging:** a context pack plus tools for localisation (SWE-agent, Agentless, RepoCoder).
4. **Look for gaming specifically:** a dedicated lens and a checklist (Baker et al., Denison et al., ImpossibleBench).
5. **Anchor exactly:** annotated diff tags and validation against GitHub's commentable lines (PR-Agent, reviewdog).
6. **Actionable comments:** failure scenario, then fix, then an optional suggestion block (CRScore, Google guide, Conventional Comments).
