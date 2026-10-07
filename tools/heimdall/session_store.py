"""O(1)-per-event session state: which slices this session has already edited,
persisted at ``.heimdall/session-<id>.json``."""

from __future__ import annotations

import json
import os
from collections.abc import Collection


class FileSessionStore:
    def __init__(self, heimdall_dir: str) -> None:
        self._dir = heimdall_dir

    def _path_for(self, session: str) -> str:
        safe = "".join(c if (c.isascii() and c.isalnum()) or c in "-_" else "_" for c in session)
        return os.path.join(self._dir, f"session-{safe}.json")

    def get_edited_slices(self, session: str) -> Collection[str]:
        p = self._path_for(session)
        if not os.path.isfile(p):
            return ()
        try:
            with open(p, encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError):
            return ()
        slices = state.get("edited_slices") if isinstance(state, dict) else None
        return tuple(s for s in slices if isinstance(s, str)) if isinstance(slices, list) else ()

    def add_edited_slice(self, session: str, slice: str) -> None:
        slices = set(self.get_edited_slices(session))
        if slice in slices:
            return
        slices.add(slice)
        os.makedirs(self._dir, exist_ok=True)
        with open(self._path_for(session), "w", encoding="utf-8", newline="\n") as f:
            json.dump({"edited_slices": sorted(slices)}, f)
