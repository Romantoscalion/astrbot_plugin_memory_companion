from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.emotion_event_contract import normalize_emotion_event  # noqa: E402
from core.store import MemoryStore  # noqa: E402


class EmotionE4AfterglowDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def make_store(self) -> MemoryStore:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = MemoryStore(Path(temp_dir.name) / "memory.db")
        store.initialize()
        self.addCleanup(store.close)
        return store

    async def add_event(self, store: MemoryStore, *, session_id: str, suffix: str) -> dict:
        event = normalize_emotion_event({
            "event_id": f"emo-{suffix}",
            "trace_id": f"trace-{suffix}",
            "origin_kind": "memory_recall",
            "session_id": session_id,
            "event_type": "warm_memory",
            "applied_energy_delta": 3.5,
            "valence_hint": 0.4,
            "dedupe_key": suffix,
        }, producer_plugin="memory_companion")
        return await store.upsert_emotion_event(event)

    async def test_list_retries_until_ack_and_returns_redacted_projection(self) -> None:
        store = self.make_store()
        event = await self.add_event(store, session_id="session-a", suffix="a")
        first = await store.list_emotion_event_deliveries(
            consumer_id="companion", session_id="session-a", limit=5
        )
        retry = await store.list_emotion_event_deliveries(
            consumer_id="companion", session_id="session-a", limit=5
        )
        self.assertEqual([event["event_id"]], [item["event_id"] for item in first["events"]])
        self.assertEqual(first["events"], retry["events"])
        self.assertNotIn("memory_id", repr(first))
        self.assertNotIn("actor_ref", repr(first))
        ack = await store.ack_emotion_event_deliveries(
            consumer_id="companion",
            event_refs=[{"event_id": event["event_id"], "revision": 1}],
        )
        self.assertEqual(1, ack["acked"])
        empty = await store.list_emotion_event_deliveries(
            consumer_id="companion", session_id="session-a", limit=5
        )
        self.assertEqual([], empty["events"])

    async def test_session_and_exclusion_filters_prevent_double_delivery(self) -> None:
        store = self.make_store()
        await self.add_event(store, session_id="session-a", suffix="a")
        event_b = await self.add_event(store, session_id="session-b", suffix="b")
        page = await store.list_emotion_event_deliveries(
            consumer_id="companion", exclude_session_id="session-a", limit=5
        )
        self.assertEqual([event_b["event_id"]], [item["event_id"] for item in page["events"]])


if __name__ == "__main__":
    unittest.main()
