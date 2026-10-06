# Failure cases

Crexil handles failures through a catalogue of named failure cases. The code, the tests and the end-to-end suite cite these cases by short codes. This document says what each code means and what Crexil does today.

## 1. How to read this

A **failure case** is one named way a run can go wrong, or one state the artist must never be left guessing about. Each case has a code. The letter is the family:

| Family | Covers |
|---|---|
| `A` | generation failures: one take dies |
| `B` | external engines and money |
| `C` | queue and session states |
| `D` | data integrity |
| `E` | checks before Run |
| `F` | ambient: small things around the work |

A variant carries a letter suffix, such as `A5a`. Codes are names. They are never renumbered and never reused.

The codes appear in code comments (`# A1/A11's detector`), in pytest names (`test_b4_...`), and at the start of client and end-to-end test titles (`"C2: an OOM under a foreign hold ..."`).

**The code is the authority.** If this document and the code disagree, the code is right and this document has a bug. Each case has one status:

- **implemented**: the handling was found in the code, with at least one test.
- **partly implemented**: some handling exists. The entry says what is missing.
- **not implemented**: no handling was found.

Words used below. A **Run** creates one **turn** of **slots**. A slot is one grid cell with a fixed position. One attempt to fill a slot is a **take**. Local engines share one GPU and run one at a time (the **local lane**). External engines run side by side and cost money (the **external lane**). A slot is `queued`, `running`, `landed`, `failed`, `held`, `skipped`, `set_aside` or `late`. "Law N" means law N in section 3 of [PRD.md](PRD.md).

Paths are relative to `cockpit/`. Server code is in `server/app/`, server tests in `server/tests/`, client code in `web/src/app/`, end-to-end specs in `web/e2e/`. Bare file names such as `scheduler.py` or `test_scheduler.py` refer to those folders.

## 2. Rules that cut across cases

### 2.1 Failure kinds and the failure message

An executor reports every failure to the scheduler as one `EngineFailure` with a kind (`executors.py`). Only the scheduler changes a slot's state.

| Kind | Meaning | Cases |
|---|---|---|
| `oom` | a local engine ran out of video memory | A1, A11, C2 |
| `blank` | the engine reported success and returned an empty frame | A5a |
| `error` | any other engine error | A2, A3, A4 |
| `unreachable` | the engine's service or the network did not answer | A2, B3 |
| `timeout` | no result came back in time | A6 |
| `dead_key` | the provider refused the key or the account has no credit | B1 |
| `rate_limit` | the provider asked us to slow down | B2 |
| `charged_blank` | the provider billed and returned no image | B4 |
| `moderation` | the provider's content filter refused the prompt | A5b, B5 |
| `model_down` | the provider cannot serve this model right now | none |
| `interrupted` | the take was stopped by our own cancel | C6 |
| `restart` | an external call was in flight when the server restarted | C1 |
| `internal` | the cockpit's own code failed on the take | none |
| `collection` | files were reported but could not be collected, or an audio take could not be measured; the bytes are kept | none |
| `preflight` | the request was stopped before it reached the provider; nothing was sent | none |

A `dead_key` or `unreachable` failure from an audio provider benches that provider only (`provider:elevenlabs`, `provider:voice-runner`), so its engines wait while every other engine keeps working (law 1). OpenRouter's failures bench the external lane, as before.

Every failed slot keeps three fields. `cause` is one plain line per kind, from `plain_cause` in `scheduler.py`. It never carries a status code or a stack trace (law 3). `detail` is the engine's raw text, cut to 1600 characters, shown only behind the cell's DETAILS fold. `kind` picks the buttons. The same facts go into a permanent `slot.failed` event with the attempt, engine, frame and cost.

An audio adapter that refuses a take before generation words its own line instead (`EngineFailure.plain`): the cause, "Nothing was charged." and the next move, such as replacing the key, adding credit, choosing another voice take or freeing a voice slot. The scheduler shows those words as the cause, and as the bench's cause when the refusal benches the provider. A rate limit keeps the curated line, which counts the tries. ElevenLabs' own message stays in `detail`.

The cell's second line states cost (law 5): GPU time and takes still queued for a local take, dollars for a paid take, including "nothing was charged". Free buttons come first. A paid button comes last, shows its price, and is never the default (laws 4 and 6). Failed cells that share one cause are also named once in the turn's footer (law 1). This logic is in `batch/grid.ts` and tested in `batch/grid.test.ts`.

If the cockpit's own code fails on a take, `capture.py` records a `cockpit.failure` event with the traceback. The slot fails as `internal` with a plain line, and the lane serves the next take (`test_law_3_an_internal_crash_still_speaks_plainly` in `test_laws.py`, `test_capture.py`).

### 2.2 What may pause the queue

A pause is a **bench**. A bench has a scope. Queued takes under that scope become `held`. They return to `queued` when the bench is lifted. A take that is already running finishes. There are three scopes: one engine (`engine:<id>`), one engine inside one turn (`engine:<id>@turn:<turn>`), and one lane (`lane:local` or `lane:external`). Nothing wider than a lane exists, so nothing can pause both lanes at once (law 10).

