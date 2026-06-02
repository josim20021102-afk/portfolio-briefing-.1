from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Iterable
from urllib.parse import quote_plus

import feedparser
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from dotenv import load_dotenv

os.environ.setdefault("MPLCONFIGDIR", os.path.join(os.getcwd(), ".matplotlib"))

from pykrx import stock

KST = dt.timezone(dt.timedelta(hours=9), name="KST")

PORTFOLIO = {
    "삼성전자우": {"kind": "kr", "ticker": "005935", "query_ko": "삼성전자우 OR 삼성전자"},
    "알파벳A": {"kind": "us", "ticker": "GOOGL", "query_ko": "알파벳 구글 GOOGL", "query_en": "Alphabet Google GOOGL"},
    "JEPQ": {"kind": "us", "ticker": "JEPQ", "query_ko": "JEPQ ETF", "query_en": "JEPQ ETF"},
    "NVDL": {"kind": "us", "ticker": "NVDL", "query_ko": "NVDL ETF 엔비디아", "query_en": "NVDL ETF Nvidia"},
    "TQQQ": {"kind": "us", "ticker": "TQQQ", "query_ko": "TQQQ ETF 나스닥", "query_en": "TQQQ ETF Nasdaq"},
}

WATCHLIST = {
    "NVDA": "엔비디아", "TSLA": "테슬라", "MSFT": "마이크로소프트",
    "AMZN": "아마존", "META": "메타", "AAPL": "애플",
    "AMD": "AMD", "PLTR": "팔란티어", "005930": "삼성전자",
    "000660": "SK하이닉스", "035420": "NAVER", "051910": "LG화학",
}

WATCHLIST_KR = {"005930", "000660", "035420", "051910"}

@dataclass
class Price:
    name: str
    ticker: str
    close: float
    currency: str
    date: dt.date

@dataclass
class NewsItem:
    title: str
    source: str
    link: str
    published: dt.datetime | None
    summary: str = ""

@dataclass
class TechnicalSignal:
    ticker: str
    name: str
    close: float
    rsi: float | None
    ma5: float | None
    ma20: float | None
    change_pct: float | None
    volume_ratio: float | None

def today_kst() -> dt.date:
    explicit = os.getenv("BRIEFING_DATE", "").strip()
    if explicit:
        return dt.date.fromisoformat(explicit)
    return dt.datetime.now(KST).date()

def clean_text(value: str) -> str:
    value = BeautifulSoup(html.unescape(value or ""), "html.parser").get_text(" ")
    return " ".join(value.split())

def fetch_kr_close(name: str, ticker: str, as_of: dt.date) -> Price:
    start = (as_of - dt.timedelta(days=14)).strftime("%Y%m%d")
    end = as_of.strftime("%Y%m%d")
    frame = stock.get_market_ohlcv_by_date(start, end, ticker)
    if frame.empty:
        raise RuntimeError(f"{name}({ticker})의 최근 종가를 찾지 못했습니다.")
    row = frame.dropna().iloc[-1]
    close_date = frame.dropna().index[-1].date()
    return Price(name=name, ticker=ticker, close=float(row["종가"]), currency="KRW", date=close_date)

def fetch_us_close(name: str, ticker: str) -> Price:
    hist = yf.Ticker(ticker).history(period="10d", auto_adjust=False)
    if hist.empty:
        raise RuntimeError(f"{name}({ticker})의 최근 종가를 찾지 못했습니다.")
    hist = hist.dropna(subset=["Close"])
    row = hist.iloc[-1]
    close_date = hist.index[-1].date()
    return Price(name=name, ticker=ticker, close=float(row["Close"]), currency="USD", date=close_date)

def fetch_prices(as_of: dt.date) -> list[Price]:
    prices: list[Price] = []
    for name, meta in PORTFOLIO.items():
        if meta["kind"] == "kr":
            prices.append(fetch_kr_close(name, meta["ticker"], as_of))
        else:
            prices.append(fetch_us_close(name, meta["ticker"]))
    return prices

def parse_feed_datetime(entry) -> dt.datetime | None:
    raw = getattr(entry, "published", "") or getattr(entry, "updated", "")
    if not raw: return None
    try: return parsedate_to_datetime(raw).astimezone(KST)
    except Exception: return None

