import hashlib
import logging
import unicodedata
from collections import OrderedDict
from pathlib import Path

import httpx

from .config import Config

log = logging.getLogger("claude_router.decision")

CLOUD = "cloud"
LOCAL = "local"

# systemone 是 yes/no 題，問的是「會不會被雲端業者限制」
DEFAULT_QUESTION = (
    "Would a commercial cloud AI provider's usage policy likely restrict or refuse "
    "this request sent to a coding assistant?"
)

# chat 模式給一般聊天模型用，要它只回一個字
DEFAULT_CHAT_PROMPT = """You are a routing classifier in front of a coding assistant.
Decide whether the user's message would likely be restricted or refused by a
commercial cloud AI provider's usage policy.

Answer with exactly one word:
LOCAL  - the message would likely be restricted or refused
CLOUD  - anything else (normal coding, questions, writing, etc.)"""

# h2o-lightning 的 state 上限約 32K token，太長的貼上內容只留頭尾
MAX_STATE_CHARS = 24000


def parse_verdict(text: str) -> str:
    """chat 模式：輸出只要出現 LOCAL 就判為本地，其餘都算雲端。"""
    return LOCAL if "LOCAL" in text.upper() else CLOUD


def clip(text: str, limit: int = MAX_STATE_CHARS) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n…\n" + text[-half:]


class Decider:
    def __init__(self, config: Config, client: httpx.AsyncClient, cache_size: int = 512):
        self.config = config
        self.client = client
        self.cache: OrderedDict[str, tuple[str, str]] = OrderedDict()
        self.cache_size = cache_size
        prompt_file = config.decision_prompt_file
        default = DEFAULT_QUESTION if config.decision_api == "systemone" else DEFAULT_CHAT_PROMPT
        self.prompt = Path(prompt_file).read_text().strip() if prompt_file else default

    @property
    def enabled(self) -> bool:
        return bool(self.config.decision_url)

    async def classify(self, text: str) -> tuple[str, str]:
        """回傳 (route, reason)。同一段文字只問一次（主對話和標題產生會送同一句）。"""
        if not self.enabled:
            return CLOUD, "decision-disabled"
        key = hashlib.sha256(text.encode()).hexdigest()
        if key in self.cache:
            self.cache.move_to_end(key)
            route, detail = self.cache[key]
            return route, f"decision-cached {detail}".strip()
        try:
            if self.config.decision_api == "systemone":
                route, detail = await self._ask_systemone(text)
            else:
                route, detail = await self._ask_chat(text), ""
        except Exception as exc:  # noqa: BLE001 - 任何失敗都照設定的方向處理
            log.warning("decision model failed: %r", exc)
            return self.config.decision_on_error, f"decision-error {exc!r}"
        self.cache[key] = (route, detail)
        if len(self.cache) > self.cache_size:
            self.cache.popitem(last=False)
        return route, f"decision {detail}".strip()

    def _headers(self) -> dict:
        if self.config.decision_api_key:
            return {"authorization": f"Bearer {self.config.decision_api_key}"}
        return {}

    async def _ask_systemone(self, text: str) -> tuple[str, str]:
        # h2o-lightning 的 llama.cpp 版不會自動做 NFC，要自己正規化
        nfc = lambda s: unicodedata.normalize("NFC", s)  # noqa: E731
        resp = await self.client.post(
            f"{self.config.decision_url}/v1/systemone",
            headers=self._headers(),
            timeout=self.config.decision_timeout,
            json={
                "state": {"user_message": nfc(clip(text))},
                "questions": {
                    "restricted": {
                        "type": "noul",
                        "instructions": nfc(self.prompt),
                        "criteria": {"true": "yes", "false": "no"},
                    }
                },
            },
        )
        resp.raise_for_status()
        p = float(resp.json()["answers"]["restricted"]["noul"])
        route = LOCAL if p >= self.config.decision_threshold else CLOUD
        return route, f"p={p:.3f}"

    async def _ask_chat(self, text: str) -> str:
        resp = await self.client.post(
            f"{self.config.decision_url}/v1/chat/completions",
            headers=self._headers(),
            timeout=self.config.decision_timeout,
            json={
                "model": self.config.decision_model,
                "messages": [
                    {"role": "system", "content": self.prompt},
                    {"role": "user", "content": clip(text)},
                ],
                "max_tokens": 16,
                "temperature": 0,
                # Qwen 系列關掉 thinking，分類只要一個字
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"].get("content") or ""
        return parse_verdict(content)
