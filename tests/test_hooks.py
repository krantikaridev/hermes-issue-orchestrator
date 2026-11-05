"""Automatic progress comments and token accounting."""

from __future__ import annotations

import hio.service as service
import hio.store as store
from hio.settings import parse_settings
from tests.fake_github import FakeGitHub


def _settings(**overrides):
    values = {"repo": "acme/demo", "auth_method": "pat", "progress_min_interval_seconds": 90}
    values.update(overrides)
    return parse_settings(values)


def _use(fake: FakeGitHub):
    service.set_client_factory(lambda settings: fake)
    return fake


def _progress_comments(fake: FakeGitHub) -> list[str]:
    return [comment["body"] for comment in fake.comments if "tokens" in comment["body"]]


def test_turn_posts_tokens_once_and_rate_limits_the_next_line():
    fake = _use(FakeGitHub())
    fake.add_issue(1, "Work")
    settings = _settings()
    service.set_clock(lambda: 1_000.0)
    service.claim_issue(settings, {"issue_number": 1}, session_id="sess")
    service.note_tokens(settings, "sess", {"prompt_tokens": 10, "completion_tokens": 4}, api_request_id="req-1")
    service.note_tokens(settings, "sess", {"prompt_tokens": 10, "completion_tokens": 4}, api_request_id="req-1")
    service.note_turn(settings, "sess", "Narrowed it to the parser.")
    assert _progress_comments(fake) == ["Hermes: Narrowed it to the parser. · tokens 10 in / 4 out"]

    service.note_tokens(settings, "sess", {"total_tokens": 7}, api_request_id="req-2")
    service.note_turn(settings, "sess", "A different sentence.")
    assert len(_progress_comments(fake)) == 1

    service.set_clock(lambda: 1_090.0)
    service.note_turn(settings, "sess", "A different sentence.")
    assert _progress_comments(fake)[-1] == "Hermes: A different sentence. · tokens 7 in / 0 out"


def test_same_sentence_without_new_tokens_is_not_repeated():
    fake = _use(FakeGitHub())
    fake.add_issue(2, "Work")
    settings = _settings()
    service.set_clock(lambda: 1_000.0)
    service.claim_issue(settings, {"issue_number": 2}, session_id="sess")
    service.note_turn(settings, "sess", "Same line")
    service.set_clock(lambda: 2_000.0)
    service.note_turn(settings, "sess", "Same line")
    assert len(_progress_comments(fake)) == 1


def test_no_claim_does_not_post_and_two_claims_need_a_session():
    fake = _use(FakeGitHub())
    fake.add_issue(3, "One")
    fake.add_issue(4, "Two")
    settings = _settings()
    service.note_tokens(settings, "nobody", {"prompt_tokens": 3, "completion_tokens": 1}, "req")
    service.note_turn(settings, "nobody", "hello")
    assert fake.comments == []

    service.claim_issue(settings, {"issue_number": 3}, session_id="a")
    service.claim_issue(settings, {"issue_number": 4}, session_id="b")
    service.set_clock(lambda: 5_000.0)
    service.note_tokens(settings, "", {"prompt_tokens": 9, "completion_tokens": 1}, "loose")
    service.note_turn(settings, "", "unscoped")
    assert _progress_comments(fake) == []
    service.note_tokens(settings, "b", {"prompt_tokens": 2, "completion_tokens": 2}, "for-b")
    service.note_turn(settings, "b", "session b")
    assert len(_progress_comments(fake)) == 1
    assert fake.comments[-1]["number"] == 4


def test_failed_progress_comment_can_be_retried():
    fake = _use(FakeGitHub())
    fake.add_issue(5, "Retry")
    settings = _settings()
    service.set_clock(lambda: 100.0)
    service.claim_issue(settings, {"issue_number": 5}, session_id="sess")
    fake.fail_comments = 1
    before = len(fake.comments)
    service.note_tokens(settings, "sess", {"prompt_tokens": 1, "completion_tokens": 1}, "t")
    service.note_turn(settings, "sess", "will retry")
    assert len([comment for comment in fake.comments if "tokens" in comment["body"]]) == 0
    service.note_turn(settings, "sess", "will retry")
    posted = [comment["body"] for comment in fake.comments[before:] if "tokens" in comment["body"]]
    assert posted == ["Hermes: will retry · tokens 1 in / 1 out"]
    assert store.session_claim("sess") is not None


def test_progress_line_redacts_bearer_text():
    from hio.textutil import one_line

    line = one_line("saw Bearer super-secret-value in the log")
    assert "Bearer" not in line
    assert "[redacted]" in line


def test_token_pair_reads_common_usage_shapes():
    assert service.token_pair({"prompt_tokens": 3, "completion_tokens": 4}) == (3, 4)
    assert service.token_pair({"input_tokens": 8, "output_tokens": 1}) == (8, 1)
    assert service.token_pair({"total_tokens": 6}) == (6, 0)
    assert service.token_pair(None) == (0, 0)
    assert service.token_pair({"prompt_tokens": True, "total_tokens": 2}) == (2, 0)


def test_registered_hooks_accept_the_host_payload():
    fake = _use(FakeGitHub())
    fake.add_issue(6, "Hook")
    settings = _settings()
    service.claim_issue(settings, {"issue_number": 6}, session_id="sess")

    class Ctx:
        def __init__(self):
            self.hooks = {}
            self.config = {"repo": "acme/demo", "progress_min_interval_seconds": 0}

        def register_tool(self, *args, **kwargs):
            return None

        def register_hook(self, name, callback, **kwargs):
            self.hooks[name] = callback

        def register_command(self, *args, **kwargs):
            return None

        def register_skill(self, *args, **kwargs):
            return None

        def get_config(self, key, default=None):
            return self.config.get(key, default)

    import hio

    ctx = Ctx()
    hio.register(ctx)
    service.set_clock(lambda: 50.0)
    ctx.hooks["post_api_request"](
        session_id="sess",
        usage={"prompt_tokens": 5, "completion_tokens": 1},
        api_request_id="hook-req",
        extra="ignored",
    )
    ctx.hooks["post_llm_call"](
        session_id="sess",
        assistant_response="hook line\nwith detail",
        model="demo",
        platform="cli",
    )
    assert _progress_comments(fake) == ["Hermes: hook line with detail · tokens 5 in / 1 out"]