| Trigger | Bench scope |
|---|---|
| `dead_key` | `lane:external` |
| `unreachable` | the failing take's lane. For a 3D engine: that engine only |
| `model_down`, `timeout` | that engine |
| second `error`, `oom` or `charged_blank` of one engine in one turn (A9) | that engine, that turn only |
| `oom` on a local take while another program holds the card (C2) | `lane:local`, kind `gpu_held` |
| at startup: ComfyUI does not answer | `lane:local` |
| at startup: the 3D runner does not answer | each 3D engine |
| at startup: no provider key, or the key is refused | `lane:external` |

These never bench anything: `moderation`, `rate_limit`, `interrupted`, `blank`, `internal`, `restart`, and a single `error`, `oom` or `charged_blank`. A full disk does not pause the queue at run time (see A7).

A bench is written as an `engine.benched` event and replayed at startup, so it survives a restart. It is lifted by `POST /api/unbench` with the scope. The buttons UNBENCH and TRY ONCE MORE call that route. The C2 bench lifts itself when the card is free. At startup a missing-key or unreachable bench clears when the service answers again, and a key bench clears when the key check succeeds.

Code: `bench_scope_for`, `bench`, `unbench` in `scheduler.py`, `startup_checks` in `wiring.py`. Tests: the bench section of `test_scheduler.py`, `test_law_10_*` in `test_laws.py`.

### 2.3 Automatic retries and their visibility

Only two kinds retry on their own (`default_retry_policy` in `scheduler.py`):

| Kind | Retries | Wait |
|---|---|---|
| `rate_limit` | `COCKPIT_RATE_LIMIT_RETRIES` (4, so 5 attempts) | the provider's own window, capped at `COCKPIT_RATE_LIMIT_MAX_WAIT_S` (120 s). If the provider names none: `COCKPIT_RATE_LIMIT_BACKOFF_S` (10 s) |
| `unreachable` | 1 | `COCKPIT_RETRY_BACKOFF_S` (2 s) |

The provider's window is read from the `Retry-After` header (seconds or a date) or from `X-RateLimit-Reset` (`retry_after_from` in `openrouter.py`). While an external take waits, it gives back its concurrency permit, so one slow provider does not block the others. A take re-queued under C2 is also marked as a retry (`gpu_held`). Every other kind is final. Running it again is the artist's choice.

A project run freezes whether its slots may retry on their own: audio slots do, image and 3D slots do not. Both kinds that retry mean the provider refused the request or never received it, so a retry never pays twice. The refused attempt's known zero charge is recorded on its `slot.retrying` event, so its reservation stops counting against every cap (law 22).

Nothing retries silently (law 9). Before the wait, the scheduler writes a `slot.retrying` event with the kind, the attempt, the limit, the wait, whose clock it is, and the time of the next try. The slot returns to `queued` with `retry_of` set. `GET /api/queue` lists the waits under `retries`. The cell reads RETRYING · ATTEMPT i OF n with a countdown, and the queue strip marks a running retry. `retry_of` stays on the row after the take lands. A manual reroll clears it. A cancel during the wait wins: the slot is skipped and nothing more is sent.

Tests: the retry section of `test_scheduler.py`, the `test_b2_*` tests in `test_hardware_cases.py`, `test_law_9_*` in `test_laws.py`, and the `law 9` tests in `batch/grid.test.ts` and `batch/TurnGrid.test.tsx`.

### 2.4 Take timeouts

- Local takes (ComfyUI, region takes, 3D): `COCKPIT_LOCAL_TAKE_TIMEOUT_S`. It is unset by default, which means no limit.
- External takes: `COCKPIT_EXTERNAL_TAKE_TIMEOUT_S`. It is unset by default. The only limit is then the HTTP client's fixed 300 s read timeout (10 s to connect) in `openrouter.py`.
- When a configured limit passes, the scheduler asks the executor to cancel the job. In every case, a configured limit or the HTTP read timeout, the slot fails as `timeout`, that engine is benched, and nothing retries. For a priced external take the scheduler also writes `slot.charge_unverified` with reason `timeout`, because the call may have finished and billed on the provider's side.
- Polling is not a deadline. ComfyUI is polled every `COCKPIT_COMFY_POLL_S` (1 s) and the 3D runner every `COCKPIT_THREED_POLL_S` (2 s). After ComfyUI signals completion, its history is re-read up to `COCKPIT_HISTORY_CONFIRM_TRIES` (20) times, `COCKPIT_HISTORY_CONFIRM_INTERVAL_S` (0.5 s) apart. If no result appears, the take fails.

### 2.5 The bill re-check (B4)

When a paid take returns a cost above zero and no image, the amount the response reported enters the ledger at once. Then `_bill_check` in `scheduler.py` asks the provider's own generation record what it billed. It asks up to 3 times (fixed in code), `COCKPIT_BILL_CHECK_GAP_S` (20 s) apart, and stops at the first answer. It never generates again. Each ask is a `slot.bill_check` event and shows on the cell while it runs.

- The record agrees: a `slot.charge_verified` event. The ledger is unchanged.
- The record differs: the ledger is corrected by the difference, once (`slot.charged`), then `slot.charge_verified`.
- No answer after 3 asks, or the response carried no generation id: `slot.charge_unverified`. The amount stays as reported.

### 2.6 Committed spend, charged spend and caps

