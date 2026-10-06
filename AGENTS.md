# Crexil: instructions for coding agents

Crexil is a local-first bench for AI-made media: one brief goes to many image engines, the
takes land in one grid, a chosen take can go on to local 3D engines, and every decision is
recorded. Start with [README.md](README.md),
then read what your change touches:

- [docs/PRD.md](docs/PRD.md): the product model (§2) and the 23 binding laws (§3). Code cites
  them as `law N`. Never renumber a law, and never weaken one to make a change fit.
- [docs/failure-cases.md](docs/failure-cases.md): the failure cases the code and tests cite by
  code (`A1`, `C2`, `B4`). The code is the authority; the catalogue explains it.
- [docs/development.md](docs/development.md): commands, environment variables, engines, traps.

## Commands

```bash
scripts/check.sh            # ruff, pytest, vitest, tsc (app and specs); run before you call work done
scripts/check.sh ruff --fix # ruff only, arguments forwarded
scripts/gen-api-types.sh    # after ANY route or wire-model change; commit both outputs
```

The checks need no GPU, engine or API key. Never point a test or a script at a live engine, a
paid API or someone's real data unless the person you work for asked for exactly that.

## Rules

- **The server is the only writer** (law 19). The client, scripts and agents go through the
  HTTP API. The one exception is `scripts/seed_canvas_fixture.py`, which seeds a throwaway e2e
  store directly.
- **Comments and docstrings are contracts in the present tense**: what the thing owns,
  guarantees and never touches. No ticket numbers, no decision dates, no history. The commit
  message holds the reason.
- **Adding an engine, preset or graph is configuration, never code.**
- **Nothing personal enters the repository**: no keys, machine paths, weights, generated
  assets, or names of private documents. An installation lives in `.env` and on its owner's
  disk.
- Keep changes small and in the local idiom of the file you touch.

## Commits

Write what changed and why, in plain words. Keep the repository owner's configured author
identity. End each commit with exactly one `Co-authored-by` trailer per AI tool that
materially contributed, each at most once, and nothing else (no session links or IDs):

```text
Co-authored-by: Claude <noreply@anthropic.com>
Co-authored-by: Codex <noreply@openai.com>
```

Review alone is not co-authorship. Never push, publish or deploy unless asked to.
