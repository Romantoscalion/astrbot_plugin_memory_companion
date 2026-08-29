# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

try:
    from .package_bootstrap import bootstrap_package
except ImportError:
    from package_bootstrap import bootstrap_package


bootstrap_package()

if "quart" not in sys.modules:
    quart_stub = types.ModuleType("quart")
    quart_stub.jsonify = lambda payload=None, **kwargs: payload or kwargs
    quart_stub.request = SimpleNamespace(args={}, method="GET")
    quart_stub.send_file = AsyncMock()
    sys.modules["quart"] = quart_stub

page_api_module = importlib.import_module("astrbot_plugin_memory_companion.page_api")
PluginPageApi = page_api_module.PluginPageApi


class PageMemoryVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_regular_memory_list_excludes_bot_personal_bridge_placeholders(self) -> None:
        records = [
            SimpleNamespace(source_plugin="bot_personal_bridge", id="placeholder"),
            SimpleNamespace(source_plugin="memory_companion", id="real"),
        ]
        store = SimpleNamespace(list_memories=AsyncMock(return_value=records))
        plugin = SimpleNamespace(service=SimpleNamespace(store=store))
        api = PluginPageApi(plugin)
        request = SimpleNamespace(args={"limit": "50"})

        with (
            patch.object(page_api_module, "request", request),
            patch.object(page_api_module, "serialize_memory", side_effect=lambda record: {"id": record.id}),
            patch.object(page_api_module, "jsonify", side_effect=lambda body: body),
        ):
            response = await api.memories()

        self.assertEqual([{"id": "real"}], response["memories"])
        self.assertEqual("bot_personal_bridge", store.list_memories.await_args.kwargs["source_plugin_exclude"])

