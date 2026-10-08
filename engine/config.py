"""读取 config/*.yaml 与 state/account.json。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BEIJING = timezone(timedelta(hours=8))


def now_beijing() -> datetime:
    return datetime.now(BEIJING)


def to_date(x) -> date:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x)[:10])


@dataclass
class Contract:
    product: str
    app_name: str
    kind: str              # equity / bond
    history_symbol: str
    history_name: str
    multiplier: float
    tick: float
    margin_rate: float
    fee: dict
    month: str

    @property
    def code(self) -> str:
        return f"{self.product}{self.month}"

    def fee_per_lot(self, price: float, side: str = "open") -> float:
        """单边手续费（元/手）。side: open / close / close_today。"""
        rate = float(self.fee.get(side, self.fee.get("open", 0.0)))
        if self.fee.get("type") == "fixed":
            return rate
        return rate * price * self.multiplier

    def margin_per_lot(self, price: float) -> float:
        return price * self.multiplier * self.margin_rate

    def round_tick(self, price: float) -> float:
        s = f"{self.tick:.10f}".rstrip("0")
        decimals = len(s.split(".")[1]) if "." in s else 0
        return round(round(price / self.tick) * self.tick, decimals)


@dataclass
class ContractSpec:
    month: str
    equity_expiry: date
    bond_expiry: date
    contracts: list[Contract] = field(default_factory=list)

    @property
    def products(self) -> list[str]:
        return [c.product for c in self.contracts]

    def by_product(self, product: str) -> Contract:
        for c in self.contracts:
            if c.product == product:
                return c
        raise KeyError(product)

    def by_code(self, code: str) -> Contract | None:
        for c in self.contracts:
            if c.code == code:
                return c
        return None


def load_contracts(path: Path | None = None) -> ContractSpec:
    path = path or ROOT / "config" / "contracts.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    month = str(raw["contract_month"])
    spec = ContractSpec(
        month=month,
        equity_expiry=to_date(raw["expiry"]["equity"]),
        bond_expiry=to_date(raw["expiry"]["bond"]),
    )
    for c in raw["contracts"]:
        spec.contracts.append(
            Contract(
                product=c["product"],
                app_name=c["app_name"],
                kind=c["kind"],
                history_symbol=c["history_symbol"],
                history_name=c["history_name"],
                multiplier=float(c["multiplier"]),
                tick=float(c["tick"]),
                margin_rate=float(c["margin_rate"]),
                fee=dict(c["fee"]),
                month=month,
            )
        )
    return spec


def load_strategy(path: Path | None = None) -> dict:
    path = path or ROOT / "config" / "strategy.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_account(path: Path | None = None) -> dict:
    path = path or ROOT / "state" / "account.json"
    acc = json.loads(path.read_text(encoding="utf-8"))
    acc.setdefault("positions", [])
    for p in acc["positions"]:
        if p.get("side") not in ("long", "short"):
            raise ValueError(f"持仓 side 只能是 long 或 short：{p}")
        if int(p.get("lots", 0)) < 0:
            raise ValueError(f"持仓手数不能为负：{p}")
    return acc
