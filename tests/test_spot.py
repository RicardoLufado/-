"""现货 ETF / 债券拆分估值。"""
import numpy as np
import pandas as pd
import pytest

from engine.spot import split_spot, spot_value_at

ACCOUNT = {"spot_value": 984_052.96, "spot_value_asof": "2026-09-30", "spot_cost_date": "2026-09-18"}


def test_split():
    etf, bond = split_spot(984_052.96, csi_cost=4000.0, csi_asof=3900.0)
    assert etf == pytest.approx(500_000 * 3900 / 4000)      # 487,500
    assert bond == pytest.approx(984_052.96 - 487_500)
    assert etf + bond == pytest.approx(984_052.96)


def _csi():
    idx = pd.to_datetime(["2026-09-17", "2026-09-18", "2026-09-29", "2026-09-30", "2026-10-09", "2026-10-12"])
    return pd.Series([3990.0, 4000.0, 3950.0, 3900.0, 3980.0, 4100.0], index=idx)


def test_value_on_asof_equals_reported():
    bond_ret = pd.Series([0.001, -0.002], index=pd.to_datetime(["2026-09-29", "2026-09-30"]))
    v = spot_value_at(ACCOUNT, _csi(), bond_ret, pd.Timestamp("2026-09-30").date())
    assert v["total"] == pytest.approx(984_052.96)          # 在 asof 当天，估值等于 App 报告值
    assert v["etf"] == pytest.approx(487_500)


def test_value_rolls_forward():
    bond_ret = pd.Series([0.001, 0.0005, -0.0002, 0.0003],
                         index=pd.to_datetime(["2026-09-30", "2026-10-09", "2026-10-12", "2026-10-13"]))
    v = spot_value_at(ACCOUNT, _csi(), bond_ret, pd.Timestamp("2026-10-12").date())
    etf_expected = 500_000 * 4100 / 4000
    bond_asof = 984_052.96 - 487_500
    bond_expected = bond_asof * np.exp(0.0005 - 0.0002)     # 只滚动 asof 之后到 t 的收益
    assert v["etf"] == pytest.approx(etf_expected)
    assert v["bond"] == pytest.approx(bond_expected)
    assert v["total"] == pytest.approx(etf_expected + bond_expected)


def test_missing_cost_date_uses_previous_close():
    csi = _csi().drop(pd.Timestamp("2026-09-18"))
    v = spot_value_at(ACCOUNT, csi, pd.Series(dtype=float), pd.Timestamp("2026-09-30").date())
    assert v["csi300_cost_date"] == "2026-09-17"
    assert v["csi300_cost"] == 3990.0
