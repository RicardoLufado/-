"""数据抓取（AKShare）+ 缓存 + 失败回退。

每个请求：最多重试 3 次、超时 20 秒、请求间隔 1 秒。成功结果存到 data/cache/；
失败时读缓存，并在 warnings 里写明用的是哪天的数据；连缓存也没有就记为「数据缺失」。
"""
from __future__ import annotations

import json
import time
import traceback
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd

from .config import ROOT, ContractSpec, now_beijing, to_date

CACHE_DIR = ROOT / "data" / "cache"

# 本赛季已知休市日（仅在新浪交易日历抓取失败时回退用）。
# 依据：榜单显示 09-18 为第 7 个、09-30 为第 14 个交易日 → 09-25（中秋）休市；
# 新浪行情在 10-08 有日线 → 10-08 正常交易，国庆休市为 10-01~10-07。
KNOWN_HOLIDAYS_2026 = [
    "2026-09-25",
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
]


@dataclass
class SourceRecord:
    key: str
    name: str
    call: str
    status: str                 # ok / cache / missing / synthetic
    fetched_at: str | None = None
    rows: int = 0
    first_date: str | None = None
    last_date: str | None = None
    columns: list[str] = field(default_factory=list)
    elapsed_sec: float | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MarketData:
    calendar: list[date]
    calendar_source: str
    closes: dict[str, pd.Series]             # 产品 -> 历史收盘（指数 / 国债主连），index 为 Timestamp
    contract_daily: dict[str, pd.DataFrame]  # 产品 -> 2612 合约日线（date, close, settle）
    spot_quotes: dict[str, dict]             # 产品 -> {"price": float, "time": str}
    sources: list[SourceRecord]
    warnings: list[str]
    synthetic: bool = False
    akshare_version: str | None = None


