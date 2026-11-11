"""Fixed names. Secrets stay in Hermes; nothing here is a credential value."""

from __future__ import annotations

PLUGIN_NAME = "hermes-issue-orchestrator"
REPO_SETTING = f"plugins.entries.{PLUGIN_NAME}.settings.repo"
REPO_REQUIRED = f"set {REPO_SETTING}"
TOKEN_ENV = "HERMES_ISSUE_ORCHESTRATOR_GITHUB_TOKEN"
CLIENT_ID_ENV = "HERMES_ISSUE_ORCHESTRATOR_GITHUB_CLIENT_ID"
CLIENT_SECRET_ENV = "HERMES_ISSUE_ORCHESTRATOR_GITHUB_CLIENT_SECRET"
OAUTH_ID = "github"
OAUTH_HINT = f"hermes auth add {PLUGIN_NAME}:{OAUTH_ID}"
PAT_HINT = f"hermes config set {TOKEN_ENV}"
API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "hermes-issue-orchestrator"
IN_PROGRESS_LABEL = "in-progress"
LABEL_COLOR = "fbca04"
REQUEST_TIMEOUT_SECONDS = 20.0
PROGRESS_CHAR_LIMIT = 180
COMMENT_CHAR_LIMIT = 4000
RATE_LIMIT_RETRIES = 2
RATE_LIMIT_MAX_SLEEP_SECONDS = 8.0
MARKER_PREFIX = "<!-- hermes-issue-orchestrator:"
