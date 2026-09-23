import datetime as dt
import json

from finfluencer_digest.state import Paths, load_seen, save_calls, save_seen


def test_save_seen_drops_entries_older_than_60_days(tmp_path):
    p = Paths(tmp_path)
    today = dt.date(2026, 9, 23)
    save_seen(p, {"old": "2026-07-01", "keep": "2026-08-01"}, today)
    assert load_seen(p) == {"keep": "2026-08-01"}


def test_save_calls_appends_jsonl(tmp_path):
    p = Paths(tmp_path)
    save_calls(p, [{"a": 1}])
    save_calls(p, [{"b": "中"}])
    lines = p.calls_file.read_text(encoding="utf-8").splitlines()
    assert [json.loads(x) for x in lines] == [{"a": 1}, {"b": "中"}] and "中" in lines[1]