def google_news(query: str, lang: str, country: str, limit: int, as_of: dt.date) -> list[NewsItem]:
    url = f"https://news.google.com/rss/search?q={quote_plus(query + ' when:1d')}&hl={lang}-{country}&gl={country}&ceid={country}:{lang}"
    feed = feedparser.parse(url)
    strict_items, fallback_items = [], []
    for entry in feed.entries:
        published = parse_feed_datetime(entry)
        source = clean_text(getattr(getattr(entry, "source", None), "title", "") or "Google News")
        title = clean_text(entry.title)
        suffix = f" - {source}"
        if title.endswith(suffix): title = title[: -len(suffix)]
        item = NewsItem(title=title, source=source, link=getattr(entry, "link", ""), published=published, summary=clean_text(getattr(entry, "summary", "")))
        if published and published.date() == as_of: strict_items.append(item)
        fallback_items.append(item)
    items = dedupe_news(strict_items)
    if len(items) < limit:
        items.extend(x for x in dedupe_news(fallback_items) if x.link not in {i.link for i in items})
    return dedupe_news(items)[:limit]

def naver_news(query: str, limit: int, as_of: dt.date) -> list[NewsItem]:
    client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
    client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret: return []
    response = requests.get("https://openapi.naver.com/v1/search/news.json", params={"query": query, "display": 30, "sort": "date"}, headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}, timeout=20)
    if not response.ok: return []
    items = []
    for item in response.json().get("items", []):
        published = parsedate_to_datetime(item["pubDate"]).astimezone(KST)
        if published.date() != as_of: continue
        items.append(NewsItem(title=clean_text(item.get("title", "")), source="네이버 뉴스", link=item.get("originallink") or item.get("link", ""), published=published, summary=clean_text(item.get("description", ""))))
        if len(items) >= limit: break
    return dedupe_news(items)[:limit]

def dedupe_news(items: Iterable[NewsItem]) -> list[NewsItem]:
    seen, result = set(), []
    for item in items:
        key = item.link or item.title
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result

def fetch_domestic_news(as_of: dt.date) -> list[NewsItem]:
    queries = [meta["query_ko"] for meta in PORTFOLIO.values()]
    items = []
    for portfolio_query in queries:
        items.extend(naver_news(portfolio_query, 1, as_of))
        if len(dedupe_news(items)) >= 3: break
    if len(items) < 3:
        for portfolio_query in queries:
            items.extend(google_news(portfolio_query, "ko", "KR", 1, as_of))
            if len(dedupe_news(items)) >= 3: break
    return dedupe_news(items)[:3]

def fetch_global_news(as_of: dt.date) -> list[NewsItem]:
    items = []
    for meta in PORTFOLIO.values():
        query = meta.get("query_en", meta["ticker"])
        items.extend(google_news(query, "en", "US", 1, as_of))
        if len(dedupe_news(items)) >= 3: break
    return dedupe_news(items)[:3]

def fetch_market_outlook(as_of: dt.date) -> list[NewsItem]:
    queries, items = ["코스피 전망", "나스닥 전망", "금리 환율 전망"], []
    for query in queries: items.extend(naver_news(query, 1, as_of))
    if len(items) < 3:
        for query in queries:
            items.extend(google_news(query, "ko", "KR", 1, as_of))
            if len(dedupe_news(items)) >= 3: break
    return dedupe_news(items)[:3]

def translate_global_news(items: list[NewsItem]) -> list[NewsItem]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not items: return items
    if not api_key and gemini_key: return translate_global_news_with_gemini(items, gemini_key)
    if not api_key: return items

    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
    prompt_items = [{"idx": i, "title": x.title, "summary": x.summary} for i, x in enumerate(items)]
    try:
        response = requests.post("https://api.openai.com/v1/responses", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json={"model": model, "input": [{"role": "system", "content": "Translate and summarize financial news into concise Korean. Return only JSON."}, {"role": "user", "content": f"Return array of objects with idx, title_ko, summary_ko. Items: {json.dumps(prompt_items, ensure_ascii=False)}"}], "text": {"format": {"type": "json_object"}}}, timeout=40)
        data = json.loads(response.json().get("output_text") or response.json()["output"][0]["content"][0]["text"])
        translations = data if isinstance(data, list) else data.get("items", [])
        by_idx = {int(x["idx"]): x for x in translations}
        for idx, item in enumerate(items):
            if translated := by_idx.get(idx):
                item.title = clean_text(translated.get("title_ko", item.title))
                item.summary = clean_text(translated.get("summary_ko", item.summary))
    except Exception: pass
    return items

