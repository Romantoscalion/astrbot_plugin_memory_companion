from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.emotion_targeting import memory_emotion_refs  # noqa: E402
from core.models import EntityRef, MemoryRecord, SessionContext  # noqa: E402


class EmotionE7ActorTargetMemoryTests(unittest.TestCase):
    def test_matching_subject_and_session_are_preserved(self) -> None:
        memory = MemoryRecord(
            id="m1",
            session_id="session-a",
            subject=EntityRef(kind="user", id="u1", role="speaker"),
        )
        ctx = SessionContext(session_id="session-a", scope="private", user_id="u1", bot_id="b1")
        refs = memory_emotion_refs(memory, ctx)
        self.assertEqual("u1", refs["actor_ref"]["id"])
        self.assertEqual("b1", refs["target_ref"]["id"])

    def test_cross_session_or_different_user_is_rejected(self) -> None:
        ctx = SessionContext(session_id="session-a", scope="private", user_id="u1", bot_id="b1")
        for memory in (
            MemoryRecord(id="m1", session_id="session-b", subject=EntityRef(kind="user", id="u1")),
            MemoryRecord(id="m2", session_id="session-a", subject=EntityRef(kind="user", id="u2")),
            MemoryRecord(id="m3", session_id="session-a", subject=EntityRef(kind="other", id="nickname")),
        ):
            with self.subTest(memory=memory.id):
                self.assertIsNone(memory_emotion_refs(memory, ctx))

    def test_nickname_text_is_not_used_as_identity(self) -> None:
        memory = SimpleNamespace(
            session_id="session-a",
            content="小明说你是垃圾",
            subject=SimpleNamespace(kind="user", id="", role="speaker", name="小明"),
        )
        ctx = SessionContext(session_id="session-a", scope="private", user_id="u1", bot_id="b1")
        self.assertIsNone(memory_emotion_refs(memory, ctx))


if __name__ == "__main__":
    unittest.main()
