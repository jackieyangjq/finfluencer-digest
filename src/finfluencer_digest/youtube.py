"""找新视频：先读频道的 RSS，失效时改用 YouTube 官方数据接口；按时间窗口、已处理记录和 Shorts 过滤。"""
from __future__ import annotations

import datetime as dt
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .llm import short_error

ATOM_NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}


def http_get(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as r:
        return r.read()


def videos_from_rss(channel_id: str) -> list[dict]:
    root = ET.fromstring(http_get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"))
    videos = []
    for e in root.findall("a:entry", ATOM_NS):
        link = e.find("a:link", ATOM_NS).get("href", "")
        videos.append({
            "id": e.find("yt:videoId", ATOM_NS).text,
            "title": e.find("a:title", ATOM_NS).text or "",
            "published": dt.datetime.fromisoformat(e.find("a:published", ATOM_NS).text),
            "is_short": "/shorts/" in link,
        })
    return videos


def videos_from_api(channel_id: str, api_key: str) -> list[dict]:
    """RSS 失效时的备用方案：YouTube 官方数据接口（需要 YOUTUBE_API_KEY）。"""
    query = urlencode({"part": "snippet", "playlistId": "UU" + channel_id[2:], "maxResults": 10, "key": api_key})
    data = json.loads(http_get(f"https://www.googleapis.com/youtube/v3/playlistItems?{query}"))
    return [{
        "id": it["snippet"]["resourceId"]["videoId"],
        "title": it["snippet"]["title"],
        "published": dt.datetime.fromisoformat(it["snippet"]["publishedAt"].replace("Z", "+00:00")),
        "is_short": False,
    } for it in data.get("items", [])]


def find_new_videos(cfg: dict, seen: dict, *, now: dt.datetime, fetch_rss=videos_from_rss,
                    fetch_api=videos_from_api, yt_key: str | None = None) -> tuple[list[dict], list[str]]:
    """返回（每位博主最近 lookback_hours 小时内没处理过的视频，获取失败的说明）。
    now 是带时区的当前时间；yt_key 是 YouTube 官方接口的密钥，RSS 失效时才用，没有就不用备用方案。"""
    since = now - dt.timedelta(hours=cfg["lookback_hours"])
    picked, errors = [], []
    for ch in cfg["channels"]:
        try:
            vids = fetch_rss(ch["id"])
        except Exception as e:
            try:
                if not yt_key:
                    raise
                vids = fetch_api(ch["id"], yt_key)
            except Exception:
                errors.append(f"频道「{ch['name']}」的视频列表获取失败：{short_error(e)}")
                continue
        fresh = [v for v in vids
                 if v["published"] >= since and v["id"] not in seen
                 and not (v["is_short"] and cfg.get("skip_shorts", True))]
        fresh.sort(key=lambda v: v["published"], reverse=True)
        for v in fresh[: cfg["max_videos_per_channel"]]:
            v["channel"] = ch["name"]
            v["url"] = f"https://www.youtube.com/watch?v={v['id']}"
            picked.append(v)
    return picked, errors


def find_channel_id(handle: str) -> str | None:
    handle = handle if handle.startswith("@") else "@" + handle
    html = http_get(f"https://www.youtube.com/{quote(handle)}").decode("utf-8", "ignore")
    m = (re.search(r"feeds/videos\.xml\?channel_id=(UC[\w-]{22})", html)
         or re.search(r'"externalId":"(UC[\w-]{22})"', html))
    return m.group(1) if m else None
