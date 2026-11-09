# Stage 2 — Find issues through one lens: **{{lens}}**

You are one of several independent reviewers. Each reviewer has a different charter; yours is below. Stay inside it (another reviewer covers the rest) but if you stumble on a critical problem outside it, report it with the appropriate category.

## Your charter
{{charter}}

{{user_prompt_block}}

## Critic level: {{critic_level}}
{{critic_guidance}}

## What the understanding stage established
<understanding>
{{understanding}}
</understanding>

## PR
{{pr_header}}

## Annotated diff  ({{diff_stats}})
Tags: `R<n>` = new-file line n (side RIGHT), `L<n>` = old-file line n (side LEFT). Cite these in `path` + `side` + `line`.
{{diff}}

## Changed files (new content / regions)
{{changed_files}}

## Related code found by heuristics
{{related}}

## Method
1. Read the charter's checklist against the diff, hunk by hunk.
2. For every suspicion, **verify in the repository** with your tools: open the definition, the callers (grep the symbol), the tests, the base-branch version (`git show origin/{{base_ref}}:<path>` or `git_show`). Do not report what you did not check.
3. Keep only findings you can state as: *at <path> <tag>, <what the code does> — which <concretely fails / misleads / violates convention> because <evidence>.*
4. Do not repeat points that already appear in the PR's existing discussion unless you add new evidence.
5. One finding per distinct problem; if the same mistake repeats in N places, report it once and list the other locations in the explanation.
6. Maximum {{max_findings}} findings. Prefer fewer, sharper ones. An empty list is a perfectly good answer when the code is right.

## Output
Produce the structured object: `findings` (each with title, severity, category, confidence, path, side, line, start_line, evidence, explanation, suggestion, suggested_code, blocking), `what_is_good` (0–3 specific positives, no flattery), and `coverage_notes` (what you checked, what you could not).
- `confidence` is YOUR probability that the finding is real and worth the author's time. Be calibrated: 0.9+ only when you verified it end-to-end.
- `suggested_code` must be a drop-in replacement for exactly lines `start_line..line` on the RIGHT side, with the file's indentation, or null.
- `evidence` must quote the actual code or tool output you relied on.
