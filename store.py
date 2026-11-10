"""SQLite dedupe, claims, and token totals in the Hermes plugin database."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from .host import open_plugin_db

_lock = threading.Lock()
_factory: Callable[[], sqlite3.Connection] | None = None


def set_connection_factory(factory: Callable[[], sqlite3.Connection] | None) -> None:
    """Tests pass an in-memory connection. Hermes uses plugin_db when this is unset."""
    global _factory
    _factory = factory


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    with _lock:
        owned = False
        if _factory is not None:
            conn = _factory()
        else:
            conn = open_plugin_db()
            owned = True
        conn.row_factory = sqlite3.Row
        try:
            _migrate(conn)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if owned:
                conn.close()


def get_claim(repo: str, number: int) -> dict[str, Any] | None:
    with connection() as conn:
        return _row_dict(_one(conn, "SELECT * FROM claims WHERE repo=? AND issue_number=?", (repo, number)))


def mark_claimed(repo: str, number: int, session_id: str, title: str) -> dict[str, Any]:
    """Record a claim. ``created`` is false when that issue is already claimed."""
    stamp = _iso()
    with connection() as conn:
        current = _one(conn, "SELECT * FROM claims WHERE repo=? AND issue_number=?", (repo, number))
        if current is not None and current["state"] == "claimed":
            if session_id:
                _bind_session(conn, session_id, repo, number)
            return {"created": False, "generation": int(current["generation"]), "previous": None}
        previous = _row_dict(current)
        generation = int(current["generation"]) + 1 if current is not None else 1
        _write_claim(conn, repo, number, "claimed", session_id, generation, title, stamp)
        if session_id:
            _bind_session(conn, session_id, repo, number)
        return {"created": True, "generation": generation, "previous": previous}


def restore_claim(previous: dict[str, Any] | None, repo: str, number: int) -> None:
    with connection() as conn:
        if previous is None:
            conn.execute("DELETE FROM claims WHERE repo=? AND issue_number=?", (repo, number))
            conn.execute("DELETE FROM session_claims WHERE repo=? AND issue_number=?", (repo, number))
            return
        _write_claim(
            conn,
            repo,
            number,
            str(previous["state"]),
            str(previous["session_id"]),
            int(previous["generation"]),
            str(previous["title"]),
            str(previous["updated_at"]),
        )


def set_state(repo: str, number: int, state: str, title: str = "") -> None:
    stamp = _iso()
    with connection() as conn:
        current = _one(conn, "SELECT * FROM claims WHERE repo=? AND issue_number=?", (repo, number))
        if current is None:
            _write_claim(conn, repo, number, state, "", 1, title, stamp)
        else:
            kept = title or str(current["title"])
            conn.execute(
                "UPDATE claims SET state=?, title=?, updated_at=? WHERE repo=? AND issue_number=?",
                (state, kept, stamp, repo, number),
            )
        if state != "claimed":
            conn.execute("DELETE FROM session_claims WHERE repo=? AND issue_number=?", (repo, number))


def session_claim(session_id: str) -> dict[str, Any] | None:
    if not session_id:
        return None
    with connection() as conn:
        bound = _one(conn, "SELECT repo, issue_number FROM session_claims WHERE session_id=?", (session_id,))
        if bound is None:
            return None
        return _row_dict(
            _one(
                conn,
                "SELECT * FROM claims WHERE repo=? AND issue_number=?",
                (bound["repo"], int(bound["issue_number"])),
            )
        )


def claimed_for_repo(repo: str) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM claims WHERE repo=? AND state='claimed' ORDER BY updated_at ASC",
            (repo,),
        ).fetchall()
        return [_row_dict(row) or {} for row in rows]


def list_claims(repo: str, limit: int = 20) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM claims WHERE repo=? ORDER BY updated_at DESC LIMIT ?",
            (repo, limit),
        ).fetchall()
        return [_row_dict(row) or {} for row in rows]


def try_insert_event(key: str, kind: str) -> bool:
    with connection() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO events (dedupe_key, kind, created_at) VALUES (?, ?, ?)",
            (key, kind, _iso()),
        )
        return cursor.rowcount == 1


def delete_event(key: str) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM events WHERE dedupe_key=?", (key,))


_PENDING = "pending:"


def mark_event_pending(key: str) -> None:
    """The POST may or may not have reached GitHub. Keep the key so a retry checks first."""
    with connection() as conn:
        conn.execute(
            "UPDATE events SET kind=? || kind WHERE dedupe_key=? AND kind NOT LIKE ?",
            (_PENDING, key, _PENDING + "%"),
        )


def event_pending(key: str) -> bool:
    with connection() as conn:
        row = _one(conn, "SELECT kind FROM events WHERE dedupe_key=?", (key,))
        return row is not None and str(row["kind"]).startswith(_PENDING)


def confirm_event(key: str) -> None:
    with connection() as conn:
        conn.execute(
            "UPDATE events SET kind=substr(kind, ?) WHERE dedupe_key=? AND kind LIKE ?",
            (len(_PENDING) + 1, key, _PENDING + "%"),
        )


def add_tokens(repo: str, number: int, prompt: int, completion: int, api_request_id: str = "") -> bool:
    if prompt == 0 and completion == 0:
        return False
    with connection() as conn:
        if not _remember(conn, api_request_id):
            return False
        _add_progress(conn, repo, number, prompt, completion)
        return True


def add_session_tokens(session_id: str, prompt: int, completion: int, api_request_id: str = "") -> bool:
    if not session_id or (prompt == 0 and completion == 0):
        return False
    with connection() as conn:
        if not _remember(conn, api_request_id):
            return False
        row = _one(conn, "SELECT pending_prompt, pending_completion FROM session_usage WHERE session_id=?", (session_id,))
        if row is None:
            conn.execute(
                "INSERT INTO session_usage (session_id, pending_prompt, pending_completion) VALUES (?, ?, ?)",
                (session_id, prompt, completion),
            )
        else:
            conn.execute(
                "UPDATE session_usage SET pending_prompt=pending_prompt+?, pending_completion=pending_completion+? "
                "WHERE session_id=?",
                (prompt, completion, session_id),
            )
        return True


def take_session_tokens(session_id: str) -> tuple[int, int]:
    if not session_id:
        return 0, 0
    with connection() as conn:
        row = _one(
            conn,
            "SELECT pending_prompt, pending_completion FROM session_usage WHERE session_id=?",
            (session_id,),
        )
        if row is None:
            return 0, 0
        conn.execute("DELETE FROM session_usage WHERE session_id=?", (session_id,))
        return int(row["pending_prompt"]), int(row["pending_completion"])


def reserve_progress(
    repo: str,
    number: int,
    sentence_hash: str,
    now: float,
    interval: int,
) -> dict[str, Any] | None:
    """Claim the right to post one progress comment, and return the token totals to include."""
    with connection() as conn:
        row = _one(
            conn,
            "SELECT last_posted_at, last_hash, pending_prompt, pending_completion FROM progress "
            "WHERE repo=? AND issue_number=?",
            (repo, number),
        )
        last_at = float(row["last_posted_at"]) if row is not None else 0.0
        last_hash = str(row["last_hash"]) if row is not None else ""
        prompt = int(row["pending_prompt"]) if row is not None else 0
        completion = int(row["pending_completion"]) if row is not None else 0
        if not _should_post(last_at, last_hash, sentence_hash, prompt, completion, now, interval):
            return None
        if row is None:
            conn.execute(
                "INSERT INTO progress (repo, issue_number, last_posted_at, last_hash, pending_prompt, pending_completion) "
                "VALUES (?, ?, ?, ?, 0, 0)",
                (repo, number, now, sentence_hash),
            )
        else:
            conn.execute(
                "UPDATE progress SET last_posted_at=?, last_hash=?, pending_prompt=0, pending_completion=0 "
                "WHERE repo=? AND issue_number=?",
                (now, sentence_hash, repo, number),
            )
        return {
            "prompt": prompt,
            "completion": completion,
            "previous_posted_at": last_at,
            "previous_hash": last_hash,
        }


def restore_progress(
    repo: str,
    number: int,
    *,
    posted_at: float,
    sentence_hash: str,
    prompt: int,
    completion: int,
) -> None:
    with connection() as conn:
        conn.execute(
            "UPDATE progress SET last_posted_at=?, last_hash=?, "
            "pending_prompt=pending_prompt+?, pending_completion=pending_completion+? "
            "WHERE repo=? AND issue_number=?",
            (posted_at, sentence_hash, prompt, completion, repo, number),
        )


def _should_post(
    last_at: float,
    last_hash: str,
    sentence_hash: str,
    prompt: int,
    completion: int,
    now: float,
    interval: int,
) -> bool:
    pending = prompt + completion
    within = bool(last_at) and (now - last_at) < interval
    same = bool(last_hash) and sentence_hash == last_hash
    if same and (within or pending == 0):
        return False
    if not same and within:
        return False
    return True


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS claims (
            repo TEXT NOT NULL,
            issue_number INTEGER NOT NULL,
            state TEXT NOT NULL,
            session_id TEXT NOT NULL DEFAULT '',
            generation INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (repo, issue_number)
        );
        CREATE TABLE IF NOT EXISTS session_claims (
            session_id TEXT PRIMARY KEY,
            repo TEXT NOT NULL,
            issue_number INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            dedupe_key TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS progress (
            repo TEXT NOT NULL,
            issue_number INTEGER NOT NULL,
            last_posted_at REAL NOT NULL DEFAULT 0,
            last_hash TEXT NOT NULL DEFAULT '',
            pending_prompt INTEGER NOT NULL DEFAULT 0,
            pending_completion INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (repo, issue_number)
        );
        CREATE TABLE IF NOT EXISTS session_usage (
            session_id TEXT PRIMARY KEY,
            pending_prompt INTEGER NOT NULL DEFAULT 0,
            pending_completion INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS token_seen (
            api_request_id TEXT PRIMARY KEY
        );
        """
    )


