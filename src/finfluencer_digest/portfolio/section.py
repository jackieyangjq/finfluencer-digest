"""持仓梳理：读取持仓快照和手动持仓表，加上免费行情、新闻和博主观点，生成邮件最前面的“我的持仓”部分。
包括总览、风险提示、盘面，以及每只持仓一张深度卡片（价位、技术面、分析师、新闻、多空证据）。
只做数据整理和证据对比，不给买卖建议。发给 Gemini 的只有公开数据，不含你的数量、成本和金额。
持仓文件都在 holdings_root 下：portfolio/*.json（券商持仓快照）和配置里 manual_file 指定的手动持仓表。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml
import yfinance as yf
from google.genai import types
from pydantic import BaseModel, Field

from ..config import LOCAL_TZ
from ..state import Paths
from . import longbridge as longbridge_api
from . import news
from . import technicals as ta

HKD_PER_USD_FALLBACK = 7.8  # 港币与美元挂钩，汇率取不到时用这个
CURRENCY_MARK = {"USD": "$", "HKD": "HK$"}
logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # ETF 没有财报日期等数据时库会报错，属正常


# ---------- 读持仓 ----------

def yahoo_symbol(symbol: str) -> str:
    """长桥格式转成行情库格式：NVDA.US → NVDA，700.HK → 0700.HK，00700.HK → 0700.HK。"""
    code, _, market = symbol.upper().partition(".")
    if market == "HK":
        return code.lstrip("0").zfill(4) + ".HK"
    return code


def base_symbol(symbol: str) -> str:
    """用来和博主提到的代码对上：NVDA.US → NVDA，00700.HK → 700。"""
    return symbol.upper().split(".")[0].lstrip("$").lstrip("0") or "0"


def hkd_per_usd() -> float:
    try:
        return float(yf.Ticker("HKD=X").history(period="5d")["Close"].iloc[-1])
    except Exception:
        return HKD_PER_USD_FALLBACK


def to_usd(amount: float, currency: str, fx: float) -> float:
    return amount / fx if currency == "HKD" else amount


def load_holdings(pcfg: dict, fx: float, root: Path) -> tuple[list[dict], float, list[tuple[str, str]]]:
    positions, cash_usd, sources = [], 0.0, []
    for f in sorted((root / "portfolio").glob("*.json")):
        snap = json.loads(f.read_text(encoding="utf-8"))
        sources.append((snap["broker"], snap["as_of"]))
        cash_usd += to_usd(snap.get("total_cash", 0), snap.get("currency", "USD"), fx)
        positions += [{**p, "broker": snap["broker"]} for p in snap["positions"]]
    manual_path = root / pcfg["manual_file"]
    if manual_path.exists():
        manual = yaml.safe_load(manual_path.read_text(encoding="utf-8")) or {}
        sources.append((manual.get("broker", "手动"), str(manual.get("updated", ""))))
        positions += [{**p, "broker": manual.get("broker", "手动")} for p in manual.get("positions") or []]
        cash_usd += sum(to_usd(v, k, fx) for k, v in (manual.get("cash") or {}).items())
    return positions, cash_usd, sources


# ---------- 行情、分析师、估值 ----------

SESSIONS = {"US": ("America/New_York", dt.time(9, 30), dt.time(16, 0)),
            "HK": ("Asia/Hong_Kong", dt.time(9, 30), dt.time(16, 10))}


def trading_now(market: str) -> bool:
    tz, start, end = SESSIONS[market]
    now = dt.datetime.now(ZoneInfo(tz))
    return now.weekday() < 5 and start <= now.time() < end


def with_latest(hist: pd.DataFrame, q: dict | None) -> pd.DataFrame:
    """雅虎偶尔缺最新一天的收盘价（返回空值）：用长桥的最新成交价补上或覆盖。"""
    df = hist.loc[hist["Close"].notna(), ["Close", "Volume"]].copy()
    if q and q.get("date") and len(df):
        last_date = df.index[-1].date()
        if q["date"] > last_date:
            ts = pd.Timestamp(q["date"])
            df.loc[ts.tz_localize(df.index.tz) if df.index.tz else ts] = [q["last"], q["volume"]]
        elif q["date"] == last_date:
            df.iloc[-1, df.columns.get_loc("Close")] = q["last"]
    return df


def stock_data(yahoo: str, q: dict | None) -> dict:
    ticker = yf.Ticker(yahoo)
    close = with_latest(ticker.history(period="1y", auto_adjust=False), q)
    if close.empty:
        return {}
    d = {"t": ta.compute(close), "analyst": None, "valuation": {}, "earnings": None,
         "price_date": close.index[-1].date(), "source": "长桥" if q else "雅虎"}
    d["prev"] = q["prev"] if q else (float(close["Close"].iloc[-2]) if len(close) > 1 else None)
    try:
        tgt = ticker.analyst_price_targets or {}
        rec = ticker.recommendations_summary
        if tgt.get("mean") and rec is not None and len(rec):
            r = rec.iloc[0]
            d["analyst"] = {"mean": tgt["mean"], "low": tgt.get("low"), "high": tgt.get("high"),
                            "buy": int(r["strongBuy"] + r["buy"]), "hold": int(r["hold"]),
                            "sell": int(r["sell"] + r["strongSell"])}
    except Exception:
        pass
    try:
        info = ticker.info
        d["valuation"] = {k: info[k] for k in ("forwardPE", "trailingPE") if info.get(k)}
    except Exception:
        pass
    try:
        dates = (ticker.calendar or {}).get("Earnings Date") or []
        d["earnings"] = dates[0] if dates else None
    except Exception:
        pass
    return d


def returns(yahoo: str, q: dict | None) -> dict | None:
    try:
        close = with_latest(yf.Ticker(yahoo).history(period="3mo"), q)["Close"]
        return {"last": float(close.iloc[-1]), "r1d": float(close.iloc[-1] / close.iloc[-2] - 1),
                "r1m": float(close.iloc[-1] / close.iloc[-22] - 1) if len(close) > 22 else None}
    except Exception:
        return None


# ---------- Gemini：主题和证据整理（一次调用） ----------

class HoldingAnalysis(BaseModel):
    symbol: str = Field(description="原样返回给定的代码")
    theme: str = Field(description="投资大类，2-8 个字")
    good_news: list[str] = Field(description="近 7 天新闻里的利好，最多 3 条；每条用一句简体中文概括要点"
                                             "（不要照抄英文标题），末尾括号注明（月-日 来源）")
    bad_news: list[str] = Field(description="近 7 天新闻里的利空，最多 3 条；每条用一句简体中文概括要点"
                                            "（不要照抄英文标题），末尾括号注明（月-日 来源）")
    bull_points: list[str] = Field(description="综合全部数据的看多证据，2-4 条，引用具体数字")
    bear_points: list[str] = Field(description="综合全部数据的看空证据，2-4 条，引用具体数字")
    watch: list[str] = Field(description="接下来值得盯的 2-3 个信号：关键价位、财报、行业数据等")
    lean: str = Field(description="证据整体偏向，只能是：偏多、偏空、分歧")
    lean_reason: str = Field(description="一句话说明证据为什么偏向这一边")


class Analyses(BaseModel):
    items: list[HoldingAnalysis]


ANALYSIS_PROMPT = """今天是 {today}。你是一名客观的证券研究助理。下面是几只股票的公开数据：价位和技术指标、与板块对比、分析师共识、估值、近 7 天新闻（带日期和来源）、中文财经博主的观点。
请为每只股票整理新闻利好利空、看多证据、看空证据、接下来要盯的信号和证据偏向，并标注投资大类。
规则：
1. 只使用下面提供的数据，不要编造；引用数字时用数据里的原数，板块对比等结论也以数据为准，不要自己推算。
2. good_news 和 bad_news 只能来自“近7天新闻”列表，不要把技术指标或估值写进去；只挑对公司业务或股价有实际影响的新闻，
   忽略“股价涨了 X%，你该知道什么”“X 和 Y 哪个更值得买”这类没有新信息的稿件；没有合适的就留空。
