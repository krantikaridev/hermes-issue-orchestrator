"""Short, credential-free text for GitHub comments."""

from __future__ import annotations

import re

from .constants import PROGRESS_CHAR_LIMIT

_SECRET_TEXT = re.compile(r"(?i)\bbearer\s+\S+|\bgithub_pat_\S+|\bghp_[A-Za-z0-9]+")


def scrub(text: object) -> str:
    return _SECRET_TEXT.sub("[redacted]", str(text or ""))


def one_line(text: object, *, limit: int = PROGRESS_CHAR_LIMIT) -> str:
    collapsed = " ".join(scrub(text).split())
    if not collapsed:
        return "Turn finished."
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def paragraph(text: object, *, limit: int) -> str:
    collapsed = " ".join(scrub(text).split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"