def _remember(conn: sqlite3.Connection, api_request_id: str) -> bool:
    if not api_request_id:
        return True
    cursor = conn.execute("INSERT OR IGNORE INTO token_seen (api_request_id) VALUES (?)", (api_request_id,))
    return cursor.rowcount == 1


def _add_progress(conn: sqlite3.Connection, repo: str, number: int, prompt: int, completion: int) -> None:
    row = _one(
        conn,
        "SELECT pending_prompt FROM progress WHERE repo=? AND issue_number=?",
        (repo, number),
    )
    if row is None:
        conn.execute(
            "INSERT INTO progress (repo, issue_number, last_posted_at, last_hash, pending_prompt, pending_completion) "
            "VALUES (?, ?, 0, '', ?, ?)",
            (repo, number, prompt, completion),
        )
        return
    conn.execute(
        "UPDATE progress SET pending_prompt=pending_prompt+?, pending_completion=pending_completion+? "
        "WHERE repo=? AND issue_number=?",
        (prompt, completion, repo, number),
    )


def _write_claim(
    conn: sqlite3.Connection,
    repo: str,
    number: int,
    state: str,
    session_id: str,
    generation: int,
    title: str,
    stamp: str,
) -> None:
    conn.execute(
        """
        INSERT INTO claims (repo, issue_number, state, session_id, generation, title, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo, issue_number) DO UPDATE SET
            state=excluded.state,
            session_id=excluded.session_id,
            generation=excluded.generation,
            title=excluded.title,
            updated_at=excluded.updated_at
        """,
        (repo, number, state, session_id, generation, title, stamp),
    )


def _bind_session(conn: sqlite3.Connection, session_id: str, repo: str, number: int) -> None:
    conn.execute(
        """
        INSERT INTO session_claims (session_id, repo, issue_number) VALUES (?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET repo=excluded.repo, issue_number=excluded.issue_number
        """,
        (session_id, repo, number),
    )


def _one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
