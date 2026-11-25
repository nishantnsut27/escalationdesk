"""Offline hosting checks; no database or provider required."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from starlette.applications import Starlette
from starlette.routing import Mount

from backend.api import create_app
from backend.spa import AppStaticFiles


class SPAFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        (self.root / "index.html").write_text("<title>TraceDesk fixture</title>")
        (self.root / "assets").mkdir()
        (self.root / "assets" / "app.js").write_text("/* fixture */")
        app = Starlette(routes=[Mount("/", AppStaticFiles(directory=self.root, html=True))])
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def test_recognized_routes_refresh_and_encoded_ids(self):
        paths = ["/", "/dashboard", "/login", "/signup", "/demo-login", "/account", "/forgot", "/privacy",
                 "/connections?oauth=connected", "/logs?evidence=source&query=A%26B", "/import", "/errors",
                 "/collections/saved%20%2F%20id", "/investigations/report%3F%23", "/demo/case",
                 "/overview", "/history/report", "/evidence/saved", "/imports"]
        for path in paths:
            for method in ("GET", "HEAD"):
                response = await self.client.request(method, path)
                self.assertEqual(response.status_code, 200, (method, path))
                self.assertIn("text/html", response.headers["content-type"])
                self.assertEqual(response.text, "" if method == "HEAD" else "<title>TraceDesk fixture</title>")

    async def test_unknown_api_asset_and_method_errors_are_preserved(self):
        for path in ["/missing", "/assets/missing.js", "/missing.png", "/api/missing", "/api/auth/me", "/dashboard/nested", "/collections/a/b"]:
            self.assertEqual((await self.client.get(path)).status_code, 404, path)
        self.assertEqual((await self.client.get("/assets/app.js")).text, "/* fixture */")
        self.assertEqual((await self.client.post("/dashboard")).status_code, 405)

    async def test_backend_api_errors_survive_static_mount(self):
        (self.root / "dist").mkdir()
        (self.root / "dist" / "index.html").write_text("TraceDesk")
        with patch("backend.api.ROOT", self.root):
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test")
            async with client:
                self.assertEqual((await client.get("/dashboard")).status_code, 200)
                response = await client.get("/api/missing")
                self.assertEqual(response.status_code, 404)
                self.assertIn("application/json", response.headers["content-type"])
                self.assertEqual((await client.get("/assets/missing.js")).status_code, 404)
