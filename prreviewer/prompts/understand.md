# Stage 1 — Understand the repository and the change

Before anyone judges this PR, build an accurate picture of (a) what this repository is and how it expects code to be written, and (b) what the PR claims versus what the diff actually does. Your output is handed to several independent reviewers, so be factual and specific; do not review yet.

{{user_prompt_block}}

## PR
{{pr_header}}

## Repository conventions (verbatim excerpts)
{{conventions}}

## Repository layout
{{tree}}

## Annotated diff  ({{diff_stats}})
Tags: `R<n>` = new-file line n (side RIGHT), `L<n>` = old-file line n (side LEFT).
{{diff}}

## Changed files (new content / regions)
{{changed_files}}

## Related code found by heuristics
{{related}}

## Recent history of touched files
{{history}}

## Your task
Explore with your tools as needed (depth: {{depth}}). Then produce:
- `repo_summary`: what this project is, its architecture around the touched area, how it is tested and released (3–8 sentences).
- `languages`: main languages/frameworks involved in the change.
- `conventions`: concrete rules reviewers must apply (from CLAUDE.md/AGENTS.md/CONTRIBUTING/linters/test layout). Quote the rule and its source file. Say "none found" if so.
- `pr_claims`: what the title/body/linked issue/commits CLAIM: the problem, the intended fix, any claims of testing or verification. Quote claims verbatim where possible.
- `change_summary`: what the diff ACTUALLY does, file by file, in neutral language. Note anything in the diff that the description does not mention, and anything the description mentions that is not in the diff.
- `risk_areas`: where a bug would hurt most (public API, data, auth, concurrency, migrations, CI config, tests that were modified or deleted...).
- `files_to_read`: non-diff files a careful reviewer should open (callers of changed functions, the tests that cover them, config that consumes changed values). Repo-relative paths.
- `notes_for_reviewers`: anything else that will make the review sharper (e.g. "the test file was changed in the same PR — check assertions were not weakened", "function X has 14 callers, see grep").
