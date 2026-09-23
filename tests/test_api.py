from fastapi.testclient import TestClient

from equity_research import api


def test_health():
    assert TestClient(api.app).get("/health").json() == {"status": "ok"}


def test_rejects_bad_input():
    client = TestClient(api.app)
    assert client.post("/research", json={"ticker": "AAPL; rm -rf", "question": "revenue?"}).status_code == 422
    assert client.post("/debate", json={"ticker": "AAPL", "plant": "bogus"}).status_code == 422
