import os
import json
import re
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf
from cachetools.func import ttl_cache
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from google import genai
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"
DEFAULT_DATA_PERIOD = "1mo"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"
DEFAULT_MSTR_BTC_HOLDINGS = 331200
DEFAULT_SHARES_OUTSTANDING = 200000000
DEFAULT_MSTR_CASH_BALANCE = 0.0
DEFAULT_MSTR_TOTAL_DEBT = 0.0
DEFAULT_MSTR_PREFERRED_STOCK = 0.0
STRATEGY_PAGE_URL = "https://www.strategy.com/strategy"
STRATEGY_MSTR_KPI_API_URL = "https://api.strategy.com/btc/mstrKpiData"
STRATEGY_BITCOIN_KPI_API_URL = "https://api.strategy.com/btc/bitcoinKpis"
STRATEGY_API_TIMEOUT_SECONDS = 10
STRATEGY_SNAPSHOT_CACHE_TTL_SECONDS = 3600
INDICATOR_CACHE_TTL_SECONDS = 3600
NEXT_DATA_PATTERN = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

load_dotenv(BASE_DIR / ".env")


def parse_allowed_origins() -> list[str]:
    raw_origins = os.getenv("ALLOWED_ORIGINS", "")
    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]


app = FastAPI(title="MSTR Premium Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_allowed_origins(),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


class IndicatorData(BaseModel):
    date: str
    mstrPrice: float
    btcPrice: float
    btcValue: float
    enterpriseValue: float
    mnav: float
    mnavPremiumPct: float


def get_mstr_holdings_fallback() -> int:
    return int(os.getenv("MSTR_BTC_HOLDINGS", str(DEFAULT_MSTR_BTC_HOLDINGS)))


def get_float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value == "":
        return default
    return float(raw_value)


def get_mstr_snapshot_fallback() -> dict:
    return {
        "btc_holdings": get_mstr_holdings_fallback(),
        "cash_balance": get_float_env("MSTR_CASH_BALANCE", DEFAULT_MSTR_CASH_BALANCE),
        "total_debt": get_float_env("MSTR_TOTAL_DEBT", DEFAULT_MSTR_TOTAL_DEBT),
        "preferred_stock": get_float_env("MSTR_PREFERRED_STOCK", DEFAULT_MSTR_PREFERRED_STOCK),
        "shares_outstanding": int(get_float_env("MSTR_BASIC_SHARES_OUTSTANDING", DEFAULT_SHARES_OUTSTANDING)),
        "as_of_date": None,
    }


def parse_strategy_next_data(html: str) -> dict:
    match = NEXT_DATA_PATTERN.search(html)
    if not match:
        raise ValueError("Could not find __NEXT_DATA__ in Strategy page HTML.")
    return json.loads(match.group(1))


def parse_number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("$", "").strip()
        if cleaned == "":
            return None
        number = float(cleaned)
        return int(number) if number.is_integer() else number
    return value


@ttl_cache(maxsize=1, ttl=STRATEGY_SNAPSHOT_CACHE_TTL_SECONDS)
def get_strategy_mstr_snapshot() -> dict:
    fallback = get_mstr_snapshot_fallback()

    try:
        page_response = requests.get(STRATEGY_PAGE_URL, headers=REQUEST_HEADERS, timeout=STRATEGY_API_TIMEOUT_SECONDS)
        page_response.raise_for_status()
        next_data = parse_strategy_next_data(page_response.text)
        metric_data = next_data["props"]["pageProps"].get("metricData", [])
        latest_metric = next((item for item in metric_data if item.get("latest")), metric_data[0] if metric_data else {})

        mstr_kpi_response = requests.get(
            STRATEGY_MSTR_KPI_API_URL,
            headers=REQUEST_HEADERS,
            timeout=STRATEGY_API_TIMEOUT_SECONDS,
        )
        mstr_kpi_response.raise_for_status()
        mstr_kpi_rows = mstr_kpi_response.json()
        mstr_kpi = mstr_kpi_rows[0] if mstr_kpi_rows else {}

        bitcoin_kpi_response = requests.get(
            STRATEGY_BITCOIN_KPI_API_URL,
            headers=REQUEST_HEADERS,
            timeout=STRATEGY_API_TIMEOUT_SECONDS,
        )
        bitcoin_kpi_response.raise_for_status()
        bitcoin_kpis = bitcoin_kpi_response.json().get("results", {})

        btc_holdings = parse_number(bitcoin_kpis.get("btcHoldings"))
        total_debt = parse_number(mstr_kpi.get("debt"))
        preferred_stock = parse_number(mstr_kpi.get("pref"))
        cash_balance = latest_metric.get("cash")
        shares_outstanding = latest_metric.get("basic_shares_outstanding")

        snapshot = {
            "btc_holdings": int(btc_holdings) if btc_holdings else fallback["btc_holdings"],
            "cash_balance": float(cash_balance) if cash_balance is not None else fallback["cash_balance"],
            "total_debt": float(total_debt) * 1_000_000 if total_debt is not None else fallback["total_debt"],
            "preferred_stock": float(preferred_stock) * 1_000_000 if preferred_stock is not None else fallback["preferred_stock"],
            "shares_outstanding": int(shares_outstanding) if shares_outstanding else fallback["shares_outstanding"],
            "as_of_date": latest_metric.get("as_of_date"),
        }

        return snapshot
    except Exception:
        return fallback


def build_insight_prompt(recent_data: list[dict]) -> str:
    return f"""
    你是一位專業的加密貨幣與美股分析師。
    以下是 MicroStrategy (MSTR) 最近 7 天的「mNAV」、「mNAV Premium / Discount」與「比特幣價格」數據：
    {recent_data}

    請用繁體中文，給出一段約 100 到 150 字的專業總結。
    請分析這幾天「mNAV變化」和「比特幣價格波動」之間的關聯，並給出當前市場情緒的解讀。
    不要過多廢話，直接給出洞察。
    """


def generate_insight(recent_data: list[dict]) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Gemini service is not configured. Set GEMINI_API_KEY in the deployment environment.",
        )

    model_name = os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
    prompt = build_insight_prompt(recent_data)
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(model=model_name, contents=prompt)
    insight_text = (response.text or "").strip()

    if not insight_text:
        raise ValueError("Gemini returned an empty response.")

    return insight_text


