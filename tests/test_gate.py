import datetime as dt
from zoneinfo import ZoneInfo

from finfluencer_digest.gate import should_run

LONDON = ZoneInfo("Europe/London")


def test_before_eight_does_not_run(tmp_path):
    assert should_run(dt.datetime(2026, 9, 23, 7, 59, tzinfo=LONDON), tmp_path) == (False, "英国时间还没到 8 点")


def test_already_sent_today_does_not_run(tmp_path):
    (tmp_path / "2026-09-23.md").write_text("x")
    ok, why = should_run(dt.datetime(2026, 9, 23, 8, 10, tzinfo=LONDON), tmp_path)
    assert ok is False and "2026-09-23" in why


def test_runs_when_due(tmp_path):
    assert should_run(dt.datetime(2026, 9, 23, 8, 10, tzinfo=LONDON), tmp_path) == (True, "")
    assert should_run(dt.datetime(2026, 9, 23, 8, 10, tzinfo=LONDON), tmp_path / "missing") == (True, "")
