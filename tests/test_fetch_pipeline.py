"""用假的 akshare 模块（列名与 AKShare 1.19 源码一致）跑完整的真实数据流程：抓取 → 规范化 → 建模 → 输出。"""
import sys
import types
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from engine.config import BEIJING, load_contracts, load_strategy
from engine.data import fetch_market_data, weekday_calendar
from engine.run import build
from tests.helpers import base_account

DAYS = [d for d in weekday_calendar(date(2023, 1, 2), date(2026, 12, 31)) if d <= date(2026, 9, 30)]
LEVEL = {"sh000300": 4250.0, "sh000016": 2770.0, "sh000905": 7150.0, "sh000852": 7080.0,
         "TS0": 102.6, "TF0": 106.4, "T0": 109.4, "TL0": 116.9}
VOL = {"sh000300": 0.011, "sh000016": 0.01, "sh000905": 0.014, "sh000852": 0.016,
       "TS0": 0.0004, "TF0": 0.001, "T0": 0.0016, "TL0": 0.004}
BASIS = {"IF": -0.004, "IH": -0.002, "IC": -0.012, "IM": -0.018, "TS": 0, "TF": 0, "T": 0, "TL": 0}
MAIN = {"IF": "sh000300", "IH": "sh000016", "IC": "sh000905", "IM": "sh000852",
        "TS": "TS0", "TF": "TF0", "T": "T0", "TL": "TL0"}


def _path(sym):
    rng = np.random.default_rng(abs(hash(sym)) % 2**32)
    r = rng.normal(0, VOL[sym], len(DAYS))
    return LEVEL[sym] * np.exp(np.cumsum(r) - r.sum())


def make_fake_ak(fail=()):
    ak = types.ModuleType("akshare")
    ak.__version__ = "1.19.fake"

    def maybe_fail(name):
        if name in fail:
            raise ConnectionError(f"{name} 模拟失败")

    def tool_trade_date_hist_sina():
        maybe_fail("calendar")
        cal = weekday_calendar(date(2023, 1, 2), date(2026, 12, 31))
        return pd.DataFrame({"trade_date": cal})                       # datetime.date

    def stock_zh_index_daily(symbol):
        maybe_fail(symbol)
        p = _path(symbol)
        return pd.DataFrame({"date": DAYS, "open": p, "high": p, "low": p, "close": p, "volume": 1.0})

    def futures_main_sina(symbol, start_date="19900101", end_date="22220101"):
        maybe_fail(symbol)
        p = _path(symbol)
        keep = [i for i, d in enumerate(DAYS) if d >= date(2023, 5, 1)]
        return pd.DataFrame({"日期": [DAYS[i] for i in keep], "开盘价": p[keep], "最高价": p[keep],
                             "最低价": p[keep], "收盘价": p[keep], "成交量": 1, "持仓量": 1, "动态结算价": p[keep]})

    def futures_zh_daily_sina(symbol):
        maybe_fail(symbol)
        prod = symbol[:-4]
        p = _path(MAIN[prod])[-80:] * (1 + BASIS[prod])
        return pd.DataFrame({"date": [d.isoformat() for d in DAYS[-80:]], "open": p, "high": p, "low": p,
                             "close": p, "volume": 1, "hold": 1, "settle": p})

    def futures_zh_spot(symbol, market="CF", adjust="0"):
        raise ValueError("Length mismatch: Expected axis has 50 elements")  # 列数对不上时 AKShare 的典型报错

    for f in (tool_trade_date_hist_sina, stock_zh_index_daily, futures_main_sina, futures_zh_daily_sina,
              futures_zh_spot):
        setattr(ak, f.__name__, f)
    return ak


@pytest.fixture()
def fake_ak(monkeypatch):
    def install(fail=()):
        monkeypatch.setitem(sys.modules, "akshare", make_fake_ak(fail))
    return install


def _strategy():
    s = load_strategy()
    s["data"]["interval_sec"] = 0
    return s


def test_real_pipeline_with_fake_akshare(fake_ak, tmp_path):
    fake_ak()
    spec, strategy = load_contracts(), _strategy()
    md = fetch_market_data(spec, strategy, tmp_path, log=lambda *_: None)
    assert md.akshare_version == "1.19.fake"
    assert set(md.closes) == set(spec.products)
    assert all(s.status == "ok" for s in md.sources if s.key != "spot")
    assert md.spot_quotes == {}                                        # 盘中价失败 → 跳过
    res = build(spec, strategy, base_account(), md, datetime(2026, 10, 9, 8, 45, tzinfo=BEIJING),
                paths_screen=200, paths_final=600, robustness=False)
    assert res["data_asof"] == "2026-09-30"
    assert res["synthetic"] is False
    im = next(c for c in res["contracts"] if c["product"] == "IM")
    assert im["basis"] == pytest.approx(-0.018)
    assert im["ref_source"] == "最近收盘价"
    assert res["engine"]["akshare_version"] == "1.19.fake"


def test_failed_source_falls_back_to_cache(fake_ak, tmp_path):
    fake_ak()
    spec, strategy = load_contracts(), _strategy()
    fetch_market_data(spec, strategy, tmp_path, log=lambda *_: None, with_spot=False)   # 先建好缓存
    fake_ak(fail=("sh000852", "IM2612"))
    strategy["data"]["retries"] = 2
    md = fetch_market_data(spec, strategy, tmp_path, log=lambda *_: None, with_spot=False)
    st = {s.key: s.status for s in md.sources}
    assert st["hist_IM"] == "cache" and st["daily_IM2612"] == "cache"
    assert any("缓存" in w and "中证1000" in w for w in md.warnings)


def test_missing_source_without_cache_excludes_product(fake_ak, tmp_path):
    fake_ak(fail=("TL0", "TL2612"))
    spec, strategy = load_contracts(), _strategy()
    strategy["data"]["retries"] = 1
    md = fetch_market_data(spec, strategy, tmp_path, log=lambda *_: None, with_spot=False)
    res = build(spec, strategy, base_account(), md, datetime(2026, 10, 9, 8, 45, tzinfo=BEIJING),
                paths_screen=200, paths_final=600, robustness=False)
    tl = next(c for c in res["contracts"] if c["product"] == "TL")
    assert tl["tradable"] is False and tl["ref_price"] is None
    assert all(m["lots"].get("TL", 0) == 0 for m in res["modes"])
    assert any("TL" in w and "缺失" in w for w in res["warnings"])


def test_critical_source_missing_raises(fake_ak, tmp_path):
    fake_ak(fail=("sh000300",))
    spec, strategy = load_contracts(), _strategy()
    strategy["data"]["retries"] = 1
    md = fetch_market_data(spec, strategy, tmp_path, log=lambda *_: None, with_spot=False)
    with pytest.raises(RuntimeError, match="沪深300"):
        build(spec, strategy, base_account(), md, datetime(2026, 10, 9, 8, 45, tzinfo=BEIJING),
              paths_screen=100, paths_final=200, robustness=False)
