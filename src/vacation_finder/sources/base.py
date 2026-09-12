"""Shared plumbing for price sources: retries, rate limiting, error types."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class SourceError(RuntimeError):
    """A price source failed in a way the caller should log and step over."""


class SourceUnavailable(SourceError):
    """The source is not configured (missing credentials) or not importable."""


class RateLimiter:
    """Crude minimum-interval limiter. Amadeus's free tier caps at ~10 req/s."""

    def __init__(self, min_interval: float = 0.15) -> None:
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    limiter: RateLimiter | None = None,
    attempts: int = 4,
    timeout: float = 30.0,
    **kwargs: Any,
) -> requests.Response:
    """Issue a request, retrying transient failures with exponential backoff."""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        if limiter:
            limiter.wait()
        try:
            response = session.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            last_exc = exc
        else:
            if response.status_code not in RETRY_STATUS:
                return response
            last_exc = SourceError(f"{response.status_code} from {url}: {response.text[:300]}")

        if attempt < attempts - 1:
            delay = (2**attempt) + random.uniform(0, 0.5)
            log.warning("retrying %s %s in %.1fs (%s)", method, url, delay, last_exc)
            time.sleep(delay)

    raise SourceError(f"{method} {url} failed after {attempts} attempts: {last_exc}")
