# Crexil product contract

This document holds what binds the code: what the application is today, the product model,
and the laws. Code and tests cite it as `PRD §2`, `PRD §3` and `law N`. A product ruling is an
edit to this document, and its reason lives in the commit that makes it. Failure handling has
its own catalogue in [failure-cases.md](failure-cases.md).

---

## 1. What this is

Crexil is **a supervisory layer for AI-made media**: engines put work in front of a human, the
human decides, and every decision is kept. **The record is the property.**

Working name in code: **cockpit** (the `cockpit/` directory, the package names and the
`COCKPIT_` variables). The name describes supervisory control: the automation flies, the human
watches the instruments and decides, and a recorder keeps everything.

**Today it is a fan-out bench for humans.** One brief goes to every image engine you have: local
diffusion through ComfyUI graphs and hosted models through OpenRouter. The takes land in one
grid, one cell per take, position never moving. Every take remembers its parents. A winner goes
on to Refine, to the local 3D engines, or out through Promote, which copies the artefact and its
manifest to the hand-off folder for a DCC tool. Nothing is lost, and every prompt is indexed for
search (a search surface is not built yet).

**It is not** a chat app, a node-graph editor, version control for pixels, a DCC, or a
compliance product. Workflow authoring stays in ComfyUI's format; the canvas shows lineage,
never plumbing. **It is the front door and the record, never the workbench** (law 16).

## 2. The product model

Everything else is a view over these six entities. This is the model the code is built toward;
the known gaps in §3 say where today's code differs. Table names stay as they are. User-facing
documentation may say *project* for workspace, *run* for turn, *item* for node and *release*
for `node.promoted`.

- **Node**: one generation. Immutable once landed; a re-run makes a new node. Fields: id,
  payload (`image | 3d`; the enum extends only when a payload actually lands),
  **parents[] (0..n)**, prompt, engine, seed (nullable), dial values, the verb that produced
  it, turn, status, cost, `setAside`.
- **Turn**: a batch. One Run creates one turn of N slots and states its cap (law 22).
- **Slot**: a reserved cell created **before any work happens**, with a fixed position for the
  life of the turn, an assigned engine, and a lifecycle
  `queued → running → landed | failed | held | skipped | setAside | late`.
- **Engine**: id, name, lane (`local | external`), **provider** (the adapter it is reached
  through: ComfyUI, the 3D runner, OpenRouter, a later one), price, what it **accepts**
  (payload types and reference kinds: `mask`, `seed`, `imageRef`, `styleRef`, `maxResolution`,
  `tokenLimit`) and what it **produces** (one payload type), each capability carrying a
  verification state (`verified | unverified`), and the `verbs` it serves. **Capabilities
  drive the UI; they are not documentation.** An engine that is not configured appears nowhere.
- **Verb**: an action producing nodes, declaring which payload types it accepts and which
  engines serve it. `verb` is the branching grain of the graph. Every 3D step is a verb; so is
  every later step, prompt refinement included.
- **Workspace**: one asset's whole graph, plus canvas viewport, dial state and the set-aside
  tray.

**The record.** Every change is an event: append-only, in sequence, carrying its author
(law 23). A **pick** is an event: node ids, author, one line of reason. The Keep is the view
over these events: what was kept and why. The **manifest** (v4) is the stamp on every promoted
artefact: node ids, lineage, engine, seed, the prompt as typed and as submitted, model files by
hash, cost, the submitted graph. **v4 is the public format, frozen; changes are versioned,
additive and documented.**

Three additive manifest extensions exist, each with its own version:

- `shared_request` (version 1) keeps the accepted preparation, its source contributions, the
  matching operation revision, who asked, and the original attempt controls where they were
  recorded. A take from a sequence also keeps the frozen binding and the authorization event
  that preceded acceptance. Missing history stays missing: the current project, direction,
  operation or sequence never stands in for it.
- `restoration` (version 1) names the source backup and project, the original node, any earlier
  restore chain and the node aliases that matter. A restored export keeps its historical
  requests and never checks old model hashes against the current installation's files.
- `related_files_version` (version 1) on a material lists its independently owned companion
  files with hashes, media types, roles, whether each is required and whether it is available.
  Portable file names replace live ownership paths; a nested source record is history and
  never grants access to old paths or runtimes.

Imported material has unknown creation provenance: its transport, provider, capability,
local-only claim and observed prompt are null, and its replay tier is record only. A local mesh
claims no ComfyUI provider or job identity.

**Reuse across projects.** An explicit, reviewed import copies the chosen original and its
available related files into another project. The copy keeps its known source project, node,
participant and frozen provenance, attaches no foreign live parents and inherits no permission to
execute. A missing required file refuses the copy; missing optional history stays visibly
missing. Replaying the acceptance returns the same imported node, even after the source is purged.

**Project portability.** A project backup is a versioned ZIP holding a sealed database and the
available material, with missing or purged files named. Restoring creates a separate project with
its own bytes and explicit identity mappings. Requests, graphs, authorship, charges and decisions
stay history: unfinished attempts do not resume, earlier deliveries are not sent again, and
continuing needs a new reviewed preparation. Restored charges count in the project's history
without adding spending to the current installation. Installation settings, secrets and model
weights stay out of the backup.

