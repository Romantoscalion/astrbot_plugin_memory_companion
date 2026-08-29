from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE_API = ROOT / "page_api.py"
PANEL_SCRIPT = ROOT / "pages" / "记忆面板" / "app.js"
PANEL_PAGE = ROOT / "pages" / "记忆面板" / "index.html"


def assignment(tree: ast.AST, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return node.value
    raise AssertionError(f"missing assignment: {name}")


def literal(value: ast.AST):
    return ast.literal_eval(value)


class UiBackendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.backend_source = PAGE_API.read_text(encoding="utf-8")
        cls.frontend_source = PANEL_SCRIPT.read_text(encoding="utf-8")
        cls.page_source = PANEL_PAGE.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.backend_source)

    def route_paths(self) -> set[str]:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_route_specs":
                paths = {
                    item.elts[0].value
                    for item in ast.walk(node)
                    if isinstance(item, ast.Tuple)
                    and item.elts
                    and isinstance(item.elts[0], ast.Constant)
                    and isinstance(item.elts[0].value, str)
                    and item.elts[0].value.startswith("/")
                }
                self.assertTrue(paths)
                return paths
        self.fail("missing _route_specs")

    def frontend_paths(self) -> set[str]:
        paths = set(
            re.findall(
                r"api(?:Get|Post)\(\s*[\"'`](/[^\"'`?${}]*)",
                self.frontend_source,
            )
        )
        dynamic_match = re.search(
            r"UI_DYNAMIC_ENDPOINTS\s*=\s*new Set\(\[([^\]]*)\]\)",
            self.frontend_source,
        )
        self.assertIsNotNone(dynamic_match)
        paths.update(re.findall(r'[\"\'](/[^\"\']+)[\"\']', dynamic_match.group(1)))
        return paths

    def test_ui_contract_version_and_modes_match(self) -> None:
        backend_version = literal(assignment(self.tree, "UI_CONTRACT_VERSION"))
        backend_modes = literal(assignment(self.tree, "UI_MODES"))
        self.assertEqual("memory.page.ui.v2", backend_version)
        self.assertIn(f'UI_CONTRACT_VERSION = "{backend_version}"', self.frontend_source)
        self.assertEqual({"standard", "cinema"}, {item["id"] for item in backend_modes})
        self.assertIn("简洁管理", self.page_source)
        self.assertIn("放映馆界面", self.page_source)

    def test_every_frontend_api_call_has_a_registered_backend_route(self) -> None:
        missing = self.frontend_paths() - self.route_paths()
        self.assertEqual(set(), missing, f"frontend endpoints without backend routes: {sorted(missing)}")

    def test_page_bridge_uses_registered_namespace_and_separates_query_params(self) -> None:
        self.assertIn('const API = "/api/v1/plugins/extensions/astrbot_plugin_memory_companion/page"', self.frontend_source)
        self.assertIn('const PAGE_ENDPOINT_PREFIX = "page"', self.frontend_source)
        self.assertIn("data = await bridgeRequest(bridge, path, method, options.body);", self.frontend_source)
        self.assertIn("const bridge = getBridge() || await waitForBridge();", self.frontend_source)
        self.assertIn("url.pathname.replace(/^\\/+/, \"\")", self.frontend_source)
        self.assertIn("Object.fromEntries(url.searchParams.entries())", self.frontend_source)
        self.assertIn("bridge.apiGet(endpoint, Object.keys(params).length ? params : undefined)", self.frontend_source)
        self.assertIn('data.status === "error"', self.frontend_source)

    def test_personal_album_uses_bridge_loaded_data_urls(self) -> None:
        self.assertIn("data-album-image-src", self.frontend_source)
        self.assertIn("async function hydratePersonalAlbumImages", self.frontend_source)
        self.assertIn('apiGet(endpoint)', self.frontend_source)
        self.assertIn('result.data_url', self.frontend_source)
        self.assertIn('personal-photo-data', self.frontend_source)

    def test_starmap_detail_unwraps_memory_api_payload(self) -> None:
        self.assertIn('const response = await apiGet("/memory?id=" + encodeURIComponent(memoryId));', self.frontend_source)
        self.assertIn('const memory = response && response.memory ? response.memory : response;', self.frontend_source)

    def test_every_backend_only_route_has_an_explicit_exposure_reason(self) -> None:
        routes = self.route_paths()
        frontend = self.frontend_paths()
        exposure = literal(assignment(self.tree, "UI_ENDPOINT_EXPOSURE"))
        self.assertTrue(set(exposure).issubset(routes))
        backend_only = routes - frontend
        self.assertEqual(
            set(),
            backend_only - set(exposure),
            f"backend-only routes need an explicit internal/compat/advanced reason: {sorted(backend_only - set(exposure))}",
        )
        for path in backend_only:
            self.assertIn(exposure[path]["exposure"], {"internal", "compat", "advanced"})
            self.assertTrue(exposure[path]["reason"].strip())

    def test_declared_view_endpoints_are_registered(self) -> None:
        routes = self.route_paths()
        views = literal(assignment(self.tree, "UI_VIEW_ENDPOINTS"))
        self.assertEqual(
            {"overview", "users", "groups", "personal", "knowledge", "microscope", "archive"},
            set(views),
        )
        for view, endpoints in views.items():
            self.assertTrue(endpoints, view)
            self.assertEqual(set(), set(endpoints) - routes, view)


if __name__ == "__main__":
    unittest.main()
