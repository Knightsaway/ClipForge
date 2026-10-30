"""Undo / redo for the Studio: snapshots of the whole edit, one per settled change.

A snapshot is the Studio's state dict; its key (a JSON string of everything that matters) tells whether anything
actually changed, so a click that changes nothing doesn't become an undo step.
"""
from __future__ import annotations

import json

LIMIT = 150


def snapshot_key(state: dict) -> str:
    """What makes two snapshots the same edit (which piece is selected doesn't count)."""
    st = dict(state, current=None,
              pieces=[dict(p, clip=getattr(p["clip"], "path", p["clip"])) for p in state.get("pieces", [])])
    return json.dumps(st, sort_keys=True, default=str)


class EditHistory:
    def __init__(self, limit: int = LIMIT):
        self.limit = limit
        self._steps: list[tuple[str, dict]] = []
        self._i = -1

    def reset(self) -> None:
        self._steps, self._i = [], -1

    def record(self, state: dict) -> bool:
        """Add a step after the current one (dropping anything you could have redone). False = nothing changed."""
        key = snapshot_key(state)
        if 0 <= self._i < len(self._steps) and self._steps[self._i][0] == key:
            return False
        del self._steps[self._i + 1:]
        self._steps.append((key, state))
        if len(self._steps) > self.limit:
            del self._steps[0]
        self._i = len(self._steps) - 1
        return True

    @property
    def can_undo(self) -> bool:
        return self._i > 0

    @property
    def can_redo(self) -> bool:
        return self._i < len(self._steps) - 1

    def undo(self) -> dict | None:
        if not self.can_undo:
            return None
        self._i -= 1
        return self._steps[self._i][1]

    def redo(self) -> dict | None:
        if not self.can_redo:
            return None
        self._i += 1
        return self._steps[self._i][1]

    def __len__(self) -> int:
        return len(self._steps)
