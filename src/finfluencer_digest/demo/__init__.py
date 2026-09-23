"""演示模式的素材：虚构的频道、X 账号和录好的模型回复，不需要密钥和网络就能跑完整流程。
config.yaml 是演示配置；rss/<频道 ID>.xml 是频道的 RSS 订阅源；x/<账号>.json 是 X 帖子接口的原始回复；
responses.json 按调用标签存模型回复（video:<视频 ID>、x:<账号>、synth）。
股票代码是真实的，博主、帖子、观点和数字都是虚构的。
演示不读环境变量，关注列表固定为 NVDA，让日报出现“你关注的股票”一节。
"""
from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from importlib.resources import files
from typing import NamedTuple

import yaml

from ..llm import RecordedGemini
from ..youtube import parse_feed

NOW = dt.datetime(2026, 9, 23, 9, 0, tzinfo=dt.UTC)  # 固定的“当前时间”，每次运行输出都一样
WATCH = frozenset({"NVDA"})  # 固定的关注列表（正式运行来自环境变量 WATCHLIST）
TOKENS = (48000, 3000)  # 每次模型调用记的输入、输出 tokens，让用量行的量级接近真实运行
ROOT = files(__name__)


class Demo(NamedTuple):
    cfg: dict
    fetch_rss: Callable[[str], list[dict]]  # 频道 ID → 视频列表
    fetch_x: Callable[[str], dict]  # X 账号 → 帖子接口的原始回复
    llm: RecordedGemini
    now: dt.datetime
    watch: frozenset[str]  # 你关注的股票


def _text(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def load_demo() -> Demo:
    return Demo(
        cfg=yaml.safe_load(_text("config.yaml")),
        fetch_rss=lambda channel_id: parse_feed(ROOT.joinpath("rss", f"{channel_id}.xml").read_bytes()),
        fetch_x=lambda handle: json.loads(_text("x", f"{handle}.json")),
        llm=RecordedGemini(json.loads(_text("responses.json")), tokens=TOKENS),
        now=NOW,
        watch=WATCH,
    )
