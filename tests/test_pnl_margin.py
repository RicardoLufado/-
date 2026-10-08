"""单手盈亏与保证金计算。"""
import numpy as np
import pytest

from engine.config import load_contracts
from engine.portfolio import evaluate
from tests.helpers import make_scenario


@pytest.fixture(scope="module")
def spec():
    return load_contracts()


@pytest.mark.parametrize("product,price,expected", [
    ("IF", 4248.0, 191_160.0),
    ("IC", 7122.8, 213_684.0),
    ("IM", 7022.2, 210_666.0),
    ("TS", 102.6, 20_520.0),
    ("TL", 116.86, 52_587.0),
])
def test_margin_per_lot_matches_app(spec, product, price, expected):
    assert spec.by_product(product).margin_per_lot(price) == pytest.approx(expected, abs=1.0)


def test_fee_per_lot(spec):
    assert spec.by_product("IF").fee_per_lot(4000.0, "open") == pytest.approx(4000 * 300 * 0.000023)
    assert spec.by_product("IF").fee_per_lot(4000.0, "close_today") == pytest.approx(4000 * 300 * 0.00023)
    assert spec.by_product("T").fee_per_lot(109.4, "open") == 3.0


def test_round_tick(spec):
    assert spec.by_product("IF").round_tick(4248.13) == 4248.2
    assert spec.by_product("TS").round_tick(102.6031) == 102.604
    assert spec.by_product("TL").round_tick(116.866) == 116.87


def test_pnl_and_margin_tensor_by_hand():
    # 1 条路径、2 天、2 个合约（IF 样式 + TS 样式）
    F = np.array([[[4258.0, 102.70], [4238.0, 102.50]]])
    sc = make_scenario(F, F0=[4248.0, 102.60], mult=[300, 20000], margin_rate=[0.15, 0.01])
    assert sc.pnl[0, 0, 0] == pytest.approx(10 * 300)        # +10 点 × 300
    assert sc.pnl[0, 1, 1] == pytest.approx(-0.10 * 20000)   # −0.1 元 × 2 万
    assert sc.margin[0, 1, 0] == pytest.approx(4238 * 300 * 0.15)

    # 组合：多 1 手 IF + 空 3 手 TS
    r = evaluate(sc, np.array([[1, -3]]), E0=1_000_000, liq_ratio=1.0)
    expected = 1_000_000 + (4238 - 4248) * 300 - 3 * (102.50 - 102.60) * 20000
    assert r.E_final[0, 0] == pytest.approx(expected)
    assert not r.liquidated[0, 0]


def test_fees_are_deducted():
    F = np.full((1, 3, 1), 4000.0)
    sc = make_scenario(F, F0=[4000.0], mult=[300], margin_rate=[0.15], fee_open=[27.6], fee_close=[27.6])
    r = evaluate(sc, np.array([[2]]), E0=1_000_000, liq_ratio=1.0)
    assert r.E_final[0, 0] == pytest.approx(1_000_000 - 4 * 27.6)
