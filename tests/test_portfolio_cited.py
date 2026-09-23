from finfluencer_digest.portfolio.section import cited_only

NEWS = [{"source": "Reuters"}, {"source": "长桥资讯"}]


def test_keeps_points_whose_source_matches():
    pts = ["营收超预期（09-22 Reuters）", "指引下调（09-21｜长桥资讯）", "RSI 超买（09-22 技术指标）", "没有来源的一句"]
    assert cited_only(pts, NEWS) == pts[:2]
