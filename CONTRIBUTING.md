# Contributing

Crexil is built for its maintainer's own use and shared in the open. Issues are read. Pull
requests are reviewed when time allows, and a small focused change is far more likely to land
than a large one. For anything beyond a fix, open an issue first and describe the problem.

## Before you send a change

1. Run the checks from a clean state: `scripts/check.sh` (ruff, pytest, vitest, `tsc -b`). They
   need no GPU, no engine and no API key.
2. If you touched a route or a wire model, run `scripts/gen-api-types.sh` and commit both
   generated files. A server test fails until you do.
3. Read the law or failure case your change touches: [docs/PRD.md](docs/PRD.md) section 3 and
   [docs/failure-cases.md](docs/failure-cases.md). The laws are binding. A change that needs a
   law to bend is a change to that document first, with the reason in the commit.

## Conventions

- **Comments and docstrings are contracts, in the present tense.** Say what the thing owns,
  what it guarantees and what it never touches. No ticket numbers, no dates of decisions, no
  history: the commit message is where the story lives.
- **Cite, do not restate.** Laws are cited as `law N`, failure cases by their code (`A1`, `C2`).
- **Adding an engine, a preset or a graph is configuration, never code.** See
  `presets/README.md` and `workflows/README.md`.
- **Nothing personal goes in the repository.** No keys, no machine paths, no weights, no
  generated assets. Your installation lives in `.env` and on your disk.
- **Commit messages carry their own reason.** Write what changed and why in plain words.
- Work done with an AI coding tool is credited with a `Co-authored-by` trailer for that tool.

## Licence of contributions

By contributing you agree that your contribution is licensed under the Apache License 2.0,
the licence of this repository (section 5 of the licence).
