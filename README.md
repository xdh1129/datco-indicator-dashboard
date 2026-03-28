# MSTR Premium Dashboard

這是一個使用 FastAPI 提供資料 API 與靜態前端頁面的專案，會抓取 `MSTR` 與 `BTC-USD` 市場資料，並可選擇性地透過 Gemini 產生趨勢摘要。

## 環境變數

可參考 `.env.example`：

- `GEMINI_API_KEY`: 啟用 `/api/insights` 所需；未設定時，AI 分析端點會回傳 `503`。
- `GEMINI_MODEL`: 可選，預設 `gemini-flash-latest`。
- `ALLOWED_ORIGINS`: 可選，用逗號分隔允許的前端來源。若前後端同站部署，可不設定。
- `DATA_PERIOD`: 可選，預設 `1mo`。
- `MSTR_BTC_HOLDINGS`: 可選，預設 `331200`。

目前專案仍使用 `google-generativeai`，它已被官方標示為 deprecated。現在可以部署，但若要長期維護，建議後續改遷移到 `google.genai`。

## 本機執行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

開啟 `http://127.0.0.1:8000`。

## 健康檢查

- `GET /healthz`

## 雲端部署

這個專案已經包含 `Dockerfile`，可直接部署到支援 Docker 的平台。

- Build: 平台讀取 `Dockerfile`
- Run: 容器會使用 `uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}`
- 必設環境變數: 若要啟用 AI 摘要，請在平台設定 `GEMINI_API_KEY`
