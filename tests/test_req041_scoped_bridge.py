from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from core.bridge import MemoryCompanionBridge
from core.namespace import NamespaceContext
from core.namespace_capability import validate_namespace_capability
from core.scoped_domain_contract import build_scoped_domain_payload
from core.scoped_store import ScopedStore


EPOCH = "req041-20260810-001"
POLICY = "req041-v1"


def _context(*, kind: str = "private", identity: str = "person-a", group: str = "", **changes: str):
    values = {
        "kind": kind,
        "identity_id": identity,
        "group_id": group,
        "assurance": "verified",
        "profile_status": "active",
        "policy_version": POLICY,
        "migration_epoch": EPOCH,
    }
    values.update(changes)
    return NamespaceContext(**values).to_dict()


class ScopedBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "scoped.sqlite3"
        self.store = ScopedStore(self.path, clock=lambda: 100.0)
        self.companion = object()
        self.context = SimpleNamespace(
            get_all_stars=lambda: [
                SimpleNamespace(
                    star_cls=self.companion,
                    root_dir_name="astrbot_plugin_private_companion",
                    name="PrivateCompanion",
                    activated=True,
                )
            ]
        )
        self.service = SimpleNamespace(scoped_store=self.store, context=self.context)
        self.bridge = MemoryCompanionBridge(self.service)
        self.capability = self.bridge.register_private_companion(self.companion)
        self.assertIsNotNone(self.capability)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _bind(self, **changes: str):
        values = {
            "operation_id": "bind-1",
            "expected_previous_epoch": "",
            "migration_epoch": EPOCH,
            "policy_version": POLICY,
        }
        values.update(changes)
        return self.bridge.bind_namespace_migration_epoch(self.capability, **values)

    def test_probe_is_unavailable_before_binding_and_ready_after(self) -> None:
        before = self.bridge.probe_namespace_context_capabilities()
        self.assertFalse(before["available"])
        self.assertEqual("namespace_scoped_api_not_bound", before["error_code"])
        self.assertEqual([], validate_namespace_capability(before, require_available=False))
        denied = self.bridge.bind_namespace_migration_epoch(
            object(), operation_id="bad", expected_previous_epoch="",
            migration_epoch=EPOCH, policy_version=POLICY,
        )
        self.assertEqual("producer_capability_required", denied["code"])
        bound = self._bind()
        self.assertTrue(bound["ok"])
        self.assertEqual("bound", bound["code"])
        ready = self.bridge.probe_namespace_context_capabilities()
        self.assertTrue(ready["available"])
        self.assertEqual([], validate_namespace_capability(ready))

    def test_private_group_and_identity_sentinels_are_isolated_through_bridge(self) -> None:
        self._bind()
        contexts = (
            (_context(), "private"),
            (_context(kind="group_member", group="group-a"), "group-a-person-a"),
            (_context(kind="group_member", group="group-b"), "group-b-person-a"),
            (_context(kind="group_member", identity="person-b", group="group-a"), "group-a-person-b"),
        )
        for index, (context, marker) in enumerate(contexts, start=1):
            result = self.bridge.upsert_scoped_record(
                self.capability, context, record_kind="memory", record_id="same-id", revision=1,
                payload={"marker": marker}, event_id=f"write-{index}",
            )
            self.assertTrue(result["ok"])
        observed = []
        for context, _marker in contexts:
            result = self.bridge.read_scoped_record(
                self.capability, context, record_kind="memory", record_id="same-id"
            )
            observed.append(result["record"]["payload"]["marker"])
        self.assertEqual([marker for _context_value, marker in contexts], observed)

    def test_missing_context_pending_stale_epoch_and_stale_policy_fail_closed(self) -> None:
        self._bind()
        missing = _context()
        missing.pop("group_id")
        self.assertEqual(
            "namespace_context_fields_invalid",
            self.bridge.read_scoped_record(
                self.capability, missing, record_kind="memory", record_id="x"
            )["code"],
        )
        missing_persona = _context()
        missing_persona.pop("persona_id")
        self.assertEqual(
            "namespace_context_fields_invalid",
            self.bridge.read_scoped_record(
                self.capability, missing_persona, record_kind="memory", record_id="x"
            )["code"],
        )
        pending = _context(kind="pending", assurance="unverified")
        self.assertEqual(
            "namespace_pending_denied",
            self.bridge.read_scoped_record(
                self.capability, pending, record_kind="memory", record_id="x"
            )["code"],
        )
        stale_epoch = _context(migration_epoch="old-epoch")
        stale_policy = _context(policy_version="old-policy")
        self.assertEqual(
            "scoped_migration_epoch_stale",
            self.bridge.read_scoped_record(
                self.capability, stale_epoch, record_kind="memory", record_id="x"
            )["code"],
        )
        self.assertEqual(
            "scoped_policy_version_stale",
            self.bridge.read_scoped_record(
                self.capability, stale_policy, record_kind="memory", record_id="x"
            )["code"],
        )

    def test_epoch_binding_persists_and_rotation_is_compare_and_swap(self) -> None:
        self._bind()
        self.bridge.upsert_scoped_record(
            self.capability, _context(), record_kind="memory", record_id="m1", revision=1,
            payload={"value": "stable"}, event_id="write-1",
        )
        reopened_store = ScopedStore(self.path, clock=lambda: 200.0)
        reopened_service = SimpleNamespace(scoped_store=reopened_store, context=self.context)
        reopened_bridge = MemoryCompanionBridge(reopened_service)
        reopened_capability = reopened_bridge.register_private_companion(self.companion)
        self.assertTrue(reopened_bridge.probe_namespace_context_capabilities()["available"])
        failed = reopened_bridge.bind_namespace_migration_epoch(
            reopened_capability, operation_id="rotate-bad", expected_previous_epoch="wrong",
            migration_epoch="req041-20260811-001", policy_version="req041-v2",
        )
        self.assertEqual("scoped_epoch_compare_and_swap_failed", failed["code"])
        rotated = reopened_bridge.bind_namespace_migration_epoch(
            reopened_capability, operation_id="rotate-good", expected_previous_epoch=EPOCH,
            migration_epoch="req041-20260811-001", policy_version="req041-v2",
        )
        self.assertEqual("rotated", rotated["code"])
        new_context = _context(migration_epoch="req041-20260811-001", policy_version="req041-v2")
        read = reopened_bridge.read_scoped_record(
            reopened_capability, new_context, record_kind="memory", record_id="m1"
        )
        self.assertEqual("stable", read["record"]["payload"]["value"])
        stale = reopened_bridge.read_scoped_record(
            reopened_capability, _context(), record_kind="memory", record_id="m1"
        )
        self.assertEqual("scoped_migration_epoch_stale", stale["code"])

    def test_idempotency_conflict_list_and_tombstone_are_preserved(self) -> None:
        self._bind()
        context = _context()
        create = dict(
            record_kind="profile_fact", record_id="nickname", revision=1,
            payload={"value": "A"}, event_id="write-1",
        )
        self.assertEqual("created", self.bridge.upsert_scoped_record(self.capability, context, **create)["code"])
        self.assertEqual("duplicate", self.bridge.upsert_scoped_record(self.capability, context, **create)["code"])
        conflict = self.bridge.upsert_scoped_record(
            self.capability, context, **{**create, "payload": {"value": "B"}}
        )
        self.assertEqual("scoped_event_conflict", conflict["code"])
        listed = self.bridge.list_scoped_records(
            self.capability, context, record_kind="profile_fact", limit=10
        )
        self.assertEqual(["nickname"], [item["record_id"] for item in listed["records"]])
        deleted = self.bridge.tombstone_scoped_record(
            self.capability, context, record_kind="profile_fact", record_id="nickname",
            revision=2, event_id="delete-1",
        )
        self.assertEqual("tombstoned", deleted["code"])
        self.assertEqual(
            "not_found",
            self.bridge.read_scoped_record(
                self.capability, context, record_kind="profile_fact", record_id="nickname"
            )["code"],
        )

    def test_bridge_exposes_atomic_namespace_tombstone(self) -> None:
        self._bind()
        context = _context()
        written = self.bridge.upsert_scoped_record(
            self.capability, context, record_kind="memory", record_id="req041-private-memory-a",
            revision=1, payload=build_scoped_domain_payload(
                domain="memory", source_kind="private", content={"value": "a"}, source_revision=1,
            ), event_id="write-a",
        )
        self.assertTrue(written["ok"])
        result = self.bridge.tombstone_scoped_namespace(
            self.capability, context, operation_id="archive-1", reason_code="person_archive",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(1, result["count"])
        self.assertEqual("not_found", self.bridge.read_scoped_record(
            self.capability, context, record_kind="memory", record_id="req041-private-memory-a"
        )["code"])


if __name__ == "__main__":
    unittest.main()
