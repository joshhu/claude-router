# claude-router

Claude Code 前面的一層本機 proxy。每個 request 先交給決策模型判斷，一般內容原封不動送到 Anthropic（照樣用你原本的模型和登入），判定為受限的內容改送到你指定 IP 上的自架模型。

```
Claude Code（預設模型、登入都不動）
      │  ANTHROPIC_BASE_URL=http://127.0.0.1:8787
      ▼
claude-router ── 決策模型（h2o-lightning-4b，本機 llama.cpp）
      ├─ 一般內容 → api.anthropic.com（header、認證、model 原樣轉送）
      └─ 受限內容 → 自架模型（llama.cpp 等 Anthropic Messages 相容端點）
```

## 運作方式

- **只判斷使用者自己打的字**：取最後一則 user 訊息，去掉 Claude Code 自動塞入的 `<system-reminder>`，不看 tool_result。只有工具結果的續傳 request 不重新判斷。
- **整段 session 黏住**：一旦某個 session 被判成受限，之後所有 request（包括工具迴圈、標題產生）都留在本地，重啟 proxy 也一樣。否則下一輪送回雲端時，整段歷史還是會被帶上去。session 以 Claude Code 送的 `x-claude-code-session-id` header 辨識。
- **送本地前改寫 request**：換 model 名稱；拿掉 thinking 區塊（雲端簽章本地看不懂）；對話中間的 `role: system` 訊息改成 user 的 `<system-reminder>`；拿掉 web_search 這類只有 Anthropic 才有的 server tool 與參數。
- **失敗時不外流**：本地模型連不上就直接回錯誤，絕不改送雲端。決策模型掛掉時預設當成受限處理（`DECISION_ON_ERROR=local`）。
- 相同文字的判斷結果有快取，主對話和標題產生只會問一次。

## 需求

- macOS（Apple Silicon）或 Linux，Python 3.12+、[uv](https://docs.astral.sh/uv/)
- 決策模型：[h2oai/h2o-lightning-4b-gguf](https://huggingface.co/h2oai/h2o-lightning-4b-gguf) 的 Q8_0，需要 llama.cpp **build 11773 以上**（`/v1/systemone` 端點）。Homebrew 的 llama.cpp 可能還沒跟上，可用 GitHub release 的預編譯版。
- 本地模型：支援 Anthropic Messages API（`/v1/messages`）的伺服器，例如 llama.cpp server，且要能處理 Claude Code 的工具呼叫

## 安裝

```bash
uv sync
cp .env.example .env   # 依需求修改

# llama.cpp（以 macOS arm64 為例）
mkdir -p vendor && cd vendor
gh release download b11799 -R ggml-org/llama.cpp -p 'llama-b11799-bin-macos-arm64.tar.gz'
tar -xzf llama-b11799-bin-macos-arm64.tar.gz && mv llama-b11799 llama && cd ..

# 決策模型（約 4.5 GB）
hf download h2oai/h2o-lightning-4b-gguf h2o-lightning-4b-Q8_0.gguf --local-dir models/h2o-lightning-4b
```

## 使用

```bash
scripts/serve-decision.sh     # 決策模型，127.0.0.1:8741
uv run claude-router          # proxy，127.0.0.1:8787

# 另一個終端機
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude
```

Claude Code 用訂閱帳號（OAuth）或 API key 登入都可以，proxy 只是轉手。注意 hook 或外掛啟動的子 Claude 會繼承 `ANTHROPIC_BASE_URL`，也會經過 proxy。

### 管理

```bash
tail -f logs/router.jsonl                          # 每個 request 的去向與決策機率
curl -s localhost:8787/_router/sessions            # 已鎖定在本地的 session
curl -s -X DELETE localhost:8787/_router/sessions/<session-id>   # 解除鎖定
```

### 調整判斷

- `DECISION_THRESHOLD`：h2o-lightning 回傳的是「會被雲端業者限制」的機率，大於等於門檻就送本地。一般程式開發的訊息實測約落在 0.10 到 0.15（M4，單次約 350 ms），請用自己的實際案例調整。
- `DECISION_PROMPT_FILE`：換掉預設的判斷問題，可以寫得更貼近你要分流的內容類型。
- `DECISION_API=chat`：改用一般聊天模型當決策模型（OpenAI 相容 `/v1/chat/completions`，回 `LOCAL` / `CLOUD`）。

## 限制

- 送本地的 session 無法再回到雲端（除非手動解鎖，但解鎖後整段歷史會送上雲端）。
- prompt cache 只在雲端有效，切到本地後那個 session 的成本與速度取決於自架模型。
- Anthropic 官方不支援把 Claude Code 接到非 Claude 模型，本地模型的工具呼叫品質要自行驗證。

## 測試

```bash
uv run pytest
```

## License

MIT
