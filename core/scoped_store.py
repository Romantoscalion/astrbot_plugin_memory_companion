"""REQ-041 revisioned namespace-scoped record store.

This is the new-path storage primitive.  It never guesses a namespace and it
does not fall back to legacy tables.  Callers must pass a validated context and
an assurance-authorized purpose on every read and write.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Iterator

from .namespace import AssurancePolicy, NamespaceContext


MAX_RECORD_BYTES = 262144
RECORD_KINDS = frozenset({"profile_fact", "memory", "rule", "evidence", "summary"})
_PURPOSE_BY_KIND = {
    "profile_fact": ("profile_read", "profile_write"),
    "memory": ("memory_read", "memory_write"),
    "summary": ("memory_read", "memory_write"),
    "rule": ("rule_read", "rule_write"),
    "evidence": ("rule_read", "rule_write"),
}


class ScopedStoreError(RuntimeError):
    pass


class ScopedRecordConflict(ScopedStoreError):
    pass


class ScopedRevisionGap(ScopedStoreError):
    pass


def _token(value: Any, limit: int = 128) -> str:
    if not isinstance(value, str):
        return ""
    result = value.strip()
    if not result or len(result) > limit or any(ord(ch) < 32 for ch in result):
        return ""
    return result


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _payload(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise ScopedStoreError("scoped_payload_invalid")
    try:
        encoded = _canonical(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ScopedStoreError("scoped_payload_invalid") from exc
    if len(encoded.encode("utf-8")) > MAX_RECORD_BYTES:
        raise ScopedStoreError("scoped_payload_too_large")
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ScopedStore:
    def __init__(self, path: str | Path, *, clock: Any = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock if callable(clock) else time.time
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=15.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                if conn.in_transaction:
                    conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    def _initialize(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS scoped_records (
                    namespace_scope TEXT NOT NULL,
                    record_kind TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    migration_epoch TEXT NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(namespace_scope, record_kind, record_id)
                );
                CREATE INDEX IF NOT EXISTS idx_scoped_records_list
                    ON scoped_records(namespace_scope, record_kind, deleted, revision, record_id);
                CREATE TABLE IF NOT EXISTS scoped_operations (
                    event_id TEXT NOT NULL,
                    migration_epoch TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    result_code TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(event_id, migration_epoch)
                );
                """
            )

    @staticmethod
    def _authorize(context: NamespaceContext | None, record_kind: str, *, write: bool) -> None:
        if record_kind not in RECORD_KINDS:
            raise ScopedStoreError("scoped_record_kind_invalid")
        purpose = _PURPOSE_BY_KIND[record_kind][1 if write else 0]
        decision = AssurancePolicy.authorize(context, purpose)
        if not decision.allowed:
            raise ScopedStoreError(decision.code)

    @staticmethod
    def _scope(context: NamespaceContext) -> str:
        # Durable ownership is stable across policy revisions and migration
        # epochs.  Assurance/status/policy still gate each operation, but must
        # not silently create a second physical namespace for the same owner.
        # This local key is never emitted to logs; cache_scope() remains the
        # redacted, revision-aware cache key.
        return _canonical({
            "kind": context.kind,
            "identity_id": context.identity_id,
            "group_id": context.group_id,
        })

    def upsert(
        self,
        context: NamespaceContext,
        *,
        record_kind: str,
        record_id: str,
        revision: int,
        payload: dict[str, Any],
        event_id: str,
    ) -> str:
        self._authorize(context, record_kind, write=True)
        identifier = _token(record_id)
        event = _token(event_id)
        if not identifier or not event or revision < 1:
            raise ScopedStoreError("scoped_envelope_invalid")
        encoded, digest = _payload(payload)
        scope = self._scope(context)
        request_hash = hashlib.sha256(
            _canonical({
                "scope": scope, "kind": record_kind, "id": identifier, "revision": revision,
                "payload_hash": digest, "policy_version": context.policy_version,
            }).encode("utf-8")
        ).hexdigest()
        now = float(self._clock())
        with self._transaction() as conn:
            prior_operation = conn.execute(
                "SELECT request_hash,result_code FROM scoped_operations WHERE event_id=? AND migration_epoch=?",
                (event, context.migration_epoch),
            ).fetchone()
            if prior_operation is not None:
                if prior_operation["request_hash"] != request_hash:
                    raise ScopedRecordConflict("scoped_event_conflict")
                return "duplicate"
            row = conn.execute(
                "SELECT revision,payload_hash,deleted FROM scoped_records WHERE namespace_scope=? AND record_kind=? AND record_id=?",
                (scope, record_kind, identifier),
            ).fetchone()
            if row is None:
                if revision != 1:
                    raise ScopedRevisionGap(f"scoped_revision_gap:0:{revision}")
                result = "created"
            else:
                current = int(row["revision"])
                if int(row["deleted"]) == 1:
                    raise ScopedRecordConflict("scoped_record_tombstoned")
                if revision == current and row["payload_hash"] == digest:
                    result = "duplicate"
                elif revision != current + 1:
                    raise ScopedRevisionGap(f"scoped_revision_gap:{current}:{revision}")
                else:
                    result = "updated"
            if result != "duplicate":
                conn.execute(
                    """INSERT INTO scoped_records(
                        namespace_scope,record_kind,record_id,revision,payload_json,payload_hash,
                        policy_version,migration_epoch,deleted,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,0,?)
                    ON CONFLICT(namespace_scope,record_kind,record_id) DO UPDATE SET
                        revision=excluded.revision,payload_json=excluded.payload_json,payload_hash=excluded.payload_hash,
                        policy_version=excluded.policy_version,migration_epoch=excluded.migration_epoch,
                        deleted=0,updated_at=excluded.updated_at""",
                    (
                        scope, record_kind, identifier, revision, encoded, digest,
                        context.policy_version, context.migration_epoch, now,
                    ),
                )
            conn.execute(
                "INSERT INTO scoped_operations(event_id,migration_epoch,request_hash,result_code,created_at) VALUES(?,?,?,?,?)",
                (event, context.migration_epoch, request_hash, result, now),
            )
        return result

    def read(self, context: NamespaceContext, *, record_kind: str, record_id: str) -> dict[str, Any] | None:
        self._authorize(context, record_kind, write=False)
        identifier = _token(record_id)
        if not identifier:
            raise ScopedStoreError("scoped_record_id_invalid")
        with self._connection() as conn:
            row = conn.execute(
                """SELECT revision,payload_json,payload_hash,policy_version,migration_epoch,updated_at
                FROM scoped_records WHERE namespace_scope=? AND record_kind=? AND record_id=? AND deleted=0""",
                (self._scope(context), record_kind, identifier),
            ).fetchone()
        if row is None:
            return None
        return {
            "record_id": identifier,
            "record_kind": record_kind,
            "revision": int(row["revision"]),
            "payload": json.loads(row["payload_json"]),
            "payload_hash": row["payload_hash"],
            "policy_version": row["policy_version"],
            "migration_epoch": row["migration_epoch"],
            "updated_at": row["updated_at"],
        }

    def list_records(self, context: NamespaceContext, *, record_kind: str, limit: int = 100) -> list[dict[str, Any]]:
        self._authorize(context, record_kind, write=False)
        safe_limit = max(1, min(1000, int(limit)))
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT record_id,revision,payload_json,payload_hash,policy_version,migration_epoch,updated_at
                FROM scoped_records WHERE namespace_scope=? AND record_kind=? AND deleted=0
                ORDER BY revision,record_id LIMIT ?""",
                (self._scope(context), record_kind, safe_limit),
            ).fetchall()
        return [
            {
                "record_id": row["record_id"], "record_kind": record_kind, "revision": int(row["revision"]),
                "payload": json.loads(row["payload_json"]), "payload_hash": row["payload_hash"],
                "policy_version": row["policy_version"], "migration_epoch": row["migration_epoch"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def tombstone(
        self,
        context: NamespaceContext,
        *,
        record_kind: str,
        record_id: str,
        revision: int,
        event_id: str,
    ) -> str:
        self._authorize(context, record_kind, write=True)
        identifier = _token(record_id)
        event = _token(event_id)
        if not identifier or not event or revision < 1:
            raise ScopedStoreError("scoped_envelope_invalid")
        scope = self._scope(context)
        request_hash = hashlib.sha256(
            _canonical({"scope": scope, "kind": record_kind, "id": identifier, "revision": revision, "delete": True}).encode("utf-8")
        ).hexdigest()
        now = float(self._clock())
        with self._transaction() as conn:
            prior = conn.execute(
                "SELECT request_hash FROM scoped_operations WHERE event_id=? AND migration_epoch=?",
                (event, context.migration_epoch),
            ).fetchone()
            if prior is not None:
                if prior["request_hash"] != request_hash:
                    raise ScopedRecordConflict("scoped_event_conflict")
                return "duplicate"
            row = conn.execute(
                "SELECT revision,deleted FROM scoped_records WHERE namespace_scope=? AND record_kind=? AND record_id=?",
                (scope, record_kind, identifier),
            ).fetchone()
            if row is None or revision != int(row["revision"]) + 1:
                current = int(row["revision"]) if row is not None else 0
                raise ScopedRevisionGap(f"scoped_revision_gap:{current}:{revision}")
            if int(row["deleted"]) == 1:
                raise ScopedRecordConflict("scoped_record_tombstoned")
            conn.execute(
                "UPDATE scoped_records SET revision=?,deleted=1,updated_at=? WHERE namespace_scope=? AND record_kind=? AND record_id=?",
                (revision, now, scope, record_kind, identifier),
            )
            conn.execute(
                "INSERT INTO scoped_operations(event_id,migration_epoch,request_hash,result_code,created_at) VALUES(?,?,?,?,?)",
                (event, context.migration_epoch, request_hash, "tombstoned", now),
            )
        return "tombstoned"


__all__ = [
    "MAX_RECORD_BYTES", "RECORD_KINDS", "ScopedRecordConflict", "ScopedRevisionGap", "ScopedStore",
    "ScopedStoreError",
]
