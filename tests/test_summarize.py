"""scripts/summarize.py 的缺失项检查：Actions 日志里「无缺失 ✓」的依据。"""
import importlib.util
from datetime import datetime
from pathlib import Path

import pytest

from engine.config import BEIJING, load_contracts, load_strategy
from engine.run import build, clean_json
from engine.synthetic import synthetic_market_data
from tests.helpers import base_account

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("summarize", ROOT / "scripts" / "summarize.py")
summarize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(summarize)


@pytest.fixture(scope="module")
def result():
    spec, strategy = load_contracts(), load_strategy()
    md = synthetic_market_data(spec)
    res = build(spec, strategy, base_account(), md, datetime(2026, 10, 9, 8, 45, tzinfo=BEIJING),
                paths_screen=200, paths_final=500, robustness=False)
    return clean_json(res)


def test_only_synthetic_source_flagged(result):
    miss = summarize.find_missing(result)
    # 合成数据源的 status 是 synthetic（不是 ok），所以只应报这一项
    assert len(miss) == 1 and "合成" in miss[0]


def test_detects_missing_fields(result):
    bad = dict(result)
    bad["contracts"] = [dict(result["contracts"][0], ref_price=None)] + result["contracts"][1:]
    bad["modes"] = [dict(result["modes"][0], metrics=dict(result["modes"][0]["metrics"], promotion_prob=None))]
    bad["account"] = dict(result["account"], total=None)
    miss = summarize.find_missing(bad)
    code = result["contracts"][0]["code"]
    assert f"contracts.{code}.ref_price" in miss
    assert "modes.A.metrics.promotion_prob" in miss
    assert "account.total" in miss


def test_fallback_without_modes_is_flagged():
    miss = summarize.find_missing({"generated_at": None, "modes": [], "data_sources": []})
    assert "modes（空）" in miss and "generated_at" in miss
