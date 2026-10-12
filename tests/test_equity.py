"""期货权益自动盯市估算（合成数据）。"""
from datetime import date, datetime

import pandas as pd
import pytest

from engine.config import BEIJING, load_contracts, load_strategy
from engine.equity import mark_to_market
from engine.run import build
from engine.synthetic import synthetic_market_data
from tests.helpers import base_account


@pytest.fixture(scope="module")
def spec():
    return load_contracts()


def daily(closes: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime(list(closes)), "close": list(closes.values())})


DAILY = {
    "IM": daily({"2026-10-08": 7022.2, "2026-10-09": 6900.0, "2026-10-12": 6950.0}),
    "T": daily({"2026-10-08": 109.36, "2026-10-09": 109.42, "2026-10-12": 109.50}),
}


def acc(positions, updated="2026-10-08", eq=1_000_000.0):
    return {"futures_equity": eq, "updated_at": updated, "positions": positions}


def test_no_positions_keeps_reported(spec):
    out = mark_to_market(acc([]), spec, DAILY, date(2026, 10, 12))
    assert out["estimate"] == 1_000_000 and not out["estimated"]


def test_long_and_short_marked_to_latest_close(spec):
    pos = [{"code": "IM2612", "side": "long", "lots": 3, "avg_price": 7000.0},
           {"code": "T2612", "side": "short", "lots": 2, "avg_price": 109.3}]
    out = mark_to_market(acc(pos), spec, DAILY, date(2026, 10, 12))
    im = 3 * (6950.0 - 7022.2) * 200
    t = -2 * (109.50 - 109.36) * 10000
    assert out["estimated"]
    assert out["estimate"] == pytest.approx(1_000_000 + im + t)
    assert {a["code"] for a in out["adjustments"]} == {"IM2612", "T2612"}


def test_no_adjustment_when_data_not_newer(spec):
    pos = [{"code": "IM2612", "side": "long", "lots": 3, "avg_price": 7000.0}]
    out = mark_to_market(acc(pos, updated="2026-10-09"), spec, DAILY, date(2026, 10, 9))
    assert out["estimate"] == 1_000_000 and not out["estimated"]


def test_weekend_update_uses_previous_close(spec):
    pos = [{"code": "IM2612", "side": "long", "lots": 1, "avg_price": 7000.0}]
    out = mark_to_market(acc(pos, updated="2026-10-10"), spec, DAILY, date(2026, 10, 12))   # 10-10 周六
    assert out["adjustments"][0]["close_ref_date"] == "2026-10-09"
    assert out["estimate"] == pytest.approx(1_000_000 + (6950.0 - 6900.0) * 200)


def test_intraday_mark_price_and_live_quote(spec):
    """盘中抄的权益（带 mark_prices）→ 按实时价盯市；收盘后按当天收盘盯市。"""
    pos = [{"code": "IM2612", "side": "long", "lots": 3, "avg_price": 6914.6}]
    a = dict(acc(pos, updated="2026-10-12", eq=1_002_184.58), mark_prices={"IM2612": 6918.4})
    live = mark_to_market(a, spec, DAILY, date(2026, 10, 9), live={"IM": 6930.0}, today=date(2026, 10, 12))
    assert live["estimated"] and live["estimate"] == pytest.approx(1_002_184.58 + 3 * (6930.0 - 6918.4) * 200)
    close = mark_to_market(a, spec, DAILY, date(2026, 10, 12))
    assert close["estimate"] == pytest.approx(1_002_184.58 + 3 * (6950.0 - 6918.4) * 200)


def test_same_day_without_mark_price_or_stale_data_keeps_reported(spec):
    pos = [{"code": "IM2612", "side": "long", "lots": 3, "avg_price": 6914.6}]
    a = acc(pos, updated="2026-10-12")
    out = mark_to_market(a, spec, DAILY, date(2026, 10, 9), live={"IM": 6930.0}, today=date(2026, 10, 12))
    assert out["estimate"] == 1_000_000 and not out["estimated"]          # 不知道抄权益时的价格，不盯市
    stale = mark_to_market(a, spec, DAILY, date(2026, 10, 9))
    assert stale["estimate"] == 1_000_000 and stale["warnings"]            # 行情比账户旧：不倒推


