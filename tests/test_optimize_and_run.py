"""搜索、日历、端到端（合成数据）与兜底。"""
import json
import math
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from engine.config import BEIJING, load_account, load_contracts, load_strategy
from engine.data import Fetcher, ensure_calendar_covers, weekday_calendar
from engine.fallback import write_fallback
from engine.model import count_trading_days_after
from engine.optimize import enumerate_candidates, margin_ok, search
from engine.portfolio import ramp_objective
from engine.run import build, clean_json, write_outputs
from engine.synthetic import synthetic_market_data
from tests.helpers import make_scenario


def test_season_calendar_counts():
    cal = weekday_calendar(date(2026, 9, 1), date(2026, 12, 31))
    assert date(2026, 10, 8) not in cal and date(2026, 10, 9) in cal
    assert count_trading_days_after(cal, date(2026, 9, 30), date(2026, 11, 20)) == 31   # 10-09 起约 31 天
    assert count_trading_days_after(cal, date(2026, 9, 30), date(2026, 12, 18)) == 51
    assert count_trading_days_after(cal, date(2026, 9, 10), date(2026, 9, 18)) == 6     # 9-18 是第 7 个交易日


def test_calendar_extension_warns():
    w = []
    cal = ensure_calendar_covers([date(2026, 11, 30)], date(2026, 12, 18), w)
    assert cal[-1] == date(2026, 12, 18) and w


def test_enumeration_and_margin_filter():
    K = 8
    kinds = ["equity"] * 4 + ["bond"] * 4
    F0 = np.array([4248.0, 2764.4, 7122.8, 7022.2, 102.6, 106.4, 109.4, 116.9])
    mult = np.array([300, 300, 200, 200, 20000, 10000, 10000, 10000.0])
    rate = np.array([0.15, 0.15, 0.15, 0.15, 0.01, 0.02, 0.03, 0.045])
    sc = make_scenario(np.tile(F0, (2, 3, 1)), F0, mult, rate)
    srch = {"equity_lot_range": 3, "max_equity_products": 2, "bond_levels": 15}
    N = enumerate_candidates(sc, kinds, 1_000_000, srch, 0.7)
    eq_opts = 1 + 4 * 6 + 6 * 36
    assert len(N) % eq_opts == 0
    assert (np.count_nonzero(N[:, :4], axis=1) <= 2).all()
    assert (np.count_nonzero(N[:, 4:], axis=1) <= 1).all()
    ok = margin_ok(N, sc.margin0, 700_000)
    assert ok.any() and not ok.all()
    assert (np.abs(N[ok]) @ sc.margin0 <= 700_000 + 1e-6).all()
    assert any((row == 0).all() for row in N)            # 空仓在候选里


def test_search_respects_margin_cap_and_beats_empty():
    spec, strategy = load_contracts(), load_strategy()
    md = synthetic_market_data(spec)
    res = build(spec, strategy, load_account(), md, datetime(2026, 10, 9, 8, 45, tzinfo=BEIJING),
                paths_screen=300, paths_final=1500, robustness=False)
    A = next(m for m in res["modes"] if m["id"] == "A")
    E = next(m for m in res["modes"] if m["id"] == "empty")
    assert A["metrics"]["margin_ratio"] <= 0.70 + 1e-9
    assert A["metrics"]["promotion_prob"] >= E["metrics"]["promotion_prob"]
    D = next(m for m in res["modes"] if m["id"] == "D")
    assert D["metrics"]["W_std"] <= E["metrics"]["W_std"] + 1e-6


@pytest.fixture(scope="module")
def synthetic_result():
    spec, strategy = load_contracts(), load_strategy()
    md = synthetic_market_data(spec)
    return build(spec, strategy, load_account(), md, datetime(2026, 10, 9, 8, 45, tzinfo=BEIJING),
                 paths_screen=300, paths_final=1000, robustness=True)


