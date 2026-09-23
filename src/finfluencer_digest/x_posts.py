"""读取 X（推特）账号的最新帖子，交给 Gemini 按博主观点的格式整理。
用免费的 FxTwitter 公开接口：不需要密钥，也不用你的 X 账号。它不是官方服务，随时可能失效；
失效时只在日报末尾提示，不影响其他内容。
"""
from __future__ import annotations

import datetime as dt
import json
from urllib.request import Request, urlopen

FX_URL = "https://api.fxtwitter.com/2/profile/{handle}/statuses?count=40"

X_PROMPT = """下面是 X 账号 @{handle}（{name}）最近 {n} 条帖子，按时间倒序。请像整理视频一样提炼这位博主的观点：
1. 只转述博主说了什么，不要加入你自己的判断或建议。
2. 跳过纯广告（推销产品、课程、会员、AI 工具）和“交易心态”类内容，只整理与市场、个股相关的内容；如果全部都是这类内容，is_market_related 设为 false。
3. 事实（财报数字、评级调整、内部人交易等）放 key_facts；预测、判断、价位喊单放 main_points，喊单保留原来的价位。
4. 股票用美股代码；明确说要买、看涨或看突破的记为“看多”，看跌的记为“看空”，只是罗列的记为“中性”。
5. 全部用简体中文。

帖子：
{posts}"""  # noqa: E501


def _fetch_json(url: str) -> dict:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def recent_posts(handle: str, since: dt.datetime, seen: dict, *, fetch=_fetch_json) -> list[dict]:
    """返回 since 之后、没处理过的原创帖子（跳过转发），按时间倒序。"""
    data = fetch(FX_URL.format(handle=handle))
    posts = []
    for t in data.get("results") or []:
        created = dt.datetime.fromtimestamp(t["created_timestamp"], dt.UTC)
        key = f"x:{t['id']}"
        if created >= since and key not in seen and not t.get("reposted_by"):
            posts.append({"key": key, "time": created, "text": t.get("text") or "", "url": t.get("url", "")})
    return sorted(posts, key=lambda p: p["time"], reverse=True)


def prompt_for(account: dict, posts: list[dict]) -> str:
    body = "\n\n".join(f"[{p['time']:%m-%d %H:%M} UTC] {p['text']}" for p in posts)
    return X_PROMPT.format(handle=account["handle"], name=account["name"], n=len(posts), posts=body)
