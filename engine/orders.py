"""下单指令：目标持仓相对 state/account.json 当前持仓的差额。先平后开，释放保证金。"""
from __future__ import annotations

from .config import ContractSpec


def current_book(positions: list[dict]) -> dict[str, dict]:
    """{code: {"long": 手数, "short": 手数}}；同一合约多空可同时存在（锁仓）。"""
    book: dict[str, dict] = {}
    for p in positions:
        b = book.setdefault(p["code"], {"long": 0, "short": 0})
        b[p["side"]] += int(p["lots"])
    return book


def target_book(target: dict[str, int]) -> dict[str, dict]:
    return {code: {"long": max(n, 0), "short": max(-n, 0)} for code, n in target.items() if n != 0}


def make_orders(positions: list[dict], target: dict[str, int], spec: ContractSpec,
                ref_prices: dict[str, float | None]) -> list[dict]:
    """target: {合约代码: 净手数}。返回按「先平后开」排序的指令列表。"""
    cur = current_book(positions)
    tgt = target_book(target)
    closes, opens = [], []
    for code in sorted(set(cur) | set(tgt)):
        c, t = cur.get(code, {"long": 0, "short": 0}), tgt.get(code, {"long": 0, "short": 0})
        if c["long"] > t["long"]:
            closes.append((code, "sell_close", c["long"] - t["long"]))
        if c["short"] > t["short"]:
            closes.append((code, "buy_close", c["short"] - t["short"]))
        if t["long"] > c["long"]:
            opens.append((code, "buy_open", t["long"] - c["long"]))
        if t["short"] > c["short"]:
            opens.append((code, "sell_open", t["short"] - c["short"]))

    labels = {
        "buy_open": ("买多", "开仓"),
        "sell_open": ("卖空", "开仓"),
        "sell_close": ("卖出", "平仓（平多）"),
        "buy_close": ("买入", "平仓（平空）"),
    }
    out = []
    for code, action, lots in closes + opens:
        contract = spec.by_code(code)
        name = contract.app_name if contract else code
        price = ref_prices.get(code)
        if price is not None and contract is not None:
            price = contract.round_tick(price)
        direction, offset = labels[action]
        price_txt = f"{price:g}" if price is not None else "数据缺失"
        out.append({
            "code": code,
            "name": name,
            "action": action,
            "direction": direction,
            "offset": offset,
            "lots": int(lots),
            "ref_price": price,
            "text": f"{name}（{code}）{direction} {offset} {lots} 手，参考价 {price_txt}",
            "in_model": contract is not None,
        })
    return out
