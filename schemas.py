"""Tool schemas the model sees."""

from __future__ import annotations

from typing import Any

CLAIM: dict[str, Any] = {
    "name": "issue_claim",
    "description": (
        "Claim the next open GitHub issue in the configured repository, or one issue "
        "number, and mark it in progress. Use this to take the next task from the "
        "issue queue. Optional label and assignee narrow the queue for this call. "
        "Claiming the same issue again does not post a second claim comment."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "issue_number": {
                "type": "integer",
                "description": "Claim this open issue. Omit to take the oldest matching issue.",
            },
            "label": {
                "type": "string",
                "description": "Only consider open issues with this label. Empty string means no label filter.",
            },
            "assignee": {
                "type": "string",
                "description": "GitHub login, none for unassigned issues, or * for any assignee.",
            },
        },
        "required": [],
    },
}

COMMENT_PROGRESS: dict[str, Any] = {
    "name": "issue_comment_progress",
    "description": (
        "Post a progress note on the claimed GitHub issue. Use it for a milestone a "
        "maintainer should read. The same note is not posted twice. Automatic turn "
        "summaries are posted separately and are rate-limited."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "note": {
                "type": "string",
                "description": "The progress note to post on the issue.",
            },
            "issue_number": {
                "type": "integer",
                "description": "Issue to comment on. Omit to use the issue claimed in this session.",
            },
        },
        "required": ["note"],
    },
}

HANDOFF: dict[str, Any] = {
    "name": "issue_handoff",
    "description": (
        "Finish the claimed issue. close posts the summary and closes it. reassign "
        "moves it to another GitHub login. release clears the in-progress label and "
        "assignee so it returns to the queue. A summary is required every time."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "What was done, or why the issue is moving.",
            },
            "action": {
                "type": "string",
                "enum": ["close", "reassign", "release"],
                "description": "close, reassign, or release.",
            },
            "assignee": {
                "type": "string",
                "description": "GitHub login. Required when action is reassign.",
            },
            "issue_number": {
                "type": "integer",
                "description": "Issue to hand off. Omit to use the issue claimed in this session.",
            },
        },
        "required": ["summary", "action"],
    },
}