3. 不要给出买入、卖出、持有、加仓、减仓、止损等操作建议，也不要给出你自己的目标价。
4. 投资大类用尽量宽的类别（如 AI数据中心、新能源汽车、半导体、消费、金融）；业绩主要靠同一股需求驱动的必须用完全相同的名称，例如光模块、存储芯片、数据中心电源散热都写“AI数据中心”。
5. 全部用简体中文。

{data}"""  # noqa: E501


def relative_text(r: dict, b: dict) -> str:
    t = r["d"]["t"]
    if not b or b.get("r1m") is None or t["r1m"] is None:
        return "无数据"
    gap = (t["r1m"] - b["r1m"]) * 100
    return (f"近 1 个月个股 {ta.pct(t['r1m'])}，板块 {r['benchmark']} {ta.pct(b['r1m'])}，"
            f"个股{'跑赢' if gap >= 0 else '跑输'}板块 {abs(gap):.1f} 个百分点")


def analyst_text(an: dict | None) -> str:
    if not an:
        return "无分析师覆盖"
    return (f"{an['buy'] + an['hold'] + an['sell']} 位分析师：{an['buy']} 买入、{an['hold']} 持有、{an['sell']} 卖出；"
            f"平均目标价 {an['mean']:.2f}，最低 {an['low']:.2f}，最高 {an['high']:.2f}")


def analyze(ask, cfg: dict, payload: list[dict]) -> dict[str, HoldingAnalysis]:
    prompt = ANALYSIS_PROMPT.format(today=dt.date.today().isoformat(), data=json.dumps(payload, ensure_ascii=False))
    resp = ask(cfg["synth_models"], label="portfolio", contents=prompt,
               config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Analyses))
    return {base_symbol(a.symbol): a for a in resp.parsed.items}


# ---------- 博主观点 ----------

def blogger_views(held: dict[str, str], today_rows: list[dict], days: int, calls_file: Path) -> dict[str, list[str]]:
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    rows = ([json.loads(line) for line in calls_file.read_text(encoding="utf-8").splitlines()]
            if calls_file.exists() else [])
    views, seen = defaultdict(list), set()
    for r in rows + today_rows:
        b = base_symbol(r["symbol"])
        key = (r["video_id"], b)
        if r["date"] >= since and b in held and key not in seen:
            seen.add(key)
            views[held[b]].append(f"{r['date'][5:]} {r['channel']}（{r['stance']}）：{r['reason']}")
    return views


# ---------- 拼报告 ----------

def money(x: float, signed: bool = False) -> str:
    sign = "-" if x < 0 else ("+" if signed and x > 0 else "")
    return f"{sign}${abs(x):,.0f}"


def build_section(cfg: dict, ask, today_rows: list[dict], *, holdings_root: Path) -> tuple[str, set[str]]:
    """返回（邮件里“我的持仓”这一部分的 Markdown，持仓代码集合）。出错时返回说明文字，不影响日报发送。"""
    try:
        return _build_section(cfg, ask, today_rows, holdings_root)
    except Exception as e:
        return f"## 我的持仓\n\n（持仓部分这次生成失败：{type(e).__name__}: {str(e)[:150]}）\n", set()


def _build_section(cfg: dict, ask, today_rows: list[dict], holdings_root: Path) -> tuple[str, set[str]]:
    pcfg = cfg["portfolio"]
    extra = pcfg.get("holdings_extra") or {}
    fx = hkd_per_usd()
    positions, cash_usd, sources = load_holdings(pcfg, fx, holdings_root)
    if not positions:
        return "## 我的持仓\n\n（还没有持仓数据）\n", set()

    symbols = [yahoo_symbol(p["symbol"]) for p in positions]
    benchmarks = {extra.get(s, {}).get("benchmark") or ("^HSI" if s.endswith(".HK") else "SPY") for s in symbols}
    live = longbridge_api.quotes(symbols + sorted(benchmarks | set(pcfg["market"].values())))

    rows = []
    for p in positions:
        yahoo = yahoo_symbol(p["symbol"])
        print(f"  整理持仓 {yahoo}…", flush=True)
        d = stock_data(yahoo, live.get(yahoo))
        qty, cost, cur = p["quantity"], p.get("cost_price"), p.get("currency", "USD")
        price = d["t"]["last"] if d else None
        prev = d.get("prev")
        market = "HK" if yahoo.endswith(".HK") else "US"
        ex = extra.get(yahoo, {})
        queries = ex.get("news_queries") or [f"{p['name']} stock"]
        items, used = news.collect({"lb_symbol": p["symbol"], "yahoo": yahoo, "market": market, "queries": queries},
                                   pcfg["news_days"], pcfg["news_per_stock"])
        rows.append({
            **p, "yahoo": yahoo, "d": d, "price": price, "news": items, "news_sources": used, "market": market,
            "intraday": bool(d) and d["price_date"] == dt.datetime.now(ZoneInfo(SESSIONS[market][0])).date()
                        and trading_now(market),
            "benchmark": ex.get("benchmark") or ("^HSI" if market == "HK" else "SPY"),
            "value": to_usd(price * qty, cur, fx) if price else None,
            "pnl": to_usd((price - cost) * qty, cur, fx) if price and cost else None,
            "pnl_pct": price / cost - 1 if price and cost else None,
            "day": to_usd((price - prev) * qty, cur, fx) if price and prev else 0.0,
        })
    stock_value = sum(r["value"] or 0 for r in rows)
    for r in rows:
        r["weight"] = (r["value"] or 0) / stock_value if stock_value else 0
    rows.sort(key=lambda r: -r["weight"])

    held = {base_symbol(r["symbol"]): r["yahoo"] for r in rows}
    calls_file = Paths(holdings_root).calls_file  # holdings_root 就是配置目录，博主观点记录也在这里
    views = blogger_views(held, today_rows, pcfg["blogger_days"], calls_file)
    bench = {s: returns(s, live.get(s)) for s in benchmarks | set(pcfg["market"].values())}

    # 交给 Gemini 的只有公开数据（不含数量、成本、金额）
    payload = []
    for r in rows:
        t = r["d"].get("t")
        if not t:
            continue
        b = bench.get(r["benchmark"]) or {}
        payload.append({
            "代码": r["yahoo"], "名称": r["name"],
            "价位": {"最新价": round(t["last"], 2),
                   "价格时间": "盘中" if r["intraday"] else f"{r['d']['price_date']} 收盘",
                   "较前一交易日": ta.pct(r["price"] / r["d"]["prev"] - 1 if r["d"].get("prev") else None),
                   "5日": ta.pct(t["r5d"]),
                   "1个月": ta.pct(t["r1m"]), "今年以来": ta.pct(t["ytd"]),
                   "52周收盘区间": [round(x, 2) for x in t["range52w"]],
                   "近20日区间": [round(x, 2) for x in t["range20"]], "近60日区间": [round(x, 2) for x in t["range60"]],
                   "均线": {f"{n}日": round(v, 2) for n, v in t["ma"].items() if v}},
            "技术面": ta.describe(t),
            "板块对比": relative_text(r, b),
            "分析师": analyst_text(r["d"].get("analyst")),
            "估值": {("预期市盈率" if k == "forwardPE" else "过去12个月市盈率"): round(v, 1)
                   for k, v in (r["d"].get("valuation") or {}).items()},
            "财报日期": str(r["d"]["earnings"]) if r["d"].get("earnings") else None,
            "近7天新闻": [f"{x['date'][5:]}｜{x['source']}｜{x['title']}｜{x['summary']}" for x in r["news"]],
            "博主观点": views.get(r["yahoo"], []),
        })
    try:
        analyses = analyze(ask, cfg, payload) if payload else {}
    except Exception as e:
        print(f"  持仓分析失败：{type(e).__name__}", flush=True)
        analyses = {}
    theme_of = {b: a.theme for b, a in analyses.items()}
    for r in rows:  # 配置里写死的大类优先，保证每天一致
        if extra.get(r["yahoo"], {}).get("theme"):
            theme_of[base_symbol(r["symbol"])] = extra[r["yahoo"]]["theme"]

    flags = risk_flags(rows, theme_of, pcfg)
    day_total = sum(r["day"] for r in rows)
    with_cost = [r for r in rows if r["pnl"] is not None]
    pnl_total = sum(r["pnl"] for r in with_cost)
    cost_total = sum(r["value"] - r["pnl"] for r in with_cost)

    as_of_text = "；".join(f"{b} {a[:16].replace('T', ' ')}" for b, a in sources)
    out = ["## 我的持仓", "",
           f"持仓数据截至：{as_of_text}。价格来自长桥实时行情（取不到时用雅虎），日期见各卡片标题。", "",
           f"**总览**：股票市值 {money(stock_value)}，现金约 {money(cash_usd)}，合计约 {money(stock_value + cash_usd)}"
           f"（港币按 1 美元 = {fx:.2f} 港币折算）。上个交易日 {money(day_total, True)}"
           + (f"；按成本计累计 {money(pnl_total, True)}（{pnl_total / cost_total:+.1%}）" if cost_total else "")
           + "。", "",
           "| 股票 | 券商 | 数量 | 成本价 | 最新价 | 较成本 | 占股票市值 | 证据偏向 |",
           "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        mark = CURRENCY_MARK.get(r.get("currency", "USD"), "")
        cost = f"{mark}{r['cost_price']:,.2f}" if r.get("cost_price") else "—"
        price = f"{mark}{r['price']:,.2f}" if r["price"] else "—"
        pnl = f"{r['pnl_pct']:+.1%}（{money(r['pnl'], True)}）" if r["pnl"] is not None else "—"
        a = analyses.get(base_symbol(r["symbol"]))
        out.append(f"| {r['yahoo']} {r['name']} | {r['broker']} | {r['quantity']} | {cost} | {price} | {pnl} "
                   f"| {r['weight']:.0%} | {a.lean if a else '—'} |")
    out += ["", "### 风险提示", ""] + ([f"- {f}" for f in flags] or ["- 今天没有触发任何提示"]) + [""]
    out += ["### 盘面", "", market_line(pcfg["market"], bench), ""]
    for r in rows:
        out += card(r, bench.get(r["benchmark"]), views.get(r["yahoo"], []), analyses.get(base_symbol(r["symbol"])))
    out += ["“证据偏向”由 Gemini 根据上面的公开数据归纳，只说明证据倾向哪一边，不是买卖建议；"
            "技术指标的解读是技术分析里的通常说法，不保证应验。", ""]
    return "\n".join(out), set(held.values()) | set(held)


def risk_flags(rows: list[dict], theme_of: dict, pcfg: dict) -> list[str]:
    """全部按固定规则计算，不经过 AI（主题名称除外）。"""
    flags = []
    today = dt.datetime.now(LOCAL_TZ).date()
    for r in rows:
        sym, t = r["yahoo"], r["d"].get("t")
        if r["weight"] > pcfg["single_stock_warn"]:
            flags.append(f"**单只集中**：{sym} 占股票市值 {r['weight']:.0%}（提示线 {pcfg['single_stock_warn']:.0%}）")
        if r["pnl_pct"] is not None and r["pnl_pct"] <= -pcfg["loss_from_cost_warn"]:
            flags.append(f"**较成本下跌**：{sym} 比成本价低 {-r['pnl_pct']:.0%}")
        if t and t["ma"][200] and t["last"] < t["ma"][200]:
            flags.append(f"**低于 200 日均线**：{sym} 比 200 日均线低 {1 - t['last'] / t['ma'][200]:.0%}"
                         "（长期趋势偏弱的常用信号）")
        earnings = r["d"].get("earnings")
        if isinstance(earnings, dt.date) and 0 <= (earnings - today).days <= pcfg["earnings_within_days"]:
            flags.append(f"**财报临近**：{sym} 预计 {earnings:%m-%d} 发布财报，股价可能大幅波动")
    by_theme = defaultdict(list)
    for r in rows:
        theme = theme_of.get(base_symbol(r["symbol"]))
        if theme:
            by_theme[theme].append(r)
    for theme, rs in by_theme.items():
        w = sum(r["weight"] for r in rs)
        if len(rs) >= 2 and w > pcfg["theme_warn"]:
            flags.append(f"**主题集中**：{'、'.join(r['yahoo'] for r in rs)} 都属于“{theme}”，合计占股票市值 {w:.0%}")
    missing = [r["yahoo"] for r in rows if r["pnl"] is None]
    if missing:
        flags.append(f"**缺成本价**：{'、'.join(missing)} 没有成本价，"
                     f"没算进累计盈亏（在 {pcfg['manual_file']} 填上即可）")
    expiry = news.longbridge_token_expiry()
    if expiry and (expiry - today).days <= 14:
        flags.append(f"**长桥凭证即将过期**：{expiry:%m-%d} 到期，"
                     "请在长桥开放平台更新 Access Token（过期后长桥新闻会缺失）")
    return flags


def market_line(market: dict, bench: dict) -> str:
    parts = []
    for name, sym in market.items():
        b = bench.get(sym)
        if not b:
            continue
        if sym == "^VIX":
            mood = "通常视为情绪平稳" if b["last"] < 20 else "通常视为情绪紧张" if b["last"] < 30 else "通常视为恐慌"
            parts.append(f"{name} {b['last']:.1f}（{mood}）")
        else:
            parts.append(f"{name} 上个交易日 {ta.pct(b['r1d'])}、近 1 个月 {ta.pct(b['r1m'])}")
    return "；".join(parts) + "。" if parts else "（盘面数据暂时取不到）"


def cited_only(points: list[str], news_items: list[dict]) -> list[str]:
    """只保留末尾注明的来源确实出现在抓到的新闻里的条目，防止模型把技术指标当新闻、或编造来源。"""
    sources = {x["source"].lower() for x in news_items}
    kept = []
    for point in points:
        m = re.search(r"[（(]\s*\d{1,2}-\d{1,2}[\s｜|]*([^（）()]+)[）)]\s*$", point)
        cited = m.group(1).strip().lower() if m else ""
        if cited and any(cited in s or s in cited for s in sources):
            kept.append(point)
    return kept


def card(r: dict, b: dict | None, blogger: list[str], a: HoldingAnalysis | None) -> list[str]:
    d, t = r["d"], r["d"].get("t")
    mark = CURRENCY_MARK.get(r.get("currency", "USD"), "")
    if not t:
        return [f"### {r['yahoo']} {r['name']}", "", "（行情数据暂时取不到）", ""]
    when = "盘中" if r["intraday"] else f"{d['price_date']:%m-%d} 收盘"
    change = ta.pct(t["last"] / d["prev"] - 1) if d.get("prev") else ta.pct(t["r1d"])
    head = (f"### {r['yahoo']} {r['name']}｜{mark}{t['last']:,.2f}（{when}，{change}）"
            + (f"｜证据：{a.lean}" if a else ""))
    lo52, hi52 = t["range52w"]
    lines = [f"**价位**：5 日 {ta.pct(t['r5d'])}，1 个月 {ta.pct(t['r1m'])}，今年以来 {ta.pct(t['ytd'])}；"
             f"52 周收盘区间 {mark}{lo52:,.2f}–{mark}{hi52:,.2f}，距高点 {ta.pct(t['last'] / hi52 - 1)}"
             + (f"；距你的成本价 {ta.pct(r['pnl_pct'])}" if r["pnl_pct"] is not None else "")]
    lines += [f"**{k}**：{v}" for k, v in ta.describe(t).items()]
    lo20, hi20 = t["range20"]
    lo60, hi60 = t["range60"]
    ma_text = "、".join(f"{n} 日均线 {mark}{v:,.2f}" for n, v in t["ma"].items() if v)
    lines.append(f"**关键价位**：近 20 日收盘区间 {mark}{lo20:,.2f}–{mark}{hi20:,.2f}；"
                 f"近 60 日 {mark}{lo60:,.2f}–{mark}{hi60:,.2f}；{ma_text}")
    if b and b.get("r1m") is not None and t["r1m"] is not None:
        gap = (t["r1m"] - b["r1m"]) * 100
        lines.append(f"**与板块对比**：{r['benchmark']} 近 1 个月 {ta.pct(b['r1m'])}，{r['yahoo']} "
                     f"{'跑赢' if gap >= 0 else '跑输'} {abs(gap):.1f} 个百分点")
    an = d.get("analyst")
    if an:
        lines.append(f"**分析师**：{an['buy'] + an['hold'] + an['sell']} 位——{an['buy']} 买入、{an['hold']} 持有、"
                     f"{an['sell']} 卖出；平均目标价 {mark}{an['mean']:,.2f}"
                     f"（比现价 {ta.pct(an['mean'] / t['last'] - 1)}），"
                     f"区间 {mark}{an['low']:,.2f}–{mark}{an['high']:,.2f}")
    if d.get("valuation"):
        lines.append("**估值**：" + "，".join(f"{'预期' if k == 'forwardPE' else '过去 12 个月'}市盈率 {v:.1f} 倍"
                                           for k, v in d["valuation"].items()))
    if isinstance(d.get("earnings"), dt.date):
        lines.append(f"**下次财报**：预计 {d['earnings']:%Y-%m-%d}")
    out = [head, ""] + [f"- {x}" for x in lines] + [""]

    sources = "、".join(r["news_sources"]) or "无"
    good, bad = (cited_only(a.good_news, r["news"]), cited_only(a.bad_news, r["news"])) if a else ([], [])
    if good or bad:
        out += [f"**近 7 天新闻**（来源：{sources}）", ""]
        out += [f"- 利好：{x}" for x in good] + [f"- 利空：{x}" for x in bad] + [""]
    elif r["news"]:
        out += [f"**近 7 天新闻**（来源：{sources}）", ""]
        out += [f"- {x['date'][5:]} {x['source']}：{x['title']}" for x in r["news"][:5]] + [""]
    if blogger:
        out += ["**博主怎么看**", ""] + [f"- {v}" for v in blogger] + [""]
    if a:
        out += ["**看多证据**", ""] + [f"- {x}" for x in a.bull_points] + [""]
        out += ["**看空证据**", ""] + [f"- {x}" for x in a.bear_points] + [""]
        out += ["**接下来要盯**", ""] + [f"- {x}" for x in a.watch] + [""]
        out += [f"**证据天平：{a.lean}**——{a.lean_reason}", ""]
    return out