- **Committed** is the price quoted at Run from the provider catalogue. It is stored on the slot and the node. It is empty when the price is unlisted, and the client then says so instead of guessing.
- **Charged** is what the provider's response reported for that take. The two are separate values everywhere, including the manifest of a promoted take.
- The ledger is the `charged_usd` column of the append-only events table. A confirmed charge enters it exactly once: when a take lands, when a take fails as `charged_blank`, or as a separate `slot.charged` entry (a bill correction, or a paid result that arrived after its slot had already moved on). Totals sum charged amounts only (`GET /api/costs` for today and this month, plus the workspace total). A quote never counts. Local takes never write a charge.
- When a charge cannot be confirmed, no amount is invented. A `slot.charge_unverified` event states the committed amount and the reason: `cancel`, `timeout`, `restart`, `bill_check` or `no_job_id`.
- **Caps.** On the project surface every run states a spending limit and the project may set its own. A run whose known prices exceed either is refused before any engine is called, and a run with any engine whose price is unknown is refused whole (law 22). Only the artist changes a project's limits; an agent's change answers `403`. On the legacy canvas, what limits paid work: the LOCAL ONLY switch (`local_only`), under which the server refuses any Run with external takes and any reroll or backfill of a paid cell. A Run holds at most 16 takes (8 on the 3D route). `COCKPIT_EXTERNAL_CONCURRENCY` (4) limits calls in flight, not money.

Tests: the money section of `test_scheduler.py`, `test_local_only_is_proved_on_every_money_verb` in `test_api_v4.py`, `e2e/costs.spec.ts`.

### 2.7 Cancel

`cancel_slot` and `cancel_turn` in `scheduler.py` decide by the slot's state:

| State at cancel | Result |
|---|---|
| `landed`, `failed`, `skipped`, `set_aside`, `late` | kept as it is |
| `queued` or `held` | `skipped`, with the note "nothing spent" |
| `running`, local | the executor stops the job. For ComfyUI it interrupts only when the running job is ours. The slot fails with cause "cancelled", kind `interrupted`. No bench |
| `running`, external | the call is never cut, because the money is already in flight. The slot becomes `late` and `slot.charge_unverified` states the committed amount |

A `late` slot keeps its cell. If the call still delivers, the take lands into that same cell, the charge is recorded, and a `slot.late_landed` event is written. If the call ends without an image, the slot stays `late` and a `slot.late_failed` event is written. A `late` cell has no button and cannot be rerolled, because the cell is reserved for that arrival. Cancelling a turn flags every slot first, then cancels each one, and writes one `turn.cancelled` summary event. Tests: the cancel and late sections of `test_scheduler.py`.

## 3. Family A: generation failures

### A1 · Out of video memory at the requested size
**Status: implemented.**
- **Artist sees:** a red cell that names the engine and the size. Buttons: RETRY AT the smaller size (the default), then RETRY AS-IS · SAME SEED. With no smaller size to offer: RETRY AS-IS, then REROLL · NEW SEED. All are free.
- **System does:** `classify_execution_error` reads ComfyUI's error and marks it `oom`. The 3D lane uses the same text table. The slot fails at its frame with no automatic retry and no bench. The offered size is the largest frame this engine has landed with fewer pixels. If there is none, it is three quarters per side on a 64 px grid, never below 512. A retry at that size keeps the seed, and the size becomes the cell's own. A size change on a paid cell is refused. A 3D take has no frame, so its message says to free the card.
- **Lives in:** `executors.py`, `mesh.py`, `scheduler.py` (`smaller_frame`, `_oom_facts`), `db.py` (`frames_landed`), `api_v4.py` (`_reroll`), `batch/grid.ts`. Tests: `test_a1_*`, `test_retry_at_a_smaller_frame_*` and `test_the_smaller_frame_*` in `test_hardware_cases.py`, `A1:` in `batch/grid.test.ts` and `e2e/hardware-cases.spec.ts`.

### A2 · Engine crashed or its process died mid-take
**Status: partly implemented.**
- **System does:** there is no crash detector of its own. A service that stops answering is `unreachable`: one visible retry, then the slot fails and the lane is benched (for a 3D engine, only that engine). A job that fails is `error`: no retry, and a second one in the same turn benches the engine (A9). A 3D runner that lost the job says it may have restarted.
- **Missing:** a hung engine is not detected by default, because local takes have no timeout (A6).
- **Lives in:** `executors.py`, `mesh.py`, `scheduler.py`. Tests: `test_unreachable_retries_once_then_fails_and_benches` and `test_comfy_unreachable_benches_the_local_lane` in `test_scheduler.py`, `test_an_unreachable_runner_never_benches_the_whole_local_lane` in `test_mesh.py`.

### A3 · Model file missing or moved
**Status: not implemented.** No check for a missing model file was found. A graph that ComfyUI refuses or fails becomes a plain `error` (A2, A9). `GET /api/health` reports whether the models folder exists and is not empty.

### A4 · Model file corrupt or failed to load
**Status: not implemented.** A model that fails to load is not told apart from any other `error`. That kind never retries on its own.

### A5a · Blank or black frame from a local engine
**Status: implemented.**
- **Artist sees:** a red cell saying the engine finished but the frame came back empty, and "that's the engine, not your prompt". Buttons: RUN AGAIN · SAME SEED (the default), then REROLL · NEW SEED. Both are free.
- **System does:** `blank_frame` checks the returned image. If every colour band is flat within 4 levels, the frame is empty (black, white or one flat colour). The take fails as `blank` and never lands. The bytes are kept in staging and the path is named under DETAILS (law 7). There is no automatic retry and no bench, even when it repeats. The check runs on ComfyUI takes and on each pass of a region take. Bytes that are not an image are not treated as blank.
- **Lives in:** `executors.py`, `region_exec.py`, `scheduler.py`, `batch/grid.ts`. Tests: the `test_a5a_*` tests in `test_trust_cases.py`, `test_blank_pass_keeps_its_artefact` in `test_region_exec.py`, `A5a:` in `batch/grid.test.ts`, `batch/TurnGrid.test.tsx` and `e2e/trust-cases.spec.ts`.

