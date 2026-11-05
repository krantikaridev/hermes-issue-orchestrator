"""GitHub HTTPS client. PAT comes from Hermes secrets. OAuth never reveals the token."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol

from .constants import API_ROOT, API_VERSION, REQUEST_TIMEOUT_SECONDS, TOKEN_ENV, USER_AGENT
from .errors import OrchestratorError, assert_never
from .host import oauth_exchange, pat_hint, read_secret
from .settings import Settings


class Transport(Protocol):
    def send(self, method: str, url: str, body: dict[str, Any] | None) -> tuple[int, bytes]:
        """Perform one GitHub request and return the status and raw body."""


class GitHubClient:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        if method not in {"GET", "POST", "PATCH", "DELETE"}:
            raise OrchestratorError("refusing an unsupported GitHub method")
        if not path.startswith("/") or path.startswith("//"):
            raise OrchestratorError("refusing a malformed GitHub path")
        if method in {"GET", "DELETE"} and body is not None:
            raise OrchestratorError("refusing a body on this GitHub request")
        url = API_ROOT + path
        guard_url(url)
        status, raw = self._transport.send(method, url, body)
        return status, decode_body(raw)


class PatTransport:
    """Send the Hermes-stored PAT only to https://api.github.com."""

    def __init__(
        self,
        secret_fn: Any = None,
        opener: Any = None,
    ) -> None:
        self._secret_fn = secret_fn
        self._opener = opener or urllib.request.build_opener(_NoRedirect)

    def send(self, method: str, url: str, body: dict[str, Any] | None) -> tuple[int, bytes]:
        guard_url(url)
        token = self._token()
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = base_headers()
        headers["Authorization"] = f"Bearer {token}"
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return int(getattr(response, "status", 200)), response.read()
        except urllib.error.HTTPError as exc:
            return int(exc.code), exc.read()
        except OrchestratorError:
            raise
        except Exception:
            raise OrchestratorError("GitHub request failed") from None

    def _token(self) -> str:
        raw = self._secret_fn() if self._secret_fn is not None else read_secret(TOKEN_ENV)
        token = str(raw or "").strip()
        if not token:
            raise OrchestratorError(f"{TOKEN_ENV} is not set in Hermes secrets", hint=pat_hint())
        if "\n" in token or "\r" in token:
            raise OrchestratorError("refusing a GitHub token that contains a line break")
        return token


class OAuthTransport:
    """Ask Hermes to attach the GitHub OAuth grant. This object never sees it."""

    def __init__(self, request_fn: Any = None) -> None:
        self._request_fn = request_fn or oauth_exchange

    def send(self, method: str, url: str, body: dict[str, Any] | None) -> tuple[int, bytes]:
        guard_url(url)
        headers = base_headers()
        if body is not None:
            headers["Content-Type"] = "application/json"
        if "Authorization" in headers:
            raise OrchestratorError("refusing to attach an OAuth token from plugin code")
        return self._request_fn(method, url, body, headers)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise OrchestratorError(f"refusing GitHub redirect ({code})")


def build_transport(settings: Settings) -> PatTransport | OAuthTransport:
    match settings.auth_method:
        case "pat":
            return PatTransport()
        case "oauth":
            return OAuthTransport()
        case unreachable:
            assert_never(unreachable)


def guard_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "api.github.com" or parsed.port not in (None, 443):
        raise OrchestratorError("refusing a GitHub request outside https://api.github.com")
    if parsed.username or parsed.password:
        raise OrchestratorError("refusing a GitHub URL with user info")


def base_headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": API_VERSION,
    }


def decode_body(raw: bytes) -> Any:
    if not raw:
        return None
    text = raw.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"message": text[:300]}
