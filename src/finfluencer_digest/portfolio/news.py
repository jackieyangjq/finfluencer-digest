"""为每只持仓收集近期新闻：长桥、Finnhub、Google 新闻、免费行情库，去重后按时间排序。
任何一个来源失败或没配置密钥，都只是跳过，不影响其他来源。
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from urllib.request import Request, urlopen

import yfinance as yf

from . import longbridge as longbridge_api

PER_SOURCE = 8  # 每个来源最多取几条，避免某个来源刷屏


def _get(url: str, headers: dict | None = None) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0", **(headers or {})}), timeout=20) as r:
        return r.read()


def _item(date: dt.date, source: str, title: str, summary: str = "", url: str = "") -> dict:
    return {"date": date.isoformat(), "source": source, "title": title.strip(),
            "summary": re.sub(r"\s+", " ", summary or "").strip()[:300], "url": url}


def _as_date(value) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value, dt.UTC).date()
    return dt.date.fromisoformat(str(value)[:10])


def from_longbridge(symbol: str, since: dt.date) -> list[dict]:
    """长桥资讯（中文）。symbol 用长桥格式，如 VRT.US、1211.HK。"""
    out = [_item(_as_date(n.published_at), "长桥", n.title, n.description, n.url) for n in longbridge_api.news(symbol)]
    return [x for x in out if x["date"] >= since.isoformat()][:PER_SOURCE]


def from_finnhub(us_symbol: str, since: dt.date) -> list[dict]:
    """Finnhub 公司新闻（英文，仅美股）。"""
    key = os.getenv("FINNHUB_API_KEY")
    if not key:
        return []
    url = f"https://finnhub.io/api/v1/company-news?symbol={us_symbol}&from={since}&to={dt.date.today()}"
    data = json.loads(_get(url, {"X-Finnhub-Token": key}))
    data.sort(key=lambda d: -d.get("datetime", 0))
    return [_item(_as_date(d["datetime"]), d.get("source") or "Finnhub", d["headline"], d.get("summary"), d.get("url"))
            for d in data[:PER_SOURCE] if d.get("headline")]


def from_google(query: str, since: dt.date) -> list[dict]:
    """Google 新闻订阅，不需要密钥。查询词含中文时搜中文新闻。"""
    chinese = bool(re.search(r"[一-鿿]", query))
    region = "hl=zh-CN&gl=CN&ceid=CN:zh-Hans" if chinese else "hl=en-US&gl=US&ceid=US:en"
    root = ET.fromstring(_get(f"https://news.google.com/rss/search?q={quote(query)}+when:7d&{region}"))
    out = []
    for it in root.iter("item"):
        source = it.findtext("source") or "Google 新闻"
        title = re.sub(rf"\s+-\s+{re.escape(source)}$", "", it.findtext("title") or "")
        out.append(_item(parsedate_to_datetime(it.findtext("pubDate")).date(), source, title,
                         url=it.findtext("link") or ""))
    out.sort(key=lambda x: x["date"], reverse=True)
    return [x for x in out if x["date"] >= since.isoformat()][:PER_SOURCE]


def from_yahoo(yahoo_symbol: str, since: dt.date) -> list[dict]:
    out = []
    for n in yf.Ticker(yahoo_symbol).get_news(count=20) or []:
        c = n.get("content", n)
        if c.get("title") and c.get("pubDate"):
            out.append(_item(_as_date(c["pubDate"]), (c.get("provider") or {}).get("displayName", "Yahoo"),
                             c["title"], c.get("summary"), (c.get("canonicalUrl") or {}).get("url", "")))
    return [x for x in out if x["date"] >= since.isoformat()][:PER_SOURCE]


def collect(stock: dict, days: int, limit: int) -> tuple[list[dict], list[str]]:
    """stock 需要：lb_symbol（长桥格式）、yahoo（行情库格式）、market（US/HK）、queries（Google 新闻查询词）。
    返回（去重后的新闻，成功取到新闻的来源名）。"""
    since = dt.date.today() - dt.timedelta(days=days)
    jobs = [("长桥", lambda: from_longbridge(stock["lb_symbol"], since))]
    if stock["market"] == "US":
        jobs.append(("Finnhub", lambda: from_finnhub(stock["yahoo"], since)))
    jobs += [("Google 新闻", lambda q=q: from_google(q, since)) for q in stock["queries"]]
    jobs.append(("雅虎财经", lambda: from_yahoo(stock["yahoo"], since)))

    items, used = [], []
    for name, job in jobs:
        try:
            got = job()
        except Exception as e:
            print(f"  新闻来源 {name} 获取 {stock['yahoo']} 失败：{type(e).__name__}", flush=True)
            continue
        if got and name not in used:
            used.append(name)
        items += got
    seen, unique = set(), []
    for x in items:  # 同一条新闻常被多个来源转载，按标题去重
        key = re.sub(r"\W", "", x["title"].lower())[:50]
        if key and key not in seen:
            seen.add(key)
            unique.append(x)
    today = dt.date.today().isoformat()
    unique = [x for x in unique if x["date"] <= today]  # 个别来源的时间戳会错到未来
    unique.sort(key=lambda x: x["date"], reverse=True)
    return unique[:limit], used


def longbridge_token_expiry() -> dt.date | None:
    """长桥 Access Token 的过期日期（令牌里自带）；读不出来就返回 None。"""
    parts = os.getenv("LONGBRIDGE_ACCESS_TOKEN", "").split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        return dt.datetime.fromtimestamp(payload["exp"], dt.UTC).date()
    except Exception:
        return None
