import asyncio
import base64

from httpx import ASGITransport, AsyncClient

from app.server import create_app
from tests.conftest import PY_SAMPLE


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def test_analyze_endpoint():
    async def run():
        async with _client(create_app()) as c:
            r = await c.post(
                "/analyze", params={"language": "python"},
                content=PY_SAMPLE.encode(),
            )
            assert r.status_code == 200
            body = r.json()
            assert body["language"] == "python"
            assert body["highlights"] and body["outline"]
            assert body["formatted"].endswith("\n")
            assert body["degraded"] is False

    asyncio.run(run())


def test_analyze_unknown_language_400():
    async def run():
        async with _client(create_app()) as c:
            r = await c.post("/analyze", params={"language": "cobol"}, content=b"x")
            assert r.status_code == 400
            assert r.json()["error"]["code"] == "language-not-supported"

    asyncio.run(run())


def test_edit_endpoint():
    async def run():
        old = b"x = 1\n"
        new = b"x = 2\n"
        async with _client(create_app()) as c:
            r = await c.post("/edit", json={
                "language": "python",
                "old_b64": base64.b64encode(old).decode(),
                "new_b64": base64.b64encode(new).decode(),
                "edit": {
                    "start": {"line": 0, "col": 4},
                    "end": {"line": 0, "col": 5},
                    "new_text": "2",
                },
            })
            assert r.status_code == 200
            body = r.json()
            assert body["result"]["formatted"] == "x = 2\n"
            assert body["affected"]["start"] == {"line": 0, "col": 0}

    asyncio.run(run())


def test_meta_endpoints():
    async def run():
        async with _client(create_app()) as c:
            langs = (await c.get("/languages")).json()["languages"]
            assert langs == ["json", "python"]
            cfg = (await c.get("/config")).json()
            assert cfg["cache_capacity"] > 0 and cfg["max_file_size"] > 0

    asyncio.run(run())
