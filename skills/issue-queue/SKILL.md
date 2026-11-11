---
name: issue-queue
description: Work the configured GitHub repository's open issues as the task queue.
---

# Issue queue

Use this when the user wants Hermes to take work from GitHub issues.

1. `/issues` shows the open queue and which issues are claimed.
2. `issue_claim` takes the oldest matching open issue, or the issue number you were given, and marks it in progress.
3. Do the work described in that issue. Stay on it until you hand it off.
4. `issue_comment_progress` posts a milestone a maintainer should read. A hook already posts a short line and token counts after turns, so do not repeat that line.
5. `issue_handoff` closes the issue with a summary, reassigns it, or releases it back to the queue.

If a tool returns `"ok": false`, stop and show `error` and `hint`. Do not invent issue numbers, tokens, or repository names. The repository comes from the plugin setting. The default credential is a GitHub personal access token stored by Hermes.
