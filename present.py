"""Text for the /issues command."""

from __future__ import annotations

from typing import Any


def format_queue(payload: dict[str, Any]) -> str:
    lines = [
        str(payload.get("repo") or ""),
        f"auth: {payload.get('auth_method') or 'pat'}",
    ]
    label = str(payload.get("queue_label") or "")
    if label:
        lines.append(f"label: {label}")
    session_issue = payload.get("session_issue")
    if session_issue:
        lines.append(f"this session: #{session_issue}")
    lines.extend(["", "Claims"])
    claims = payload.get("claims") or []
    if not claims:
        lines.append("  (none)")
    else:
        for row in claims:
            title = str(row.get("title") or "").strip()
            suffix = f" {title}" if title else ""
            lines.append(f"  #{row.get('issue_number')} {row.get('state')}{suffix}")
    if payload.get("skipped_github"):
        return "\n".join(lines)
    lines.extend(["", "Open queue"])
    error = str(payload.get("error") or "")
    issues = payload.get("issues") or []
    if error:
        lines.append(f"  (GitHub list failed: {error})")
    elif not issues:
        lines.append("  (none)")
    else:
        for issue in issues:
            mark = " in progress" if issue.get("in_progress") else ""
            assignee = str(issue.get("assignee") or "")
            who = f" @{assignee}" if assignee else ""
            lines.append(f"  #{issue.get('number')}{mark}{who} {issue.get('title') or ''}".rstrip())
        if payload.get("truncated"):
            lines.append("  (showing the first 30)")
    return "\n".join(lines)