def test_latest_json_fields(synthetic_result, tmp_path):
    r = synthetic_result
    for key in ["generated_at", "data_asof", "data_sources", "account", "threshold", "status",
                "recommended_mode", "modes", "explanations", "warnings"]:
        assert key in r
    assert r["synthetic"] is True
    assert r["status"] in ("追赶", "持平", "锁定")
    assert {m["id"] for m in r["modes"]} == {"A", "D", "empty", "ts1"}
    for m in r["modes"]:
        assert {"target_positions", "orders", "metrics"} <= set(m)
        for k in ["promotion_prob", "liquidation_prob", "wipeout_prob", "W_p05", "W_p50", "W_p95",
                  "margin_ratio", "var99_1d"]:
            assert math.isfinite(m["metrics"][k])
    assert 3 <= len(r["explanations"]) <= 6
    rob = r["modes"][0]["robustness"]
    assert len(rob["runs"]) == 3 and rob["min"] <= rob["max"]
    assert r["horizon"]["trading_days"] == 31
    for mid, hm in r["histogram"]["modes"].items():
        assert len(hm["edges"]) == len(hm["freq"]) + 1
        assert sum(hm["freq"]) == pytest.approx(1.0, abs=1e-3)
        assert hm["edges"][0] <= r["threshold"]["low"] and hm["edges"][-1] >= r["threshold"]["high"]

    now = datetime(2026, 10, 9, 8, 45, tzinfo=BEIJING)
    out, cache = tmp_path / "site", tmp_path / "cache"
    write_outputs(r, out, cache, now)
    data = json.loads((out / "latest.json").read_text(encoding="utf-8"))   # 严格 JSON（无 NaN）
    assert data["data_asof"] == "2026-09-30"
    assert (out / "history" / "20261009_0845.json").exists()
    assert json.loads((out / "history" / "index.json").read_text()) == ["20261009_0845.json"]
    assert (cache / "last_good" / "latest.json").exists()


def test_fallback_uses_last_good(synthetic_result, tmp_path):
    now = datetime(2026, 10, 9, 8, 45, tzinfo=BEIJING)
    out, cache = tmp_path / "site", tmp_path / "cache"
    write_outputs(synthetic_result, out, cache, now)
    (out / "latest.json").unlink()
    d = write_fallback(out, cache, "测试：数据源超时")
    assert d["fallback"]["showing"] == synthetic_result["generated_at"]
    assert "测试：数据源超时" in d["warnings"][0]
    assert json.loads((out / "latest.json").read_text(encoding="utf-8"))["modes"]


def test_fallback_without_cache(tmp_path):
    d = write_fallback(tmp_path / "site", tmp_path / "cache", "无缓存")
    assert d["status"] == "数据缺失" and d["modes"] == []


def test_clean_json_removes_nan():
    assert clean_json({"a": float("nan"), "b": np.float64(1.5), "_hidden": 1, "c": [np.inf]}) == \
        {"a": None, "b": 1.5, "c": [None]}


def test_fetcher_retries_then_uses_cache(tmp_path):
    logs = []
    f = Fetcher(tmp_path, retries=3, timeout=1, interval=0, log=logs.append)
    good = pd.DataFrame({"date": ["2026-09-29", "2026-09-30"], "close": [1.0, 2.0]})
    df, rec = f.fetch("k", "测试", "fake()", lambda: good, date_col="date")
    assert rec.status == "ok" and rec.last_date == "2026-09-30"

    calls = []

    def boom():
        calls.append(1)
        raise TimeoutError("超时")

    f2 = Fetcher(tmp_path, retries=3, timeout=1, interval=0, log=logs.append)
    df2, rec2 = f2.fetch("k", "测试", "fake()", boom, date_col="date")
    assert len(calls) == 3                               # 最多重试 3 次
    assert rec2.status == "cache" and rec2.last_date == "2026-09-30"
    assert len(df2) == 2

    df3, rec3 = f2.fetch("never", "测试2", "fake()", boom, date_col="date")
    assert df3 is None and rec3.status == "missing" and "超时" in rec3.error