### A5b · Provider filter fired
**Status: implemented**, as B5. A provider's filter is not a generation failure, so it has no handling of its own here.

### A6 · Take timed out
**Status: partly implemented.**
- **System does:** see section 2.4. A take that times out fails as `timeout` with a line saying the engine went silent, the engine is benched, and a priced take gets `slot.charge_unverified`.
- **Missing:** no per-engine limits and no default limit for local takes, so a hung local engine waits forever. The scheduler's own timeout path has no test.
- **Lives in:** `config.py`, `scheduler.py` (`_run_take`), `openrouter.py`. Tests: `test_openrouter_read_timeout_is_its_own_kind` in `test_executors.py`, `test_law_10_*` in `test_laws.py`.

### A7 · Disk full while writing a result
**Status: not implemented.** Nothing pauses the queue when the disk fills at run time. A write error while saving a take falls into the net for the cockpit's own errors (section 2.1). The check before Run is E6.

### A8 · Mask unusable
**Status: implemented.**
- **Artist sees:** the region surface keeps its run button disarmed and says which region has no ink.
- **System does:** this is a check, not a failure. The region route refuses a mask with no ink before any row is written. It resizes every mask to the source image's size, so a mask cannot fall outside the image. The refusal stops the whole region run, not single slots.
- **Lives in:** `api_v4_regions.py`, `regions.py`, `regions/fanout.ts`. Tests: `test_refusal_matrix` in `test_api_v4_regions.py`, `regions/fanout.test.ts`.

### A9 · Repeat failure of the same engine inside one turn
**Status: implemented.**
- **Artist sees:** one footer line naming the two slots that failed. The engine's remaining cells in that turn read HELD, each with TRY ONCE MORE. The queue strip says the engine is benched for this turn only.
- **System does:** after the second `error`, `oom` or `charged_blank` of one engine in one turn, `_repeat_check` benches that engine for that turn. Its queued takes are held, not dropped. Other engines and other turns keep running. TRY ONCE MORE lifts the bench. `blank`, `moderation` and `rate_limit` do not count.
- **Lives in:** `scheduler.py` (`A9_KINDS`, `_repeat_check`, `turn_scope`), `db.py` (`failed_in_turn`), `batch/grid.ts`. Tests: the `test_a9_*` tests and `test_the_queue_view_names_a_turn_scoped_bench_on_its_held_cells` in `test_hardware_cases.py`, `A9:` in `e2e/hardware-cases.spec.ts`.

### A10 · Model swap thrash
**Status: not implemented.** The queue never reorders. A Run lays its slots out engine by engine and the local lane runs them in slot order, so one turn's takes on one engine run back to back (`test_run_lays_out_batches_engine_by_engine_each_with_its_own_raws` in `test_api_v4.py`). No time estimate exists (law 17).

### A11 · Out of memory at a size this engine has held before
**Status: implemented.**
- **Artist sees:** the A1 cell, but the line says the engine held that size before, names the turn, and says the size is not the problem. RETRY AS-IS · SAME SEED is the default. The smaller size is still offered second.
- **System does:** `_oom_facts` looks up whether this engine has ever landed this exact frame. The answer is stored with the failure as `held_before`.
- **Lives in:** `scheduler.py`, `db.py` (`frames_landed`), `batch/grid.ts`. Tests: `test_a11_the_record_absolves_a_size_this_engine_held_before` in `test_hardware_cases.py`, `A11:` in `batch/grid.test.ts` and `e2e/hardware-cases.spec.ts`.

### A12 · Estimate drift
**Status: not implemented.** Crexil shows elapsed time only and predicts no durations (law 17), so there is no estimate that can drift.

## 4. Family B: external engines and money

### B1 · API key rejected or payment declined
**Status: partly implemented.**
- **Artist sees:** the failed cell says the provider turned the key away, then "nothing was charged". Buttons: RUN LOCALLY · FREE (the default), then the paid REROLL. The other external cells read HELD. The queue strip shows the benched lane with UNBENCH.
- **System does:** HTTP 401 and 402 become `dead_key`. There is no retry. `lane:external` is benched once, with one cause. Local takes keep running. At startup a missing or refused key benches the lane in the same way.
- **Missing:** the client has no place to enter a new key. `POST /api/setup/openrouter` checks and stores one, but it does not lift the bench. UNBENCH, or the next startup check, does.
- **Lives in:** `openrouter.py`, `scheduler.py`, `wiring.py`, `api.py`. Tests: `test_dead_key_benches_the_whole_external_lane_in_one_record` in `test_scheduler.py`, `test_law_1_*` in `test_laws.py`.

