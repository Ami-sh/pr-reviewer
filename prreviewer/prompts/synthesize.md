# Stage 4 — Synthesize the review

Several independent reviewers examined this PR; their findings were then adversarially verified. You now write the review a strong human reviewer would post: a short, decisive summary and the final wording of each inline comment.

{{user_prompt_block}}

## PR
{{pr_header_short}}

## What the understanding stage established
<understanding>
{{understanding}}
</understanding>

## Verified findings (input)
Each has an `id`. `verification` summarises the independent check (verdict, votes, confidence).
<findings>
{{findings}}
</findings>

## Positives noted by reviewers
{{what_is_good}}

## Reviewer coverage notes
{{coverage_notes}}

## Your task
1. **Deduplicate and rank.** If two findings describe the same underlying problem (same root cause, even at different lines), keep the better-anchored one and set `merged_into` on the other with `keep=false`. Drop (`keep=false`) findings that are not worth the author's time given the critic level ({{critic_level}}) — but never drop a confirmed critical/high.
2. **Write each kept comment's `body`** (markdown, posted inline at its line). Rules:
   - Lead with the problem and the concrete failure scenario, then the fix. 2–6 sentences; code only when it clarifies.
   - Do not restate the title or the severity (they are added automatically). No "Consider..." fluff, no greetings.
   - If the finding is a question, ask it directly and say what answer would resolve it.
   - Keep the `severity` unless the verification clearly justifies changing it.
3. **Write `summary_markdown`** — the review body. Structure:
   - One paragraph: what the PR does (as actually implemented) and whether it matches what it claims. Call out any mismatch explicitly.
   - "**Verdict:**" one line with the decision and the reason.
   - "**Key issues**" — bullet list of the blocking/high findings as `path:line — one-line summary` (only if any).
   - "**Also worth addressing**" — one-line bullets for medium/low (only if any; omit nits).
   - "**What's good**" — 1–3 specific, non-flattering positives (only if genuinely true).
   - "**Review scope**" — one or two sentences on what was checked and what was not (e.g. tests not executed).
   Keep it under ~350 words.
4. **Decide `verdict`**:
   - `REQUEST_CHANGES` if any kept finding is critical, or any high finding is blocking, or the PR does not do what it claims.
   - `APPROVE` only if nothing above `low` remains and the PR verifiably does what it claims.
   - `COMMENT` otherwise (e.g. mediums only, or questions that must be answered first).
   {{verdict_extra}}
