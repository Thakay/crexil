"""The decision record export: the artist's own record of what was chosen, among
which takes, by whom, and why, derived from selection and assessment events.

It is never a training or evaluation dataset and holds no audio: ElevenLabs'
Prohibited Use Policy forbids using its output in either, and Crexil records
decisions, not material. A hidden run's candidates are exported only after its
reveal (law 23 keeps the author of every line).
"""

from __future__ import annotations

PURPOSE = ("The artist's decision record: which take was chosen, among which, by whom and why. "
           "It is not a training or evaluation dataset and holds no audio.")


class NotBuilt(ValueError):
    """The export is not built yet; routes answer 501 in words."""


async def export(db, workspace_id: str) -> dict:  # noqa: ARG001
    """A `PreferenceExport` for one project, one record per decision."""
    raise NotBuilt('The decision record export is not built yet. Nothing was written.')
