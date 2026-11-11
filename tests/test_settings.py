"""Settings parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

import hio.settings as settings
from hio.constants import REPO_REQUIRED, TOKEN_ENV
from hio.errors import OrchestratorError

ROOT = Path(__file__).resolve().parents[1]


def test_repo_is_required_and_other_defaults_remain():
    for raw in ({}, {"repo": ""}, {"repo": "  "}):
        with pytest.raises(OrchestratorError, match=REPO_REQUIRED):
            settings.parse_settings(raw)
    parsed = settings.parse_settings({"repo": "acme/demo"})
    assert parsed.repo == "acme/demo"
    assert parsed.auth_method == "pat"
    assert parsed.progress_enabled is True
    assert parsed.progress_min_interval_seconds == 90
    assert parsed.in_progress_label == "in-progress"


def test_repo_accepts_a_github_url():
    parsed = settings.parse_settings({"repo": "https://github.com/acme/demo.git"})
    assert parsed.repo == "acme/demo"


@pytest.mark.parametrize(
    "repo",
    ["acme", "acme/demo/extra", "https://github.com/acme/../demo", "acme/../demo", "not a repo"],
)
def test_repo_rejects_malformed_values(repo):
    with pytest.raises(OrchestratorError, match="repo must look like owner/name"):
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


def test_manifest_and_docs_make_repo_required_without_a_sandbox_default():
    manifest = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    assert "default: krantikaridev/hermes-orchestrator-sandbox" not in manifest
    assert "hermes-orchestrator-sandbox" not in manifest
    repo_block = manifest.split("  repo:\n", 1)[1].split("  auth_method:", 1)[0]
    assert "required: true" in repo_block
    assert "default:" not in repo_block
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    catalog = (ROOT / "docs/plugin-catalog/hermes-issue-orchestrator.yaml").read_text(encoding="utf-8")
    constants = (ROOT / "constants.py").read_text(encoding="utf-8")
    for text in (readme, catalog, constants, manifest):
        assert "hermes-orchestrator-sandbox" not in text
        assert "DEFAULT_REPO" not in text
    assert REPO_REQUIRED in readme
    assert "owner/example-repo" in readme
