"""定时任务的前置检查。GitHub 的免费定时在繁忙时会延迟甚至丢弃，所以每 30 分钟检查一次：
英国时间已过 8 点、且今天的日报还没发，才真正运行，保证一天只发一封。"""
from __future__ import annotations

import datetime as dt
from pathlib import Path


def should_run(now_london: dt.datetime, digest_dir: Path) -> tuple[bool, str]:
    """返回（该不该运行，不运行的原因）。digest_dir 不存在视为今天还没发。"""
    today = now_london.strftime("%Y-%m-%d")
    if now_london.hour < 8:
        return False, "英国时间还没到 8 点"
    if (digest_dir / f"{today}.md").exists():
        return False, f"今天（{today}）的日报已经发过"
    return True, ""
