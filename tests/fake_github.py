"""In-memory GitHub issue tracker for tool and hook tests."""

from __future__ import annotations

import urllib.parse
from typing import Any


class FakeGitHub:
    def __init__(self) -> None:
        self.user = {"login": "octocat"}
        self.issues: dict[int, dict[str, Any]] = {}
        self.order: list[int] = []
        self.comments: list[dict[str, Any]] = []
        self.defined_labels: set[str] = set()
        self.calls: list[tuple[str, str, Any]] = []
        self.fail_assign = False
        self.fail_labels = 0
        self.fail_comments = 0

    def add_issue(
        self,
        number: int,
        title: str,
        *,
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
        pull_request: bool = False,
        state: str = "open",
    ) -> None:
        self.order.append(number)
        issue: dict[str, Any] = {
            "number": number,
            "title": title,
            "state": state,
            "labels": [{"name": name} for name in (labels or [])],
            "assignees": [{"login": login} for login in (assignees or [])],
            "html_url": f"https://github.com/acme/demo/issues/{number}",
        }
        if pull_request:
            issue["pull_request"] = {"url": f"https://api.github.com/repos/acme/demo/pulls/{number}"}
        self.issues[number] = issue

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        self.calls.append((method, path, body))
        parsed = urllib.parse.urlsplit(path)
        parts = [urllib.parse.unquote(part) for part in parsed.path.strip("/").split("/") if part]
        query = urllib.parse.parse_qs(parsed.query)
        if parts == ["user"] and method == "GET":
            return 200, self.user
        if len(parts) < 4 or parts[0] != "repos":
            return 404, {"message": "not found"}
        return self._repo(method, parts[3:], query, body)

    def _repo(
        self,
        method: str,
        rest: list[str],
        query: dict[str, list[str]],
        body: dict[str, Any] | None,
    ) -> tuple[int, Any]:
        if rest == ["labels"] and method == "POST":
            name = str((body or {}).get("name") or "")
            if name in self.defined_labels:
                return 422, {"message": "already_exists"}
            self.defined_labels.add(name)
            return 201, {"name": name}
        if rest == ["issues"] and method == "GET":
            matched = [self.issues[number] for number in self.order if self._matches(self.issues[number], query)]
            return 200, matched
        if len(rest) >= 2 and rest[0] == "issues" and rest[1].isdigit():
            number = int(rest[1])
            issue = self.issues.get(number)
            if issue is None:
                return 404, {"message": "Not Found"}
            tail = rest[2:]
            if not tail and method == "GET":
                return 200, issue
            if not tail and method == "PATCH":
                return self._patch(issue, body or {})
            if tail == ["labels"] and method == "POST":
                if self.fail_labels:
                    self.fail_labels -= 1
                    return 500, {"message": "label write failed"}
                for name in (body or {}).get("labels") or []:
                    if name not in [label["name"] for label in issue["labels"]]:
                        issue["labels"].append({"name": name})
                    self.defined_labels.add(name)
                return 200, issue["labels"]
            if len(tail) == 2 and tail[0] == "labels" and method == "DELETE":
                issue["labels"] = [label for label in issue["labels"] if label["name"] != tail[1]]
                return 204, None
            if tail == ["comments"] and method == "POST":
                if self.fail_comments:
                    self.fail_comments -= 1
                    return 500, {"message": "comment failed"}
                text = str((body or {}).get("body") or "")
                self.comments.append({"number": number, "body": text})
                return 201, {"id": len(self.comments), "body": text}
        return 404, {"message": "not found"}

    def _patch(self, issue: dict[str, Any], body: dict[str, Any]) -> tuple[int, Any]:
        if self.fail_assign and "assignees" in body:
            return 422, {"message": "cannot assign"}
        if "state" in body:
            issue["state"] = body["state"]
        if "assignees" in body:
            issue["assignees"] = [{"login": login} for login in body["assignees"]]
        return 200, issue

    def _matches(self, issue: dict[str, Any], query: dict[str, list[str]]) -> bool:
        if query.get("labels"):
            needed = [name for name in query["labels"][0].split(",") if name]
            names = [label["name"] for label in issue["labels"]]
            if any(name not in names for name in needed):
                return False
        if query.get("assignee"):
            who = query["assignee"][0]
            logins = [person["login"] for person in issue["assignees"]]
            if who == "none" and logins:
                return False
            if who not in {"*", ""} and who not in logins:
                return False
        return True
