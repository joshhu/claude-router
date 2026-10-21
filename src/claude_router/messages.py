"""處理 Anthropic Messages request body 的純函式。"""

import copy
import hashlib
import json
import re

REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
SESSION_RE = re.compile(r"session_([0-9a-fA-F-]{36})")


def _block_texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    return [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]


def latest_user_text(body: dict) -> str | None:
    """最後一則 user 訊息裡使用者自己打的字。

    去掉 Claude Code 自動塞的 <system-reminder>，也不看 tool_result；
    只剩工具結果的 request（工具迴圈的續傳）回傳 None。
    """
    for msg in reversed(body.get("messages") or []):
        if msg.get("role") != "user":
            continue
        text = "\n".join(REMINDER_RE.sub("", t) for t in _block_texts(msg.get("content", [])))
        text = text.strip()
        return text or None
    return None


def session_id(headers, body: dict) -> str | None:
    """找出這個 request 屬於哪個 Claude Code session。

    優先用 header，其次是 metadata.user_id 裡的 session_<uuid>（舊版是字串、
    新版是 JSON），都沒有就用第一則 user 訊息的 hash 當替代鍵。
    """
    sid = headers.get("x-claude-code-session-id")
    if sid:
        return sid
    user_id = (body.get("metadata") or {}).get("user_id") or ""
    try:
        parsed = json.loads(user_id)
        if isinstance(parsed, dict) and parsed.get("session_id"):
            return str(parsed["session_id"])
    except (ValueError, TypeError):
        pass
    if m := SESSION_RE.search(user_id):
        return m.group(1)
    messages = body.get("messages") or []
    if messages:
        first = "\n".join(_block_texts(messages[0].get("content", [])))
        return "first-msg-" + hashlib.sha256(first.encode()).hexdigest()[:16]
    return None


def to_local(body: dict, model: str) -> dict:
    """改寫成本地模型能吃的 request。

    - 換 model 名稱
    - 拿掉 thinking 區塊（雲端的簽章本地看不懂，本地的簽章是空字串）
    - 對話中間的 role=system 訊息（Anthropic beta）改成 user 的 <system-reminder>，
      Qwen 等 chat template 只接受開頭的 system
    - 相鄰同角色訊息合併，維持 user/assistant 交替
    - 拿掉 server tools（web_search 這類只有 Anthropic 端才有）
    - 拿掉只有 Anthropic API 認得的參數
    """
    body = copy.deepcopy(body)
    body["model"] = model
    for key in ("thinking", "context_management", "output_config", "metadata", "container", "mcp_servers"):
        body.pop(key, None)
    merged: list[dict] = []
    for msg in body.get("messages") or []:
        content = msg.get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else list(content or [])
        blocks = [b for b in blocks if not (isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking"))]
        role = msg.get("role")
        if role == "system":
            role = "user"
            blocks = [{"type": "text", "text": f"<system-reminder>\n{t}\n</system-reminder>"} for t in _block_texts(blocks)]
        if not blocks:
            continue
        if merged and merged[-1]["role"] == role:
            merged[-1]["content"].extend(blocks)
        else:
            merged.append({"role": role, "content": blocks})
    body["messages"] = merged
    if "tools" in body:
        body["tools"] = [t for t in body["tools"] if "input_schema" in t]
        if not body["tools"]:
            body.pop("tools")
            body.pop("tool_choice", None)
    return body
