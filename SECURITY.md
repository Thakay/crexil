# Security

## The model Crexil assumes

Crexil is a single-user, local application. Read this before exposing it anywhere:

- The server has **no authentication**. It is meant to listen on localhost only, and the
  shipped Docker setup publishes it on `127.0.0.1`. Do not bind it to a public or shared
  network interface.
- API keys are read from the environment (`.env`, which is git-ignored). A key saved through
  the localhost-only setup route (`POST /api/setup/openrouter`; the client has no form for it
  yet) is kept in a small `secrets.json` file beside the database (mode 0600, git-ignored); an
  environment value always wins over it. Keys are never logged, and the API only ever shows a
  masked form (the first six and last four characters). Law 18 of the product contract
  applies: a secret never lives in the database, so a copied or attached store never carries
  a key.
- The server writes generated files to its staging directory and, on Promote, copies them into
  the configured promote folder. `PROMOTE_ROOTS` fences where a promotion may go.
- Hosted engines are paid services, and the per-run spend cap of law 22 is not built yet.
  What limits paid work today is the LOCAL ONLY switch and the limit of 16 takes per run.
  The key you configure is yours to protect.

## Reporting a vulnerability

Please report security problems privately rather than in a public issue. If the repository's
private vulnerability reporting is enabled ("Report a vulnerability" under the Security tab),
use it; otherwise open an issue that asks for a private contact and leave the details out.
Include what you found, how to reproduce it, and what it lets an attacker do. You will get an
answer as soon as the maintainer can give one; this is a one-person project, so please allow
some time before disclosing.
