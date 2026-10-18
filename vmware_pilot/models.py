"""Workflow data models — state machine, steps, and persistence."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from vmware_pilot.renames import current_tool_name

_DEFAULT_DB = Path("~/.vmware/workflows.db").expanduser()


class WorkflowState(str, Enum):
    DRAFT = "draft"
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    ROLLING_BACK = "rolling_back"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"  # terminal: approval rejected or explicit cancel — never runs


@dataclass
class WorkflowStep:
    index: int
    action: str
    skill: str
    tool: str
    params: dict[str, Any]
    status: str = "pending"  # pending | running | success | failed | skipped | rolled_back
    result: Any = None
    started_at: str = ""
    completed_at: str = ""
    rollback_tool: str = ""
    rollback_params: dict[str, Any] = field(default_factory=dict)
    group_id: str = (
        ""  # non-empty = parallel-group sibling; agent may dispatch concurrently with peers
    )

    def __post_init__(self) -> None:
        # The one choke point for pre-2.0 tool names (HLD §16 D-15, [M4]): every
        # step — template, YAML, draft, create_workflow, a stored row — is built
        # here, so review, the approval gate, dispatch and rollback see the new name.
        self.tool = current_tool_name(self.skill, self.tool)
        self.rollback_tool = current_tool_name(self.skill, self.rollback_tool)


def _creator() -> str:
    """Who is creating a workflow: the network caller (token:<name>) or the OS user.

    HLD §18 D-23: recorded so an ``approve: others`` role can refuse approving a
    workflow you created. A workflow loaded from storage keeps what was stored.
    """
    try:
        from vmware_policy.caller import audit_user

        return audit_user()
    except Exception:  # noqa: BLE001 — never block creating a workflow
        return ""


@dataclass
class Workflow:
    id: str
    workflow_type: str
    state: WorkflowState
    steps: list[WorkflowStep]
    params: dict[str, Any]
    created_at: str
    updated_at: str
    approved_by: str = ""
    diff_report: dict[str, Any] = field(default_factory=dict)
    audit_log: list[dict[str, Any]] = field(default_factory=list)
    blocked_reason: str = ""
    rollback_results: list[dict[str, Any]] = field(default_factory=list)
    created_by: str = field(default_factory=_creator)
    #: Everyone else who changed or confirmed it while it was a draft. For
    #: ``approve: others`` they count as creators: editing someone's draft and then
    #: approving it would otherwise pass as separation of duties (review M3).
    authors: list[str] = field(default_factory=list)

    def with_author(self, who: str) -> None:
        """Record ``who`` among the authors (no-op when empty or already there)."""
        if who and who != self.created_by and who not in self.authors:
            self.authors = [*self.authors, who]

    def log(self, action: str, detail: str = "") -> None:
        self.audit_log.append(
            {
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "action": action,
                "detail": detail,
            }
        )
        self.updated_at = datetime.now(tz=timezone.utc).isoformat()

    def current_step(self) -> WorkflowStep | None:
        for step in self.steps:
            if step.status in ("pending", "running"):
                return step
        return None

    def completed_steps(self) -> list[WorkflowStep]:
        return [s for s in self.steps if s.status == "success"]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d


def new_workflow_id() -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    short = uuid.uuid4().hex[:6]
    return f"wf-{ts}-{short}"


# ── SQLite Persistence ────────────────────────────────────────────────

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS workflows (
    id         TEXT PRIMARY KEY,
    type       TEXT NOT NULL,
    state      TEXT NOT NULL,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


# Parameter keys whose values must never be written to the state DB.
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "token",
        "secret",
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "private_key",
        "credential",
        "credentials",
        # A Supervisor/TKC kubeconfig carries a bearer token; under --dispatch it
        # comes back in a step result and must not reach workflows.db in clear.
        "kubeconfig",
    }
)


# Placeholder written to the DB in place of a redacted secret value.
REDACTED_PLACEHOLDER = "***"


def is_sensitive_key(key: Any) -> bool:
    """True if ``key`` names a value that must never be persisted in clear.

    Shared by the persistence redaction and the executor's dispatch guard so
    both use the exact same notion of "sensitive" (case-insensitive match
    against ``_SENSITIVE_KEYS``)."""
    return isinstance(key, str) and key.lower() in _SENSITIVE_KEYS


def _redact_for_persistence(obj: Any) -> Any:
    """Deep-copy ``obj`` with sensitive dict keys masked to '***'.

    Recurses through dicts and lists so secrets nested in step params are
    caught too. Non-container values are returned unchanged.
    """
    if isinstance(obj, dict):
        return {
            k: (REDACTED_PLACEHOLDER if is_sensitive_key(k) else _redact_for_persistence(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_for_persistence(item) for item in obj]
    return obj


class WorkflowStore:
    """Persist workflows to SQLite (separate from audit.db).

    A per-process in-memory cache keeps the LIVE ``Workflow`` objects so that
    secrets in params survive a save→load round-trip within the same process
    (the DB copy is redacted to '***'). Secrets do NOT survive a process
    restart: after a crash, ``load()`` falls back to the redacted DB row and
    secrets must be re-sourced from env / a secret store.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._path = Path(db_path).expanduser() if db_path else _DEFAULT_DB
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._cache: dict[str, Workflow] = {}
        with closing(self._connect()) as conn:
            conn.execute(_CREATE_TABLE)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.commit()
        self._harden_permissions()

    def _harden_permissions(self) -> None:
        """Restrict the state dir to 0700 and DB files (incl. WAL/SHM) to 0600.

        Workflow params are persisted here and can contain sensitive values, so
        keep them owner-only. Best-effort: never raises."""
        try:
            os.chmod(self._path.parent, 0o700)
        except OSError:
            pass
        for suffix in ("", "-wal", "-shm"):
            candidate = self._path.with_name(self._path.name + suffix)
            try:
                if candidate.exists():
                    os.chmod(candidate, 0o600)
            except OSError:
                pass

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._path), timeout=5)

    def save(self, wf: Workflow) -> None:
        # Redact secrets BEFORE persisting: workflow/step params may carry
        # passwords/tokens that a caller passed in. They stay in the in-memory
        # Workflow object (the per-process cache below — downstream steps and
        # run/approve in the same process see the real values); the DB never
        # stores them. After a crash, secrets are re-sourced from env/secret
        # store, not recovered from disk.
        data = json.dumps(_redact_for_persistence(wf.to_dict()), default=str)
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO workflows (id, type, state, data, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (wf.id, wf.workflow_type, wf.state.value, data, wf.created_at, wf.updated_at),
            )
            conn.commit()
        # Cache the live object so a load() in this process returns the
        # unredacted workflow (DB load happens only on cache miss).
        self._cache[wf.id] = wf

    def load(self, workflow_id: str) -> Workflow | None:
        cached = self._cache.get(workflow_id)
        if cached is not None:
            return cached
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT data FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
        if not row:
            return None
        return _from_dict(json.loads(row[0]))

    def list_active(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, type, state, created_at, updated_at FROM workflows "
                "WHERE state NOT IN ('completed', 'failed', 'cancelled') ORDER BY created_at DESC"
            ).fetchall()
        return [
            {"id": r[0], "type": r[1], "state": r[2], "created_at": r[3], "updated_at": r[4]}
            for r in rows
        ]

    def list_all(self, limit: int = 20) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, type, state, created_at, updated_at FROM workflows "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"id": r[0], "type": r[1], "state": r[2], "created_at": r[3], "updated_at": r[4]}
            for r in rows
        ]

    def delete(self, workflow_id: str) -> None:
        self._cache.pop(workflow_id, None)
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
            conn.commit()


def _from_dict(d: dict[str, Any]) -> Workflow:
    # Backward compat: older workflows persisted before group_id was added
    steps = []
    for s in d.get("steps", []):
        s.setdefault("group_id", "")
        steps.append(WorkflowStep(**s))
    return Workflow(
        id=d["id"],
        workflow_type=d["workflow_type"],
        state=WorkflowState(d["state"]),
        steps=steps,
        params=d.get("params", {}),
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        approved_by=d.get("approved_by", ""),
        diff_report=d.get("diff_report", {}),
        audit_log=d.get("audit_log", []),
        blocked_reason=d.get("blocked_reason", ""),
        rollback_results=d.get("rollback_results", []),
        # "" for workflows saved before creators were recorded (HLD §18 D-23).
        created_by=d.get("created_by", ""),
        authors=list(d.get("authors", [])),
    )
