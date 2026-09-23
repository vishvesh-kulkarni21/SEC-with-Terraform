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
        path = self._cache_path(url)
        if path.exists() and not refresh:
            return json.loads(path.read_text(encoding="utf-8"))

        self._throttle()
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()

        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
        return data

    def _cache_path(self, url: str) -> Path:
        """Map a URL to a Windows-safe file path inside cache_dir."""
        name = hashlib.sha256(url.encode()).hexdigest()
        return self.cache_dir / f"{name}.json"

    def _throttle(self) -> None:
        """Sleep if the previous network request was less than min_interval ago."""
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.monotonic()
