# hermes-issue-orchestrator

Hermes Agent plugin that treats GitHub issues as the agent's task queue.

Hermes can claim an open issue, mark it in progress, post progress (including a short automatic note with token use), and hand the issue off by closing it, reassigning it, or putting it back on the queue.

**Credential decision:** the supported day-to-day path is a GitHub personal access token stored in Hermes secrets. That is what the demo below uses. GitHub OAuth is optional and off unless you set `auth_method` to `oauth`. Hermes holds that grant; this plugin never reads the OAuth token.

**Required:** set `repo` to the GitHub `owner/name` you want Hermes to work, for example `owner/example-repo`. There is no default. Until it is set, tools, hooks and `/issues` return `set plugins.entries.hermes-issue-orchestrator.settings.repo` and do not call GitHub.

## What it registers

| Surface | Name | Role |
| --- | --- | --- |
| Tool | `issue_claim` | Pick or claim an open issue and mark it in progress |
| Tool | `issue_comment_progress` | Post a progress note. The same note is not posted twice |
| Tool | `issue_handoff` | Close with a summary, reassign, or release back to the queue |
| Hook | `post_api_request` | Add that response's token counts to the claimed issue |
| Hook | `post_llm_call` | Post one short progress line plus those totals, rate-limited |
| Slash command | `/issues` | List the queue and local claim state |
| Skill | `issue-queue` | Tell the agent to claim, work, and hand off |

State lives in the plugin SQLite database Hermes provides (`plugin_db`). Claim comments, identical notes, and repeated progress lines are deduped there. The database stores issue numbers, titles, session ids, comment hashes, and token totals. It does not store the GitHub token.

## Disclosure

Read this before installing.

- The plugin calls `https://api.github.com` only: list, read and close issues, add and remove one label, add or remove assignees (only its own login, plus the target on `reassign`), post comments, read an issue's comments when retrying a failed post, and read the authenticated user (`GET /user`).
- The default credential is `HERMES_ISSUE_ORCHESTRATOR_GITHUB_TOKEN`, read with `agent.secret_scope.get_secret`. The plugin does not read `.env` files, `auth.json`, or another tool's token file.
- Optional OAuth uses `credentialed_provider_request("github", ...)`. Hermes attaches and refreshes the token. Plugin code does not receive it. The declared scope is `public_repo` (public repositories only). Private repositories need a PAT.
- `post_llm_call` posts to the claimed issue without a separate approval prompt for each comment. By default the post contains only a fixed sentence plus token counts; the model's reply is included only with `progress_include_reply: true`. Posts are skipped when nothing is claimed, when two claims exist and the session did not claim one, when the line repeats, and when the last post was inside `progress_min_interval_seconds` (default 90). Explicit tool calls still go through Hermes's normal tool path.
- Each comment carries a hidden HTML marker (`<!-- hermes-issue-orchestrator:… -->`). If a comment request fails in transit, the next retry reads the issue's comments for that marker before posting, so a lost response does not cause a duplicate.
- On HTTP 429, or a 403 secondary rate limit, a request is retried twice with a short backoff (2 s, then 4 s) and then fails with a clear "rate limit" error.
- No shell commands, no background process, no telemetry, no self-update, and no change to Hermes core.
- OAuth sign-in is `hermes auth add hermes-issue-orchestrator:github` in a terminal. Hermes does not start that browser flow from cron or the gateway. If the grant is missing, the tool returns an error instead of waiting.

Requires Hermes `>=0.22.0`.

## Install

Hermes installs plugins from Git, so install from this public repository (pin a commit with `--ref <40-char sha>` if you want an exact version):

```bash
hermes plugins install https://github.com/krantikaridev/hermes-issue-orchestrator --enable
```

Once the catalog entry is merged this becomes `hermes plugins install hermes-issue-orchestrator`.

The install directory name should stay `hermes-issue-orchestrator`. Settings below use that id. `hermes plugins list` shows the id if a checkout used a different folder name.

## Configure the PAT (default)

**Recommended:** a fine-grained personal access token limited to **only the queue repository**, with **Issues: read and write** (Metadata: read is added automatically) and nothing else. Every comment, label and assignment is made as that token's user, so use a token (or a bot account) you are happy to have speak on those issues. A classic `public_repo` token also works but reaches every public repository you can write to.

Store it in Hermes. The shell `read` line keeps the paste out of the command you type:

```bash
read -rs PAT
hermes config set HERMES_ISSUE_ORCHESTRATOR_GITHUB_TOKEN "$PAT"
unset PAT
```

In Hermes Desktop, the same value is the plugin's **GitHub token** secret. Hermes stores it under `HERMES_ISSUE_ORCHESTRATOR_GITHUB_TOKEN`.

Point the queue at a repository you control. This setting is required; there is no default:

```bash
hermes config set plugins.entries.hermes-issue-orchestrator.settings.repo owner/example-repo
hermes config set plugins.entries.hermes-issue-orchestrator.settings.auth_method pat
```

`auth_method` already defaults to `pat`. The second command only makes that explicit.

Leave one open issue in that repository. The token's user needs permission to label, assign, and comment.

## Settings

| Key | Default | Meaning |
| --- | --- | --- |
| `repo` | _(required, no default)_ | `owner/name`, for example `owner/example-repo` |
| `auth_method` | `pat` | `pat` or `oauth` |
| `queue_label` | empty | Auto-claim only issues with this label |
| `queue_assignee` | empty | Login, `none` for unassigned, or empty for no filter |
| `in_progress_label` | `in-progress` | Applied on claim, removed on handoff |
| `progress_enabled` | `true` | Automatic progress comments after claimed turns |
| `progress_include_reply` | `false` | Put the first line of the model's reply in the automatic progress line |
| `progress_min_interval_seconds` | `90` | Minimum gap between those comments. `0` turns the time gate off |

