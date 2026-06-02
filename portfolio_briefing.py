from __future__ import annotations

import datetime as dt
import html
import json
import os
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
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).astimezone(KST)
    except Exception:
        return None


def google_news(query: str, lang: str, country: str, limit: int, as_of: dt.date) -> list[NewsItem]:
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query + ' when:1d')}&hl={lang}-{country}&gl={country}&ceid={country}:{lang}"
    )
    feed = feedparser.parse(url)
    strict_items: list[NewsItem] = []
    fallback_items: list[NewsItem] = []
    for entry in feed.entries:
        published = parse_feed_datetime(entry)
        source = clean_text(getattr(getattr(entry, "source", None), "title", "") or "Google News")
        title = clean_text(entry.title)
        suffix = f" - {source}"
        if title.endswith(suffix):
            title = title[: -len(suffix)]
        item = NewsItem(
            title=title,
            source=source,
            link=getattr(entry, "link", ""),
            published=published,
            summary=clean_text(getattr(entry, "summary", "")),
        )
        if published and published.date() == as_of:
            strict_items.append(item)
        fallback_items.append(item)

    items = dedupe_news(strict_items)
    if len(items) < limit:
        items.extend(x for x in dedupe_news(fallback_items) if x.link not in {i.link for i in items})
    return dedupe_news(items)[:limit]


def naver_news(query: str, limit: int, as_of: dt.date) -> list[NewsItem]:
    client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
    client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return []

    response = requests.get(
        "https://openapi.naver.com/v1/search/news.json",
        params={"query": query, "display": 30, "sort": "date"},
        headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret},
        timeout=20,
    )
    response.raise_for_status()

    items: list[NewsItem] = []
    for item in response.json().get("items", []):
        published = parsedate_to_datetime(item["pubDate"]).astimezone(KST)
        if published.date() != as_of:
            continue
        items.append(
            NewsItem(
                title=clean_text(item.get("title", "")),
                source="네이버 뉴스",
                link=item.get("originallink") or item.get("link", ""),
                published=published,
                summary=clean_text(item.get("description", "")),
            )
        )
        if len(items) >= limit:
            break
    return dedupe_news(items)[:limit]


def dedupe_news(items: Iterable[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    result: list[NewsItem] = []
    for item in items:
        key = item.link or item.title
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def fetch_domestic_news(as_of: dt.date) -> list[NewsItem]:
    queries = [meta["query_ko"] for meta in PORTFOLIO.values()]
    items: list[NewsItem] = []
    for portfolio_query in queries:
        items.extend(naver_news(portfolio_query, 1, as_of))
        if len(dedupe_news(items)) >= 3:
            break
    if len(items) < 3:
        for portfolio_query in queries:
            items.extend(google_news(portfolio_query, "ko", "KR", 1, as_of))
            if len(dedupe_news(items)) >= 3:
                break
    return dedupe_news(items)[:3]


def fetch_global_news(as_of: dt.date) -> list[NewsItem]:
    items: list[NewsItem] = []
    for meta in PORTFOLIO.values():
        query = meta.get("query_en", meta["ticker"])
        items.extend(google_news(query, "en", "US", 1, as_of))
        if len(dedupe_news(items)) >= 3:
            break
    return dedupe_news(items)[:3]


def fetch_market_outlook(as_of: dt.date) -> list[NewsItem]:
    queries = ["코스피 전망", "나스닥 전망", "금리 환율 전망"]
    items: list[NewsItem] = []
    for query in queries:
        items.extend(naver_news(query, 1, as_of))
    if len(items) < 3:
        for query in queries:
            items.extend(google_news(query, "ko", "KR", 1, as_of))
            if len(dedupe_news(items)) >= 3:
                break
    return dedupe_news(items)[:3]


def translate_global_news(items: list[NewsItem]) -> list[NewsItem]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not items:
        return items

    if not api_key and gemini_key:
        return translate_global_news_with_gemini(items, gemini_key)
    if not api_key:
        return items

    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
    prompt_items = [{"idx": i, "title": x.title, "summary": x.summary} for i, x in enumerate(items)]
    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": "Translate and summarize financial news into concise Korean. Return only JSON.",
                },
                {
                    "role": "user",
                    "content": (
                        "For each item, return an array of objects with idx, title_ko, summary_ko. "
                        f"Items: {json.dumps(prompt_items, ensure_ascii=False)}"
                    ),
                },
            ],
            "text": {"format": {"type": "json_object"}},
        },
        timeout=40,
    )
    response.raise_for_status()
    payload = response.json()
    text = payload.get("output_text") or payload["output"][0]["content"][0]["text"]
    data = json.loads(text)
    translations = data if isinstance(data, list) else data.get("items", [])
    by_idx = {int(x["idx"]): x for x in translations}
    for idx, item in enumerate(items):
        translated = by_idx.get(idx)
        if translated:
            item.title = clean_text(translated.get("title_ko", item.title))
            item.summary = clean_text(translated.get("summary_ko", item.summary))
    return items