**Execution model.** Local engines share one GPU and run **strictly sequentially, across
executors**. External engines run **concurrently** and cost money. A single Run mixes both, so
slots complete out of order.

**What ships, and what does not.** The product ships code, the adapter contract, the engine
definitions, the reference graphs, a downloader that fetches weights on the user's consent with
the licence class shown, and docs. It never ships weights, keys, or anyone's paths. Which
engines are on, where weights live, the keys and the promote folder are the user's
**installation**: on their disk and in no repository. The maintainer's machine is one
installation among others. Engine rosters are config, never law. A non-commercial weight may be
defined and is flagged; it is never a default; no download the user asks for is blocked. 3D
ships as code and contract, its engines unconfigured for a stranger until the runner has an
install path of its own.

**Foundations, proven:** ComfyUI, FastAPI, SQLite, React with Vite, OpenRouter. Replacing one
needs a commit that states why it failed.

## 3. The laws

Binding invariants. **Numbers are names: code cites them as `law N`; never renumber.**

A law binds new work even where today's code falls short of it. Known gaps: the legacy canvas
(`#/w`) keeps the older limits: its hosted engines take a text brief only, so a take cannot feed
a hosted engine there (law 21); its runs have no spend cap (law 22); and its events record no
author (law 23). The project surface (`#/projects`) prepares hosted continuations and
multi-source requests, states run and project limits before dispatch, and records the
participant behind each preparation, selection, assessment and execution control; an assessment
carries its reason. View state (canvas positions, viewport and dials) is stored
without events, and engine definitions carry no `provider` field yet (§2). The downloader shows
no licence class, its default groups include non-commercial and unconfirmed weights, and the
SDXL reference graphs load a checkpoint whose card now says non-commercial (§2, what ships; see
[third-party.md](third-party.md)).
[failure-cases.md](failure-cases.md) says what is implemented for each failure case.

1. **Failures are per slot; messages are per cause.** One dead engine never stops the other
   eight. Nine slots killed by one cause produce one message.
2. **No modals.** Nothing blocks the canvas or the queue. A pause is a queue state with a
   visible resume control.
3. **No status codes, no "something went wrong", no stack traces** in the primary message. A
   collapsed `details` affordance may carry raw text.
4. **Every failure offers the next move as a button.**
5. **Line 2 states cost**: seconds and takes for local, dollars for external, including "you
   weren't charged" wherever money was plausibly in play.
6. **The costed action is never the default button.** Free remedies left; paid last, with the
   price on the button face.
7. **Nothing is silently lost.** No path may delete a node, prompt, seed or take. *Set aside*
   is a place, not a delete. Purge is the only delete verb, and it is held and explained.
   Provenance marks on an imported file are never stripped.
8. **Grid geometry is frozen at Run.** Failures change a cell's contents, never the layout.
9. **Nothing retries silently.** Auto-retries are permitted, must be visible while running,
   and are recorded permanently.
10. **The queue pauses only when every remaining slot is guaranteed to fail.**
11. **Three input triggers only:** click retargets · `N`/right-click summons at cursor · drag
    applies a verb by destination. **No hover toolbars.**
12. **Badge, never filter.** An incapable engine or verb stays visible, selectable and
    de-emphasised, with the reason in plain words. Unverified capability is shown as
    unverified, never guessed.
13. **Say out loud that one GPU means locals take turns.** Present in every surface with a
    queue.
14. **One heavy local job at a time.** Local engines serialise across executors; a resident
    idle engine is not a running one; external jobs run concurrently.
15. **Crexil never acts unasked.** It never generates, judges, picks or alters a prompt on its
    own, and never runs a loop or pipeline of its own. An LLM may serve as an engine behind a
    verb, prompt refinement included, when a client asks for it by name; what was typed and
    what was submitted are both kept and both shown. An LLM is never an operator that decides
    or changes work uninvited.
16. **The front door and the record, never the workbench.** Every 3D step is an engine-shaped
    verb: artefact in, artefact out through a manual handle, landing as a node with parents.
    Editing (sculpt, topology, UV, materials, animation) stays in the DCC.
17. **Observed, never predicted.** Durations are elapsed, a dial's consequences are
    qualitative words, no per-card hardware profile, no predicted totals.
18. **Secrets never live in the store.** A key is never in a database that gets copied, backed
    up or attached to a bug report.
19. **The server is the only writer.** Every feeder (UI, script, agent) is a client through the
    HTTP API. Scheduling is in-process; no broker, no second writer, no second service for the
    store.
20. **A surface is an adapter.** A medium lands as a payload, its engines, a node body, an
    inspection surface, a promote convention and its verbs. Nothing else is invented per
    medium.
21. **Compatibility is by type, never by provider.** An engine declares what it accepts and
    what it produces. Any take of an accepted type, from any engine, any provider, any medium,
    may be its input. Each hop is one verb applied to one take by a client, and lands as a node
    with parents. A mismatch is badged, never hidden (law 12).
22. **Money never moves without a cap.** A turn states its cap before dispatch. If the known
    prices exceed it, the turn is refused before any engine is called. An unknown price counts
    as over the cap, never as zero.
23. **Every decision has an author.** Every event records the human or the named agent that
    caused it. A pick carries a reason. Promote is a human act: an agent may propose, never
    release.