def translate_global_news_with_gemini(items: list[NewsItem], api_key: str) -> list[NewsItem]:
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    prompt_items = [{"idx": i, "title": x.title, "summary": x.summary} for i, x in enumerate(items)]
    try:
        response = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", headers={"x-goog-api-key": api_key, "Content-Type": "application/json"}, json={"contents": [{"role": "user", "parts": [{"text": f"Translate to Korean JSON schema {{'items':[{{'idx':0,'title_ko':'...','summary_ko':'...'}}]}}. Items: {json.dumps(prompt_items, ensure_ascii=False)}"}]}], "generationConfig": {"response_mime_type": "application/json"}}, timeout=40)
        payload = json.loads(response.json()["candidates"][0]["content"]["parts"][0]["text"])
        translations = payload if isinstance(payload, list) else payload.get("items", [])
        by_idx = {int(x["idx"]): x for x in translations}
        for idx, item in enumerate(items):
            if translated := by_idx.get(idx):
                item.title = clean_text(translated.get("title_ko", item.title))
                item.summary = clean_text(translated.get("summary_ko", item.summary))
    except Exception: pass
    return items

def fetch_macro_data() -> dict:
    macro, symbols = {}, {"USD/KRW 환율": "KRW=X", "미국 10년물 금리(%)": "^TNX", "코스피 지수": "^KS11", "나스닥 지수": "^IXIC"}
    for label, symbol in symbols.items():
        try:
            hist = yf.Ticker(symbol).history(period="5d", auto_adjust=False)
            if not hist.empty: macro[label] = round(float(hist.dropna(subset=["Close"]).iloc[-1]["Close"]), 2)
        except Exception: pass
    return macro

def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1: return None
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0: return 100.0
    return round(100 - (100 / (1 + (avg_gain / avg_loss))), 1)

def calc_ma(closes: list[float], period: int) -> float | None:
    return round(sum(closes[-period:]) / period, 2) if len(closes) >= period else None

def fetch_technical(ticker: str, name: str, is_kr: bool = False) -> TechnicalSignal | None:
    try:
        if is_kr:
            today = dt.date.today()
            frame = stock.get_market_ohlcv_by_date((today - dt.timedelta(days=60)).strftime("%Y%m%d"), today.strftime("%Y%m%d"), ticker).dropna()
            if len(frame) < 5: return None
            closes, volumes = frame["종가"].tolist(), frame["거래량"].tolist()
        else:
            hist = yf.Ticker(ticker).history(period="60d", auto_adjust=False).dropna(subset=["Close"])
            if len(hist) < 5: return None
            closes, volumes = hist["Close"].tolist(), hist["Volume"].tolist()

        close = closes[-1]
        change_pct = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2) if len(closes) >= 2 else None
        vol_avg20 = sum(volumes[-20:]) / len(volumes[-20:]) if len(volumes) >= 20 else None
        return TechnicalSignal(ticker=ticker, name=name, close=close, rsi=calc_rsi(closes), ma5=calc_ma(closes, 5), ma20=calc_ma(closes, 20), change_pct=change_pct, volume_ratio=round(volumes[-1] / vol_avg20, 2) if vol_avg20 and vol_avg20 > 0 else None)
    except Exception: return None

def fetch_portfolio_signals() -> list[TechnicalSignal]:
    return [sig for name, meta in PORTFOLIO.items() if (sig := fetch_technical(meta["ticker"], name, is_kr=(meta["kind"] == "kr")))]

def fetch_market_top5() -> list[TechnicalSignal]:
    signals = [sig for ticker, name in WATCHLIST.items() if (sig := fetch_technical(ticker, name, is_kr=(ticker in WATCHLIST_KR)))]
    bullish = sorted([s for s in signals if s.rsi is not None and s.rsi <= 40], key=lambda s: s.rsi)[:3]
    bearish = sorted([s for s in signals if s.rsi is not None and s.rsi >= 65], key=lambda s: -s.rsi)[:2]
    return bullish + bearish

