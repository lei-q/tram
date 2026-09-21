"""Atomic ProjectState persistence (.tram/state.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from tram.models.state import ProjectState


class StateNotFoundError(RuntimeError):
    pass


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> ProjectState:
        if not self.path.exists():
            raise StateNotFoundError(f"state file not found: {self.path} (run `tram init`)")
        return ProjectState.model_validate(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, state: ProjectState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
