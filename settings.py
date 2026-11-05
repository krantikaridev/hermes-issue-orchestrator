"""Plugin settings under ``plugins.entries.<id>.settings``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from .constants import DEFAULT_REPO, IN_PROGRESS_LABEL, OAUTH_HINT, PAT_HINT
from .errors import OrchestratorError

AuthMethod = Literal["pat", "oauth"]

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.:/+-]{0,49}$")
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


@dataclass(frozen=True)
class Settings:
    repo: str
    auth_method: AuthMethod
    queue_label: str
    queue_assignee: str
    in_progress_label: str
    progress_enabled: bool
    progress_min_interval_seconds: int


def auth_hint(settings: Settings) -> str:
    if settings.auth_method == "oauth":
        return OAUTH_HINT
    return PAT_HINT


def normalize_repo(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        text = DEFAULT_REPO
    text = re.sub(r"\.git$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^https?://github\.com/", "", text, flags=re.IGNORECASE)
    text = text.strip("/")
    if "\\" in text or " " in text or not _REPO.fullmatch(text):
        raise OrchestratorError("repo must look like owner/name")
    owner, name = text.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."} or ".." in owner or ".." in name:
        raise OrchestratorError("repo must look like owner/name")
    return f"{owner}/{name}"


def normalize_label(value: object, *, allow_empty: bool) -> str:
    text = str(value or "").strip()
    if not text and allow_empty:
        return ""
    if not _LABEL.fullmatch(text):
        raise OrchestratorError("labels must be 1-50 characters without commas")
    return text


def normalize_login(value: object, *, allow_filter: bool) -> str:
    text = str(value or "").strip()
    if allow_filter and text in {"", "none", "*"}:
        return text
    if not _LOGIN.fullmatch(text):
        raise OrchestratorError("assignee must be a GitHub login")
    return text


def parse_settings(values: Mapping[str, Any] | None = None) -> Settings:
    raw = dict(values or {})
    return Settings(
        repo=normalize_repo(raw.get("repo")),
        auth_method=_auth_method(raw.get("auth_method", "pat")),
        queue_label=normalize_label(raw.get("queue_label", ""), allow_empty=True),
        queue_assignee=normalize_login(raw.get("queue_assignee", ""), allow_filter=True),
        in_progress_label=normalize_label(raw.get("in_progress_label", IN_PROGRESS_LABEL), allow_empty=False),
        progress_enabled=_bool(raw.get("progress_enabled", True), default=True),
        progress_min_interval_seconds=_interval(raw.get("progress_min_interval_seconds", 90)),
    )


def load_settings(ctx: Any) -> Settings:
    keys = (
        "repo",
        "auth_method",
        "queue_label",
        "queue_assignee",
        "in_progress_label",
        "progress_enabled",
        "progress_min_interval_seconds",
    )
    values = {key: ctx.get_config(key, None) for key in keys}
    cleaned = {key: value for key, value in values.items() if value is not None}
    return parse_settings(cleaned)


def _auth_method(value: object) -> AuthMethod:
    text = str(value or "pat").strip().lower()
    if text == "pat":
        return "pat"
    if text == "oauth":
        return "oauth"
    raise OrchestratorError("auth_method must be pat or oauth")


def _bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, int) and not isinstance(value, bool):
        return bool(value)
    return default


def _interval(value: object) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 90
    if number < 0:
        return 0
    return min(number, 86_400)
