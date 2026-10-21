import json

from claude_router.decision import CLOUD, LOCAL, clip, parse_verdict
from claude_router.messages import latest_user_text, session_id, to_local


def test_latest_user_text_strips_system_reminders():
    body = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "<system-reminder>\nCLAUDE.md stuff\n</system-reminder>"},
        {"type": "text", "text": "幫我寫個爬蟲"},
    ]}]}
    assert latest_user_text(body) == "幫我寫個爬蟲"


def test_latest_user_text_none_for_tool_results_only():
    body = {"messages": [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
    ]}
    assert latest_user_text(body) is None


def test_session_id_prefers_header():
    assert session_id({"x-claude-code-session-id": "abc"}, {}) == "abc"


def test_session_id_from_json_metadata():
    meta = {"user_id": json.dumps({"device_id": "d", "session_id": "s-123"})}
    assert session_id({}, {"metadata": meta}) == "s-123"


def test_session_id_from_legacy_metadata():
    uid = "user_abc_account_x_session_12345678-1234-1234-1234-123456789abc"
    assert session_id({}, {"metadata": {"user_id": uid}}) == "12345678-1234-1234-1234-123456789abc"


def test_session_id_falls_back_to_first_message_hash():
    body = {"messages": [{"role": "user", "content": "hello"}]}
    sid = session_id({}, body)
    assert sid.startswith("first-msg-") and sid == session_id({}, body)


def test_to_local_rewrites_body():
    body = {
        "model": "claude-opus-5-5",
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "medium"},
        "metadata": {"user_id": "x"},
        "tools": [
            {"name": "Bash", "input_schema": {"type": "object"}},
            {"type": "web_search_20250305", "name": "web_search"},
        ],
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "system", "content": [{"type": "text", "text": "# Environment"}]},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "hmm", "signature": "sig"},
                {"type": "text", "text": "hello"},
            ]},
        ],
    }
    out = to_local(body, "qwen")
    assert out["model"] == "qwen"
    assert not {"thinking", "output_config", "metadata"} & out.keys()
    assert [t["name"] for t in out["tools"]] == ["Bash"]
    # system 訊息併進前一則 user，維持交替
    assert [m["role"] for m in out["messages"]] == ["user", "assistant"]
    assert "<system-reminder>\n# Environment\n</system-reminder>" in out["messages"][0]["content"][1]["text"]
    assert out["messages"][1]["content"] == [{"type": "text", "text": "hello"}]
    # 原本的 body 不能被改到
    assert body["model"] == "claude-opus-5-5"


def test_parse_verdict():
    assert parse_verdict("LOCAL") == LOCAL
    assert parse_verdict(" cloud\n") == CLOUD
    assert parse_verdict("") == CLOUD


def test_clip_keeps_head_and_tail():
    text = "a" * 100 + "b" * 100
    out = clip(text, 20)
    assert out.startswith("a" * 10) and out.endswith("b" * 10)