### B2 · Rate limited
**Status: implemented.**
- **Artist sees:** a gold cell: RETRYING · ATTEMPT i OF 5, whose clock the wait runs on, a countdown, "nothing is charged until an image lands", "Nothing needed from you.", and GIVE UP ON THIS SLOT.
- **System does:** see section 2.3. GIVE UP cancels the waiting take: it is skipped and nothing is spent. After the last attempt the slot fails as `rate_limit` and says how many tries were made. It never benches.
- **Lives in:** `openrouter.py`, `scheduler.py` (`_retry_requeue`, `_run_external`), `batch/grid.ts`. Tests: the `test_b2_*` tests and `test_retry_after_reads_seconds_dates_and_reset_epochs` in `test_hardware_cases.py`, `B2:` in `batch/grid.test.ts` and `e2e/hardware-cases.spec.ts`.

### B3 · Network offline
**Status: implemented.**
- **Artist sees:** one failed external cell saying the provider cannot be reached, "nothing was charged", and the other external cells HELD under one cause. Local cells are untouched.
- **System does:** `unreachable` retries once, then the slot fails and `lane:external` is benched. The lane does not resume by itself when the network returns. UNBENCH lifts it, and so does the next startup check if the provider answers.
- **Lives in:** `openrouter.py`, `scheduler.py`, `wiring.py`. Tests: `test_unreachable_retries_once_then_fails_and_benches` in `test_scheduler.py`.

### B4 · Charged but no image returned
**Status: implemented.**
- **Artist sees:** a red cell saying the provider took the job and billed it, but no image came back. Line two states the dollars charged. A gold line says the bill is being asked about, with the count, while the check runs. Buttons: COPY RECEIPT (the default), RUN LOCALLY · FREE, then the paid REROLL with its price. The receipt lists the time, provider, job id, amount, prompt, seed and slot. It can also be selected under DETAILS.
- **System does:** a response with a cost above zero and no image becomes `charged_blank`, carrying the amount and the generation id. The rest is section 2.5. It never generates again on its own. A response with no image and no cost is a plain `error`.
- **Lives in:** `openrouter.py`, `executors.py` (`verify_charge`), `scheduler.py` (`_bill_check`), `batch/grid.ts` (`receipt`, `checkLine`). Tests: the `test_b4_*` tests in `test_trust_cases.py`, `B4:` in `batch/grid.test.ts`, `batch/TurnGrid.test.tsx` and `e2e/trust-cases.spec.ts`.

### B5 · Provider content-policy refusal
**Status: implemented.**
- **Artist sees:** a line saying the provider would not run this prompt, that it is their filter, and that it is not a fault at the artist's end. Then "nothing was charged". Buttons: RUN LOCALLY · FREE (the default), then the paid REROLL.
- **System does:** HTTP 403 becomes `moderation`. No retry, no bench, and it does not count toward A9. The adapter treats billing as all or nothing, so a refused take carries no charge.
- **Lives in:** `openrouter.py`, `scheduler.py`, `batch/grid.ts`. Tests: `test_moderation_fails_the_slot_without_benching` in `test_scheduler.py`, `test_a9_counts_engine_level_kinds_only_and_never_across_turns` in `test_hardware_cases.py`, the paid-failure test in `e2e/batch-laws.spec.ts`.

### B6 · Provider changed pricing mid-session
**Status: partly implemented.**
- **System does:** committed and charged are kept per take. The cell's badge shows the charged amount once it is known, with both numbers in its tooltip. The promote face states both when they differ by half a cent or more. A reroll quotes the current price again.
- **Missing:** no one-time notice that a price changed, and no statement of the old and new price.
- **Lives in:** `batch/SlotCell.tsx`, `api_v4_promote.py` (`_cost_line`), `manifests.py`. Tests: `test_committed_flows_to_the_node_and_charged_to_the_ledger` in `test_scheduler.py`.

### B7 · Slow external take, or a take that arrives after cancel
**Status: implemented.**
- **Artist sees:** after a cancel, the cell reads LATE · ALREADY PAID with the committed amount, and says the take lands here if it still arrives. The queue strip counts late slots. A slow take that was never cancelled stays IN FLIGHT and lands like any other.
- **System does:** see section 2.7. A late arrival lands into its own original cell, so the grid never reflows (laws 7 and 8).
- **Lives in:** `scheduler.py` (`cancel_slot`, `_finalize_land`, `_finalize_late_failure`). Tests: the late section of `test_scheduler.py`, `test_late_bytes_land_into_the_cell_through_the_api` in `test_api_v4.py`, `C6:` in `e2e/trust-cases.spec.ts`.

### B8 · Committed and charged diverge
**Status: implemented.** This is a rule of the data model, not a message. See section 2.6. Tests: the money section of `test_scheduler.py`, `test_db.py`, `e2e/costs.spec.ts`.

### B9 · Cancel means two different things in a mixed batch
**Status: implemented.** Handled together with C6.

### B10 · Refilling a paid slot with a local engine
**Status: not implemented.** No slot is ever refilled by another engine. RUN LOCALLY · FREE arms a new Run with the same brief, so the failed cell keeps its engine and its history. A database trigger refuses any change to a slot's engine (law 8, `test_law_8_*` in `test_laws.py`).

### B11 · External engines are never truly reproducible
**Status: partly implemented.**
- **System does:** the manifest of a promoted external take, and the promote preview, state "record only, not replayable". A local take's manifest says "exact". A seed is sent only to models whose catalogue entry says they accept one. No surface offers an exact replay.
- **Missing:** the mark is written only at promote, not on the node itself. No test asserts the external wording.
- **Lives in:** `manifests.py` (`REPLAY_EXTERNAL`), `api_v4_promote.py`, `promote/PromotePlinth.tsx`.

