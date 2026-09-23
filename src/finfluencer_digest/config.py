"""读取配置文件和 .env。密钥只从环境变量读；配置文件所在目录如果有 .env，先把它加载进环境变量。"""
from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # 云端不需要 .env
    load_dotenv = None

LOCAL_TZ = ZoneInfo("Europe/London")  # 日报日期、定时检查都按英国时间


def load_env(root: Path) -> None:
    if load_dotenv:
        load_dotenv(root / ".env")


def load_config(path: Path | str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