@app.get("/")
def serve_frontend():
    if not INDEX_FILE.exists():
        raise HTTPException(status_code=500, detail="Frontend file index.html is missing.")
    return FileResponse(INDEX_FILE)


@app.get("/healthz")
def healthcheck():
    return {
        "status": "ok",
        "frontend_ready": INDEX_FILE.exists(),
        "ai_configured": bool(os.getenv("GEMINI_API_KEY")),
    }


@ttl_cache(maxsize=8, ttl=INDICATOR_CACHE_TTL_SECONDS)
def load_indicator_data(
    period: str,
    holdings: int,
    cash_balance: float,
    total_debt: float,
    preferred_stock: float,
    shares_outstanding: int,
    snapshot_as_of_date: str | None,
) -> list[dict]:
    try:
        mstr_ticker = yf.Ticker("MSTR")
        btc_ticker = yf.Ticker("BTC-USD")

        mstr_data = mstr_ticker.history(period=period)
        btc_data = btc_ticker.history(period=period)

        if mstr_data.empty or btc_data.empty:
            raise ValueError("No market data returned from yfinance.")
        if shares_outstanding <= 0:
            raise ValueError("Shares outstanding must be positive.")

        mstr_close = mstr_data["Close"]
        btc_close = btc_data["Close"]
        mstr_close.index = mstr_close.index.tz_localize(None).normalize()
        btc_close.index = btc_close.index.tz_localize(None).normalize()

        df = pd.DataFrame({"mstrPrice": mstr_close, "btcPrice": btc_close}).dropna()
        if df.empty:
            raise ValueError("Aligned market data is empty after dropping missing values.")

        df["btcValue"] = holdings * df["btcPrice"]
        if (df["btcValue"] <= 0).any():
            raise ValueError("BTC value must be positive to calculate mNAV.")

        df["marketCap"] = df["mstrPrice"] * shares_outstanding
        df["enterpriseValue"] = df["marketCap"] + total_debt + preferred_stock - cash_balance
        df["mnav"] = df["enterpriseValue"] / df["btcValue"]
        df["mnavPremiumPct"] = (df["mnav"] - 1) * 100
        df["cashBalance"] = cash_balance
        df["totalDebt"] = total_debt
        df["preferredStock"] = preferred_stock
        df["sharesOutstanding"] = shares_outstanding
        df["btcHoldings"] = holdings
        df["snapshotAsOfDate"] = snapshot_as_of_date

        df = df.reset_index()
        df["date"] = df["Date"].dt.strftime("%Y-%m-%d")
        df["mstrPrice"] = df["mstrPrice"].round(2)
        df["btcPrice"] = df["btcPrice"].round(2)
        df["btcValue"] = df["btcValue"].round(2)
        df["marketCap"] = df["marketCap"].round(2)
        df["enterpriseValue"] = df["enterpriseValue"].round(2)
        df["mnav"] = df["mnav"].round(4)
        df["mnavPremiumPct"] = df["mnavPremiumPct"].round(2)
        df["cashBalance"] = df["cashBalance"].round(2)
        df["totalDebt"] = df["totalDebt"].round(2)
        df["preferredStock"] = df["preferredStock"].round(2)

        return df[
            [
                "date",
                "mstrPrice",
                "btcPrice",
                "btcHoldings",
                "sharesOutstanding",
                "cashBalance",
                "totalDebt",
                "preferredStock",
                "snapshotAsOfDate",
                "btcValue",
                "marketCap",
                "enterpriseValue",
                "mnav",
                "mnavPremiumPct",
            ]
        ].to_dict(orient="records")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to fetch indicator data: {exc}") from exc


@app.get("/api/indicator-data")
def get_indicator_data():
    period = os.getenv("DATA_PERIOD", DEFAULT_DATA_PERIOD)
    strategy_snapshot = get_strategy_mstr_snapshot()

    # Return copies so request handlers do not mutate the cached payload by accident.
    return [
        item.copy()
        for item in load_indicator_data(
            period,
            strategy_snapshot["btc_holdings"],
            strategy_snapshot["cash_balance"],
            strategy_snapshot["total_debt"],
            strategy_snapshot["preferred_stock"],
            strategy_snapshot["shares_outstanding"],
            strategy_snapshot["as_of_date"],
        )
    ]


@app.post("/api/insights")
def generate_insights(data: list[IndicatorData]):
    if not data:
        raise HTTPException(status_code=400, detail="Request body must contain indicator data.")

    try:
        recent_data = [item.model_dump() for item in data[-7:]]
        model_name = os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
        insight_text = generate_insight(recent_data)
        return {"insight": insight_text}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to generate AI insights with model '{model_name}': {type(exc).__name__}: {exc}",
        ) from exc
