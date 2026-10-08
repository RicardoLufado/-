"""逐个调用数据接口，打印是否成功、耗时、列名和前 3 行。

python scripts/probe_sources.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import akshare as ak  # noqa: E402

from engine.config import load_contracts  # noqa: E402
from engine.data import requests_timeout  # noqa: E402


def probe(label: str, func):
    t = time.monotonic()
    try:
        with requests_timeout(20):
            df = func()
        dt = time.monotonic() - t
        print(f"[成功] {label} | {dt:.1f}s | {len(df)} 行 | 列: {list(df.columns)}")
        print(df.head(3).to_string())
        print(df.tail(2).to_string())
        return True
    except Exception as e:  # noqa: BLE001
        dt = time.monotonic() - t
        print(f"[失败] {label} | {dt:.1f}s | {type(e).__name__}: {e}")
        return False
    finally:
        print("-" * 80, flush=True)
        time.sleep(1)


def main() -> int:
    print("akshare", ak.__version__)
    spec = load_contracts()
    ok = []
    ok.append(probe("交易日历 tool_trade_date_hist_sina", ak.tool_trade_date_hist_sina))
    for c in spec.contracts:
        if c.kind == "equity":
            ok.append(probe(f"{c.history_name} stock_zh_index_daily({c.history_symbol})",
                            lambda c=c: ak.stock_zh_index_daily(symbol=c.history_symbol)))
        else:
            ok.append(probe(f"{c.history_name} futures_main_sina({c.history_symbol})",
                            lambda c=c: ak.futures_main_sina(symbol=c.history_symbol, start_date="20230501")))
    for c in spec.contracts:
        ok.append(probe(f"{c.code} futures_zh_daily_sina", lambda c=c: ak.futures_zh_daily_sina(symbol=c.code)))
    codes = ",".join(c.code for c in spec.contracts)
    ok.append(probe(f"盘中 futures_zh_spot({codes}, FF)",
                    lambda: ak.futures_zh_spot(symbol=codes, market="FF", adjust="0")))
    print(f"共 {len(ok)} 个接口，成功 {sum(ok)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
