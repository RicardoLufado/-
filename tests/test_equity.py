"""期货权益自动盯市估算（合成数据）。"""
from datetime import date, datetime

import pandas as pd
import pytest

from engine.config import BEIJING, load_account, load_contracts, load_strategy
from engine.equity import mark_to_market
from engine.run import build
from engine.synthetic import synthetic_market_data


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


def test_unknown_contract_warns(spec):
    pos = [{"code": "IF2610", "side": "long", "lots": 1, "avg_price": 4200.0}]
    out = mark_to_market(acc(pos), spec, DAILY, date(2026, 10, 12))
    assert not out["estimated"] and out["warnings"]


def test_engine_uses_estimated_equity(spec):
    md = synthetic_market_data(spec, end=date(2026, 10, 9))
    account = load_account()
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
