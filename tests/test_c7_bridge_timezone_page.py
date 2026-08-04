from __future__ import annotations

import inspect
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from core import bot_personal_contract
from core.bridge import MemoryCompanionBridge


ROOT = Path(__file__).resolve().parents[1]


class BridgeNegativeProbeTests(unittest.TestCase):
    def test_contract_failure_is_negative_cached_and_not_reprobed(self):
        bridge = MemoryCompanionBridge(object())
        with patch.object(
            bot_personal_contract,
            "contract_self_check",
            side_effect=[RuntimeError("private prompt must not escape"), []],
        ) as checker:
            first = bridge.probe_capability_snapshot()
            second = bridge.probe_capability_snapshot()

        self.assertEqual("negative", first["capability_state"])
        self.assertEqual("negative", first["state"])
        self.assertFalse(first["available"])
        self.assertEqual("contract_self_check_exception", first["error_code"])
        self.assertEqual(1, checker.call_count)
        self.assertEqual("negative", second["capability_state"])

    def test_negative_probe_expires_and_allows_recovery(self):
        bridge = MemoryCompanionBridge(object())
        with patch.object(bot_personal_contract, "contract_self_check", side_effect=RuntimeError("broken")):
            first = bridge.probe_capability_snapshot()
        self.assertEqual("negative", first["state"])

        bridge._capability_cache._negative_at = time.monotonic() - 61.0
        with patch.object(bot_personal_contract, "contract_self_check", return_value=[]):
            recovered = bridge.probe_capability_snapshot()
        self.assertEqual("available", recovered["state"])
        self.assertTrue(recovered["available"])


class C7StaticBoundaryTests(unittest.TestCase):
    def test_service_uses_configured_local_timezone_for_wall_clock_checks(self):
        source = (ROOT / "core" / "service.py").read_text(encoding="utf-8")
        self.assertIn('LOCAL_TZ = ZoneInfo("Asia/Shanghai")', source)
        self.assertIn("datetime.now(LOCAL_TZ)", source)
        self.assertNotIn("datetime.now()", source)

    def test_page_endpoints_return_real_errors_without_exception_text(self):
        source = (ROOT / "page_api.py").read_text(encoding="utf-8")
        for endpoint in ("timeline", "relations", "graph", "threads", "logs"):
            self.assertIn(f'self._err("{endpoint}_unavailable", 500)', source)
        self.assertNotIn('return self._ok({"items": []})', source)

    def test_frontend_http_and_partial_context_errors_are_visible(self):
        source = (ROOT / "pages" / "记忆面板" / "app.js").read_text(encoding="utf-8")
        self.assertIn("if (!response.ok || data?.success === false)", source)
        self.assertIn("renderContextPanelErrors", source)
        self.assertIn("errors: {", source)
        self.assertIn("已显示可用数据", source)


if __name__ == "__main__":
    unittest.main()
