"""PAT and optional OAuth transports. Neither test talks to GitHub."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from hio.errors import OrchestratorError
from hio.github_client import GitHubClient, OAuthTransport, PatTransport, _NoRedirect
from hio.settings import parse_settings


class RecordingOpener:
    def __init__(self, status: int = 200, payload: bytes = b"{}") -> None:
        self.requests: list[urllib.request.Request] = []
        self.status = status
        self.payload = payload
        self.opened = False

    def open(self, request, timeout=None):
        self.opened = True
        self.requests.append(request)
        if self.status >= 400:
            raise urllib.error.HTTPError(
                request.full_url,
                self.status,
                "error",
                hdrs=None,
                fp=io.BytesIO(self.payload),
            )
        return _Response(self.status, self.payload)


class _Response:
    def __init__(self, status: int, payload: bytes) -> None:
        self.status = status
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


def test_pat_sends_bearer_only_to_api_github():
    opener = RecordingOpener(payload=b'{"login":"octocat"}')
    transport = PatTransport(secret_fn=lambda: "test-token-value", opener=opener)
    client = GitHubClient(transport)
    status, data = client.request("GET", "/user")
    assert status == 200
    assert data["login"] == "octocat"
    header = opener.requests[0].get_header("Authorization")
    assert header == "Bearer test-token-value"
    assert opener.requests[0].full_url == "https://api.github.com/user"


def test_pat_refuses_other_hosts_before_reading_the_secret():
    calls = {"n": 0}

    def secret():
        calls["n"] += 1
        return "test-token-value"

    opener = RecordingOpener()
    transport = PatTransport(secret_fn=secret, opener=opener)
    with pytest.raises(OrchestratorError, match="api.github.com"):
        transport.send("GET", "https://example.com/user", None)
    assert calls["n"] == 0
    assert opener.opened is False


def test_missing_pat_names_the_hermes_secret():
    transport = PatTransport(secret_fn=lambda: "  ", opener=RecordingOpener())
    with pytest.raises(OrchestratorError, match="not set") as caught:
        transport.send("GET", "https://api.github.com/user", None)
    assert "hermes config set" in caught.value.hint
    assert "HERMES_ISSUE_ORCHESTRATOR_GITHUB_TOKEN" in caught.value.hint


def test_pat_refuses_redirects():
    with pytest.raises(OrchestratorError, match="redirect"):
        _NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://example.com/")


def test_pat_http_error_returns_status_without_raising_the_request():
    opener = RecordingOpener(status=401, payload=b'{"message":"Bad credentials"}')
    client = GitHubClient(PatTransport(secret_fn=lambda: "test-token-value", opener=opener))
    status, data = client.request("GET", "/user")
    assert status == 401
    assert data["message"] == "Bad credentials"


def test_oauth_does_not_attach_authorization_or_read_a_pat():
    seen: dict = {}

    def exchange(method, url, body, headers):
        seen["method"] = method
        seen["url"] = url
        seen["headers"] = dict(headers)
        seen["body"] = body
        return 200, b'{"login":"octocat"}'

    transport = OAuthTransport(request_fn=exchange)
    status, raw = transport.send("GET", "https://api.github.com/user", None)
    assert status == 200
    assert json.loads(raw)["login"] == "octocat"
    assert "Authorization" not in seen["headers"]
    assert "login/oauth" not in seen["url"]
    assert seen["method"] == "GET"


def test_oauth_refuses_other_hosts_before_the_exchange():
    def exchange(*args):
        raise AssertionError("oauth exchange should not run")

    transport = OAuthTransport(request_fn=exchange)
    with pytest.raises(OrchestratorError, match="api.github.com"):
        transport.send("POST", "http://api.github.com/user", {"a": 1})


def test_default_transport_is_pat_and_oauth_is_opt_in():
    from hio.github_client import build_transport

    assert isinstance(build_transport(parse_settings({})), PatTransport)
    selected = build_transport(parse_settings({"auth_method": "oauth", "repo": "acme/demo"}))
    assert isinstance(selected, OAuthTransport)