## 5. Family C: queue and session states

### C1 · Closed with a queue in flight
**Status: partly implemented.**
- **System does:** the queue lives in the server, so closing the browser stops nothing. When the server restarts, `_recover` in `scheduler.py` rebuilds the benches and sorts every unfinished slot. Queued slots are queued again with the same seeds. A running local take is looked up at its engine: it lands, fails, or is queued again. If ComfyUI cannot be reached for that lookup, the local lane is benched and the slot stays `running` until the next startup looks it up again. A running external take is never sent again. It fails with a line saying the cockpit restarted during the call, and `slot.charge_unverified` states the committed amount. A slot whose prepared payload is lost is skipped and says to reroll. A `late` slot stays late. One `scheduler.recovered` event holds the counts. ElevenLabs speech is collected from ElevenLabs' history as the attempt's own take only: by its lost answer's request id, or by the one take of the same voice, model, text, settings and format, made after the attempt was sent, that no receipt binds and that no other unsettled attempt of the same line could have made. Otherwise it says the take cannot be collected safely, and nothing is generated again.
- **Missing:** no summary is shown to the artist on reopen. The remainder runs without an offer.
- **Tests:** the restart section of `test_scheduler.py`, `test_region_slot_survives_a_restart` in `test_api_v4_regions.py`, `test_a_queued_mesh_slot_survives_a_restart` in `test_api_v4_mesh.py`, and the recovery tests in `test_audio_hardening.py`.

### C2 · GPU held by another program
**Status: implemented.**
- **Artist sees:** local cells read HELD · THE CARD IS TAKEN. One footer line gives the size held and the card's total. It never names a program, because the card's reading cannot see one. Buttons: CHECK AGAIN and HOLD THE QUEUE. External cells keep landing.
- **System does:** when a local take fails as `oom`, the scheduler reads the card through ComfyUI. If memory held by something other than ComfyUI is at or above `COCKPIT_GPU_FOREIGN_HOLD_GB` (2.5), the take is not failed. It goes back to the queue as a visible retry, and `lane:local` is benched as `gpu_held`. A loop re-reads the card every `COCKPIT_GPU_RECHECK_S` (15 s) and lifts the bench when the hold is gone. CHECK AGAIN reads the card now. HOLD THE QUEUE stops the loop until the artist resumes. The bench and the loop survive a restart. With no reading, or a hold under the threshold, the failure stays A1 or A11.
- **Lives in:** `executors.py` (`read_gpu`), `scheduler.py` (`_gpu_held`, `_gpu_recheck_loop`, `gpu_check`, `gpu_hold`), `api_v4.py` (`/api/gpu/check`, `/api/gpu/hold`). Tests: the `test_c2_*` tests and `test_a_gpu_held_bench_survives_restart_and_keeps_watching` in `test_hardware_cases.py`, `C2:` in `e2e/hardware-cases.spec.ts`.

### C3 · Machine slept mid-queue
**Status: not implemented.** No sleep or wake detection was found. What happens to a running take across a sleep is not determined from the code. If the server process restarts, C1 applies.

### C4 · Run pressed while a turn is still running
**Status: implemented.**
- **Artist sees:** the Run button says how many takes it queues behind. The run bar says what is running, for how long so far, and how many are queued. These are counts and elapsed time, never a predicted total (law 17).
- **System does:** a new turn always queues. Nothing replaces a running turn. The local lane is first in, first out. `GET /api/queue` gives the depth.
- **Lives in:** `scheduler.py` (`submit_turn`), `api_v4.py` (`queue_view`), `composer/compose.ts` (`runLabel`, `busyLine`). Tests: `test_queue_view_shows_positions_elapsed_and_money` in `test_api_v4.py`, `composer/compose.test.ts`.

### C5 · Queue longer than patience
**Status: not implemented.** No threshold and no warning exist. The run bar states the queue depth (C4).

### C6 · Cancel mid-batch
**Status: implemented.**
- **Artist sees:** two presses. CANCEL TURN arms the stop and the footer says, with real numbers: which slots have landed and stay, which external takes are already paid and will land anyway, how many takes ahead cost nothing to drop, and how much GPU time the running local take loses. STOP THE QUEUE sends it. KEEP GOING or Esc stands down. It is a footer, never a dialog (law 2).
- **System does:** see section 2.7.
- **Lives in:** `scheduler.py`, `batch/grid.ts` (`stopLines`, `cancelBreath`), `batch/TurnGrid.tsx`. Tests: `test_c6_one_stop_is_three_acts_and_landed_work_is_never_touched` in `test_trust_cases.py`, the cancel section of `test_scheduler.py`, `C6:` in `batch/grid.test.ts` and `e2e/trust-cases.spec.ts`.

### C7 · Dropping a queued slot gives its turn back
**Status: implemented.**
- **Artist sees:** a queued or held cell has a cancel mark. The cell then reads SKIPPED and says no GPU time was spent and its turn was given back. It states no number of seconds (law 17). A reroll runs it after all.
- **System does:** `cancel_slot` moves the slot to `skipped` with the note "nothing spent" and removes it from the local queue.
- **Lives in:** `scheduler.py`, `batch/SlotCell.tsx`, `batch/grid.ts` (`costLine`). Tests: `test_cancel_skips_queued_slots_and_says_nothing_spent` in `test_scheduler.py`, `batch/TurnGrid.test.tsx`.

