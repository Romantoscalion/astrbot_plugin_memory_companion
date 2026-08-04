from __future__ import annotations

import tempfile
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.bridge import MemoryCompanionBridge  # noqa: E402
from core.emotion_event_contract import (  # noqa: E402
    EMOTION_EVENT_CONTRACT_FINGERPRINT,
    normalize_emotion_event,
)
from core.store import MemoryStore  # noqa: E402


class EmotionE2EventStoreTests(unittest.IsolatedAsyncioTestCase):
    def make_store(self) -> MemoryStore:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        store = MemoryStore(Path(temp_dir.name) / "memory.db")
        store.initialize()
        self.addCleanup(store.close)
        return store

    async def test_event_revisions_are_independent_from_memory_retrieval(self) -> None:
        store = self.make_store()
        first = normalize_emotion_event(
            {
                "event_type": "hurt",
                "session_id": "qq:FriendMessage:u1",
                "dedupe_key": "m1",
                "trace_id": "trace-1",
                "revision": 1,
            },
            producer_plugin="private_companion",
        )
        second = {**first, "revision": 2, "status": "revised", "event_type": "neutral", "correction_of": first["event_id"]}
        await store.upsert_emotion_event(first)
        await store.upsert_emotion_event(first)
        await store.upsert_emotion_event(second)
        trace = await store.get_emotion_trace("trace-1")
        self.assertEqual([1, 2], [item["revision"] for item in trace])
        self.assertEqual(0, store._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
        self.assertEqual(2, store._conn.execute("SELECT COUNT(*) FROM emotion_events").fetchone()[0])

    async def test_bridge_records_and_reads_redacted_trace(self) -> None:
        store = self.make_store()
        plugin = type("Plugin", (), {"store": store})()
        bridge = MemoryCompanionBridge(plugin)
        event = await bridge.record_emotion_event({
            "event_type": "comfort",
            "session_id": "qq:FriendMessage:u1",
            "dedupe_key": "m2",
            "trace_id": "trace-2",
            "raw_text": "PRIVATE",
        })
        trace = await bridge.get_emotion_trace("trace-2")
        self.assertEqual(event["event_id"], trace[0]["event_id"])
        self.assertNotIn("PRIVATE", repr(trace))
        self.assertTrue(EMOTION_EVENT_CONTRACT_FINGERPRINT)


if __name__ == "__main__":
    unittest.main()
