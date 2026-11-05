"""Hermes host calls.

These imports stay inside the functions. ``hermes plugins validate`` imports this
package and calls ``register()`` in a scratch process; importing Hermes at load
time would run host startup during that probe. Unit tests run without Hermes
installed, so a missing host module becomes an ``OrchestratorError``.
"""

from __future__ import annotations

from typing import Any

from .constants import OAUTH_HINT, OAUTH_ID, PLUGIN_NAME, TOKEN_ENV
from .errors import OrchestratorError


def read_secret(name: str) -> str | None:
    """Return one Hermes secret, or None when it is unset."""
    try:
        from agent.secret_scope import get_secret
    except ImportError as exc:
        raise OrchestratorError("Hermes secret storage is unavailable", hint=f"hermes config set {name}") from exc
    value = get_secret(name, default=None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def open_plugin_db() -> Any:
    """Open this plugin's Hermes-managed SQLite database."""
    try:
        from plugins.plugin_storage import plugin_db
    except ImportError as exc:
        raise OrchestratorError("plugin storage is unavailable outside Hermes") from exc
    return plugin_db(PLUGIN_NAME)


def oauth_exchange(
    method: str,
    url: str,
    body: dict[str, Any] | None,
    headers: dict[str, str],
) -> tuple[int, bytes]:
    """Call GitHub with the Hermes-held OAuth grant. The token never reaches us."""
    try:
        from hermes_cli.auth_constants import AuthError
        from hermes_cli.plugin_provider_requests import ProviderNotSignedIn, credentialed_provider_request
    except ImportError as exc:
        raise OrchestratorError("Hermes OAuth support is unavailable", hint=OAUTH_HINT) from exc
    try:
        response = credentialed_provider_request(
            OAUTH_ID,
            method,
            url,
            json=body,
            headers=headers,
            timeout=20.0,
        )
    except ProviderNotSignedIn as exc:
        raise OrchestratorError(str(exc)[:300], hint=OAUTH_HINT) from None
    except AuthError as exc:
        code = getattr(exc, "code", "auth")
        raise OrchestratorError(f"{code}: {exc}"[:300], hint=OAUTH_HINT) from None
    except PermissionError as exc:
        raise OrchestratorError(str(exc)[:300], hint=OAUTH_HINT) from None
    raw = response.body if isinstance(response.body, bytes) else bytes(response.body)
    return int(response.status), raw


def pat_hint() -> str:
    return f"hermes config set {TOKEN_ENV}"
