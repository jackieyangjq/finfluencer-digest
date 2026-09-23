import pytest

from finfluencer_digest.summarize import StockView, VideoSummary


@pytest.fixture
def make_result():
    def _make(channel, symbol, stance, reason="r", title="t", vid="v1"):
        v = {"channel": channel, "title": title, "id": vid, "url": f"https://www.youtube.com/watch?v={vid}",
             "published": 0}
        s = VideoSummary(is_market_related=True, one_line="一句话", main_points=["要点"], key_facts=[], risks=[],
                         stocks=[StockView(symbol=symbol, name=symbol, stance=stance, reason=reason)])
        return v, s
    return _make
