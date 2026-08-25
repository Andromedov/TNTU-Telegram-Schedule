import asyncio
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("BOT_TOKEN", "test-token")

from infrastructure.http_client import HttpClient, HttpRequestError  # noqa: E402


class FakeResponse:
    def __init__(self, status: int, text: str, headers: dict | None = None):
        self.status = status
        self._text = text
        self.headers = headers or {}

    async def text(self):
        return self._text


class FakeRequestContext:
    def __init__(self, result):
        self.result = result

    async def __aenter__(self):
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


class FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeRequestContext(self.results.pop(0))

    async def close(self):
        self.closed = True


class HttpClientTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self, results, *, max_attempts=3):
        client = HttpClient(
            max_attempts=max_attempts,
            backoff_base=0,
            concurrency=2,
            min_interval=0,
        )
        session = FakeSession(results)
        client._session = session
        return client, session

    async def test_reuses_one_session_for_multiple_requests(self):
        client, session = self.make_client(
            [
                FakeResponse(200, "first"),
                FakeResponse(200, "second"),
            ]
        )

        first = await client.request_text("get", "https://example.com/one")
        second = await client.request_text("post", "https://example.com/two", data={"x": "y"})

        self.assertEqual(("first", "second"), (first.text, second.text))
        self.assertEqual(2, len(session.calls))
        self.assertEqual(("GET", "POST"), (session.calls[0][0], session.calls[1][0]))

    async def test_retries_temporary_status_and_returns_success(self):
        client, session = self.make_client(
            [
                FakeResponse(503, "busy"),
                FakeResponse(200, "ok"),
            ]
        )

        response = await client.request_text("GET", "https://example.com")

        self.assertEqual(200, response.status)
        self.assertEqual("ok", response.text)
        self.assertEqual(2, len(session.calls))

    async def test_raises_after_network_retries_are_exhausted(self):
        client, session = self.make_client(
            [
                asyncio.TimeoutError(),
                asyncio.TimeoutError(),
            ],
            max_attempts=2,
        )

        with self.assertRaises(HttpRequestError):
            await client.request_text("GET", "https://example.com")

        self.assertEqual(2, len(session.calls))

    async def test_close_releases_session_and_is_idempotent(self):
        client, session = self.make_client([])

        await client.close()
        await client.close()

        self.assertTrue(session.closed)
        self.assertIsNone(client._session)
