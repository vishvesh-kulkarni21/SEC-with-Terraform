from types import SimpleNamespace

from fastapi.testclient import TestClient

from equity_research import api


def test_health():
    assert TestClient(api.app).get("/health").json() == {"status": "ok"}


def test_rejects_bad_input():
    client = TestClient(api.app)
    assert client.post("/research", json={"ticker": "AAPL; rm -rf", "question": "revenue?"}).status_code == 422
    assert client.post("/debate", json={"ticker": "AAPL", "plant": "bogus"}).status_code == 422


def test_debate_returns_pdf(monkeypatch):
    from tests.fakes import ScriptedChatModel
    from tests.test_debate import GOOD, make_tools, researcher_turns

    both_pass = {"verdicts": [{"index": 0, "supported": True, "reason": "reported figure"},
                              {"index": 1, "supported": True, "reason": "reported figure"}]}
    model = ScriptedChatModel(researcher_turns() + [GOOD, both_pass, GOOD, both_pass])
    monkeypatch.setattr(api, "_settings", lambda: SimpleNamespace(vertex_project=None))
    monkeypatch.setattr(api, "make_chat_model", lambda settings: model)
    monkeypatch.setattr(api, "build_tools", lambda settings, strategy: make_tools())

    r = TestClient(api.app).post("/debate", json={"ticker": "AAPL", "format": "pdf"})
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    cid = r.headers["x-conversation-id"]
    assert cid and f"AAPL_{cid}.pdf" in r.headers["content-disposition"]