## 6. Family D: data integrity

### D1 · Parent image purged, child survives
**Status: partly implemented.**
- **System does:** purge is the only delete. It marks the node row as purged and removes the files. The child keeps its parent id, prompt, seed and dials. The tray says how many children a purge would leave without their source before the button is held. The record names "A PURGED PARENT". A Run or reroll that needs the purged source is refused in words.
- **Missing:** no offer to remake the parent from the record.
- **Lives in:** `db.py` (`purge_node`), `api_v4.py` (`purge_nodes`, `_resolve_parents`), `record/record.ts`. Tests: `test_children_lineage_survives_a_purged_parent` in `test_db.py`, `record/record.test.ts`, `e2e/tray.spec.ts`.

### D2 · Engine removed after nodes were made with it
**Status: partly implemented.**
- **System does:** a reroll or backfill of a cell whose preset or model is gone is refused in words. The old take stays and nothing is spent.
- **Missing:** the node carries no mark. The artist learns it only on trying.
- **Lives in:** `api_v4.py` (`_reroll`). Tests: `test_backfill_refusals_are_per_slot_and_stop_nothing` in `test_api_v4.py`.

### D3 · Store file corrupt on open
**Status: not implemented.** There is no recovery path for a damaged store. `db.py` refuses a file that is not a Crexil store, is older than schema 4, or is newer than the code. It leaves that file untouched and startup stops with the reason (`test_db.py`). Any other SQLite error on open also stops startup.

### D4 · Two windows on one workspace
**Status: not implemented.** No handling was found. Every window is a client of the one server, which is the only writer and owns the queue (law 19).

### D5 · Promote target moved or renamed
**Status: implemented.**
- **Artist sees:** the promote face says what is wrong with the folder, then what to do, with a control to point at another folder. The take is untouched.
- **System does:** `validate_target` runs right before writing and never guesses. It checks the allowed drives first (`PROMOTE_ROOTS`), then that the drive is mounted, then that the folder exists, is a folder and is writable. It never creates the promote folder itself. Files are staged in a temporary folder inside the destination and moved last, manifest first and artefact last. A failed move rolls the earlier ones back. The route answers 409 with the message and the remedy as separate fields. The default target is `PROMOTE_DIR`. A folder chosen in the client is stored and wins over it. A delivery checks the target the same way right before each item. It writes each file under a private name beside its place, verifies its SHA-256 as it is written and then renames it, the manifest before the take; a failure removes what that attempt wrote, and a file already there with other bytes is left untouched.
- **Lives in:** `promote.py`, `api_v4_promote.py`, `crexil_delivery.py`, `promote/`. Tests: `test_promote.py`, `test_character_package.py`, `test_a_broken_target_refuses_the_hold_and_leaves_the_take_alone` in `test_api_v4_promote.py`, `promote/promote.test.ts`, `D5:` in `e2e/promote.spec.ts`.

### D6 · Seedless engine, artist tries to reproduce
**Status: implemented.**
- **Artist sees:** with the seed locked, the SEED dial says how many slots ignore the lock and names the models. Roster rows carry a seed badge with its verified or unverified state. A cell without a seed says NO SEED.
- **System does:** the seed is sent only to external models whose catalogue entry says `supports_seed`. The request record keeps exactly what was sent. Audio engine rows declare the seeds they take (`seed_range`). Review refuses a fixed or locked seed outside that range, and freeze draws a random seed inside it, so the seed recorded is the seed sent.
- **Lives in:** `composer/dials.ts` (`seedBadges`), `composer/badges.ts`, `api_v4.py`, `crexil_audio.py` (`check_seeds`, `take_seed`). Tests: `composer/dials.test.ts`, `composer/badges.test.ts`, `test_seeds_and_voices.py`.

## 7. Family E: checks before Run

A check disarms or warns. It never produces a red cell.

### E1 · Empty prompt
**Status: implemented.** Run is disarmed and the run bar says "Needs a prompt.", with or without a source image. The server also refuses external takes that have no prompt. Code: `composer/compose.ts` (`whyNot`), `api_v4.py`. Tests: `composer/compose.test.ts`, `composer/Composer.test.tsx`.

### E2 · Source image given to an engine that cannot read it
**Status: implemented**, as prevention rather than as a warning.
- **Artist sees:** with a source image attached, a local engine without an image-to-image recipe stays listed and dimmed, with the reason, and cannot be stepped. The external lane stays listed and says external models take a text brief only. A dial an engine does not read shows a badge with the reason (law 12).
- **System does:** the server refuses the same requests, so no slot can silently ignore a source. No per-slot count line exists for this case. Comments that cite "E2's register" mean that warning style: one line beside Run that does not disarm it.
- **Lives in:** `composer/compose.ts`, `composer/Composer.tsx`, `api_v4.py` (`run_turn`). Tests: `test_refine_refusals_name_the_source_problem` in `test_api_v4.py`, `composer/Composer.test.tsx`, `e2e/refine.spec.ts`.

### E3 · Size above what a selected local engine can hold
**Status: partly implemented.**
- **System does:** `frameWarning` warns at the FRAME dial before Run. The words come from the capability matrix (`configs/engines.json`), with its verified or unverified state. Run stays armed.
- **Missing:** the warning does not read this card's own failure record. That record is used only after an `oom` (A1, A11).
- **Lives in:** `composer/dials.ts`. Tests: `composer/dials.test.ts`.

