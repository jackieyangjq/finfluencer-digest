from finfluencer_digest.render import build_digest, fix_md_lists


def test_build_digest_sections(make_result):
    results = [make_result("A", "NVDA", "看多", vid="v1"), make_result("B", "NVDA", "看空", vid="v2")]
    md = build_digest("2026-09-23", results, "汇总文字", [], [], watch={"NVDA", "TSLA"},
                      usage_line="今日 Gemini 用量：x")
    assert md.startswith("# 财经博主日报 · 2026-09-23")
    assert "## 今日共识与分歧" in md and "今天整理了 2 个新视频，来自 2 位博主：A、B。" in md
    assert "## 你关注的股票" in md and "**NVDA**" in md
    assert "| ⭐ NVDA |" in md and "| 1（A） | 1（B） |" in md
    assert md.count("### ") == 2 and md.rstrip().endswith("今日 Gemini 用量：x")


def test_build_digest_held_excluded_from_watch_hits_and_title_switch(make_result):
    md = build_digest("d", [make_result("A", "NVDA", "看多")], "s", [], [], watch={"NVDA"},
                      portfolio_md="## 我的持仓\n\n（示例）", held=frozenset({"NVDA"}))
    assert md.startswith("# 投资日报 · d") and "## 你关注的股票" not in md and "## 我的持仓" in md


def test_build_digest_empty_and_failures():
    md = build_digest("d", [], "", ["频道甲失败"], [], set())
    assert "今天没有需要整理的新视频" in md and "- 频道甲失败" in md


def test_fix_md_lists_adds_blank_lines_and_indents_sublists():
    src = "段落\n- 一\n  - 子\n- 二\n结尾"
    assert fix_md_lists(src) == "段落\n\n- 一\n    - 子\n- 二\n\n结尾"
