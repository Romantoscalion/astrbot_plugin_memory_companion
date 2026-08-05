from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.bridge import MemoryCompanionBridge
from core.models import EntityRef, MemoryRecord
from core.service import MemoryCompanionService, USER_MEMORY_SUMMARY_VERSION
from core.store import MemoryStore


class _Config:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def bridge_enabled(self) -> bool:
        return self.enabled


class Req029UserMemorySummaryBridgeTests(unittest.IsolatedAsyncioTestCase):
    def make_store(self) -> MemoryStore:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = MemoryStore(Path(temp_dir.name) / "memory.db")
        store.initialize()
        self.addCleanup(store.close)
        return store

    async def add_private_memory(
        self,
        store: MemoryStore,
        *,
        record_id: str,
        user_id: str,
        memory_type: str,
        content: str,
        session_id: str | None = None,
    ) -> None:
        await store.insert_memory(
            MemoryRecord(
                id=record_id,
                memory_type=memory_type,
                subject=EntityRef(kind="user", id=user_id),
                object=EntityRef.bot_self(),
                scope="private",
                session_id=session_id or f"qq:FriendMessage:{user_id}",
                visibility="private_pair",
                lifecycle="stable_memory",
                content=content,
                occurred_at="2026-08-02T10:00:00+00:00",
            )
        )

    async def test_exact_identity_query_counts_categories_and_excludes_other_users(self) -> None:
        store = self.make_store()
        for record_id, memory_type in (
            ("profile", "user_profile"),
            ("preference", "user_preference"),
            ("relation", "relationship_claim"),
            ("conversation", "conversation_summary"),
        ):
            await self.add_private_memory(
                store,
                record_id=record_id,
                user_id="u1",
                memory_type=memory_type,
                content=f"u1-private-{record_id}",
            )
        await self.add_private_memory(
            store,
            record_id="other-user",
            user_id="u2",
            memory_type="user_profile",
            content="u2-private-only",
        )
        await store.insert_memory(
            MemoryRecord(
                id="group-u1",
                memory_type="user_profile",
                subject=EntityRef(kind="user", id="u1"),
                object=EntityRef(kind="group", id="g1"),
                scope="group",
                session_id="qq:GroupMessage:g1",
                group_id="g1",
                visibility="group_public",
                content="group-only",
            )
        )

        service = MemoryCompanionService.__new__(MemoryCompanionService)
        service.config = _Config(True)
        service.store = store
        result = await service.read_user_memory_summary("u1", session_id="qq:FriendMessage:u1", limit=8)

        self.assertEqual(USER_MEMORY_SUMMARY_VERSION, result["contract"])
        self.assertTrue(result["ok"])
        self.assertEqual("ready", result["state"])
        self.assertEqual(
            {"profile": 1, "preference": 1, "relationship": 1, "private_conversation": 1, "other": 0, "total": 4},
            result["counts"],
        )
        rendered = repr(result["summaries"])
        for forbidden in ("u1-private", "u2-private-only", "group-only"):
            self.assertNotIn(forbidden, rendered)
        self.assertTrue(all(item["content_redacted"] and item["truncated"] for item in result["summaries"]))

    async def test_session_mismatch_fails_closed_without_cross_user_fallback(self) -> None:
        store = self.make_store()
        await self.add_private_memory(
            store,
            record_id="u1-profile",
            user_id="u1",
            memory_type="user_profile",
            content="private-u1",
        )
        service = MemoryCompanionService.__new__(MemoryCompanionService)
        service.config = _Config(True)
        service.store = store

        result = await service.read_user_memory_summary("u1", session_id="qq:FriendMessage:u2")
        self.assertFalse(result["ok"])
        self.assertEqual("degraded", result["state"])
        self.assertEqual("session_identity_mismatch", result["error_code"])
        self.assertEqual(0, result["counts"]["total"])

    async def test_bridge_contract_redacts_records_and_degrades_for_disabled_or_failed_queries(self) -> None:
        store = self.make_store()
        await self.add_private_memory(
            store,
            record_id="secret-profile",
            user_id="u1",
            memory_type="user_profile",
            content="TOKEN=PRIVATE_CONTEXT_MUST_NOT_LEAK " + "x" * 500,
        )
        service = MemoryCompanionService.__new__(MemoryCompanionService)
        service.config = _Config(True)
        service.store = store
        companion = object()
        service.context = SimpleNamespace(
            get_all_stars=lambda: [
                SimpleNamespace(
                    star_cls=companion,
                    root_dir_name="astrbot_plugin_private_companion",
                    name="PrivateCompanion",
                    activated=True,
                )
            ]
        )
        bridge = MemoryCompanionBridge(service)
        capability = bridge.register_emotion_producer(companion)
        requester = bridge.create_user_memory_context(
            capability,
            bot_id="bot-1",
            platform="qq",
            scope="private",
            user_id="u1",
            session_id="qq:FriendMessage:u1",
        )

        ready = await bridge.read_user_memory_summary(
            "u1", limit=99, requester_context=requester
        )
        self.assertTrue(ready["ok"])
        self.assertEqual(USER_MEMORY_SUMMARY_VERSION, ready["contract"])
        self.assertLessEqual(len(ready["summaries"]), 8)
        self.assertNotIn("PRIVATE_CONTEXT_MUST_NOT_LEAK", repr(ready))
        self.assertNotIn("content", ready["summaries"][0])

        denied = await bridge.read_user_memory_summary("u1")
        wrong_user = await bridge.read_user_memory_summary(
            "u2", requester_context=requester
        )
        wrong_session = await bridge.read_user_memory_summary(
            "u1",
            session_id="qq:FriendMessage:other",
            requester_context=requester,
        )
        for result in (denied, wrong_user, wrong_session):
            self.assertFalse(result["ok"])
            self.assertEqual("forbidden", result["state"])
            self.assertEqual(0, result["counts"]["total"])

        service.config = _Config(False)
        disabled = await bridge.read_user_memory_summary("u1", requester_context=requester)
        self.assertFalse(disabled["ok"])
        self.assertEqual("bridge_disabled", disabled["error_code"])
        self.assertEqual(0, disabled["counts"]["total"])

        class _BrokenService:
            context = SimpleNamespace(
                get_all_stars=lambda: [
                    SimpleNamespace(
                        star_cls=companion,
                        root_dir_name="astrbot_plugin_private_companion",
                        name="PrivateCompanion",
                        activated=True,
                    )
                ]
            )

            async def read_user_memory_summary(self, *_args, **_kwargs):
                raise RuntimeError("sensitive failure details")

        broken_bridge = MemoryCompanionBridge(_BrokenService())
        broken_capability = broken_bridge.register_emotion_producer(companion)
        broken_requester = broken_bridge.create_user_memory_context(
            broken_capability,
            bot_id="bot-1",
            platform="qq",
            scope="private",
            user_id="u1",
            session_id="qq:FriendMessage:u1",
        )
        broken = await broken_bridge.read_user_memory_summary(
            "u1", requester_context=broken_requester
        )
        self.assertFalse(broken["ok"])
        self.assertEqual("bridge_exception", broken["error_code"])
        self.assertNotIn("sensitive failure details", repr(broken))

    def test_bridge_and_page_api_expose_only_the_read_only_workspace_contract(self) -> None:
        bridge_source = (ROOT / "core" / "bridge.py").read_text(encoding="utf-8")
        page_source = (ROOT / "page_api.py").read_text(encoding="utf-8")
        self.assertIn("async def read_user_memory_summary", bridge_source)
        self.assertIn('"/user-memory-summary"', page_source)
        self.assertIn("content_redacted", bridge_source)
        self.assertNotIn("write_user_memory_summary", bridge_source)


if __name__ == "__main__":
    unittest.main()