# ── 기술적 지표 + 뉴스 하이브리드 추천 엔진 ─────────────────────────────────
def generate_recommendations_with_news(portfolio_sigs: list[TechnicalSignal], market_sigs: list[TechnicalSignal], as_of: dt.date) -> list[str]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    all_sigs = {s.ticker: s for s in (portfolio_sigs + market_sigs)}.values()

    # 1차 기술적 스크리닝 (RSI, 거래량, 이평선 기반 점수화)
    candidates_scored = []
    for s in all_sigs:
        score = 0
        if s.rsi and s.rsi <= 40: score += 2
        if s.volume_ratio and s.volume_ratio >= 1.5: score += 1
        if s.ma5 and s.ma20 and s.ma5 > s.ma20: score += 1
        if score > 0: candidates_scored.append((score, s))
    
    candidates = [c[1] for c in sorted(candidates_scored, key=lambda x: -x[0])[:3]]
    if not candidates:
        return ["현재 기술적 지표 기준 강력한 시그널이 발견되지 않아 관망을 권장합니다."]

    # 2차 후보 종목 최신 뉴스 수집
    candidate_info = []
    for s in candidates:
        is_kr = s.ticker in WATCHLIST_KR or any(s.ticker == m["ticker"] and m["kind"] == "kr" for m in PORTFOLIO.values())
        news_items = naver_news(s.name, 2, as_of) if is_kr else google_news(s.name, "en", "US", 2, as_of)
        if not news_items and is_kr: news_items = google_news(s.name, "ko", "KR", 2, as_of)
        
        news_text = " | ".join([n.title for n in news_items]) if news_items else "최근 1일 내 특이 뉴스 없음"
        change_str = f"{s.change_pct:+.2f}%" if s.change_pct is not None else "0.00%"
        candidate_info.append(f"- 종목명: {s.name}({s.ticker}), 종가: {s.close}, 등락: {change_str}, RSI: {s.rsi}, 거래량비율: {s.volume_ratio}\n  관련 뉴스: {news_text}")

    candidate_text = "\n".join(candidate_info)

    if not api_key:
        return [f"[{s.name}] (당일: {s.change_pct:+.2f}%)\n   이유: 기술적 지표 우수 및 자체 뉴스 확인 요망" for s in candidates]

    # 3차 Gemini 종합 분석 프롬프트 (중괄호 오류 수정)
    prompt = f"""당신은 AI 퀀트 애널리스트입니다.
아래는 1차 기술적 분석을 통과한 추천 후보 종목들과 당일 최신 뉴스입니다.
기술적 지표의 강점과 뉴스의 호재/악재를 종합적으로 판단하여 가장 매력적인 종목을 추천해주세요.

[후보 종목 데이터]
{candidate_text}

반드시 아래 양식으로만 답변하세요 (불필요한 서론 금지):
[종목명] (종가: [숫자] | 당일: [등락률])
   이유: [기술적 분석 요약] + [뉴스 모멘텀 요약] (핵심만 한 줄로 작성)"""

    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": 400, "temperature": 0.3}},
            timeout=40,
        )
        response.raise_for_status()
        return response.json()["candidates"][0]["content"]["parts"][0]["text"].strip().split("\n\n")
    except Exception as e:
        return [f"AI 분석 오류: {e}"]

def format_technical(signals: list[TechnicalSignal]) -> str:
    if not signals: return "- 데이터 없음"
    lines = []
    for s in signals:
        rsi_label = " 과매도" if s.rsi and s.rsi <= 30 else (" 과매수" if s.rsi and s.rsi >= 70 else "")
        change_str = f"{s.change_pct:+.2f}%" if s.change_pct is not None else "N/A"
        lines.append(f"- {s.name}({s.ticker}): RSI {s.rsi}{rsi_label} | 5MA {s.ma5} / 20MA {s.ma20} | 등락 {change_str} | 거래량비율 {s.volume_ratio}")
    return "\n".join(lines)

def format_price(price: Price) -> str:
    return f"- {price.name}({price.ticker}): {f'{price.close:,.0f}원' if price.currency == 'KRW' else f'${price.close:,.2f}'} / 기준일 {price.date.isoformat()}"

def format_news(items: list[NewsItem]) -> str:
    if not items: return "- 오늘자 뉴스 없음"
    return "\n".join(f"{i}. {item.title} ({item.source}){f' - {item.summary}' if item.summary else ''}\n   {item.link}" for i, item in enumerate(items, 1))

def format_macro(macro: dict) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in macro.items()) if macro else "- 매크로 데이터 없음"

def build_briefing() -> str:
    as_of = today_kst()
    prices = fetch_prices(as_of)
    domestic_news = fetch_domestic_news(as_of)
    global_news = translate_global_news(fetch_global_news(as_of))
    outlook = fetch_market_outlook(as_of)
    macro = fetch_macro_data()