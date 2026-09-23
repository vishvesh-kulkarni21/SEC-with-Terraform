"""HTTP client for SEC EDGAR: User-Agent header, rate limiting, and a disk cache.

Cache policy: a cached response is valid forever unless refresh=True.
The cache doubles as a frozen snapshot so evaluation runs are reproducible.
"""

import hashlib
import json
import os
import time
from pathlib import Path

import requests


class EdgarClient:
    def __init__(self, user_agent: str, cache_dir: Path, min_interval: float = 0.11):
        """Create one requests.Session with the User-Agent header set.

        min_interval: minimum seconds between network requests (0.11 keeps us under 10/s).
        """
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._last_request = 0.0

    def get_json(self, url: str, refresh: bool = False) -> dict:
        """Return the JSON at url, from cache if present (unless refresh), else from SEC."""
        path = self._cache_path(url, ".json")
        if path.exists() and not refresh:
            return json.loads(path.read_text(encoding="utf-8"))

        data = self._fetch(url).json()
        self._write(path, json.dumps(data))
        return data

    def get_text(self, url: str, refresh: bool = False) -> str:
        """Return the body at url as text (e.g. a filing's HTML), cached like get_json."""
        path = self._cache_path(url, ".txt")
        if path.exists() and not refresh:
            return path.read_text(encoding="utf-8")

        text = self._fetch(url).text
        self._write(path, text)
        return text

    def _fetch(self, url: str) -> requests.Response:
        self._throttle()
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response

    @staticmethod
    def _write(path: Path, content: str) -> None:
        """Write atomically so an interrupted run never leaves a half-written cache file."""
        tmp = path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)

    def _cache_path(self, url: str, suffix: str) -> Path:
        """Map a URL to a Windows-safe file path inside cache_dir."""
        name = hashlib.sha256(url.encode()).hexdigest()
        return self.cache_dir / f"{name}{suffix}"

    def _throttle(self) -> None:
        """Sleep if the previous network request was less than min_interval ago."""
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.monotonic()
