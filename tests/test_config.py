import pytest

from equity_research.config import ConfigError, load_settings


def test_missing_user_agent_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(ConfigError):
        load_settings(env_file=tmp_path / "missing.env")


def test_reads_user_agent_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    env = tmp_path / ".env"
    env.write_text('SEC_USER_AGENT="Test User test@example.com"\n', encoding="utf-8")
    assert load_settings(env_file=env).sec_user_agent == "Test User test@example.com"
