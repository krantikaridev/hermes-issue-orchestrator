"""Settings parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

import hio.settings as settings
from hio.constants import DEFAULT_REPO, TOKEN_ENV
from hio.errors import OrchestratorError

ROOT = Path(__file__).resolve().parents[1]


def test_defaults_are_pat_and_the_sandbox_placeholder():
    parsed = settings.parse_settings({})
    assert parsed.auth_method == "pat"
    assert parsed.repo == DEFAULT_REPO
    assert parsed.progress_enabled is True
    assert parsed.progress_min_interval_seconds == 90
    assert parsed.in_progress_label == "in-progress"


def test_repo_accepts_a_github_url():
    parsed = settings.parse_settings({"repo": "https://github.com/acme/demo.git"})
    assert parsed.repo == "acme/demo"


@pytest.mark.parametrize(
    "repo",
    ["", "acme", "acme/demo/extra", "https://github.com/acme/../demo", "acme/../demo", "not a repo"],
)
def test_repo_rejects_malformed_values_when_nonempty_and_invalid(repo):
    if repo == "":
        assert settings.parse_settings({"repo": repo}).repo == DEFAULT_REPO
        return
    with pytest.raises(OrchestratorError):
        settings.parse_settings({"repo": repo})


def test_auth_method_rejects_unknown_values():
    with pytest.raises(OrchestratorError):
        settings.parse_settings({"auth_method": "basic"})


def test_manifest_declares_pat_secret_and_optional_oauth():
    text = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    assert f"env: {TOKEN_ENV}" in text
    assert "default: pat" in text
    assert "public_repo" in text
    assert "issue_claim" in text
    assert "post_llm_call" in text
    assert "post_api_request" in text