def translate_global_news_with_gemini(items: list[NewsItem], api_key: str) -> list[NewsItem]:
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    prompt_items = [{"idx": i, "title": x.title, "summary": x.summary} for i, x in enumerate(items)]
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json={
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "Translate and summarize these financial news items into concise Korean. "
                                "Return only JSON with this schema: "
                                '{"items":[{"idx":0,"title_ko":"...","summary_ko":"..."}]}. '
                                f"Items: {json.dumps(prompt_items, ensure_ascii=False)}"
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {"response_mime_type": "application/json"},
        },
        timeout=40,
    )
    if not response.ok:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise RuntimeError(f"Gemini 번역 실패: {response.status_code} {detail}")

    data = response.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    payload = json.loads(text)
    translations = payload if isinstance(payload, list) else payload.get("items", [])
    by_idx = {int(x["idx"]): x for x in translations}
    for idx, item in enumerate(items):
        translated = by_idx.get(idx)
        if translated:
            item.title = clean_text(translated.get("title_ko", item.title))
            item.summary = clean_text(translated.get("summary_ko", item.summary))
    return items


def format_price(price: Price) -> str:
    if price.currency == "KRW":
        value = f"{price.close:,.0f}원"
    else:
        value = f"${price.close:,.2f}"
    return f"- {price.name}({price.ticker}): {value} / 기준일 {price.date.isoformat()}"


def format_news(items: list[NewsItem]) -> str:
    if not items:
        return "- 오늘자 뉴스를 찾지 못했습니다."
    lines = []
    for i, item in enumerate(items, 1):
        summary = f" - {item.summary}" if item.summary else ""
        source = f" ({item.source})" if item.source else ""
        lines.append(f"{i}. {item.title}{source}{summary}\n   {item.link}")
    return "\n".join(lines)


def build_briefing() -> str:
    as_of = today_kst()
    prices = fetch_prices(as_of)
    domestic_news = fetch_domestic_news(as_of)
    global_news = translate_global_news(fetch_global_news(as_of))
    outlook = fetch_market_outlook(as_of)

    return "\n\n".join(
        [
            f"[포트폴리오 데일리 브리핑] {as_of.isoformat()} KST",
            "1) 보유 종목 최근 종가\n" + "\n".join(format_price(price) for price in prices),
            "2) 국내 뉴스 3개\n" + format_news(domestic_news),
            "3) 해외 뉴스 3개\n" + format_news(global_news),
            "4) 당일 시장/거시 전망\n" + format_news(outlook),
            "참고: 미국 종목은 현 시점 기준 가장 최근에 완료된 정규장 종가입니다.",
        ]
    )


