"""给 Gemini 的输出格式和提示词；看视频、整理 X 帖子、写“今日共识与分歧”。"""
from __future__ import annotations

import json
import re

from google.genai import types
from pydantic import BaseModel, Field

from . import x_posts


class StockView(BaseModel):
    symbol: str = Field(description="美股代码，如 NVDA；ETF 或指数也可以，如 QQQ、SPY。不确定就留空字符串")
    name: str = Field(description="公司或产品名称")
    stance: str = Field(description="博主态度，只能是：看多、看空、中性")
    reason: str = Field(description="博主给出的理由，一句话")


class VideoSummary(BaseModel):
    is_market_related: bool = Field(description="视频是否主要在讲股市、宏观经济或投资")
    one_line: str = Field(description="一句话概括视频的核心结论")
    main_points: list[str] = Field(description="3-5 条核心观点，每条一句话，按博主的立场陈述")
    stocks: list[StockView] = Field(description="视频中明确讨论到的个股、ETF 或指数")
    key_facts: list[str] = Field(description="视频中引用的具体数据或事实（带数字），最多 5 条")
    risks: list[str] = Field(description="博主提到的风险或反面因素，最多 3 条")


VIDEO_PROMPT = """你是一名客观的财经内容整理员。请完整观看这个 YouTube 视频（博主：{channel}，标题：{title}），提炼博主本人的观点。
要求：
1. 只转述博主说了什么，不要加入你自己的判断或建议。
2. 事实（有具体数字或来源）放 key_facts；预测、判断等观点放 main_points。
3. 股票用美股代码；博主没有明确表态的记为“中性”。
4. 全部用简体中文，不要出现繁体字。"""  # noqa: E501

SYNTH_PROMPT = """下面是今天 {n} 个财经博主视频的结构化摘要（JSON）。请写“今日共识与分歧”，分三组：
- **共识**：多位博主一致的判断，注明是哪几位
- **分歧**：对同一问题或同一只股票看法相反的地方，注明各方观点
- **值得自己核实**：博主给出的关键说法中，最需要读者自己去核实的（最多 3 条）
要求：
1. 只依据下面 JSON 里博主明确表达的内容；博主只是引用数据、没有表态的，不要写成他看多或看空。
2. 只整理博主观点，不给出你自己的买卖建议。
3. 不要输出任何标题，直接用 **共识**、**分歧**、**值得自己核实** 三个加粗小标题加 Markdown 列表；总共不超过 12 条；用简体中文。
4. 只有一位博主时，只写该博主最值得注意的 3 条。

{data}"""  # noqa: E501


def summarize_video(llm, cfg: dict, v: dict):
    """让 Gemini 看一个视频。返回（整理结果，实际用的模型，用量）；在线程里运行，用量由调用方在主线程记账。"""
    resp, model = llm.generate(
        cfg["video_models"], label=f"video:{v['id']}",
        contents=types.Content(role="user", parts=[
            types.Part(file_data=types.FileData(file_uri=v["url"]),
                       video_metadata=types.VideoMetadata(fps=cfg["video_fps"],
                                                          end_offset=f"{cfg.get('max_minutes', 50) * 60}s")),
            types.Part(text=VIDEO_PROMPT.format(channel=v["channel"], title=v["title"])),
        ]),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=VideoSummary,
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
        ),
    )
    if resp.parsed is None:
        raise ValueError(f"Gemini 没有返回有效结果：{(resp.text or '')[:200]}")
    return resp.parsed, model, resp.usage_metadata


def x_channel_name(account: dict) -> str:
    """X 账号在日报里显示的博主名。"""
    return f"{account['name']}（X）"


def summarize_x(llm, cfg: dict, account: dict, posts: list[dict], today: str) -> tuple[dict, VideoSummary]:
    """把一个 X 账号的新帖子当作一个“视频”整理。返回（条目信息，整理结果）；失败时抛异常。"""
    v = {"channel": x_channel_name(account), "title": f"最近 {len(posts)} 条帖子",
         "url": f"https://x.com/{account['handle']}", "id": f"x:{account['handle']}:{today}",
         "published": posts[0]["time"], "note": account.get("note", "")}
    resp = llm.ask(cfg["synth_models"], label=f"x:{account['handle']}", contents=x_posts.prompt_for(account, posts),
                   config=types.GenerateContentConfig(response_mime_type="application/json",
                                                      response_schema=VideoSummary))
    if resp.parsed is None:
        raise ValueError("Gemini 没有返回有效结果")
    return v, resp.parsed


def synthesize(llm, cfg: dict, results: list) -> str:
    data = json.dumps([{"博主": v["channel"], "标题": v["title"], **s.model_dump()} for v, s in results],
                      ensure_ascii=False)
    prompt = SYNTH_PROMPT.format(n=len(results), data=data)
    try:
        text = llm.ask(cfg["synth_models"], label="synth", contents=prompt).text
    except Exception:
        return "（今天的汇总生成失败，请直接看下面各博主要点。）"
    text = re.sub(r"^#+\s*今日共识与分歧\s*\n?", "", text.strip(), flags=re.M)
    return re.sub(r"^#+\s*(.+)$", r"**\1**", text, flags=re.M)  # 模型偶尔自带标题，统一改成加粗小标题
