from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from core.namespace import NamespaceContext
from core.scoped_domain_contract import build_scoped_domain_payload
from core.scoped_store import ScopedRecordConflict, ScopedRevisionGap, ScopedStore, ScopedStoreError


def _context(kind: str, *, identity: str = "person-a", group: str = "") -> NamespaceContext:
    return NamespaceContext(
        kind=kind,
        identity_id=identity,
        group_id=group,
        assurance="verified",
        profile_status="active",
        policy_version="req041-v1",
        migration_epoch="shadow-20260810",
    )


class ScopedStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ScopedStore(Path(self.tmp.name) / "scoped.sqlite3", clock=lambda: 100.0)
        self.private = _context("private")
        self.group_a = _context("group_member", group="group-a")
        self.group_b = _context("group_member", group="group-b")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_private_group_a_and_group_b_are_physically_isolated(self) -> None:
        contexts = ((self.private, "private-sentinel"), (self.group_a, "group-a-sentinel"), (self.group_b, "group-b-sentinel"))
        for index, (context, marker) in enumerate(contexts, start=1):
            self.assertEqual(
                "created",
                self.store.upsert(
                    context,
                    record_kind="memory",
                    record_id="same-record-id",
                    revision=1,
                    payload={"marker": marker},
                    event_id=f"event-{index}",
                ),
            )
        self.assertEqual("private-sentinel", self.store.read(self.private, record_kind="memory", record_id="same-record-id")["payload"]["marker"])
        self.assertEqual("group-a-sentinel", self.store.read(self.group_a, record_kind="memory", record_id="same-record-id")["payload"]["marker"])
        self.assertEqual("group-b-sentinel", self.store.read(self.group_b, record_kind="memory", record_id="same-record-id")["payload"]["marker"])

    def test_another_identity_in_same_group_is_isolated(self) -> None:
        other = _context("group_member", identity="person-b", group="group-a")
        self.store.upsert(self.group_a, record_kind="profile_fact", record_id="nickname", revision=1, payload={"value": "A"}, event_id="a-1")
        self.assertIsNone(self.store.read(other, record_kind="profile_fact", record_id="nickname"))

    def test_same_identity_and_group_are_isolated_between_personas(self) -> None:
        other_persona = NamespaceContext(
            kind="private", identity_id="person-a", group_id="", assurance="verified",
            profile_status="active", policy_version="req041-v1", migration_epoch="shadow-20260810",
            persona_id="persona-b",
        )
        self.store.upsert(
            self.private, record_kind="memory", record_id="same-id", revision=1,
            payload={"marker": "default"}, event_id="persona-default",
        )
        self.store.upsert(
            other_persona, record_kind="memory", record_id="same-id", revision=1,
            payload={"marker": "persona-b"}, event_id="persona-b",
        )
        self.assertEqual("default", self.store.read(self.private, record_kind="memory", record_id="same-id")["payload"]["marker"])
        self.assertEqual("persona-b", self.store.read(other_persona, record_kind="memory", record_id="same-id")["payload"]["marker"])

    def test_policy_and_epoch_changes_do_not_create_a_second_owner_namespace(self) -> None:
        self.store.upsert(
            self.private, record_kind="memory", record_id="m1", revision=1,
            payload={"value": "stable"}, event_id="event-1",
        )
        next_epoch = NamespaceContext(
            kind="private", identity_id="person-a", group_id="", assurance="explicit_linked",
            profile_status="active", policy_version="req041-v2", migration_epoch="shadow-20260811",
        )
        self.assertEqual("stable", self.store.read(next_epoch, record_kind="memory", record_id="m1")["payload"]["value"])

    def test_pending_unverified_and_missing_context_fail_closed(self) -> None:
        pending = _context("pending")
        unverified = NamespaceContext(**{**self.private.__dict__, "assurance": "unverified"}) if hasattr(self.private, "__dict__") else NamespaceContext(
            kind="private", identity_id="person-a", group_id="", assurance="unverified", profile_status="active",
            policy_version="req041-v1", migration_epoch="shadow-20260810",
        )
        with self.assertRaisesRegex(ScopedStoreError, "namespace_pending_denied"):
            self.store.read(pending, record_kind="memory", record_id="x")
        with self.assertRaisesRegex(ScopedStoreError, "identity_assurance_insufficient"):
            self.store.read(unverified, record_kind="memory", record_id="x")
        with self.assertRaisesRegex(ScopedStoreError, "namespace_context_missing"):
            self.store.read(None, record_kind="memory", record_id="x")  # type: ignore[arg-type]

    def test_revision_and_event_id_are_idempotent_and_conflict_safe(self) -> None:
        create = dict(record_kind="memory", record_id="m1", revision=1, payload={"value": 1}, event_id="event-1")
        self.assertEqual("created", self.store.upsert(self.private, **create))
        self.assertEqual("duplicate", self.store.upsert(self.private, **create))
        with self.assertRaises(ScopedRecordConflict):
            self.store.upsert(self.private, **{**create, "payload": {"value": 2}})
        with self.assertRaises(ScopedRevisionGap):
            self.store.upsert(self.private, record_kind="memory", record_id="m1", revision=3, payload={"value": 3}, event_id="event-3")
        self.assertEqual(
            "updated",
            self.store.upsert(self.private, record_kind="memory", record_id="m1", revision=2, payload={"value": 2}, event_id="event-2"),
        )

    def test_tombstone_is_next_revision_and_prevents_resurrection(self) -> None:
        self.store.upsert(self.private, record_kind="memory", record_id="m1", revision=1, payload={"value": 1}, event_id="event-1")
        self.assertEqual("tombstoned", self.store.tombstone(self.private, record_kind="memory", record_id="m1", revision=2, event_id="delete-1"))
        self.assertIsNone(self.store.read(self.private, record_kind="memory", record_id="m1"))
        self.assertEqual("duplicate", self.store.tombstone(self.private, record_kind="memory", record_id="m1", revision=2, event_id="delete-1"))
        with self.assertRaisesRegex(ScopedRecordConflict, "scoped_record_tombstoned"):
            self.store.upsert(self.private, record_kind="memory", record_id="m1", revision=3, payload={"value": 3}, event_id="event-3")

    def test_group_shared_cannot_store_subject_profile(self) -> None:
        shared = _context("group_shared", identity="", group="group-a")
        with self.assertRaisesRegex(ScopedStoreError, "group_shared_subject_access_denied"):
            self.store.upsert(shared, record_kind="profile_fact", record_id="nickname", revision=1, payload={"value": "x"}, event_id="event-1")
        self.assertEqual(
            "created",
            self.store.upsert(shared, record_kind="memory", record_id="group-topic", revision=1, payload={"value": "topic"}, event_id="event-2"),
        )

    def test_namespace_tombstone_is_atomic_idempotent_and_prevents_resurrection(self) -> None:
        self.store.upsert(
            self.private, record_kind="memory", record_id="req041-private-memory-a", revision=1,
            payload=build_scoped_domain_payload(
                domain="memory", source_kind="private", content={"value": "a"}, source_revision=1,
            ), event_id="write-a",
        )
        self.store.upsert(
            self.private, record_kind="rule", record_id="req041-private-rule-a", revision=1,
            payload=build_scoped_domain_payload(
                domain="learning", source_kind="private", content={"value": "rule"},
                source_revision=1, approval_state="approved", approved_by="user",
            ), event_id="write-rule",
        )
        self.store.upsert(
            self.private, record_kind="memory", record_id="legacy-unmanaged", revision=1,
            payload={"value": "keep"}, event_id="write-legacy",
        )
        result = self.store.tombstone_namespace(
            self.private, operation_id="archive-1", reason_code="person_archive",
        )
        self.assertEqual(2, result["count"])
        self.assertEqual(result, self.store.tombstone_namespace(
            self.private, operation_id="archive-1", reason_code="person_archive",
        ))
        self.assertIsNone(self.store.read(self.private, record_kind="memory", record_id="req041-private-memory-a"))
        self.assertEqual("keep", self.store.read(
            self.private, record_kind="memory", record_id="legacy-unmanaged"
        )["payload"]["value"])
        with self.assertRaisesRegex(ScopedRecordConflict, "scoped_record_tombstoned"):
            self.store.upsert(
                self.private, record_kind="memory", record_id="req041-private-memory-a", revision=3,
                payload=build_scoped_domain_payload(
                    domain="memory", source_kind="private", content={"value": "resurrect"},
                    source_revision=3,
                ), event_id="resurrect-a",
            )
        with self.assertRaisesRegex(ScopedRecordConflict, "scoped_namespace_operation_conflict"):
            self.store.tombstone_namespace(
                self.private, operation_id="archive-1", reason_code="different_reason",
            )


if __name__ == "__main__":
    unittest.main()
