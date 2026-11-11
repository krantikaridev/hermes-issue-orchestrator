"""Claim, comment, hand off, and post rate-limited progress."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.parse
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import quote

from .constants import COMMENT_CHAR_LIMIT, LABEL_COLOR, MARKER_PREFIX
from .errors import OrchestratorError, assert_never
from .github_client import GitHubClient, build_transport
from .settings import Settings, auth_hint, normalize_label, normalize_login
from . import store
from .present import format_queue
from .textutil import one_line, paragraph

logger = logging.getLogger(__name__)

Action = Literal["close", "reassign", "release"]

_clock: Callable[[], float] = time.time
_client_factory: Callable[[Settings], Any] | None = None

CLAIM_TEXT = "Hermes: claimed this issue and marked it in progress."


def set_clock(clock: Callable[[], float] | None) -> None:
    global _clock
    _clock = clock or time.time


def set_client_factory(factory: Callable[[Settings], Any] | None) -> None:
    global _client_factory
    _client_factory = factory


def now() -> float:
    return float(_clock())


def client_for(settings: Settings) -> Any:
    if _client_factory is not None:
        return _client_factory(settings)
    return GitHubClient(build_transport(settings))


def run_tool(fn: Callable[[], dict[str, Any]]) -> str:
    try:
        return json.dumps(fn(), ensure_ascii=False)
    except OrchestratorError as exc:
        payload: dict[str, Any] = {"ok": False, "error": str(exc)}
        if exc.hint:
            payload["hint"] = exc.hint
        return json.dumps(payload, ensure_ascii=False)
    except Exception as exc:
        logger.warning("tool failed: %s", type(exc).__name__)
        return json.dumps({"ok": False, "error": "unexpected failure"})


def claim_issue(settings: Settings, args: object, session_id: str = "") -> dict[str, Any]:
    payload = coerce_args(args)
    client = client_for(settings)
    me = _my_login(settings, client)
    issue = _select_issue(settings, client, payload, me)
    number = int(issue["number"])
    title = _title(issue)
    marked = store.mark_claimed(settings.repo, number, session_id, title)
    try:
        _ensure_label(settings, client, settings.repo, number)
        login, warning = _try_assign(settings, client, settings.repo, number, me)
        posted = post_unique(
            settings,
            client,
            settings.repo,
            number,
            "claim",
            f"claim:{marked['generation']}",
            CLAIM_TEXT,
        )
    except Exception:
        if marked["created"]:
            store.restore_claim(marked["previous"], settings.repo, number)
        raise
    return {
        "ok": True,
        "repo": settings.repo,
        "issue_number": number,
        "title": title,
        "url": _url(settings.repo, number, issue),
        "state": "claimed",
        "deduped": (not marked["created"]) and (not posted),
        "assignee": login,
        "assignee_warning": warning,
        "in_progress_label": settings.in_progress_label,
    }


def comment_progress(settings: Settings, args: object, session_id: str = "") -> dict[str, Any]:
    payload = coerce_args(args)
    note = paragraph(payload.get("note"), limit=COMMENT_CHAR_LIMIT)
    if not note:
        raise OrchestratorError("note is required")
    repo, number = _resolve_target(settings, session_id, parse_issue_number(payload.get("issue_number")))
    posted = post_unique(settings, client_for(settings), repo, number, "note", note, f"Hermes: {note}")
    return {
        "ok": True,
        "repo": repo,
        "issue_number": number,
        "deduped": not posted,
        "url": f"https://github.com/{repo}/issues/{number}",
    }


def handoff_issue(settings: Settings, args: object, session_id: str = "") -> dict[str, Any]:
    payload = coerce_args(args)
    action = _action(payload.get("action"))
    summary = paragraph(payload.get("summary"), limit=COMMENT_CHAR_LIMIT)
    if not summary:
        raise OrchestratorError("summary is required")
    assignee = ""
    if action == "reassign":
        assignee = normalize_login(payload.get("assignee"), allow_filter=False)
    repo, number = _resolve_target(settings, session_id, parse_issue_number(payload.get("issue_number")))
    client = client_for(settings)
    issue = _load_issue(settings, client, repo, number)
    if "pull_request" in issue:
        raise OrchestratorError(f"#{number} is a pull request, not an issue")
    if issue.get("state") != "open":
        if action == "close":
            store.set_state(repo, number, "closed", _title(issue))
            return _handoff_result(repo, number, action, deduped=True, state="closed")
        raise OrchestratorError(f"issue #{number} is not open")
    existing = store.get_claim(repo, number)
    me = _my_login(settings, client)
    held = existing is not None and existing.get("state") == "claimed"
    if not held and me not in _assignee_logins(issue):
        raise OrchestratorError(
            f"refusing to {action} #{number}: this Hermes has no claim on it and it is not assigned to {me}",
            hint="claim the issue first with issue_claim",
        )
    generation = int(existing["generation"]) if existing else 0
    material = f"{generation}:{action}:{summary}:{assignee}"
    body = _handoff_body(action, summary, assignee)
    post_unique(settings, client, repo, number, "handoff", material, body)
    _apply_handoff(settings, client, repo, number, action, me, assignee)
    _remove_label(settings, client, repo, number)
    state = _handoff_state(action)
    store.set_state(repo, number, state, _title(issue))
    return _handoff_result(repo, number, action, deduped=False, state=state)


def list_queue(settings: Settings, session_id: str = "", *, include_github: bool = True) -> dict[str, Any]:
    claims = store.list_claims(settings.repo)
    session = store.session_claim(session_id)
    payload: dict[str, Any] = {
        "ok": True,
        "repo": settings.repo,
        "auth_method": settings.auth_method,
        "queue_label": settings.queue_label,
        "session_issue": int(session["issue_number"]) if session and session.get("state") == "claimed" else None,
        "claims": claims,
        "issues": [],
        "error": "",
        "truncated": False,
        "skipped_github": not include_github,
    }
    if not include_github:
        return payload
    try:
        issues = _list_issues(settings, client_for(settings), settings.repo, settings.queue_label, settings.queue_assignee)
    except OrchestratorError as exc:
        payload["ok"] = False
        payload["error"] = str(exc)
        return payload
    payload["truncated"] = len(issues) >= 30
    for issue in issues:
        if not isinstance(issue, dict) or "pull_request" in issue:
            continue
        names = _label_names(issue)
        logins = _assignee_logins(issue)
        payload["issues"].append(
            {
                "number": int(issue.get("number") or 0),
                "title": _title(issue),
                "in_progress": settings.in_progress_label in names,
                "assignee": logins[0] if logins else "",
                "url": _url(settings.repo, int(issue.get("number") or 0), issue),
            }
        )
    return payload


def issues_text(settings: Settings, raw_args: str, session_id: str = "") -> str:
    arg = " ".join(str(raw_args or "").split()).lower()
    if arg in {"help", "-h", "--help"}:
        return "Usage: /issues [queue|claimed|help]"
    if arg not in {"", "queue", "claimed"}:
        return "Usage: /issues [queue|claimed|help]"
    payload = list_queue(settings, session_id=session_id, include_github=arg != "claimed")
    return format_queue(payload)


def note_tokens(settings: Settings, session_id: str, usage: object, api_request_id: str = "") -> None:
    if not settings.progress_enabled:
        return
    prompt, completion = token_pair(usage)
    if prompt == 0 and completion == 0:
        return
    if session_id:
        store.add_session_tokens(session_id, prompt, completion, api_request_id)
        return
    row = _hook_claim(settings, "")
    if row is not None:
        store.add_tokens(str(row["repo"]), int(row["issue_number"]), prompt, completion, api_request_id)


def note_turn(settings: Settings, session_id: str, assistant_response: str) -> None:
    try:
        _note_turn(settings, session_id, assistant_response)
    except Exception as exc:
        logger.warning("progress hook failed: %s", type(exc).__name__)


def token_pair(usage: object) -> tuple[int, int]:
    if not isinstance(usage, dict):
        return 0, 0
    prompt = _count(usage, "prompt_tokens", "input_tokens")
    completion = _count(usage, "completion_tokens", "output_tokens")
    if prompt == 0 and completion == 0:
        prompt = _count(usage, "total_tokens")
    return prompt, completion


def coerce_args(args: object) -> dict[str, Any]:
    if args is None:
        return {}
    if isinstance(args, str):
        if not args.strip():
            return {}
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError as exc:
            raise OrchestratorError("tool arguments are not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise OrchestratorError("tool arguments must be an object")
        return parsed
    if isinstance(args, dict):
        return args
    raise OrchestratorError("tool arguments must be an object")


def parse_issue_number(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise OrchestratorError("issue_number must be a positive integer")
    number: int | None = None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    if number is None or number < 1:
        raise OrchestratorError("issue_number must be a positive integer")
    return number


def post_unique(
    settings: Settings,
    client: Any,
    repo: str,
    number: int,
    kind: str,
    material: str,
    body: str,
) -> bool:
    key = _event_key(kind, repo, number, material)
    marker = f"{MARKER_PREFIX}{key.rsplit(':', 1)[-1][:16]} -->"
    if not store.try_insert_event(key, kind):
        if not store.event_pending(key):
            return False
        # An earlier POST failed in transit and may have landed. Look before posting again.
        if _marker_on_issue(settings, client, repo, number, marker):
            store.confirm_event(key)
            return False
    try:
        status, data = client.request(
            "POST",
            f"{_repo_path(repo)}/issues/{number}/comments",
            {"body": f"{body[:60_000]}\n\n{marker}"},
        )
    except OrchestratorError as exc:
        if exc.status:
            store.delete_event(key)
        else:
            store.mark_event_pending(key)
        raise
    except Exception:
        store.mark_event_pending(key)
        raise
    if status not in (200, 201):
        store.delete_event(key)
        _fail(settings, status, data)
    store.confirm_event(key)
    return True


def _marker_on_issue(settings: Settings, client: Any, repo: str, number: int, marker: str) -> bool:
    data = _request(settings, client, "GET", f"{_repo_path(repo)}/issues/{number}/comments?per_page=100")
    if not isinstance(data, list):
        return False
    return any(isinstance(item, dict) and marker in str(item.get("body") or "") for item in data)


def _note_turn(settings: Settings, session_id: str, assistant_response: str) -> None:
    if not settings.progress_enabled:
        if session_id:
            store.take_session_tokens(session_id)
        return
    row = _hook_claim(settings, session_id)
    moved = store.take_session_tokens(session_id) if session_id else (0, 0)
    if row is None:
        return
    repo = str(row["repo"])
    number = int(row["issue_number"])
    if moved != (0, 0):
        store.add_tokens(repo, number, moved[0], moved[1], "")
    sentence = one_line(assistant_response) if settings.progress_include_reply else "Turn finished on this issue."
    sentence_hash = hashlib.sha256(sentence.encode("utf-8")).hexdigest()
    reserved = store.reserve_progress(repo, number, sentence_hash, now(), settings.progress_min_interval_seconds)
    if reserved is None:
        return
    body = f"Hermes: {sentence} · tokens {reserved['prompt']} in / {reserved['completion']} out"
    material = f"{sentence_hash}:{reserved['prompt']}:{reserved['completion']}"
    try:
        posted = post_unique(settings, client_for(settings), repo, number, "progress", material, body)
    except Exception:
        store.restore_progress(
            repo,
            number,
            posted_at=float(reserved["previous_posted_at"]),
            sentence_hash=str(reserved["previous_hash"]),
            prompt=int(reserved["prompt"]),
            completion=int(reserved["completion"]),
        )
        raise
    if not posted:
        store.restore_progress(
            repo,
            number,
            posted_at=float(reserved["previous_posted_at"]),
            sentence_hash=str(reserved["previous_hash"]),
            prompt=int(reserved["prompt"]),
            completion=int(reserved["completion"]),
        )


def _hook_claim(settings: Settings, session_id: str) -> dict[str, Any] | None:
    if session_id:
        row = store.session_claim(session_id)
        if row and row.get("state") == "claimed":
            return row
        return None
    rows = store.claimed_for_repo(settings.repo)
    if len(rows) == 1:
        return rows[0]
    return None


def _select_issue(settings: Settings, client: Any, args: dict[str, Any], me: str) -> dict[str, Any]:
    number = parse_issue_number(args.get("issue_number"))
    if number is not None:
        issue = _load_open_issue(settings, client, settings.repo, number)
        _refuse_if_held_elsewhere(settings, issue, number, me)
        return issue
    label = _filter_label(settings, args)
    assignee = _filter_assignee(settings, args)
    for issue in _list_issues(settings, client, settings.repo, label, assignee):
        if _skipped_auto(settings, issue, me):
            continue
        return issue
    raise OrchestratorError("no open issue matched the queue filters")


def _resolve_target(settings: Settings, session_id: str, number: int | None) -> tuple[str, int]:
    if number is not None:
        return settings.repo, number
    if session_id:
        row = store.session_claim(session_id)
        if row and row.get("state") == "claimed":
            return str(row["repo"]), int(row["issue_number"])
    rows = store.claimed_for_repo(settings.repo)
    if len(rows) == 1:
        return str(rows[0]["repo"]), int(rows[0]["issue_number"])
    raise OrchestratorError("no claimed issue for this session; pass issue_number or claim one")


def _list_issues(settings: Settings, client: Any, repo: str, label: str, assignee: str) -> list[dict[str, Any]]:
    params = [("state", "open"), ("sort", "created"), ("direction", "asc"), ("per_page", "30")]
    if label:
        params.append(("labels", label))
    if assignee:
        params.append(("assignee", assignee))
    query = urllib.parse.urlencode(params)
    data = _request(settings, client, "GET", f"{_repo_path(repo)}/issues?{query}")
    if not isinstance(data, list):
        raise OrchestratorError("GitHub returned an unexpected issue list")
    return [issue for issue in data if isinstance(issue, dict)]


def _load_open_issue(settings: Settings, client: Any, repo: str, number: int) -> dict[str, Any]:
    issue = _load_issue(settings, client, repo, number)
    if "pull_request" in issue:
        raise OrchestratorError(f"#{number} is a pull request, not an issue")
    if issue.get("state") != "open":
        raise OrchestratorError(f"issue #{number} is not open")
    return issue


def _load_issue(settings: Settings, client: Any, repo: str, number: int) -> dict[str, Any]:
    try:
        data = _request(settings, client, "GET", f"{_repo_path(repo)}/issues/{number}")
    except OrchestratorError as exc:
        if exc.status == 404:
            raise OrchestratorError(f"issue #{number} was not found in {repo}", hint=exc.hint, status=404) from None
        raise
    if not isinstance(data, dict):
        raise OrchestratorError("GitHub returned an unexpected issue")
    return data


def _skipped_auto(settings: Settings, issue: dict[str, Any], me: str) -> bool:
    if "pull_request" in issue or issue.get("state") != "open":
        return True
    if settings.in_progress_label in _label_names(issue):
        return True
    return any(login != me for login in _assignee_logins(issue))


def _refuse_if_held_elsewhere(settings: Settings, issue: dict[str, Any], number: int, me: str) -> None:
    existing = store.get_claim(settings.repo, number)
    if existing is not None and existing.get("state") == "claimed":
        return
    others = [login for login in _assignee_logins(issue) if login != me]
    if others:
        raise OrchestratorError(
            f"refusing to claim #{number}: it is assigned to {', '.join(others)}",
            hint="pick another issue, or ask the assignee to hand it off",
        )
    if settings.in_progress_label in _label_names(issue):
        raise OrchestratorError(
            f"refusing to claim #{number}: it already has the {settings.in_progress_label} label "
            "and this Hermes holds no claim on it",
            hint="another agent may be working it; release it there or remove the label by hand",
        )


def _my_login(settings: Settings, client: Any) -> str:
    user = _request(settings, client, "GET", "/user")
    login = str(user.get("login") or "") if isinstance(user, dict) else ""
    if not login:
        raise OrchestratorError("GitHub did not return a login for this credential", hint=auth_hint(settings))
    return login


def _ensure_label(settings: Settings, client: Any, repo: str, number: int) -> None:
    name = settings.in_progress_label
    status, data = client.request(
        "POST",
        f"{_repo_path(repo)}/labels",
        {"name": name, "color": LABEL_COLOR, "description": "Claimed by Hermes issue orchestrator"},
    )
    if status not in (200, 201, 422):
        _fail(settings, status, data)
    status, data = client.request(
        "POST",
        f"{_repo_path(repo)}/issues/{number}/labels",
        {"labels": [name]},
    )
    if status not in (200, 201):
        _fail(settings, status, data)


def _remove_label(settings: Settings, client: Any, repo: str, number: int) -> None:
    name = quote(settings.in_progress_label, safe="")
    status, data = client.request("DELETE", f"{_repo_path(repo)}/issues/{number}/labels/{name}")
    if status not in (200, 204, 404):
        _fail(settings, status, data)


def _try_assign(settings: Settings, client: Any, repo: str, number: int, login: str) -> tuple[str, str]:
    # POST adds an assignee; PATCH {"assignees": [...]} would replace everyone else.
    status, data = client.request("POST", f"{_repo_path(repo)}/issues/{number}/assignees", {"assignees": [login]})
    if status not in (200, 201):
        message = data.get("message") if isinstance(data, dict) else "could not assign"
        return "", f"could not assign {login}: {str(message)[:200]}"
    return login, ""


def _request(settings: Settings, client: Any, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    status, data = client.request(method, path, body)
    if status in (200, 201, 204):
        return data
    _fail(settings, status, data)


def _fail(settings: Settings, status: int, data: Any) -> None:
    message = "GitHub request failed"
    if isinstance(data, dict) and data.get("message"):
        message = str(data["message"])[:300]
    hint = auth_hint(settings) if status in (401, 403) else ""
    raise OrchestratorError(f"GitHub HTTP {status}: {message}", hint=hint, status=status)


def _filter_label(settings: Settings, args: dict[str, Any]) -> str:
    if "label" in args and args["label"] is not None:
        return normalize_label(args["label"], allow_empty=True)
    return settings.queue_label


def _filter_assignee(settings: Settings, args: dict[str, Any]) -> str:
    if "assignee" in args and args["assignee"] is not None:
        return normalize_login(args["assignee"], allow_filter=True)
    return settings.queue_assignee


def _apply_handoff(
    settings: Settings, client: Any, repo: str, number: int, action: Action, me: str, assignee: str
) -> None:
    path = f"{_repo_path(repo)}/issues/{number}"
    match action:
        case "close":
            _request(settings, client, "PATCH", path, {"state": "closed", "state_reason": "completed"})
        case "release":
            _unassign(settings, client, repo, number, me)
        case "reassign":
            if assignee != me:
                _unassign(settings, client, repo, number, me)
            _request(settings, client, "POST", f"{path}/assignees", {"assignees": [assignee]})
        case unreachable:
            assert_never(unreachable)


def _unassign(settings: Settings, client: Any, repo: str, number: int, login: str) -> None:
    """Remove only this token's login; other assignees stay."""
    _request(settings, client, "DELETE", f"{_repo_path(repo)}/issues/{number}/assignees", {"assignees": [login]})


