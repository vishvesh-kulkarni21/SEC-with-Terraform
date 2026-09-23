"""Load settings from environment variables, with an optional .env file.

Real environment variables always win over values in .env.
"""

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    """Raised when a required setting is missing."""


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=value lines, # comments, optional quotes."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    sec_user_agent: str
    cache_dir: Path
    # Model access. Exact model IDs are pinned here so eval runs are comparable.
    gemini_api_key: str | None = None
    vertex_project: str | None = None  # set on Cloud Run to use Vertex AI instead of AI Studio
    vertex_location: str = "us-central1"
    embedding_model: str = "gemini-embedding-001"
    chat_model: str = "gemini-2.5-flash"


def load_settings(env_file: Path | None = None) -> Settings:
    _load_dotenv(env_file or PROJECT_ROOT / ".env")

    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not user_agent:
        raise ConfigError(
            "SEC_USER_AGENT is not set. Copy .env.example to .env and fill it in. "
            "SEC requires a User-Agent with your name and email."
        )

    cache_dir = Path(os.environ.get("CACHE_DIR", ".cache"))
    if not cache_dir.is_absolute():
        cache_dir = PROJECT_ROOT / cache_dir

    return Settings(
        sec_user_agent=user_agent,
        cache_dir=cache_dir,
        gemini_api_key=os.environ.get("GEMINI_API_KEY") or None,
        vertex_project=os.environ.get("VERTEX_PROJECT") or None,
        vertex_location=os.environ.get("VERTEX_LOCATION", "us-central1"),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "gemini-embedding-001"),
        chat_model=os.environ.get("CHAT_MODEL", "gemini-2.5-flash"),
    )
