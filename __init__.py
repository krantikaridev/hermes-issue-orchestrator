"""Register the GitHub issue queue with Hermes."""

from __future__ import annotations

import logging
from pathlib import Path

from .errors import OrchestratorError
from .schemas import CLAIM, COMMENT_PROGRESS, HANDOFF
from .service import claim_issue, comment_progress, handoff_issue, issues_text, note_tokens, note_turn, run_tool
from .settings import load_settings

logger = logging.getLogger(__name__)

TOOLSET = "hermes-issue-orchestrator"


def register(ctx: object) -> None:
    """Wire tools, hooks, and /issues. Called once when Hermes loads the plugin."""

    def settings():
        return load_settings(ctx)

    def on_claim(args, **kwargs):
        return run_tool(lambda: claim_issue(settings(), args, session_id=_session(kwargs)))

    def on_comment(args, **kwargs):
        return run_tool(lambda: comment_progress(settings(), args, session_id=_session(kwargs)))

    def on_handoff(args, **kwargs):
        return run_tool(lambda: handoff_issue(settings(), args, session_id=_session(kwargs)))

    def on_api(session_id="", usage=None, api_request_id="", **kwargs):
        try:
            note_tokens(
                settings(),
                session_id=str(session_id or ""),
                usage=usage if usage is not None else kwargs.get("usage"),
                api_request_id=str(api_request_id or kwargs.get("api_request_id") or ""),
            )
        except OrchestratorError as exc:
            logger.warning("post_api_request skipped: %s", exc)
            return str(exc)
        except Exception as exc:
            logger.warning("post_api_request token note skipped: %s", type(exc).__name__)

    def on_llm(session_id="", assistant_response="", **kwargs):
        del kwargs
        try:
            note_turn(settings(), session_id=str(session_id or ""), assistant_response=str(assistant_response or ""))
        except OrchestratorError as exc:
            logger.warning("post_llm_call skipped: %s", exc)
            return str(exc)

    def on_issues(raw_args="", **kwargs):
        try:
            return issues_text(settings(), str(raw_args or ""), session_id=_session(kwargs))
        except OrchestratorError as exc:
            return str(exc)
        except Exception as exc:
            logger.warning("issues command failed: %s", type(exc).__name__)
            return "Could not list issues."

    ctx.register_tool(name="issue_claim", toolset=TOOLSET, schema=CLAIM, handler=on_claim)
    ctx.register_tool(name="issue_comment_progress", toolset=TOOLSET, schema=COMMENT_PROGRESS, handler=on_comment)
    ctx.register_tool(name="issue_handoff", toolset=TOOLSET, schema=HANDOFF, handler=on_handoff)
    ctx.register_hook("post_api_request", on_api)
    ctx.register_hook("post_llm_call", on_llm)
    ctx.register_command(
        "issues",
        handler=on_issues,
        description="List the GitHub issue queue and which issues are claimed",
        args_hint="[queue|claimed|help]",
    )
    skill = Path(__file__).resolve().parent / "skills" / "issue-queue" / "SKILL.md"
    if skill.is_file():
        ctx.register_skill("issue-queue", skill)


def _session(kwargs: dict) -> str:
    return str(kwargs.get("session_id") or "")
