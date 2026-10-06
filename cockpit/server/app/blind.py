"""Blind runs: a run whose engine identity is withheld until an authored reveal.

The run flag is set at preparation (`Preparation.blind`) and recorded at Run in
`blind_runs`. A run stays hidden until its first `run.revealed` event, which
names its author (law 23). While hidden, every read that could tell the takes
apart by engine passes through `mask`, so no route, poll, search, export or CLI
read leaks identity: the snapshot, `/api/events`, `/api/queue`, the workspace
reads, the per-take record and provider receipts. A masked item says so with
`identity_hidden: true`; its engine reads `HIDDEN_ENGINE`, never null, because a
null engine means imported material. Run totals and caps stay visible (law 22).

Decisions stay selections and assessments: a selection made while its run is
hidden records `blind: true` through `selection_facts`. Take positions are
arranged once at Run by `arrange`; after that they never move (law 8).
"""

from __future__ import annotations

HIDDEN_ENGINE = 'hidden'


class NotBuilt(ValueError):
    """A blind-run behavior that is not built yet; routes answer 501 in words."""


async def hidden_turns(db, workspace_id: str | None = None) -> frozenset[str]:
    """Runs that are blind and not yet revealed."""
    return frozenset(run['turn_id'] for run in await db.blind_runs(workspace_id) if not run['revealed'])


async def mark(db, turn_id: str, workspace_id: str, actor: dict) -> None:
    """Record at Run, inside the accepting transaction, that this run is blind."""
    await db.mark_blind_run(turn_id, workspace_id, actor)


async def apply(db, kind: str, items: list[dict], *, turn_of=None,
                workspace_id: str | None = None) -> list[dict]:
    """Mask every item that belongs to a hidden run. `turn_of` reads an item's
    turn id (default: its `turn_id`)."""
    hidden = await hidden_turns(db, workspace_id)
    if not hidden:
        return items
    turn_of = turn_of or (lambda item: item.get('turn_id'))
    return [mask(kind, item) if turn_of(item) in hidden else item for item in items]


async def is_hidden(db, turn_id: str | None) -> bool:
    return bool(turn_id) and turn_id in await hidden_turns(db)


# --- behavior the blind-runs builder fills in ---

def mask(kind: str, item: dict) -> dict:  # noqa: ARG001
    """The one masking rule. `kind` is one of: 'node', 'slot', 'event', 'queue',
    'group', 'material_record'. Returns the item as a hidden run's
    reader may see it: `identity_hidden` true, engine `HIDDEN_ENGINE`, and model,
    seed, per-take cost, job reference, provider descriptors and engine-naming
    causes withheld. Not built yet: returns the item unchanged."""
    return item


def arrange(specs: list[dict]) -> list[dict]:
    """The frozen order of a blind run's takes, decided once at Run so positions
    never group takes by engine. Not built yet: keeps the given order."""
    return specs


async def selection_facts(db, direction_id: str, node_ids: list[str]) -> dict:  # noqa: ARG001
    """Facts a selection event records about blindness, for example
    `{'blind': True, 'turn_ids': [...]}`. Not built yet: records none."""
    return {}


def may_serve(member: dict) -> bool:  # noqa: ARG001
    """Whether a hidden run's execution file may be served. The takes themselves
    are always playable; provider receipts name the provider. Not built yet:
    serves everything."""
    return True


async def reveal(db, turn_id: str, body) -> dict:  # noqa: ARG001
    """Record the authored reveal of a blind run and return its `BlindRunView`."""
    raise NotBuilt('Revealing a blind run is not built yet. The run stays as it is.')
