"""Tool behavior against a fake GitHub API."""

from __future__ import annotations

import json

import pytest

import hio
import hio.service as service
import hio.store as store
from hio.errors import OrchestratorError
from hio.settings import parse_settings
from tests.fake_github import FakeGitHub


def _settings(**overrides):
    values = {"repo": "acme/demo", "auth_method": "pat"}
    values.update(overrides)
    return parse_settings(values)


def _use(fake: FakeGitHub):
    service.set_client_factory(lambda settings: fake)
    return fake


def test_claim_picks_the_oldest_open_issue_and_dedupes_the_comment():
    fake = _use(FakeGitHub())
    fake.add_issue(1, "First", labels=["bug"])
    fake.add_issue(2, "Second", pull_request=True)
    fake.add_issue(3, "Already going", labels=["in-progress"])
    result = service.claim_issue(_settings(queue_label="bug"), {}, session_id="sess-a")
    assert result["ok"] is True
    assert result["issue_number"] == 1
    assert result["deduped"] is False
    assert result["assignee"] == "octocat"
    assert any(label["name"] == "in-progress" for label in fake.issues[1]["labels"])
    assert fake.comments[0]["body"].startswith("Hermes: claimed")
    again = service.claim_issue(_settings(queue_label="bug"), {"issue_number": 1}, session_id="sess-a")
    assert again["deduped"] is True
    assert len(fake.comments) == 1
    bound = store.session_claim("sess-a")
    assert bound is not None
    assert bound["issue_number"] == 1


def test_claim_by_number_skips_pull_requests_and_closed_issues():
    fake = _use(FakeGitHub())
    fake.add_issue(4, "A pull request", pull_request=True)
    fake.add_issue(5, "Done", state="closed")
    with pytest.raises(OrchestratorError, match="pull request"):
        service.claim_issue(_settings(), {"issue_number": 4})
    with pytest.raises(OrchestratorError, match="not open"):
        service.claim_issue(_settings(), {"issue_number": 5})


def test_empty_queue_is_an_error_and_does_not_record_a_claim():
    _use(FakeGitHub())
    with pytest.raises(OrchestratorError, match="no open issue"):
        service.claim_issue(_settings(), {})
    assert store.list_claims("acme/demo") == []


def test_label_failure_rolls_back_the_claim():
    fake = _use(FakeGitHub())
    fake.add_issue(7, "Needs a label")
    fake.fail_labels = 1
    with pytest.raises(OrchestratorError, match="label write failed"):
        service.claim_issue(_settings(), {"issue_number": 7}, session_id="sess")
    assert store.session_claim("sess") is None
    assert store.get_claim("acme/demo", 7) is None


def test_assign_failure_still_claims():
    fake = _use(FakeGitHub())
    fake.add_issue(8, "Assign later")
    fake.fail_assign = True
    result = service.claim_issue(_settings(), {"issue_number": 8})
    assert result["ok"] is True
    assert result["assignee"] == ""
    assert "could not assign" in result["assignee_warning"]
    assert fake.comments


def test_progress_note_is_not_posted_twice():
    fake = _use(FakeGitHub())
    fake.add_issue(9, "Work")
    service.claim_issue(_settings(), {"issue_number": 9}, session_id="sess")
    first = service.comment_progress(_settings(), {"note": "parser fixed"}, session_id="sess")
    second = service.comment_progress(_settings(), {"note": "parser fixed"}, session_id="sess")
    assert first["deduped"] is False
    assert second["deduped"] is True
    notes = [comment["body"] for comment in fake.comments if "parser fixed" in comment["body"]]
    assert notes == ["Hermes: parser fixed"]


def test_handoff_close_reassign_and_release():
    fake = _use(FakeGitHub())
    fake.add_issue(10, "Close me")
    fake.add_issue(11, "Move me")
    fake.add_issue(12, "Put me back")
    settings = _settings()
    service.claim_issue(settings, {"issue_number": 10}, session_id="s10")
    service.claim_issue(settings, {"issue_number": 11}, session_id="s11")
    service.claim_issue(settings, {"issue_number": 12}, session_id="s12")

    with pytest.raises(OrchestratorError, match="assignee"):
        service.handoff_issue(settings, {"action": "reassign", "summary": "needs a person"}, session_id="s11")

    closed = service.handoff_issue(settings, {"action": "close", "summary": "fixed"}, session_id="s10")
    moved = service.handoff_issue(
        settings,
        {"action": "reassign", "summary": "needs review", "assignee": "ada"},
        session_id="s11",
    )
    released = service.handoff_issue(settings, {"action": "release", "summary": "blocked"}, session_id="s12")

    assert closed["state"] == "closed"
    assert fake.issues[10]["state"] == "closed"
    assert moved["state"] == "reassigned"
    assert fake.issues[11]["assignees"] == [{"login": "ada"}]
    assert released["state"] == "released"
    assert fake.issues[12]["assignees"] == []
    assert all(label["name"] != "in-progress" for label in fake.issues[12]["labels"])
    assert store.session_claim("s10") is None
    bodies = [comment["body"] for comment in fake.comments]
    assert any(body.startswith("Hermes: closed") for body in bodies)
    assert any("reassigned to ada" in body for body in bodies)


def test_issues_command_lists_queue_and_claims():
    fake = _use(FakeGitHub())
    fake.add_issue(13, "Queued")
    settings = _settings()
    service.claim_issue(settings, {"issue_number": 13}, session_id="sess")

    class Ctx:
        def __init__(self):
            self.tools = {}
            self.hooks = {}
            self.commands = {}
            self.skills = []
            self.config = {"repo": "acme/demo", "auth_method": "pat"}

        def register_tool(self, name, toolset, schema, handler, **kwargs):
            self.tools[name] = handler

        def register_hook(self, name, callback, **kwargs):
            self.hooks[name] = callback

        def register_command(self, name, handler, **kwargs):
            self.commands[name] = handler

        def register_skill(self, name, path, **kwargs):
            self.skills.append(name)

        def get_config(self, key, default=None):
            return self.config.get(key, default)

    ctx = Ctx()
    hio.register(ctx)
    assert set(ctx.tools) == {"issue_claim", "issue_comment_progress", "issue_handoff"}
    assert set(ctx.hooks) == {"post_llm_call", "post_api_request"}
    text = ctx.commands["issues"]("", session_id="sess")
    assert "acme/demo" in text
    assert "auth: pat" in text
    assert "#13" in text
    assert "claimed" in text
    assert "this session: #13" in text
    raw = ctx.tools["issue_comment_progress"]({"note": "from the tool"}, session_id="sess")
    assert json.loads(raw)["ok"] is True


def test_tool_reports_a_missing_pat_without_calling_github():
    from hio.github_client import GitHubClient, PatTransport

    opener_calls = {"n": 0}

    class Opener:
        def open(self, request, timeout=None):
            opener_calls["n"] += 1
            raise AssertionError("network")

    service.set_client_factory(lambda settings: GitHubClient(PatTransport(secret_fn=lambda: "", opener=Opener())))
    raw = json.loads(service.run_tool(lambda: service.claim_issue(_settings(), {})))
    assert raw["ok"] is False
    assert "hermes config set" in raw["hint"]
    assert opener_calls["n"] == 0
