"""技术指标：用每日收盘价和成交量计算，并附上技术分析里通常的解读。只描述指标，不给操作建议。"""
from __future__ import annotations

import pandas as pd


def _rsi(close: pd.Series, n: int = 14) -> float:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return float((100 - 100 / (1 + up / down)).iloc[-1])


def compute(hist: pd.DataFrame) -> dict:
    close = hist["Close"].dropna()
    volume = hist["Volume"].reindex(close.index).fillna(0)
    last = float(close.iloc[-1])

    def ret(days: int):
        return last / float(close.iloc[-days - 1]) - 1 if len(close) > days else None

    this_year = close[close.index.year == close.index[-1].year]
    prior = close[close.index.year < close.index[-1].year]
    macd_line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    diff = macd_line - macd_line.ewm(span=9, adjust=False).mean()
    above = diff > 0
    flips = [i for i in range(1, min(11, len(above))) if above.iloc[-i] != above.iloc[-i - 1]]
    avg_vol = volume.iloc[-21:-1].mean()
    return {
        "last": last,
        "r1d": ret(1), "r5d": ret(5), "r1m": ret(21),
        "ytd": last / float(prior.iloc[-1]) - 1 if len(prior) and len(this_year) else None,
        "ma": {n: float(close.tail(n).mean()) if len(close) >= n else None for n in (20, 50, 200)},
        "rsi": _rsi(close) if len(close) > 15 else None,
        "macd_above": bool(above.iloc[-1]),
        "macd_cross_days": flips[0] - 1 if flips else None,  # 几个交易日前发生了交叉
        "vol_ratio": float(volume.iloc[-1] / avg_vol) if avg_vol else None,
        "range20": (float(close.tail(20).min()), float(close.tail(20).max())),
        "range60": (float(close.tail(60).min()), float(close.tail(60).max())),
        "range52w": (float(close.tail(252).min()), float(close.tail(252).max())),
    }


def describe(t: dict) -> dict[str, str]:
    """把指标翻译成一句话，并注明技术分析里通常的解读。"""
    last, ma = t["last"], t["ma"]
    out = {}
    if all(ma.values()):
        if last > ma[20] > ma[50] > ma[200]:
            out["趋势"] = "多头排列：价格在 20、50、200 日均线之上且依次排列，通常视为上升趋势"
        elif last < ma[20] < ma[50] < ma[200]:
            out["趋势"] = "空头排列：价格在 20、50、200 日均线之下且依次排列，通常视为下降趋势"
        elif all(last > v for v in ma.values()):
            out["趋势"] = "价格站在 20、50、200 日均线之上，但均线尚未依次排列，趋势偏强"
        elif all(last < v for v in ma.values()):
            out["趋势"] = "价格在 20、50、200 日均线之下，但均线尚未形成空头排列，趋势偏弱"
        else:
            parts = [f"{'高于' if last > ma[n] else '低于'} {n} 日均线（{ma[n]:,.2f}）" for n in (20, 50, 200)]
            long_term = "长期趋势偏强" if last > ma[200] else "长期趋势偏弱"
            out["趋势"] = f"均线交织：价格{'，'.join(parts)}；{long_term}"
    if t["rsi"] is not None:
        r = t["rsi"]
        zone = ("超买区，短期涨得较急，常伴随回调风险" if r >= 70 else
                "超卖区，短期跌得较急，常出现技术性反弹" if r <= 30 else "中性区间（30–70）")
        out["RSI"] = f"{r:.0f}，{zone}"
    momentum = "MACD 在信号线上方，短期动能偏强" if t["macd_above"] else "MACD 在信号线下方，短期动能偏弱"
    if t["macd_cross_days"] is not None:
        kind = "金叉（由弱转强）" if t["macd_above"] else "死叉（由强转弱）"
        momentum += (f"；{t['macd_cross_days']} 个交易日前刚出现{kind}" if t["macd_cross_days"]
                     else f"；最近一个交易日刚出现{kind}")
    out["动能"] = momentum
    if t["vol_ratio"] is not None:
        v = t["vol_ratio"]
        out["成交量"] = f"上个交易日为 20 日均量的 {v:.1f} 倍，" + (
            "明显放量，说明分歧或关注度上升" if v >= 1.5 else "缩量，交投清淡" if v <= 0.7 else "量能正常")
    return out


def pct(x: float | None) -> str:
    return f"{x:+.1%}" if x is not None else "—"
