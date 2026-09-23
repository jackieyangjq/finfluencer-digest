import os
import socket

from finfluencer_digest.cli import main


def test_demo_runs_end_to_end_without_network(tmp_path, capsys):
    assert main(["--demo", "--out", str(tmp_path)]) == 0
    md = (tmp_path / "2026-09-23.md").read_text(encoding="utf-8")
    for h in ("## 今日共识与分歧", "## 个股提及统计", "## 各博主要点", "今日 Gemini 用量"):
        assert h in md
    assert "示例博主·甲" in md and "示例交易员（X）" in md and "Shorts" not in md
    assert "NVDA" in capsys.readouterr().out


def test_demo_needs_no_keys_network_or_local_files(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("演示模式不应联网")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    for key in ("GEMINI_API_KEY", "YOUTUBE_API_KEY", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "EMAIL_TO"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WATCHLIST", "NVDA")  # 演示模式不读环境变量，所以不会出现“你关注的股票”
    (tmp_path / ".env").write_text("GEMINI_API_KEY=from-dotenv\n", encoding="utf-8")  # 也不加载 .env
    monkeypatch.chdir(tmp_path)

    assert main(["--demo"]) == 0
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert written == [".env", "demo-output", "demo-output/2026-09-23.md"]  # 没有 state/、data/、digests/
    assert "## 你关注的股票" not in (tmp_path / "demo-output" / "2026-09-23.md").read_text(encoding="utf-8")
    assert "GEMINI_API_KEY" not in os.environ
