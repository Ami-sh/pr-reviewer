# Stage 3 — Adversarial verification of ONE finding

Another reviewer reported the finding below. Your job is to try to **refute** it. Assume it may be wrong: a misread of the diff, a case already handled elsewhere, behaviour that is intentional and documented, a line number pointing at the wrong code, or a problem that exists but is not worth the author's time. Only if your honest attempt to refute fails do you confirm it.

You have not seen the reviewer's reasoning, only their claim and evidence. Investigate the repository yourself with your tools: open the file at the cited lines, the callers, the tests, the base-branch version (`git show origin/{{base_ref}}:<path>` / `git_show`). Then decide.

{{user_prompt_block}}

## The finding
<finding>
{{finding}}
</finding>

## Diff context around the anchor
```
{{snippet}}
```

## Relevant part of the annotated diff (file: {{path}})
Tags: `R<n>` = new-file line n (side RIGHT), `L<n>` = old-file line n (side LEFT).
{{file_diff}}

## What the understanding stage established
<understanding>
{{understanding_short}}
</understanding>

## Decide
- `confirmed`: the problem is real, the anchor is right (or you corrected it), and a competent author would want to know. 
- `refuted`: the claim is false, already handled, intentional per repository conventions, or not observable in practice. Explain precisely what refutes it.
- `uncertain`: you could not establish it either way with the tools available (say what would be needed).
Also:
- `confidence`: your probability that the finding is real and worth reporting (0–1), independent of the original reviewer's number.
- `corrected_*`: fix the severity, path, line or side if the original was off (otherwise null). The line must be a line that appears in the diff with the given side.
- `improved_explanation`: a tighter, more accurate explanation if the original was imprecise, else null.
- `reasoning`: what you checked (files, symbols, commands) and what you found. Concrete.
