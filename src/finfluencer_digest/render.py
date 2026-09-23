"""拼日报 Markdown，并转成邮件用的 HTML。"""
from __future__ import annotations

import re

import markdown

from .aggregate import STANCES, stock_mentions


def build_digest(date_label: str, results: list, synth: str, failures: list, skipped: list, watch: set,
                 usage_line: str = "", portfolio_md: str = "", held: frozenset = frozenset()) -> str:
    channels = list(dict.fromkeys(v["channel"] for v, _ in results))
    n_videos = sum(1 for v, _ in results if not v["id"].startswith("x:"))
    title = "投资日报" if portfolio_md else "财经博主日报"
    out = [f"# {title} · {date_label}", "", "以下只是数据整理和博主观点汇总，不构成投资建议。", ""]
    if portfolio_md:
        out += [portfolio_md, ""]
    if not results:
        out += ["## 博主观点", "", "今天没有需要整理的新视频。", ""]
    else:
        out += ["## 今日共识与分歧", "",
                f"今天整理了 {n_videos} 个新视频"
                + (f"和 {len(results) - n_videos} 个 X 账号的帖子" if len(results) > n_videos else "")
                + f"，来自 {len(channels)} 位博主：{'、'.join(channels)}。", "",
                synth, ""]

        mentions = stock_mentions(results)
        hits = [sym for sym in sorted(watch - held) if sym in mentions]  # 持有的股票已在“我的持仓”里列出
        if hits:
            out += ["## 你关注的股票", ""]
            for sym in hits:
                out += [f"**{sym}**（{mentions[sym]['name']}）", ""]
                out += [f"- {r}" for r in mentions[sym]["reasons"]]
                out.append("")

        if mentions:
            def cell(names):
                return f"{len(names)}（{'、'.join(names)}）" if names else ""
            ranked = sorted(mentions.items(), key=lambda kv: -sum(len(kv[1][s]) for s in STANCES))
            out += ["## 个股提及统计", "", "| 代码 | 名称 | 看多 | 看空 | 中性 |", "|---|---|---|---|---|"]
            for sym, a in ranked:
                mark = "⭐ " if sym in watch | held else ""
                out.append(f"| {mark}{sym} | {a['name']} | {cell(a['看多'])} | {cell(a['看空'])} | {cell(a['中性'])} |")
            out.append("")

        out += ["## 各博主要点", ""]
        for v, s in sorted(results, key=lambda r: channels.index(r[0]["channel"])):
            out += [f"### {v['channel']}：[{v['title']}]({v['url']})", ""]
            if v.get("note"):
                out += [f"*{v['note']}*", ""]
            out += [f"**一句话**：{s.one_line}", ""]
            out += [f"- {p}" for p in s.main_points] + [""]
            if s.key_facts:
                out += ["**提到的数据**：", ""] + [f"- {x}" for x in s.key_facts] + [""]
            if s.risks:
                out += ["**博主提到的风险**：", ""] + [f"- {x}" for x in s.risks] + [""]

    if skipped:
        out += (["---", "", "以下内容与投资无关，已跳过：", ""]
                + [f"- {v['channel']}：{v['title']}" for v, _ in skipped] + [""])
    if failures:
        out += (["---", "", "以下内容处理失败（仍在时间范围内的视频，下次运行会再试）：", ""]
                + [f"- {f}" for f in failures] + [""])
    if usage_line:
        out += ["---", "", usage_line, ""]
    return "\n".join(out)


HTML_TEMPLATE = """<html><head><meta charset="utf-8"><style>
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;max-width:760px;margin:auto;line-height:1.6;color:#222}}
table{{border-collapse:collapse}} td,th{{border:1px solid #ddd;padding:4px 8px;font-size:14px}}
</style></head><body>{body}</body></html>"""  # noqa: E501


LIST_ITEM = re.compile(r"\s*([-*+]|\d+\.)\s")


def fix_md_lists(md_text: str) -> str:
    """邮件排版要求列表前后有空行、子列表缩进 4 格；Gemini 写的内容常不满足，这里补齐。"""
    out = []
    for line in md_text.splitlines():
        line = re.sub(r"^ {2,3}(?=[-*+] |\d+\. )", "    ", line)
        prev = out[-1] if out else ""
        is_item = bool(LIST_ITEM.match(line))
        prev_in_list = bool(LIST_ITEM.match(prev)) or prev.startswith(" ")
        entering = is_item and not prev_in_list
        leaving = line.strip() and not is_item and not line.startswith(" ") and prev_in_list
        if prev.strip() and (entering or leaving):
            out.append("")
        out.append(line)
    return "\n".join(out)


def to_html(md: str) -> str:
    """日报 Markdown 转成邮件正文 HTML（先补齐列表排版）。"""
    return HTML_TEMPLATE.format(body=markdown.markdown(fix_md_lists(md), extensions=["tables"]))