def test_unknown_contract_warns(spec):
    pos = [{"code": "IF2610", "side": "long", "lots": 1, "avg_price": 4200.0}]
    out = mark_to_market(acc(pos), spec, DAILY, date(2026, 10, 12))
    assert not out["estimated"] and out["warnings"]


def test_engine_uses_estimated_equity(spec):
    md = synthetic_market_data(spec, end=date(2026, 10, 9))
    account = base_account()
    account = dict(account, updated_at="2026-10-08",
                   positions=[{"code": "IM2612", "side": "long", "lots": 2, "avg_price": 7000.0}])
    res = build(spec, load_strategy(), account, md, datetime(2026, 10, 9, 16, 0, tzinfo=BEIJING),
                paths_screen=200, paths_final=500, robustness=False)
    d = md.contract_daily["IM"].set_index("date")["close"]
    expected = 1_000_000 + 2 * (d[pd.Timestamp("2026-10-09")] - d[pd.Timestamp("2026-10-08")]) * 200
    assert res["account"]["futures_equity_estimated"] is True
    assert res["account"]["futures_equity"] == pytest.approx(expected)
    assert res["account"]["futures_equity_reported"] == 1_000_000
    assert res["account"]["total"] == pytest.approx(res["account"]["spot"]["value"] + expected)


def test_engine_intraday_start(spec):
    """盘中运行：起点换成实时价，情景从下一个交易日开始，权益按实时价盯市。"""
    md = synthetic_market_data(spec, end=date(2026, 10, 9))
    closes = {p: float(d["close"].iloc[-1]) for p, d in md.contract_daily.items()}
    md.spot_quotes = {p: {"price": round(v * 0.99, 1), "time": "14:00:00"} for p, v in closes.items()}
    account = dict(base_account(), updated_at="2026-10-12", futures_equity=1_000_000.0,
                   positions=[{"code": "IM2612", "side": "long", "lots": 1, "avg_price": 7000.0}],
                   mark_prices={"IM2612": closes["IM"]})
    res = build(spec, load_strategy(), account, md, datetime(2026, 10, 12, 14, 0, tzinfo=BEIJING),
                paths_screen=200, paths_final=500, robustness=False)
    assert res["start"]["intraday"] and res["start"]["date"] == "2026-10-12"
    assert res["horizon"]["start"] == "2026-10-13"
    assert res["start"]["index_move_today"] == pytest.approx(md.spot_quotes["IF"]["price"] / closes["IF"] - 1)
    im = next(c for c in res["contracts"] if c["product"] == "IM")
    assert im["F0"] == pytest.approx(md.spot_quotes["IM"]["price"])
    s_t0 = float(md.closes["IM"][md.closes["IM"].index <= pd.Timestamp("2026-10-09")].iloc[-1])
    assert im["basis"] == pytest.approx(closes["IM"] / s_t0 - 1, rel=1e-9)          # 基差率不变
    expected = 1_000_000 + (md.spot_quotes["IM"]["price"] - closes["IM"]) * 200
    assert res["account"]["futures_equity"] == pytest.approx(expected)
    assert not any("不是最近一个已收盘交易日" in w for w in res["warnings"])


def test_engine_before_open_is_not_intraday(spec):
    md = synthetic_market_data(spec, end=date(2026, 10, 9))
    md.spot_quotes = {p: {"price": float(d["close"].iloc[-1]), "time": ""} for p, d in md.contract_daily.items()}
    res = build(spec, load_strategy(), base_account(), md, datetime(2026, 10, 12, 8, 45, tzinfo=BEIJING),
                paths_screen=200, paths_final=500, robustness=False)
    assert not res["start"]["intraday"] and res["horizon"]["start"] == "2026-10-12"
