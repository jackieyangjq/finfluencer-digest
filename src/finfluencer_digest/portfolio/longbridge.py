"""长桥开放接口（只读）：行情和资讯。凭证从环境变量读取；没配置或出错时返回空，调用方自动改用其他来源。
这套凭证理论上有交易权限，本程序只调用行情和资讯，不调用任何交易功能。
"""
from __future__ import annotations

import datetime as dt
import os
from functools import cache

KEYS = ("LONGBRIDGE_APP_KEY", "LONGBRIDGE_APP_SECRET", "LONGBRIDGE_ACCESS_TOKEN")


def available() -> bool:
    return all(os.getenv(k) for k in KEYS)


@cache
def _config():
    os.environ.setdefault("LONGBRIDGE_LANGUAGE", "zh-CN")
    os.environ.setdefault("LONGBRIDGE_PRINT_QUOTE_PACKAGES", "false")  # 不打印行情权限表
    from longbridge.openapi import Config
    return Config.from_apikey(*(os.environ[k] for k in KEYS))


@cache
def _quote_ctx():
    from longbridge.openapi import QuoteContext
    return QuoteContext(_config())


@cache
def _content_ctx():
    from longbridge.openapi import ContentContext
    return ContentContext(_config())


def to_longbridge(yahoo: str) -> str | None:
    """行情库代码转长桥代码：VRT → VRT.US，1211.HK → 1211.HK，^HSI → HSI.HK；其他指数返回 None。"""
    if yahoo == "^HSI":
        return "HSI.HK"
    if yahoo.endswith(".HK"):
        return yahoo.lstrip("0")
    if yahoo.isalpha():
        return f"{yahoo}.US"
    return None


def quotes(yahoo_symbols: list[str]) -> dict[str, dict]:
    """返回 {行情库代码: {last, prev, date, volume}}；取不到的代码不在结果里。"""
    mapping = {to_longbridge(s): s for s in yahoo_symbols if to_longbridge(s)}
    if not available() or not mapping:
        return {}
    try:
        result = _quote_ctx().quote(list(mapping))
    except Exception as e:
        print(f"  长桥行情获取失败：{type(e).__name__}", flush=True)
        return {}
    out = {}
    for q in result:
        if q.symbol in mapping and float(q.last_done) > 0:
            out[mapping[q.symbol]] = {"last": float(q.last_done), "prev": float(q.prev_close),
                                      "date": q.timestamp.date() if isinstance(q.timestamp, dt.datetime) else None,
                                      "volume": int(q.volume)}
    return out


def news(symbol: str) -> list:
    """长桥资讯；symbol 用长桥格式（VRT.US、1211.HK）。"""
    return _content_ctx().news(symbol) if available() else []
