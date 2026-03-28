import os
from pathlib import Path

import pandas as pd
import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from google import genai
from pydantic import BaseModel
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"
DEFAULT_DATA_PERIOD = "1mo"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_MSTR_BTC_HOLDINGS = 331200
DEFAULT_SHARES_OUTSTANDING = 200000000

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
    navPerShare: float
    premium: float


def get_gemini_api_key() -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Gemini service is not configured. Set GEMINI_API_KEY in the deployment environment.",
        )

    return api_key


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


@app.get("/api/indicator-data")
def get_indicator_data():
    period = os.getenv("DATA_PERIOD", DEFAULT_DATA_PERIOD)
    holdings = int(os.getenv("MSTR_BTC_HOLDINGS", str(DEFAULT_MSTR_BTC_HOLDINGS)))

    try:
        mstr_ticker = yf.Ticker("MSTR")
        btc_ticker = yf.Ticker("BTC-USD")

        mstr_data = mstr_ticker.history(period=period)
        btc_data = btc_ticker.history(period=period)
        shares_outstanding = mstr_ticker.info.get("sharesOutstanding", DEFAULT_SHARES_OUTSTANDING)

        if mstr_data.empty or btc_data.empty:
            raise ValueError("No market data returned from yfinance.")
        if not shares_outstanding:
            raise ValueError("sharesOutstanding is missing from market data.")

        mstr_close = mstr_data["Close"]
        btc_close = btc_data["Close"]
        mstr_close.index = mstr_close.index.tz_localize(None).normalize()
        btc_close.index = btc_close.index.tz_localize(None).normalize()

        df = pd.DataFrame({"mstrPrice": mstr_close, "btcPrice": btc_close}).dropna()
        if df.empty:
            raise ValueError("Aligned market data is empty after dropping missing values.")

        df["navPerShare"] = (holdings * df["btcPrice"]) / shares_outstanding
        df["premium"] = ((df["mstrPrice"] - df["navPerShare"]) / df["navPerShare"]) * 100

        df = df.reset_index()
        df["date"] = df["Date"].dt.strftime("%Y-%m-%d")
        df["mstrPrice"] = df["mstrPrice"].round(2)
        df["btcPrice"] = df["btcPrice"].round(2)
        df["navPerShare"] = df["navPerShare"].round(2)
        df["premium"] = df["premium"].round(2)

        return df[["date", "mstrPrice", "btcPrice", "navPerShare", "premium"]].to_dict(orient="records")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to fetch indicator data: {exc}") from exc


@app.post("/api/insights")
def generate_insights(data: list[IndicatorData]):
    if not data:
        raise HTTPException(status_code=400, detail="Request body must contain indicator data.")

    recent_data = data[-7:]
    prompt = f"""
    你是一位專業的加密貨幣與美股分析師。
    以下是 MicroStrategy (MSTR) 最近 7 天的「溢價率 (Premium to NAV)」與「比特幣價格」數據：
    {recent_data}

    請用繁體中文，給出一段約 100 到 150 字的專業總結。
    請分析這幾天「溢價率趨勢」與「比特幣價格波動」之間的關聯，並給出當前市場情緒的解讀。
    不要過多廢話，直接給出洞察。
    """

    try:
        api_key = get_gemini_api_key()
        model_name = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=model_name, contents=prompt)
        insight_text = (response.text or "").strip()

        if not insight_text:
            raise ValueError("Gemini returned an empty response.")

        return {"insight": insight_text}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to generate AI insights: {exc}") from exc
