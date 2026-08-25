import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp


RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class HttpRequestError(RuntimeError):
    """HTTP-запит не вдалося виконати після контрольованих повторів."""


@dataclass(frozen=True)
class HttpTextResponse:
    status: int
    text: str


class HttpClient:
    def __init__(
            self,
            *,
            connect_timeout: float = 5,
            read_timeout: float = 15,
            total_timeout: float = 20,
            max_attempts: int = 3,
            backoff_base: float = 0.5,
            concurrency: int = 4,
            min_interval: float = 0.1,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")

        self._timeout = aiohttp.ClientTimeout(
            total=total_timeout,
            connect=connect_timeout,
            sock_read=read_timeout,
        )
        self._max_attempts = max_attempts
        self._backoff_base = max(0.0, backoff_base)
        self._min_interval = max(0.0, min_interval)
        self._semaphore = asyncio.Semaphore(concurrency)
        self._rate_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._next_request_at = 0.0
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is not None and not self._session.closed:
            return
        async with self._start_lock:
            if self._session is None or self._session.closed:
                connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
                self._session = aiohttp.ClientSession(
                    timeout=self._timeout,
                    connector=connector,
                    headers={"User-Agent": "TNTU-Schedule-Bot/1.4"},
                )

    async def close(self) -> None:
        async with self._start_lock:
            if self._session is not None and not self._session.closed:
                await self._session.close()
            self._session = None

    async def _wait_for_rate_limit(self) -> None:
        if self._min_interval <= 0:
            return
        loop = asyncio.get_running_loop()
        async with self._rate_lock:
            now = loop.time()
            delay = max(0.0, self._next_request_at - now)
            if delay:
                await asyncio.sleep(delay)
                now = loop.time()
            self._next_request_at = now + self._min_interval

    async def _wait_before_retry(self, attempt: int, retry_after: str | None = None) -> None:
        delay = self._backoff_base * (2 ** (attempt - 1))
        if retry_after:
            try:
                delay = max(delay, min(float(retry_after), 30.0))
            except ValueError:
                pass
        if delay:
            await asyncio.sleep(delay)

    async def request_text(self, method: str, url: str, **kwargs: Any) -> HttpTextResponse:
        await self.start()
        session = self._session
        if session is None:
            raise HttpRequestError("HTTP client is not available")

        method = method.upper()
        for attempt in range(1, self._max_attempts + 1):
            retry_after = None
            try:
                async with self._semaphore:
                    await self._wait_for_rate_limit()
                    async with session.request(method, url, **kwargs) as response:
                        body = await response.text()
                        result = HttpTextResponse(status=response.status, text=body)
                        retry_after = response.headers.get("Retry-After")

                if result.status not in RETRYABLE_STATUSES or attempt == self._max_attempts:
                    if result.status in RETRYABLE_STATUSES:
                        logging.warning(
                            "HTTP %s %s завершився статусом %s після %s спроб",
                            method, url, result.status, attempt,
                        )
                    return result

                logging.warning(
                    "HTTP %s %s повернув тимчасовий статус %s; повтор %s/%s",
                    method, url, result.status, attempt + 1, self._max_attempts,
                )
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                if attempt == self._max_attempts:
                    logging.error(
                        "HTTP %s %s не виконано після %s спроб: %s",
                        method, url, attempt, type(error).__name__,
                    )
                    raise HttpRequestError(f"{method} {url} failed") from error
                logging.warning(
                    "HTTP %s %s тимчасово недоступний (%s); повтор %s/%s",
                    method, url, type(error).__name__, attempt + 1, self._max_attempts,
                )

            await self._wait_before_retry(attempt, retry_after)

        raise HttpRequestError(f"{method} {url} failed")


http_client = HttpClient()
