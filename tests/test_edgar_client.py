"""EdgarClient tests. A fake session stands in for the network, so no SEC calls are made."""

import pytest
import requests

from equity_research.data.edgar_client import EdgarClient

URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"{self.status}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, response):
        self.response, self.calls, self.headers = response, 0, {}

    def get(self, url, timeout):
        self.calls += 1
        return self.response


def make_client(tmp_path, response):
    client = EdgarClient("Test User test@example.com", tmp_path, min_interval=0)
    client.session = FakeSession(response)
    return client


def test_second_call_is_served_from_cache(tmp_path):
    client = make_client(tmp_path, FakeResponse({"entityName": "Apple Inc."}))
    assert client.get_json(URL) == {"entityName": "Apple Inc."}
    assert client.get_json(URL) == {"entityName": "Apple Inc."}
    assert client.session.calls == 1


def test_refresh_bypasses_cache(tmp_path):
    client = make_client(tmp_path, FakeResponse({"v": 1}))
    client.get_json(URL)
    client.get_json(URL, refresh=True)
    assert client.session.calls == 2


def test_error_response_is_not_cached(tmp_path):
    client = make_client(tmp_path, FakeResponse({}, status=403))
    with pytest.raises(requests.HTTPError):
        client.get_json(URL)
    assert list(tmp_path.iterdir()) == []


def test_user_agent_header_is_set(tmp_path):
    client = EdgarClient("Test User test@example.com", tmp_path)
    assert client.session.headers["User-Agent"] == "Test User test@example.com"
