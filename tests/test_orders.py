"""下单差额生成。"""
import pytest

from engine.config import load_contracts
from engine.orders import make_orders

REF = {"IF2612": 4248.0, "IM2612": 7022.2, "TS2612": 102.6, "T2612": 109.4}


@pytest.fixture(scope="module")
def spec():
    return load_contracts()


def test_open_from_empty(spec):
    out = make_orders([], {"IM2612": 2}, spec, REF)
    assert len(out) == 1
    assert out[0]["text"] == "中证1000股指2612（IM2612）买多 开仓 2 手，参考价 7022.2"
    assert out[0]["action"] == "buy_open"


def test_open_short(spec):
    out = make_orders([], {"T2612": -5}, spec, REF)
    assert out[0]["text"] == "十债2612（T2612）卖空 开仓 5 手，参考价 109.4"


def test_reduce_long(spec):
    pos = [{"code": "IF2612", "side": "long", "lots": 2, "avg_price": 4200.0}]
    out = make_orders(pos, {"IF2612": 1}, spec, REF)
    assert [(o["action"], o["lots"]) for o in out] == [("sell_close", 1)]
    assert "平仓" in out[0]["text"]


def test_flip_closes_before_opening(spec):
    pos = [{"code": "IM2612", "side": "long", "lots": 1, "avg_price": 7000.0}]
    out = make_orders(pos, {"IM2612": -2}, spec, REF)
    assert [(o["action"], o["lots"]) for o in out] == [("sell_close", 1), ("sell_open", 2)]


def test_close_everything_when_target_empty(spec):
    pos = [{"code": "TS2612", "side": "short", "lots": 3, "avg_price": 102.5},
           {"code": "IF2612", "side": "long", "lots": 1, "avg_price": 4200.0}]
    out = make_orders(pos, {}, spec, REF)
    acts = sorted((o["code"], o["action"], o["lots"]) for o in out)
    assert acts == [("IF2612", "sell_close", 1), ("TS2612", "buy_close", 3)]


def test_unchanged_gives_no_orders(spec):
    pos = [{"code": "TS2612", "side": "long", "lots": 1, "avg_price": 102.5}]
    assert make_orders(pos, {"TS2612": 1}, spec, REF) == []


def test_locked_position_and_other_month(spec):
    pos = [{"code": "IF2612", "side": "long", "lots": 1, "avg_price": 4200.0},
           {"code": "IF2612", "side": "short", "lots": 1, "avg_price": 4250.0},
           {"code": "IF2610", "side": "long", "lots": 1, "avg_price": 4200.0}]
    out = make_orders(pos, {"IF2612": 1}, spec, REF)
    acts = sorted((o["code"], o["action"], o["lots"]) for o in out)
    assert acts == [("IF2610", "sell_close", 1), ("IF2612", "buy_close", 1)]
    other = next(o for o in out if o["code"] == "IF2610")
    assert other["in_model"] is False and other["ref_price"] is None
    assert "数据缺失" in other["text"]


def test_missing_ref_price(spec):
    out = make_orders([], {"TS2612": 1}, spec, {"TS2612": None})
    assert out[0]["text"].endswith("参考价 数据缺失")
