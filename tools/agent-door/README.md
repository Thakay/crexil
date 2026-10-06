# Agent door

An MCP server that lets an MCP client, such as Claude Code, work in Crexil through the same
shared actions the artist uses. It is one script, `crexil_mcp.py`, that speaks MCP over stdio
and talks to a local Crexil server over HTTP. It never opens the store or imports server code.
The studio guide, [docs/agents.md](../../docs/agents.md), covers the HTTP contract this door is
built on.

## Run it

You need [uv](https://docs.astral.sh/uv/) and a running Crexil server. The script declares its
own pinned dependencies (`mcp`, `httpx`), so `uv` sets them up on first launch:

```sh
uv run --script tools/agent-door/crexil_mcp.py --help
```

Register it with Claude Code, from the checkout root:

```sh
claude mcp add --transport stdio crexil -- uv run --script "$PWD/tools/agent-door/crexil_mcp.py" --agent-name Claude --max-run-usd 0
```

For a project-level entry instead, copy [`mcp.example.json`](mcp.example.json) into the project's
`.mcp.json`. No `.mcp.json` is committed, so a contributor is never asked to approve a server
they did not choose.

| Flag | Environment | Default | Meaning |
|---|---|---|---|
| `--url` | `CREXIL_URL` | `http://127.0.0.1:8000` | The Crexil server. Loopback addresses only |
| `--agent-name` | `CREXIL_AGENT_NAME` | `Claude` | Every action is recorded as `agent:<name>` |
| `--max-run-usd` | `CREXIL_DOOR_MAX_RUN_USD` | `0` | This door's spending ceiling per run. `0` means free local engines only |

## Tools

| Tool | What it does |
|---|---|
| `crexil_status` | Whether Crexil answers, which providers are connected, who the door writes as, its ceiling |
| `crexil_list_engines` | The engines a run can use: what each makes, whether it can run now and why not, where it runs, what leaves the machine, its price, its controls |
| `crexil_list_projects`, `crexil_get_project` | The projects; one project's brief, limits, runs and recent decisions |
| `crexil_create_project` | A new project with a name and a brief |
| `crexil_prepare_run` | Creates or revises a draft run and returns its review. Never runs anything |
| `crexil_review_run` | Reads a run's review again, with a fresh token |
| `crexil_submit_run` | Submits a reviewed run, restating the reviewed amount |
| `crexil_get_run` | One run's grid: states, causes, takes, selection, recent decisions. Can wait up to 55 s for it to settle |
| `crexil_propose` | Recommends takes, or comments on a run, with a reason. Never changes the artist's pick |
| `crexil_get_decisions` | The project's decision record: what was chosen or assessed, among which takes, by whom, why |
| `crexil_search_record` | Searches a project's record by words |

## What the door guarantees

- **The artist decides.** There is no tool to pick, reveal a blind run, deliver, change limits
  or start a voice session. The server also refuses reveal, delivery and voice sessions to agents.
- **Money moves only within two caps.** The run's own spending limit, which the server enforces,
  and this door's ceiling, which only the person who launches the door sets. A paid submission
  must restate the amount its review reserves, or nothing is sent.
- **Every write names its author** as `agent:<name>` and carries a delivery id. If a response is
  lost, the door sends the same request once more with the same id, and the server answers a
  replay with the work it already did. If the outcome is still unknown, the error names the id
  to replay with.
- **No media reaches the model.** Results carry facts, ids, labels and reasons only, never a
  take's bytes, a file path or an address to fetch one. ElevenLabs' terms forbid its audio as
  input to any machine learning, and the model behind an agent is one.
- **Errors come in a fixed set of kinds:** `unreachable`, `unavailable`, `not_found`,
  `conflict`, `refused`, `not_allowed`, `not_built`, `over_door_ceiling`, `invalid_input` and
  `server_error`. Each error gives the server's plain line, then the next move, then the kind.

## Tests

The door's HTTP core runs in the server's suite (`scripts/check.sh`), from
`cockpit/server/tests/test_agent_door_mcp.py`. The MCP layer needs the `mcp` package, which the
server does not depend on, so its tests run with it layered in:

```sh
cd cockpit/server && uv run --with mcp==2.3.0 pytest ../../tools/agent-door/tests -q
```

Both suites use the scripted harness in-process, with no engine, key or network.