def build_kakao_summary(briefing: str) -> str:
    lines = briefing.splitlines()
    title = lines[0] if lines else "[포트폴리오 데일리 브리핑]"
    price_lines = [line[2:] for line in lines if line.startswith("- ") and "기준일" in line]
    compact_prices = []
    short_names = {
        "삼성전자우": "삼전우",
        "알파벳A": "GOOGL",
        "JEPQ": "JEPQ",
        "NVDL": "NVDL",
        "TQQQ": "TQQQ",
    }
    for line in price_lines:
        name, value = line.split(":", 1)
        tickerless = name.split("(")[0]
        price = value.split("/")[0].strip()
        compact_prices.append(f"{short_names.get(tickerless, tickerless)} {price}")

    summary = "\n".join(
        [
            title.replace("데일리 브리핑", "브리핑"),
            ", ".join(compact_prices),
            "국내/해외/시장 뉴스 각 3개 정리 완료.",
        ]
    )
    return summary[:200]


def save_detail_report(briefing: str) -> tuple[str, str | None]:
    as_of = today_kst()
    report_dir = os.getenv("REPORT_DIR", "reports").strip() or "reports"
    os.makedirs(report_dir, exist_ok=True)
    filename = f"portfolio_briefing_{as_of.isoformat()}.html"
    path = os.path.abspath(os.path.join(report_dir, filename))
    body = html.escape(briefing).replace("\n", "<br>\n")
    document = (
        "<!doctype html>\n"
        "<html lang=\"ko\">\n"
        "<head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>포트폴리오 데일리 브리핑</title>"
        "<style>body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;line-height:1.55;margin:24px;max-width:860px}"
        "h1{font-size:22px}main{white-space:normal}</style></head>\n"
        "<body><h1>포트폴리오 데일리 브리핑</h1><main>"
        f"{body}</main></body></html>\n"
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(document)

    explicit_url = os.getenv("KAKAO_DETAIL_URL", "").strip()
    if explicit_url:
        return path, explicit_url

    base_url = os.getenv("REPORT_BASE_URL", "").strip().rstrip("/")
    if base_url:
        return path, f"{base_url}/{filename}"
    return path, None


def refresh_kakao_access_token() -> str:
    rest_key = os.getenv("KAKAO_REST_API_KEY", "").strip()
    client_secret = os.getenv("KAKAO_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("KAKAO_REFRESH_TOKEN", "").strip()
    if not rest_key or not refresh_token:
        raise RuntimeError("KAKAO_REST_API_KEY와 KAKAO_REFRESH_TOKEN을 .env에 입력해야 합니다.")

    payload = {"grant_type": "refresh_token", "client_id": rest_key, "refresh_token": refresh_token}
    if client_secret:
        payload["client_secret"] = client_secret

    response = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data=payload,
        timeout=20,
    )
    if not response.ok:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise RuntimeError(f"카카오 액세스 토큰 갱신 실패: {response.status_code} {detail}")
    data = response.json()
    if "refresh_token" in data:
        print("새 refresh_token이 발급되었습니다. .env의 KAKAO_REFRESH_TOKEN을 갱신하세요.")
        print(data["refresh_token"])
    return data["access_token"]


def send_kakao_message(text: str) -> None:
    access_token = refresh_kakao_access_token()
    report_path, detail_url = save_detail_report(text)
    fallback_url = os.getenv("KAKAO_FALLBACK_URL", "https://finance.yahoo.com").strip()
    link_url = detail_url or fallback_url
    template = {
        "object_type": "text",
        "text": build_kakao_summary(text),
        "link": {"web_url": link_url, "mobile_web_url": link_url},
        "button_title": "자세히 보기",
    }
    response = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=20,
    )
    response.raise_for_status()
    print(f"상세 리포트 저장: {report_path}")


def main() -> None:
    load_dotenv()
    briefing = build_briefing()
    print(briefing)
    if os.getenv("KAKAO_SEND", "true").lower() in {"1", "true", "yes", "y"}:
        send_kakao_message(briefing)


if __name__ == "__main__":
    main()
