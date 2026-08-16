from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

try:
    from .package_bootstrap import bootstrap_package
except ImportError:
    from package_bootstrap import bootstrap_package

ROOT = bootstrap_package()

from astrbot_plugin_memory_companion.core.models import SessionContext
from astrbot_plugin_memory_companion.core.service import MemoryCompanionService


class ScopeControlTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self, scope_control: dict | None = None) -> MemoryCompanionService:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        service = MemoryCompanionService(
            context=None,
            config={
                "retrieval": {"mode": "basic"},
                "scope_control": scope_control or {},
            },
            plugin_root=ROOT,
            data_dir=Path(temp_dir.name),
        )
        self.addCleanup(service.close)
        return service

    @staticmethod
    def private_context() -> SessionContext:
        return SessionContext(
            session_id="qq:FriendMessage:u1",
            scope="private",
            platform="qq",
            user_id="u1",
            bot_id="b1",
            message_text="remember this",
        )

    async def test_scope_controls_default_to_enabled_and_can_be_disabled(self) -> None:
        service = self.make_service(
            {
                "private_capture_enabled": False,
                "group_recall_enabled": False,
                "private_topology_enabled": False,
            }
        )
        self.assertTrue(service._scope_feature_enabled("private", "recall"))
        self.assertFalse(service._scope_feature_enabled("private", "capture"))
        self.assertFalse(service._scope_feature_enabled("group", "recall"))
        self.assertFalse(service._scope_feature_enabled("private", "topology"))
        self.assertTrue(service._scope_feature_enabled("unknown", "recall"))

    async def test_disabled_recall_skips_search_and_injection(self) -> None:
        service = self.make_service({"private_recall_enabled": False})
        ctx = self.private_context()
        results, blocked, slots = await service.search_context_slots("what", ctx)
        self.assertEqual((results, blocked, slots), ([], [], {}))
        service.search_context_slots = AsyncMock(side_effect=AssertionError("must not search"))
        req = SimpleNamespace(system_prompt="", prompt="", contexts=[], extra_user_content_parts=[])
        await service.inject_memories(ctx, req)
        service.search_context_slots.assert_not_awaited()

    async def test_disabled_capture_blocks_tool_write(self) -> None:
        service = self.make_service({"private_capture_enabled": False})
        ctx = self.private_context()
        service.identity.resolve_event_context = AsyncMock(return_value=ctx)
        result = await service.tool_remember(ctx, "remember this")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "scope capture disabled")

    async def test_disabled_topology_blocks_cross_window_acl(self) -> None:
        service = self.make_service({"group_topology_enabled": False})
        await service.store.upsert_acl_rule(
            owner_scope="private",
            owner_id="u1",
            reader_scope="group",
            reader_id="g1",
            effect="allow",
            enabled=True,
        )
        engine = await service._retrieval_engine(self.private_context())
        state = await engine._acl_state()
        self.assertIn("group", state["disabled_scopes"])
        self.assertFalse(state["allow"])


if __name__ == "__main__":
    unittest.main()