@contextmanager
def requests_timeout(seconds: float):
    """AKShare 内部直接调用 requests.get 且不设超时；这里给所有请求补上默认超时。"""
    import requests

    original = requests.Session.request

    def patched(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = seconds
        return original(self, method, url, **kwargs)

    requests.Session.request = patched
    try:
        yield
    finally:
        requests.Session.request = original


class Fetcher:
    def __init__(self, cache_dir: Path = CACHE_DIR, retries: int = 3, timeout: float = 20,
                 interval: float = 1.0, log: Callable[[str], None] = print):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.retries = retries
        self.timeout = timeout
        self.interval = interval
        self.log = log
        self._last_call = 0.0
        self.meta_path = self.cache_dir / "meta.json"
        self.meta = json.loads(self.meta_path.read_text(encoding="utf-8")) if self.meta_path.exists() else {}

    def _sleep_interval(self):
        wait = self.interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def fetch(self, key: str, name: str, call: str, func: Callable[[], pd.DataFrame],
              date_col: str | None = None) -> tuple[pd.DataFrame | None, SourceRecord]:
        rec = SourceRecord(key=key, name=name, call=call, status="missing")
        last_err = None
        t0 = time.monotonic()
        for attempt in range(1, self.retries + 1):
            self._sleep_interval()
            try:
                with requests_timeout(self.timeout):
                    df = func()
                if df is None or len(df) == 0:
                    raise ValueError("返回空表")
                rec.status = "ok"
                rec.fetched_at = now_beijing().isoformat(timespec="seconds")
                rec.elapsed_sec = round(time.monotonic() - t0, 2)
                self._describe(rec, df, date_col)
                self._save_cache(key, df, rec)
                self.log(f"[OK] {name} | {call} | {rec.rows} 行 | {rec.first_date} ~ {rec.last_date} | "
                         f"{rec.elapsed_sec}s | 列: {rec.columns}")
                self.log(df.head(3).to_string())
                return df, rec
            except Exception as e:  # noqa: BLE001 - 数据源的异常五花八门
                last_err = f"{type(e).__name__}: {e}"
                self.log(f"[重试 {attempt}/{self.retries}] {name} 失败：{last_err}")
        rec.error = last_err
        rec.elapsed_sec = round(time.monotonic() - t0, 2)
        cached = self._load_cache(key)
        if cached is not None:
            df, meta = cached
            rec.status = "cache"
            rec.fetched_at = meta.get("fetched_at")
            self._describe(rec, df, date_col)
            self.log(f"[缓存] {name} 抓取失败，改用 {rec.fetched_at} 的缓存（数据截至 {rec.last_date}）")
            return df, rec
        self.log(f"[缺失] {name} 抓取失败且无缓存：{last_err}")
        return None, rec

    @staticmethod
    def _describe(rec: SourceRecord, df: pd.DataFrame, date_col: str | None):
        rec.rows = int(len(df))
        rec.columns = [str(c) for c in df.columns]
        if date_col and date_col in df.columns:
            d = pd.to_datetime(df[date_col], errors="coerce").dropna()
            if len(d):
                rec.first_date = d.min().date().isoformat()
                rec.last_date = d.max().date().isoformat()

    def _save_cache(self, key: str, df: pd.DataFrame, rec: SourceRecord):
        df.to_csv(self.cache_dir / f"{key}.csv", index=False)
        self.meta[key] = {"fetched_at": rec.fetched_at, "rows": rec.rows, "last_date": rec.last_date}
        self.meta_path.write_text(json.dumps(self.meta, ensure_ascii=False, indent=1), encoding="utf-8")

    def _load_cache(self, key: str):
        path = self.cache_dir / f"{key}.csv"
        if not path.exists():
            return None
        try:
            return pd.read_csv(path), self.meta.get(key, {})
        except Exception:  # noqa: BLE001
            return None


# ---------- 规范化 ----------

def series_from(df: pd.DataFrame, date_col: str, close_col: str) -> pd.Series:
    s = pd.Series(pd.to_numeric(df[close_col], errors="coerce").values,
                  index=pd.to_datetime(df[date_col], errors="coerce"))
    s = s[~s.index.isna()].dropna()
    s = s[s > 0]
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s.index = s.index.normalize()
    return s


def weekday_calendar(start: date, end: date, holidays: list[str] = KNOWN_HOLIDAYS_2026) -> list[date]:
    hol = {to_date(h) for h in holidays}
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in hol:
            out.append(d)
        d += timedelta(days=1)
    return out


def ensure_calendar_covers(calendar: list[date], until: date, warnings: list[str]) -> list[date]:
    """新浪日历若没覆盖到 until（如 12-18），用「工作日 − 已知节假日」补齐并警告。"""
    if calendar and calendar[-1] >= until:
        return calendar
    start = (calendar[-1] + timedelta(days=1)) if calendar else date(2023, 1, 1)
    extra = weekday_calendar(start, until)
    warnings.append(f"交易日历只覆盖到 {calendar[-1] if calendar else '无'}，"
                    f"之后到 {until} 按「工作日 − 已知节假日」推算（待核实）")
    return list(calendar) + extra


# ---------- 真实数据 ----------

def fetch_market_data(spec: ContractSpec, strategy: dict, cache_dir: Path = CACHE_DIR,
                      log: Callable[[str], None] = print, with_spot: bool = True) -> MarketData:
    import akshare as ak

    dcfg = strategy["data"]
    start = str(dcfg["history_start"]).replace("-", "")
    f = Fetcher(cache_dir, retries=int(dcfg["retries"]), timeout=float(dcfg["timeout_sec"]),
                interval=float(dcfg["interval_sec"]), log=log)
    sources: list[SourceRecord] = []
    warnings: list[str] = []

    # 交易日历
    df, rec = f.fetch("calendar", "交易日历", "ak.tool_trade_date_hist_sina()",
                      ak.tool_trade_date_hist_sina, date_col="trade_date")
    sources.append(rec)
    if df is not None:
        calendar = sorted({d.date() for d in pd.to_datetime(df["trade_date"], errors="coerce").dropna()})
        cal_src = "新浪交易日历" + ("" if rec.status == "ok" else "（缓存）")
    else:
        calendar = weekday_calendar(date(2023, 1, 1), date(2026, 12, 31))
        cal_src = "回退：工作日 − 已知节假日（待核实）"
        warnings.append("交易日历抓取失败且无缓存，按「工作日 − 已知 2026 节假日」推算交易日（待核实）")
    calendar = ensure_calendar_covers(calendar, spec.equity_expiry, warnings)

    closes: dict[str, pd.Series] = {}
    contract_daily: dict[str, pd.DataFrame] = {}

    # 历史序列：股指用指数，国债用新浪主力连续
    for c in spec.contracts:
        if c.kind == "equity":
            sym = c.history_symbol
            df, rec = f.fetch(f"hist_{c.product}", c.history_name,
                              f"ak.stock_zh_index_daily(symbol='{sym}')",
                              lambda sym=sym: ak.stock_zh_index_daily(symbol=sym), date_col="date")
            if df is not None:
                closes[c.product] = series_from(df, "date", "close")
        else:
            sym = c.history_symbol
            df, rec = f.fetch(f"hist_{c.product}", c.history_name,
                              f"ak.futures_main_sina(symbol='{sym}', start_date='{start}')",
                              lambda sym=sym: ak.futures_main_sina(symbol=sym, start_date=start),
                              date_col="日期")
            if df is not None:
                closes[c.product] = series_from(df, "日期", "收盘价")
        sources.append(rec)
        if rec.status == "cache":
            warnings.append(f"{c.history_name} 抓取失败，使用 {rec.fetched_at} 的缓存（数据截至 {rec.last_date}）")
        elif rec.status == "missing":
            warnings.append(f"{c.history_name} 数据缺失：{rec.error}")

    # 2612 合约日线
    for c in spec.contracts:
        code = c.code
        df, rec = f.fetch(f"daily_{code}", f"{c.app_name}（{code}）日线",
                          f"ak.futures_zh_daily_sina(symbol='{code}')",
                          lambda code=code: ak.futures_zh_daily_sina(symbol=code), date_col="date")
        sources.append(rec)
        if df is not None:
            d = pd.DataFrame({
                "date": pd.to_datetime(df["date"], errors="coerce").dt.normalize(),
                "close": pd.to_numeric(df["close"], errors="coerce"),
                "settle": pd.to_numeric(df.get("settle"), errors="coerce") if "settle" in df.columns else float("nan"),
            }).dropna(subset=["date", "close"])
            d = d[d["close"] > 0].sort_values("date").reset_index(drop=True)
            if len(d):
                contract_daily[c.product] = d
        if rec.status == "cache":
            warnings.append(f"{code} 日线抓取失败，使用 {rec.fetched_at} 的缓存（数据截至 {rec.last_date}）")
        elif rec.status == "missing":
            warnings.append(f"{code} 日线数据缺失：{rec.error}")

    # 盘中最新价（只用于展示和参考限价，失败跳过，不读缓存）
    spot_quotes: dict[str, dict] = {}
    if with_spot:
        for c in spec.contracts:
            code = c.code
            q = fetch_spot_quote(ak, code, f, log)
            if q is not None:
                spot_quotes[c.product] = q
        sources.append(SourceRecord(
            key="spot", name="盘中最新价", call="ak.futures_zh_spot(symbol=..., market='FF', adjust='0')",
            status="ok" if spot_quotes else "missing",
            fetched_at=now_beijing().isoformat(timespec="seconds"),
            rows=len(spot_quotes), columns=sorted(spot_quotes),
        ))

    return MarketData(calendar=calendar, calendar_source=cal_src, closes=closes,
                      contract_daily=contract_daily, spot_quotes=spot_quotes, sources=sources,
                      warnings=warnings, synthetic=False, akshare_version=getattr(ak, "__version__", None))


def fetch_spot_quote(ak, code: str, f: Fetcher, log) -> dict | None:
    """金融期货实时行情的列名可能和商品期货不同：打印列名，宽松解析，失败返回 None。"""
    f._sleep_interval()
    try:
        with requests_timeout(f.timeout):
            df = ak.futures_zh_spot(symbol=code, market="FF", adjust="0")
        log(f"[盘中] {code} 列名: {list(df.columns)}")
        if df is None or len(df) == 0:
            return None
        row = df.iloc[0]
        price = None
        for col in ("current_price", "最新价", "last", "price"):
            if col in df.columns:
                price = pd.to_numeric(row[col], errors="coerce")
                break
        if price is None or not (price > 0):
            return None
        t = str(row["time"]) if "time" in df.columns else ""
        return {"price": float(price), "time": t, "fetched_at": now_beijing().isoformat(timespec="seconds")}
    except Exception as e:  # noqa: BLE001
        log(f"[盘中] {code} 获取失败（跳过）：{type(e).__name__}: {e}")
        log(traceback.format_exc(limit=1))
        return None
