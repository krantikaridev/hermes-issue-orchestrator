"""Errors the tools return as JSON. Messages never include credential values."""

from __future__ import annotations

from typing import Never


class OrchestratorError(Exception):
    """A failure the model can read and act on."""

    def __init__(self, message: str, *, hint: str = "", status: int = 0) -> None:
        super().__init__(message)
        self.hint = hint
        self.status = status


def assert_never(value: Never) -> None:
    raise RuntimeError(f"unhandled variant: {value!r}")
