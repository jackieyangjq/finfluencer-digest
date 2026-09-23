import pytest

pytest.importorskip("pandas")  # 持仓部分的附加依赖；没装时跳过，不影响其他测试

import numpy as np
import pandas as pd

from finfluencer_digest.portfolio.technicals import compute


def test_compute_basic_fields():
    idx = pd.bdate_range("2025-06-02", periods=320)
    close = pd.Series(np.linspace(100, 160, len(idx)), index=idx)
    hist = pd.DataFrame({"Close": close, "Volume": 1000.0}, index=idx)
    t = compute(hist)
    assert t["last"] == 160.0 and t["r1d"] > 0 and t["ma"][200] is not None and 0 <= t["rsi"] <= 100
    assert t["range52w"] == (float(close.tail(252).min()), 160.0) and t["vol_ratio"] == 1.0


def test_compute_rsi_on_noisy_rising_series():
    idx = pd.bdate_range("2025-06-02", periods=320)
    steps = np.random.default_rng(0).normal(0.2, 1.5, len(idx))  # 有涨有跌、整体向上，RSI 走正常计算而不是除以零
    close = pd.Series(100 + np.cumsum(steps), index=idx)
    t = compute(pd.DataFrame({"Close": close, "Volume": 1000.0}, index=idx))
    assert 0 < t["rsi"] < 100 and t["ma"][200] is not None
