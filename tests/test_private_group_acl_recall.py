from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_remember_you.core.models import EntityRef, MemoryRecord, SessionContext
from astrbot_plugin_remember_you.core.service import MemoryCompanionService


class PrivateToGroupAclRecallTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self) -> MemoryCompanionService:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        service = MemoryCompanionService(
            context=None,
            config={
                "retrieval": {"mode": "basic"},
                "visibility": {"enable_acl_rules": True},
                "memory_injection": {"enable_injection_logs": False, "max_chars": 3200},
            },
            plugin_root=ROOT,
            data_dir=Path(temp_dir.name),
        )
        self.addCleanup(service.close)
        return service

    @staticmethod
    def private_memory(
        content: str = "小王明确说过：中午吃了番茄鸡蛋面。",
        *,
        memory_type: str = "explicit_memory",
        occurred_at: str = "",
    ) -> MemoryRecord:
        return MemoryRecord(
            memory_type=memory_type,
            subject=EntityRef(kind="user", id="u1", name="小王"),
            object=EntityRef.bot_self("b1"),
            scope="private",
            session_id="qq:FriendMessage:u1",
            platform="qq",
            visibility="private_pair",
            sayability="indirect" if memory_type == "tool_memory" else "direct",
            reality_level="real_user_fact",
            lifecycle="stable_memory",
            content=content,
            evidence="请记住这件事。",
            confidence=0.9,
            importance=0.9,
            tags=["stable_fact", memory_type],
            metadata={"mention_policy": "direct", "mentionability_score": 0.9},
            occurred_at=occurred_at,
        )

    @staticmethod
    def group_context(
        *,
        user_id: str = "u1",
        message_text: str = "我中午吃了什么？",
        platform: str = "qq",
        bot_id: str = "b1",
    ) -> SessionContext:
        return SessionContext(
            session_id="qq:GroupMessage:g1",
            scope="group",
            platform=platform,
            user_id=user_id,
            user_name="小王" if user_id == "u1" else "小李",
            group_id="g1",
            group_name="测试群",
            bot_id=bot_id,
            message_text=message_text,
        )

    async def allow_private_to_group(self, service: MemoryCompanionService) -> dict:
        return await service.store.upsert_acl_rule(
            owner_scope="private",
            owner_id="u1",
            reader_scope="group",
            reader_id="g1",
            effect="allow",
        )

    async def test_acl_shared_explicit_memory_answers_natural_group_question(self) -> None:
        service = self.make_service()
        await service.store.insert_memory(self.private_memory())
        await self.allow_private_to_group(service)
        ctx = self.group_context()

        injection = await service._compose_memory_injection(
            ctx,
            max_chars=3200,
            write_log=False,
        )

        self.assertIn("番茄鸡蛋面", injection)
        self.assertIn("acl_allowed", injection)
        self.assertIn("权限只表示该记忆可作为候选", injection)
        self.assertIn("普通陈述或意图不清时忽略", injection)
        self.assertIn("当前发言者的核心意图", injection)

    async def test_acl_shared_tool_memory_is_kept_by_recent_state_guard(self) -> None:
        service = self.make_service()
        await service.store.insert_memory(self.private_memory(memory_type="tool_memory"))
        await self.allow_private_to_group(service)

        injection = await service._compose_memory_injection(
            self.group_context(),
            max_chars=3200,
            write_log=False,
        )

        self.assertIn("番茄鸡蛋面", injection)
        self.assertIn("普通陈述或意图不清时忽略", injection)

    async def test_production_remember_tool_record_is_recalled_across_acl(self) -> None:
        service = self.make_service()
        private_ctx = SessionContext(
            session_id="qq:FriendMessage:u1",
            scope="private",
            platform="qq",
            user_id="u1",
            user_name="小王",
            bot_id="b1",
            message_text="请记住我喜欢的面馆是云杉面馆",
        )
        service.identity.resolve_event_context = AsyncMock(return_value=private_ctx)
        stored = await service.tool_remember(
            object(),
            "小王喜欢的面馆是云杉面馆。",
            note_type="preference",
        )
        self.assertIs(stored.get("ok"), True)
        await self.allow_private_to_group(service)

        injection = await service._compose_memory_injection(
            self.group_context(message_text="我喜欢哪家面馆？"),
            max_chars=3200,
            write_log=False,
        )

        self.assertIn("云杉面馆", injection)
        self.assertNotIn("只影响语气，禁止复述", injection)
        self.assertIn("普通陈述或意图不清时忽略", injection)

    async def test_acl_shared_preference_can_be_used_without_recall_keywords(self) -> None:
        service = self.make_service()
        await service.store.insert_memory(
            self.private_memory(
                "小王喜欢的面馆是云杉面馆。",
                memory_type="user_preference",
            )
        )
        await self.allow_private_to_group(service)
        ctx = self.group_context(message_text="我喜欢哪家面馆？")

        injection = await service._compose_memory_injection(
            ctx,
            max_chars=3200,
            write_log=False,
        )

        self.assertIn("云杉面馆", injection)
        self.assertIn("普通陈述或意图不清时忽略", injection)

    async def test_no_forward_acl_reverse_acl_and_unrelated_query_stay_private(self) -> None:
        service = self.make_service()
        await service.store.insert_memory(self.private_memory())
        ctx = self.group_context()

        without_acl = await service._compose_memory_injection(ctx, max_chars=3200, write_log=False)
        self.assertNotIn("番茄鸡蛋面", without_acl)

        await service.store.upsert_acl_rule(
            owner_scope="group",
            owner_id="g1",
            reader_scope="private",
            reader_id="u1",
            effect="allow",
        )
        reverse_only = await service._compose_memory_injection(ctx, max_chars=3200, write_log=False)
        self.assertNotIn("番茄鸡蛋面", reverse_only)

        await self.allow_private_to_group(service)
        unrelated = await service._compose_memory_injection(
            self.group_context(message_text="今天天气怎么样？"),
            max_chars=3200,
            write_log=False,
        )
        self.assertNotIn("番茄鸡蛋面", unrelated)

    async def test_related_group_statement_is_prompt_guarded_instead_of_keyword_blocked(self) -> None:
        service = self.make_service()
        await service.store.insert_memory(
            self.private_memory(
                "小王喜欢的面馆是云杉面馆。",
                memory_type="user_preference",
            )
        )
        await self.allow_private_to_group(service)

        injection = await service._compose_memory_injection(
            self.group_context(message_text="云杉面馆最近在群里挺火"),
            max_chars=3200,
            write_log=False,
        )

        self.assertIn("小王喜欢的面馆", injection)
        self.assertIn("否则不要主动公开或复述", injection)

    async def test_natural_request_without_fixed_question_words_can_use_acl_candidate(self) -> None:
        service = self.make_service()
        await service.store.insert_memory(
            self.private_memory(
                "小王喜欢的面馆是云杉面馆。",
                memory_type="user_preference",
            )
        )
        await self.allow_private_to_group(service)

        injection = await service._compose_memory_injection(
            self.group_context(message_text="说说我喜欢的面馆"),
            max_chars=3200,
            write_log=False,
        )

        self.assertIn("云杉面馆", injection)
        self.assertIn("不要依赖固定疑问词判断用户意图", injection)

    async def test_omitted_subject_recall_is_not_rejected_by_pronoun_rules(self) -> None:
        service = self.make_service()
        await service.store.insert_memory(self.private_memory())
        await self.allow_private_to_group(service)

        injection = await service._compose_memory_injection(
            self.group_context(message_text="中午吃的是啥来着？"),
            max_chars=3200,
            write_log=False,
        )

        self.assertIn("番茄鸡蛋面", injection)
        self.assertIn("普通陈述或意图不清时忽略", injection)

    async def test_private_acl_does_not_cross_bot_or_platform(self) -> None:
        service = self.make_service()
        await service.store.insert_memory(self.private_memory())
        await self.allow_private_to_group(service)

        other_bot = await service._compose_memory_injection(
            self.group_context(bot_id="b2"),
            max_chars=3200,
            write_log=False,
        )
        other_platform = await service._compose_memory_injection(
            self.group_context(platform="other-platform"),
            max_chars=3200,
            write_log=False,
        )

        self.assertNotIn("番茄鸡蛋面", other_bot)
        self.assertNotIn("番茄鸡蛋面", other_platform)

    async def test_seamless_acl_use_is_limited_to_private_memory_owner(self) -> None:
        service = self.make_service()
        await service.store.insert_memory(self.private_memory())
        await self.allow_private_to_group(service)

        injection = await service._compose_memory_injection(
            self.group_context(user_id="u2", message_text="小王中午吃了什么？"),
            max_chars=3200,
            write_log=False,
        )

        self.assertNotIn("番茄鸡蛋面", injection)

    async def test_acl_revoke_removes_cached_private_group_result(self) -> None:
        service = self.make_service()
        await service.store.insert_memory(self.private_memory())
        rule = await self.allow_private_to_group(service)
        ctx = self.group_context()

        first = await service._compose_memory_injection(ctx, max_chars=3200, write_log=False)
        second = await service._compose_memory_injection(ctx, max_chars=3200, write_log=False)
        self.assertIn("番茄鸡蛋面", first)
        self.assertIn("番茄鸡蛋面", second)

        await service.store.delete_acl_rule(rule["id"])
        revoked = await service._compose_memory_injection(ctx, max_chars=3200, write_log=False)
        self.assertNotIn("番茄鸡蛋面", revoked)

    async def test_recall_tool_uses_same_group_actor_guard(self) -> None:
        service = self.make_service()
        await service.store.insert_memory(self.private_memory())
        await self.allow_private_to_group(service)

        service.identity.resolve_event_context = AsyncMock(return_value=self.group_context())
        owner_result = await service.tool_recall(object(), "我中午吃了什么？")
        self.assertEqual(["小王明确说过：中午吃了番茄鸡蛋面。"], [item["content"] for item in owner_result["memories"]])
        self.assertIn("条件候选", owner_result["usage"])

        service.identity.resolve_event_context = AsyncMock(
            return_value=self.group_context(user_id="u2", message_text="小王中午吃了什么？")
        )
        other_result = await service.tool_recall(object(), "小王中午吃了什么？")
        self.assertEqual([], other_result["memories"])

    async def test_recall_tool_keeps_other_memory_slots_when_schedules_rank_first(self) -> None:
        service = self.make_service()
        ctx = SessionContext(
            session_id="qq:FriendMessage:u1",
            scope="private",
            platform="qq",
            user_id="u1",
            user_name="小王",
            bot_id="b1",
            message_text="共同召回锚点",
        )
        for index in range(8):
            await service.store.insert_memory(
                MemoryRecord(
                    id=f"schedule-{index}",
                    memory_type="schedule_fragment",
                    subject=EntityRef.bot_self("b1"),
                    object=EntityRef(kind="user", id="u1", name="小王"),
                    scope="private",
                    session_id=ctx.session_id,
                    platform="qq",
                    visibility="bot_self",
                    reality_level="persona_life",
                    lifecycle="stable_memory",
                    content=f"共同召回锚点日程 {index}",
                    confidence=1.0,
                    importance=1.0,
                    metadata={"owner_bot_id": "b1"},
                )
            )
        await service.store.insert_memory(
            MemoryRecord(
                id="profile-anchor",
                memory_type="user_preference",
                subject=EntityRef(kind="user", id="u1", name="小王"),
                object=EntityRef.bot_self("b1"),
                scope="private",
                session_id=ctx.session_id,
                platform="qq",
                visibility="private_pair",
                lifecycle="stable_memory",
                content="共同召回锚点对应用户喜欢无糖拿铁。",
                confidence=0.8,
                importance=0.6,
                metadata={"owner_bot_id": "b1"},
            )
        )
        await service.store.insert_memory(
            MemoryRecord(
                id="summary-anchor",
                memory_type="conversation_summary",
                subject=EntityRef(kind="user", id="u1", name="小王"),
                object=EntityRef.bot_self("b1"),
                scope="private",
                session_id=ctx.session_id,
                platform="qq",
                visibility="private_pair",
                lifecycle="stable_memory",
                content="共同召回锚点对应一次咖啡偏好对话。",
                confidence=0.8,
                importance=0.6,
                metadata={"owner_bot_id": "b1"},
            )
        )
        service.identity.resolve_event_context = AsyncMock(return_value=ctx)

        result = await service.tool_recall(object(), "共同召回锚点", top_k=5)
        memory_types = [item["memory_type"] for item in result["memories"]]

        self.assertIn("user_preference", memory_types)
        self.assertIn("conversation_summary", memory_types)
        self.assertLess(memory_types.count("schedule_fragment"), len(memory_types))

    async def test_old_explicit_meal_memory_is_not_mistaken_for_current_state(self) -> None:
        service = self.make_service()
        old_time = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
        await service.store.insert_memory(self.private_memory(occurred_at=old_time))
        await self.allow_private_to_group(service)

        injection = await service._compose_memory_injection(
            self.group_context(),
            max_chars=3200,
            write_log=False,
        )

        self.assertNotIn("番茄鸡蛋面", injection)

        recalled = await service._compose_memory_injection(
            self.group_context(message_text="你还记得我上次中午吃了什么吗？"),
            max_chars=3200,
            write_log=False,
        )
        self.assertIn("番茄鸡蛋面", recalled)


if __name__ == "__main__":
    unittest.main()