### E4 · Zero slots
**Status: implemented.** Run is disarmed with "A run needs at least one take." The server refuses the same request, and any Run above 16 takes. Code: `composer/compose.ts` (`whyNot`), `api_v4.py`. Tests: `composer/compose.test.ts`, `composer/Composer.test.tsx`.

### E5 · Prompt over an engine's token limit
**Status: implemented.**
- **Artist sees:** one warning per stepped local engine whose token window the prompt overruns. It names the engine, how many of the slots it holds, the token count as ABOUT, the window and whether that window is unverified, and says nothing is cut. Run stays armed.
- **System does:** the count is a client-side estimate and says so. Engines with no window in the capability matrix say nothing, and external models get no token warning. The server never shortens a prompt, and never refuses an image prompt for length. Audio engines that declare `text_limits` are refused at review, in plain words, when the text is longer: ElevenLabs v4 takes 10,000 characters, and the local voice engine 2,000 for a line and for a voice description. ElevenLabs' Voice Design description and sound-effect prompt limits are not published and wait for a live key.
- **Lives in:** `composer/tokens.ts`, and `text_limits` in `crexil_audio.py`. Tests: `test_e5_the_server_never_truncates_and_never_refuses_a_long_prompt` in `test_trust_cases.py`, `composer/tokens.test.ts`, `E5:` in `e2e/trust-cases.spec.ts`, `test_a_description_longer_than_the_local_engine_takes_is_refused_at_review` in `test_audio_guardrails.py`.

### E6 · Free video memory and free disk at Run
**Status: implemented.**
- **Artist sees:** if the drive cannot hold the run, Run is disarmed with the numbers: what the slots need, what is free, and how much to free up. If another program holds the card, a warning says so for the local takes and Run stays armed.
- **System does:** `GET /api/headroom` states the card as ComfyUI reads it, the free bytes on the staging drive, and bytes per pixel. Bytes per pixel is the median over the newest landed local takes. Before any has landed it is `COCKPIT_DISK_FALLBACK_BYTES_PER_PX` (1.5). A take with no known frame counts as 1024 by 1024. The run is refused when free space is below the need plus `COCKPIT_DISK_MARGIN_BYTES` (64 MiB). The server refuses the same run in the same words before a turn exists. The 3D route uses a fixed 180 MiB per mesh (no test was found for that variant). The card warning uses the C2 threshold. It does not compare free memory with the frame (law 17).
- **Lives in:** `api_v4.py` (`headroom`, `_refuse_if_disk_cannot_hold`), `api_v4_mesh.py`, `composer/compose.ts` (`diskWhy`, `vramWarning`). Tests: the `test_e6_*` tests in `test_hardware_cases.py`, `E6:` in `composer/compose.test.ts` and `e2e/hardware-cases.spec.ts`.

## 8. Family F: ambient

### F1 · Set-aside tray very large
**Status: not implemented.** No paging or age-out was found in the tray.

### F2 · Month spend crosses a round number
**Status: not implemented.** The corner shows today's and this month's charged totals. No notice fires at any amount.

### F3 · Clock skew makes ordering look wrong
**Status: partly implemented.** Turns are ordered by a per-workspace index, slots by position, and events by a sequence number that is never reused (`test_event_seq_is_strictly_monotonic_across_writers` in `test_db.py`). Missing: the queue view lists slots, and restart recovery re-queues them, by creation timestamp.

### F4 · Copy to clipboard fails
**Status: partly implemented.** The only copy action is B4's receipt. If the clipboard refuses, the button reads SELECT IT BELOW and DETAILS opens with the receipt selectable (`batch/SlotCell.tsx`). Missing: a test for the refusal path.

### F5 · Drag released over nothing
**Status: implemented.**
- **Artist sees:** the card goes back to where it was lifted from. Nothing else changes.
- **System does:** `resolveDrop` answers `nothing` when the pointer is outside the canvas, the tray and every other card. The release then sends no request. It is never a silent set-aside.
- **Lives in:** `canvas/drop.ts`, `canvas/Canvas.tsx`. Tests: `canvas/drop.test.ts`, the `(F5)` test in `e2e/drag.spec.ts`.

### F6 · Undo after a failure
**Status: not implemented.** There is no undo history. The only undo-like key restores a set-aside cell.

### F7 · Long engine names breaking the roster layout
**Status: partly implemented.** The composer's roster cuts a long engine name with an ellipsis (`composer/Composer.module.css`). Failure messages are not cut. Missing: a test.

### F8 · Installed engine that has never run once
**Status: not implemented.** No first-run marker or warning was found. Local takes have no timeout by default (A6), so a slow first run is not cut off.

## 9. Adding a case

1. Take the next free code in the family. Never renumber or reuse a code.
2. Add the entry to this document: code, plain name, status, what the artist sees, what the system does, where it lives.
3. Cite the code where it is handled (`# A5a: ...`) and at the start of each test that covers it (`test_a5a_...`, `"A5a: ..."`).
4. A new failure kind also needs a plain line in `plain_cause`, a place in `bench_scope_for` if it may pause anything (law 10), a retry rule only if the failure is transient (law 9), its buttons in `batch/grid.ts` (laws 4 and 6), and its name in the `KINDS` list of `test_laws.py`.
5. Write **implemented** only when the handling and at least one test exist. Until then write **partly implemented** and say what is missing.
