import datetime as dt
from pathlib import Path

from finfluencer_digest import youtube

FIX = Path(__file__).parent / "fixtures" / "rss_sample.xml"
NOW = dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.UTC)
CFG = {"channels": [{"name": "甲", "id": "UCxxxxxxxxxxxxxxxxxxxxxx"}], "lookback_hours": 36,
       "max_videos_per_channel": 3, "skip_shorts": True}


def test_videos_from_rss_parses_entries(monkeypatch):
    monkeypatch.setattr(youtube, "http_get", lambda url: FIX.read_bytes())
    vids = youtube.videos_from_rss("UCxxxxxxxxxxxxxxxxxxxxxx")
    assert [v["id"] for v in vids] == ["abc123", "short1"]
    assert vids[0]["title"] == "财报解读" and vids[0]["is_short"] is False and vids[1]["is_short"] is True
    assert vids[0]["published"] == dt.datetime(2026, 9, 23, 6, 0, tzinfo=dt.UTC)


def test_find_new_videos_filters_seen_shorts_and_lookback():
    vids = [
        {"id": "old", "title": "t", "published": NOW - dt.timedelta(hours=40), "is_short": False},
        {"id": "seen", "title": "t", "published": NOW - dt.timedelta(hours=1), "is_short": False},
        {"id": "short", "title": "t", "published": NOW - dt.timedelta(hours=1), "is_short": True},
        {"id": "new2", "title": "t", "published": NOW - dt.timedelta(hours=5), "is_short": False},
        {"id": "new1", "title": "t", "published": NOW - dt.timedelta(hours=2), "is_short": False},
    ]
    picked, errors = youtube.find_new_videos(CFG, {"seen": "2026-09-22"}, now=NOW, fetch_rss=lambda cid: vids)
    assert [v["id"] for v in picked] == ["new1", "new2"] and errors == []
    assert picked[0]["channel"] == "甲" and picked[0]["url"] == "https://www.youtube.com/watch?v=new1"


def test_find_new_videos_caps_per_channel():
    vids = [{"id": f"v{i}", "title": "t", "published": NOW - dt.timedelta(hours=i), "is_short": False}
            for i in range(5)]
    picked, _ = youtube.find_new_videos({**CFG, "max_videos_per_channel": 2}, {}, now=NOW, fetch_rss=lambda cid: vids)
    assert [v["id"] for v in picked] == ["v0", "v1"]


def test_find_new_videos_falls_back_to_api_then_reports_error():
    def boom(cid):
        raise OSError("rss down")
    ok = [{"id": "a", "title": "t", "published": NOW, "is_short": False}]
    picked, errors = youtube.find_new_videos(CFG, {}, now=NOW, fetch_rss=boom, fetch_api=lambda cid, key: ok,
                                             yt_key="k")
    assert [v["id"] for v in picked] == ["a"] and errors == []
    picked, errors = youtube.find_new_videos(CFG, {}, now=NOW, fetch_rss=boom, fetch_api=boom, yt_key=None)
    assert picked == [] and len(errors) == 1 and "甲" in errors[0]
