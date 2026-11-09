# How to use

```bash
# 0. one-time setup
pip install -r requirements.txt          # requests (+ anthropic/openai for the API method)
gh auth login                            # or: export GITHUB_TOKEN=...
python review.py doctor                  # checks GitHub auth, claude CLI, API keys

# 1. review a PR  → saves a draft under local/<id>/ and prints the id
python review.py https://github.com/OWNER/REPO/pull/123
python review.py https://github.com/OWNER/REPO/pull/123 \
    --prompt "Focus on the migration; we never allow nullable FKs" \
    --claude-method --critic-level high

# 2. edit the draft in the browser (cut / edit / add comments, change verdict, Save)
python review.py view --id <id>

# 3. post it to GitHub as a PR review (then the local draft is deleted)
python review.py post --id <id>              # --dry-run to preview, --keep to keep the draft
```

| Flag | Meaning | Default |
|---|---|---|
| `<url>` (required) | PR link: `https://github.com/o/r/pull/N`, `.../pull/N/files`, `o/r#N`, GHE URLs; draft PRs fine | — |
| `--prompt "..."` / `--prompt-file f` | extra context/emphasis for this repo/PR | none |
| `--claude-method` | use the local Claude Code CLI (no API key) | auto |
| `--api-method` | use `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` (+`OPENAI_BASE_URL`) | auto |
| `--critic-level low\|medium\|high` | low: 2 lenses, no verify, Sonnet · medium: 5 lenses, 1 verifier vote · high: 7 lenses, 3 votes, Opus | `medium` |
| `--model` | override model (`sonnet`, `opus`, `claude-opus-5-5`, `gpt-5`, ...) | per level |
| `--allow-exec` | let the reviewer run tests/commands in the checkout (untrusted code!) | off |
| `--open` | open the editor right after the review | off |

Other commands: `python review.py list` · `show --id <id>` (print as markdown) · `delete --id <id>`.

Auto method = API if an API key is set, otherwise Claude Code. Typical cost with Claude Code: low ≈ $0.30, medium ≈ $1–3, high ≈ $5–15 per PR (depends on PR size).