Config lives at `plugins.entries.hermes-issue-orchestrator.settings.<key>`.

## Optional OAuth

Skip this for the demo. Use it only when you want Hermes to hold a GitHub OAuth grant instead of a PAT.

1. Create an OAuth app at [GitHub Developer Settings](https://github.com/settings/developers).
2. Set the authorization callback URL to `http://127.0.0.1/callback`. GitHub allows the loopback port Hermes picks. Leave wildcard matching off. The path must stay `/callback`.
3. Store the app's client id and client secret in Hermes:

```bash
hermes config set HERMES_ISSUE_ORCHESTRATOR_GITHUB_CLIENT_ID "<client id>"
read -rs SECRET
hermes config set HERMES_ISSUE_ORCHESTRATOR_GITHUB_CLIENT_SECRET "$SECRET"
unset SECRET
hermes config set plugins.entries.hermes-issue-orchestrator.settings.auth_method oauth
hermes auth add hermes-issue-orchestrator:github
```

Run `hermes auth add` from a terminal session, not from cron or the gateway. The grant can comment on **public** repositories only (`public_repo`). For a private repository, switch `auth_method` back to `pat`.

## Tools and `/issues`

`issue_claim` takes `issue_number`, or picks the oldest open issue that matches `label` / `assignee` (or the settings above). Auto-pick skips pull requests, issues with the in-progress label, and issues assigned to anyone other than the token's user. A claim by number is refused when the issue has the in-progress label or someone else's assignment, unless this Hermes already holds the claim. It creates the label if needed, **adds** the authenticated user as an assignee (other assignees stay), and posts one claim comment.

Claims are check-then-act: GitHub has no lock, so two Hermes instances that claim the same unassigned issue in the same second can both win. The label and assignee checks make that window small, not zero.

`issue_comment_progress` posts `note` on the session's claimed issue, or on `issue_number`.

`issue_handoff` requires `summary` and `action`:

- `close` — comment, close the issue as completed, clear the in-progress label
- `reassign` — comment, remove the token's own login, add `assignee`, clear the label
- `release` — comment, remove only the token's own login and the label, leave the issue open (other assignees stay)

All three are refused unless this Hermes holds a local claim on the issue or the issue is assigned to the token's user.

`/issues` lists local claims and the open queue. `/issues claimed` skips GitHub and shows local claim state. `/issues help` prints usage.

Automatic progress runs only when progress is enabled and this session claimed an issue (or, with no session id, when exactly one issue is claimed). By default the line does not include any chat text:

```text
Hermes: Turn finished on this issue. · tokens 10 in / 4 out
```

Set `progress_include_reply: true` to use the first line of the model's reply instead (scrubbed for tokens, at most 180 characters). Leave it off on public repositories.

Token counts are the provider usage reported on `post_api_request` for that session. They are posted at the end of the turn when the session has a claim. A repeated sentence is not posted again unless new tokens have arrived and the interval has passed.

## 90-second demo

PAT path only. Replace `owner/example-repo` with a repository you control and have at least one open issue there first.

```bash
# 0:00
hermes plugins install https://github.com/krantikaridev/hermes-issue-orchestrator --enable

# 0:20 — paste the PAT when read waits; it is not echoed
read -rs PAT
hermes config set HERMES_ISSUE_ORCHESTRATOR_GITHUB_TOKEN "$PAT"
unset PAT
hermes config set plugins.entries.hermes-issue-orchestrator.settings.repo owner/example-repo

# 0:45
hermes
```

In the session:

```text
/issues
```

Then ask:

```text
Claim the oldest open issue, add a one-line progress note, then release it
with a one-line summary. Do not close it.
```

By about 1:30 the issue should show a claim comment, your note, and a release comment, and `/issues` should no longer list it as claimed. Automatic progress, if a turn finished while the issue was claimed, is a separate short comment with token counts.

## Tests

Unit tests mock GitHub. They do not need a token or Hermes.

```bash
python -m pytest
```

Catalog admission, from a Hermes checkout:

```bash
hermes plugins validate /path/to/hermes-issue-orchestrator --install-deps
```

## Tested

End to end on Hermes v0.23.1 (10 Nov 2026) against a repository with example issues, using a free local model (Qwen2.5-3B-Instruct GGUF on llama.cpp, 64K context, `tools.tool_search.enabled: off`, toolset `hermes-issue-orchestrator`). One turn claimed an issue and posted a note; the `post_llm_call` hook then posted the turn's progress line with token counts; a resumed turn released the issue. Small local models work best with tool search off so the three tools are sent directly.

## Catalog draft

`docs/plugin-catalog/hermes-issue-orchestrator.yaml` is the entry to add under `plugin-catalog/` in `NousResearch/hermes-agent` when you submit. The `sha` field is a placeholder of zeros. Replace it with the full 40-character commit you want reviewed, then open that PR yourself. This repository does not open the upstream catalog PR.

## Done and not included

Done:

- `issue_claim`, `issue_comment_progress`, `issue_handoff`
- `post_llm_call` and `post_api_request` with rate limit, token totals, and `plugin_db` dedupe
- `/issues`
- PAT via Hermes secrets as the default, covered by the demo
- Optional GitHub OAuth through Hermes (`auth_method: oauth`), scope `public_repo`
- Tests against a fake GitHub API
- This README and the catalog draft

Not included:

- Opening the upstream catalog PR, or pinning `sha` to a reviewed commit.
- GitHub webhooks, Projects, multi-repo queues, or writing code for the issue. The agent does the work with Hermes's own tools; this plugin is the queue.
