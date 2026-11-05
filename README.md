# hermes-issue-orchestrator

Hermes Agent plugin that treats GitHub issues as the agent's task queue.

Hermes can claim an open issue, mark it in progress, post progress (including a short automatic note with token use), and hand the issue off by closing it, reassigning it, or putting it back on the queue.

**Credential decision:** the supported day-to-day path is a GitHub personal access token stored in Hermes secrets. That is what the demo below uses. GitHub OAuth is optional and off unless you set `auth_method` to `oauth`. Hermes holds that grant; this plugin never reads the OAuth token.

**OPEN DECISION:** the default repository is the placeholder `krantikaridev/hermes-orchestrator-sandbox`. It is not a promise that the sandbox exists or that you should use it. Set `repo` to the public repository you want Hermes to work before the demo.

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

- The plugin calls `https://api.github.com` only: list and update issues, add and remove one label, post comments, and read the authenticated user (`GET /user`).
- The default credential is `HERMES_ISSUE_ORCHESTRATOR_GITHUB_TOKEN`, read with `agent.secret_scope.get_secret`. The plugin does not read `.env` files, `auth.json`, or another tool's token file.
- Optional OAuth uses `credentialed_provider_request("github", ...)`. Hermes attaches and refreshes the token. Plugin code does not receive it. The declared scope is `public_repo` (public repositories only). Private repositories need a PAT.
- `post_llm_call` posts to the claimed issue without a separate approval prompt for each comment. Posts are skipped when nothing is claimed, when two claims exist and the session did not claim one, when the line repeats, and when the last post was inside `progress_min_interval_seconds` (default 90). Explicit tool calls still go through Hermes's normal tool path.
- No shell commands, no background process, no telemetry, no self-update, and no change to Hermes core.
- OAuth sign-in is `hermes auth add hermes-issue-orchestrator:github` in a terminal. Hermes does not start that browser flow from cron or the gateway. If the grant is missing, the tool returns an error instead of waiting.

Requires Hermes `>=0.22.0`.

## Install

From a clone of this repository:

```bash
hermes plugins install /path/to/hermes-issue-orchestrator
hermes plugins enable hermes-issue-orchestrator
```

Or from GitHub, once this repo is public:

```bash
hermes plugins install https://github.com/krantikaridev/hermes-issue-orchestrator
hermes plugins enable hermes-issue-orchestrator
```

The install directory name should stay `hermes-issue-orchestrator`. Settings below use that id. `hermes plugins list` shows the id if a checkout used a different folder name.

## Configure the PAT (default)

Create a GitHub personal access token that can read and write issues on the target repository (classic `public_repo`, or a fine-grained token with Issues read/write on that repo).

Store it in Hermes. The shell `read` line keeps the paste out of the command you type:

```bash
read -rs PAT
hermes config set HERMES_ISSUE_ORCHESTRATOR_GITHUB_TOKEN "$PAT"
unset PAT
```

In Hermes Desktop, the same value is the plugin's **GitHub token** secret. Hermes stores it under `HERMES_ISSUE_ORCHESTRATOR_GITHUB_TOKEN`.

Point the queue at a repository you control. The default below is the OPEN DECISION placeholder:

```bash
hermes config set plugins.entries.hermes-issue-orchestrator.settings.repo krantikaridev/hermes-orchestrator-sandbox
hermes config set plugins.entries.hermes-issue-orchestrator.settings.auth_method pat
```

`auth_method` already defaults to `pat`. The second command only makes that explicit.

Leave one open issue in that repository. The token's user needs permission to label, assign, and comment.

## Settings

| Key | Default | Meaning |
| --- | --- | --- |
| `repo` | `krantikaridev/hermes-orchestrator-sandbox` | `owner/name`. OPEN DECISION placeholder |
| `auth_method` | `pat` | `pat` or `oauth` |
| `queue_label` | empty | Auto-claim only issues with this label |
| `queue_assignee` | empty | Login, `none` for unassigned, or empty for no filter |
| `in_progress_label` | `in-progress` | Applied on claim, removed on handoff |
| `progress_enabled` | `true` | Automatic progress comments after claimed turns |
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

`issue_claim` takes `issue_number`, or picks the oldest open issue that matches `label` / `assignee` (or the settings above). It skips pull requests and issues that already have the in-progress label unless you pass a number. It creates the label if needed, assigns the authenticated user, and posts one claim comment.

`issue_comment_progress` posts `note` on the session's claimed issue, or on `issue_number`.

`issue_handoff` requires `summary` and `action`:

- `close` — comment, close the issue as completed, clear the in-progress label
- `reassign` — comment, set `assignee` to that GitHub login, clear the label
- `release` — comment, clear assignees and the label, leave the issue open

`/issues` lists local claims and the open queue. `/issues claimed` skips GitHub and shows local claim state. `/issues help` prints usage.

Automatic progress runs only when progress is enabled and this session claimed an issue (or, with no session id, when exactly one issue is claimed). The line looks like:

```text
Hermes: Narrowed it to the parser. · tokens 10 in / 4 out
```

Token counts are the provider usage reported on `post_api_request` for that session. They are posted at the end of the turn when the session has a claim. A repeated sentence is not posted again unless new tokens have arrived and the interval has passed.

## 90-second demo

PAT path only. Replace the repository if you are not using the sandbox placeholder. Have one open issue there first.

```bash
# 0:00
hermes plugins install /path/to/hermes-issue-orchestrator
hermes plugins enable hermes-issue-orchestrator

# 0:20 — paste the PAT when read waits; it is not echoed
read -rs PAT
hermes config set HERMES_ISSUE_ORCHESTRATOR_GITHUB_TOKEN "$PAT"
unset PAT
hermes config set plugins.entries.hermes-issue-orchestrator.settings.repo krantikaridev/hermes-orchestrator-sandbox

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

- Creating the sandbox repository. That default is an OPEN DECISION.
- Opening the upstream catalog PR, or pinning `sha` to a reviewed commit.
- GitHub webhooks, Projects, multi-repo queues, or writing code for the issue. The agent does the work with Hermes's own tools; this plugin is the queue.