def _handoff_body(action: Action, summary: str, assignee: str) -> str:
    match action:
        case "reassign":
            return f"Hermes: reassigned to {assignee} — {summary}"
        case "release":
            return f"Hermes: released back to the queue — {summary}"
        case "close":
            return f"Hermes: closed — {summary}"
        case unreachable:
            assert_never(unreachable)


def _handoff_state(action: Action) -> str:
    match action:
        case "close":
            return "closed"
        case "reassign":
            return "reassigned"
        case "release":
            return "released"
        case unreachable:
            assert_never(unreachable)


def _handoff_result(repo: str, number: int, action: str, *, deduped: bool, state: str) -> dict[str, Any]:
    return {
        "ok": True,
        "repo": repo,
        "issue_number": number,
        "action": action,
        "deduped": deduped,
        "state": state,
        "url": f"https://github.com/{repo}/issues/{number}",
    }


def _action(value: object) -> Action:
    text = str(value or "").strip().lower()
    if text == "close":
        return "close"
    if text == "reassign":
        return "reassign"
    if text == "release":
        return "release"
    raise OrchestratorError("action must be close, reassign, or release")


def _label_names(issue: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for label in issue.get("labels") or []:
        if isinstance(label, dict):
            names.append(str(label.get("name") or ""))
        else:
            names.append(str(label))
    return names


def _assignee_logins(issue: dict[str, Any]) -> list[str]:
    logins: list[str] = []
    for person in issue.get("assignees") or []:
        if isinstance(person, dict) and person.get("login"):
            logins.append(str(person["login"]))
    return logins


def _title(issue: dict[str, Any]) -> str:
    return " ".join(str(issue.get("title") or "").split())[:240]


def _url(repo: str, number: int, issue: dict[str, Any]) -> str:
    url = issue.get("html_url")
    if isinstance(url, str) and url.startswith(f"https://github.com/{repo}/"):
        return url
    return f"https://github.com/{repo}/issues/{number}"


def _repo_path(repo: str) -> str:
    owner, name = repo.split("/", 1)
    return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"


def _event_key(kind: str, repo: str, number: int, material: str) -> str:
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"{kind}:{repo}#{number}:{digest}"


def _count(usage: dict[str, Any], *keys: str) -> int:
    for key in keys:
        if key not in usage or usage[key] is None or isinstance(usage[key], bool):
            continue
        try:
            return max(0, int(usage[key]))
        except (TypeError, ValueError):
            return 0
    return 0
