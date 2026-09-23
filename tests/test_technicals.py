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
