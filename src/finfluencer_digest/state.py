"""运行记录：处理过的视频和帖子（state/seen.json）、博主观点记录（data/calls.jsonl）、日报存档（digests/）。"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


class Paths:
    """所有读写位置都相对同一个根目录（配置文件所在的目录）。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.state_file = self.root / "state" / "seen.json"
        self.calls_file = self.root / "data" / "calls.jsonl"  # 每位博主每天对每只股票的看法，日后用来算博主的真实胜率
        self.digest_dir = self.root / "digests"
        self.holdings_root = self.root  # 手动持仓表和 portfolio/*.json 持仓快照所在的目录

    @classmethod
    def from_config(cls, config_path: Path | str) -> Paths:
        return cls(Path(config_path).resolve().parent)


def load_seen(paths: Paths) -> dict:
    return json.loads(paths.state_file.read_text(encoding="utf-8")) if paths.state_file.exists() else {}


def save_seen(paths: Paths, seen: dict, today: dt.date) -> None:
    """只保留最近 60 天处理过的记录。"""
    cutoff = (today - dt.timedelta(days=60)).isoformat()
    seen = {k: d for k, d in seen.items() if d >= cutoff}
    paths.state_file.parent.mkdir(exist_ok=True)
    paths.state_file.write_text(json.dumps(seen, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def save_calls(paths: Paths, rows: list[dict]) -> None:
    paths.calls_file.parent.mkdir(exist_ok=True)
    with paths.calls_file.open("a", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
