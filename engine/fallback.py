"""引擎失败时的兜底：用缓存里上一次成功的 latest.json 部署，并在 warnings 里写明原因。

python -m engine.fallback --log engine.log
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import now_beijing
from .data import CACHE_DIR
from .run import SITE_DATA, sync_history


def failure_reason(log_path: Path | None, max_lines: int = 6) -> str:
    if not log_path or not log_path.exists():
        return "引擎运行失败（没有日志）"
    lines = [l.rstrip() for l in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    errs = [l for l in lines if "Error" in l or "Exception" in l or "错误" in l]
    tail = errs[-1:] if errs else lines[-max_lines:]
    return "；".join(tail)[-500:] or "引擎运行失败"


def write_fallback(out_dir: Path, cache_dir: Path, reason: str) -> dict:
    now = now_beijing().isoformat(timespec="seconds")
    good = cache_dir / "last_good" / "latest.json"
    if good.exists():
        data = json.loads(good.read_text(encoding="utf-8"))
        data.setdefault("warnings", [])
        data["warnings"].insert(0, f"本次计算失败（{now}），原因：{reason}。下面显示的是 {data.get('generated_at')} "
                                   f"的上次成功结果，请留意数据时间。")
        data["fallback"] = {"failed_at": now, "reason": reason, "showing": data.get("generated_at")}
    else:
        data = {
            "schema": 1,
            "generated_at": None,
            "data_asof": None,
            "status": "数据缺失",
            "modes": [],
            "data_sources": [],
            "explanations": [],
            "limitations": [],
            "warnings": [f"本次计算失败（{now}），原因：{reason}。缓存里也没有上一次成功的结果，所以页面没有可用数字。"],
            "fallback": {"failed_at": now, "reason": reason, "showing": None},
        }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    sync_history(out_dir, cache_dir)
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", type=Path)
    ap.add_argument("--out", type=Path, default=SITE_DATA)
    ap.add_argument("--cache", type=Path, default=CACHE_DIR)
    args = ap.parse_args(argv)
    reason = failure_reason(args.log)
    data = write_fallback(args.out, args.cache, reason)
    print(f"[兜底] 原因：{reason}；展示：{(data.get('fallback') or {}).get('showing')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
