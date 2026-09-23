"""汇总各博主的结构化摘要：按股票统计多空，整理成逐条落盘的观点记录。都是纯函数。"""
from __future__ import annotations

from collections import defaultdict

STANCES = ("看多", "看空", "中性")


def norm_symbol(symbol: str) -> str:
    return symbol.strip().upper().lstrip("$")


def stock_mentions(results: list) -> dict:
    agg = defaultdict(lambda: {"name": "", **{s: [] for s in STANCES}, "reasons": []})
    for v, s in results:
        for st in s.stocks:
            sym = norm_symbol(st.symbol)
            if not sym:
                continue
            stance = st.stance if st.stance in STANCES else "中性"
            a = agg[sym]
            a["name"] = a["name"] or st.name
            if v["channel"] not in a[stance]:
                a[stance].append(v["channel"])
            a["reasons"].append(f"{v['channel']}（{stance}）：{st.reason}")
    return agg


def call_rows(date: str, results: list) -> list[dict]:
    """每位博主每天对每只股票的看法，一行一条，日后用来算博主的真实胜率。"""
    return [{"date": date, "channel": v["channel"], "video_id": v["id"], "symbol": norm_symbol(st.symbol),
             "name": st.name, "stance": st.stance, "reason": st.reason}
            for v, s in results for st in s.stocks if norm_symbol(st.symbol)]
