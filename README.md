# MSTR Premium Dashboard

這是一個使用 FastAPI 提供資料 API 與靜態前端頁面的專案，會抓取 `MSTR` 與 `BTC-USD` 市場資料，並可選擇性地透過 Gemini 產生趨勢摘要。

## 環境變數

可參考 `.env.example`：

- `GEMINI_API_KEY`: 啟用 `/api/insights` 所需；未設定時，AI 分析端點會回傳 `503`。
- `GEMINI_MODEL`: 可選，預設 `gemini-2.5-flash`。若使用 preview 模型，請填裸 model id，例如 `gemini-3-flash-preview`，不要加 `models/` 前綴。
- `ALLOWED_ORIGINS`: 可選，用逗號分隔允許的前端來源。若前後端同站部署，可不設定。
- `DATA_PERIOD`: 可選，預設 `1mo`。
- `MSTR_BTC_HOLDINGS`: 可選，當 Strategy 官方資料來源暫時失敗時使用的 fallback 值，預設 `331200`。
- `MSTR_BASIC_SHARES_OUTSTANDING`: 可選，當 Strategy 官方資料來源暫時失敗時使用的 fallback 股數，預設 `200000000`。
- `MSTR_CASH_BALANCE`: 可選，當 Strategy 官方資料來源暫時失敗時使用的 fallback 現金部位，單位為美元，預設 `0`。
- `MSTR_TOTAL_DEBT`: 可選，當 Strategy 官方資料來源暫時失敗時使用的 fallback 總負債，單位為美元，預設 `0`。
- `MSTR_PREFERRED_STOCK`: 可選，當 Strategy 官方資料來源暫時失敗時使用的 fallback 特別股面額，單位為美元，預設 `0`。

`/api/indicator-data` 目前使用混合資料來源：

- `MSTR` / `BTC-USD` 歷史日線價格來自 `yfinance`
- `btcHoldings`、`debt`、`pref`、`cash`、`basic_shares_outstanding` 來自 Strategy 官方頁面與 API snapshot

`enterpriseValue` 的計算為：

```text
EV = MarketCap + Debt + Pref - Cash
```

`/api/indicator-data` 會額外回傳：

- `marketCap`
- `btcValue`
- `enterpriseValue`
- `mnav`
- `mnavPremiumPct`
- `snapshotAsOfDate`
- `btcHoldings`
- `sharesOutstanding`
- `cashBalance`
- `totalDebt`
- `preferredStock`

其中：

```text
mNAV = Enterprise Value / BTC Value
```

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
