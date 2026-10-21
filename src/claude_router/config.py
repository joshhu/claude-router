import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    # 一般內容原樣轉送的目的地
    cloud_url: str
    # 受限內容改送的本地模型（需支援 Anthropic Messages API，例如 llama.cpp server）
    local_url: str
    local_model: str
    local_api_key: str
    # 決策模型；留空代表一律走雲端
    # systemone：h2o-lightning 這類 decision model 的 /v1/systemone（回傳機率）
    # chat：一般模型的 OpenAI 相容 /v1/chat/completions（回 LOCAL / CLOUD）
    decision_url: str
    decision_api: str
    decision_threshold: float
    decision_model: str
    decision_api_key: str
    decision_timeout: float
    decision_prompt_file: str
    # 決策模型掛掉時要往哪送：cloud 或 local
    decision_on_error: str
    state_file: Path
    log_file: Path
    log_bodies: bool

    @classmethod
    def from_env(cls) -> "Config":
        on_error = _env("DECISION_ON_ERROR", "local").lower()
        if on_error not in ("cloud", "local"):
            raise ValueError("DECISION_ON_ERROR 只能是 cloud 或 local")
        api = _env("DECISION_API", "systemone").lower()
        if api not in ("systemone", "chat"):
            raise ValueError("DECISION_API 只能是 systemone 或 chat")
        return cls(
            host=_env("ROUTER_HOST", "127.0.0.1"),
            port=int(_env("ROUTER_PORT", "8787")),
            cloud_url=_env("CLOUD_URL", "https://api.anthropic.com").rstrip("/"),
            local_url=_env("LOCAL_URL").rstrip("/"),
            local_model=_env("LOCAL_MODEL", "local"),
            local_api_key=_env("LOCAL_API_KEY"),
            decision_url=_env("DECISION_URL").rstrip("/"),
            decision_api=api,
            decision_threshold=float(_env("DECISION_THRESHOLD", "0.5")),
            decision_model=_env("DECISION_MODEL", "decision"),
            decision_api_key=_env("DECISION_API_KEY"),
            decision_timeout=float(_env("DECISION_TIMEOUT", "30")),
            decision_prompt_file=_env("DECISION_PROMPT_FILE"),
            decision_on_error=on_error,
            state_file=Path(_env("STATE_FILE", "state/sessions.json")),
            log_file=Path(_env("LOG_FILE", "logs/router.jsonl")),
            log_bodies=_env("LOG_BODIES") in ("1", "true", "yes"),
        )
