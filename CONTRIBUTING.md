# Contributing

## Setup

```bash
pip install -r requirements.txt pytest
python -m pytest -q          # offline: no network, no LLM calls
python review.py doctor
```

Python 3.9 is the minimum. Use only the stdlib plus `requests`; `anthropic` and `openai` are imported lazily, and only by the API backend.

## Ground rules

- **Tests are offline.** Unit tests must never call GitHub or an LLM. Use fixtures and fake clients (see `tests/test_api_backend.py`).
- **The reviewer stays read-only by default.** Any new tool or flag that can execute code or write files must be opt-in behind `--allow-exec` or similar.
- **Prompts are code.** Prompt changes go in `prreviewer/prompts/*.md`. When you change one, run a real review on the playground PR (below) and mention the before/after in your PR.
- **Schemas and dataclasses move together.** If you add a field to `schema.py`, update `from_dict` defaults, the HTML editor (`templates/view.html`) if a user should see it, and the tests.
- **No secrets on disk** outside the git-ignored `local/` directory.

## Common changes

**Add a lens**

1. Create `prreviewer/prompts/lenses/<name>.md`: a charter of one paragraph plus a checklist, and the categories it emits.
2. Add `<name>` to `ALL_LENSES` and to whichever profiles in `config.py` should run it.
3. Document it in the lens table in DESIGN.md.

**Add a backend**

1. Subclass `backends.base.Backend` and implement `run()` so it returns `LLMResult(data=<dict matching schema>)`.
2. Wire it into `backends/__init__.make_backend` and add a `--method` choice in `cli.py`.
3. Add offline tests with a fake client.

**Tune a critic level**: edit `CRITIC_PROFILES` in `config.py`, then update the table in DESIGN.md and the costs in HOW_TO_USE.md.

## End-to-end check

`Ami-sh/pr-reviewer-playground` (private) has PR #2, which deliberately plants: a hard-coded special case, a skipped test, a weakened assertion, an uncapped backoff, an off-by-one retry bound, a deleted notification, and a `shell=True` injection.

```bash
python review.py https://github.com/Ami-sh/pr-reviewer-playground/pull/2 --critic-level low
python review.py show --id <id>
python review.py post --id <id> --dry-run
```

A healthy run at `low` flags all seven problems for about $0.30.

## Commits and PRs

Keep changes small and focused. Use present-tense commit subjects. Run `pytest` before pushing.
