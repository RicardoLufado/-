"""检查真实的 state/*.json 格式（只查格式，不依赖具体数字）。"""
import json
from datetime import date

from engine.config import ROOT, load_account, load_contracts


def test_account_json_is_valid():
    acc = load_account()                       # side / lots 不合法会直接报错
    date.fromisoformat(acc["updated_at"][:10])
    date.fromisoformat(acc["spot_value_asof"])
    assert acc["futures_equity"] > 0 and acc["spot_value"] > 0
    spec = load_contracts()
    for p in acc["positions"]:
        assert {"code", "side", "lots", "avg_price"} <= set(p), p
        assert spec.by_code(p["code"]) is not None, f"{p['code']} 不是模型里的 2612 合约"


def test_leaderboard_json_is_valid():
    data = json.loads((ROOT / "state" / "leaderboard.json").read_text(encoding="utf-8"))
    for snap in data["snapshots"]:
        date.fromisoformat(snap["date"])
        for rank, total in snap.get("top", {}).items():
            assert int(rank) >= 1 and total > 0
