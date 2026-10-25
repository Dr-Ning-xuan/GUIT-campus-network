"""离线评测：对目录里已有的验证码图片跑识别器，输出逐张结果（不做网络请求）。

用法：
    python tools/eval_offline.py --src _eval/imgs            # 用当前 src.ocr
    python tools/eval_offline.py --src _eval/imgs --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ocr import get_math_solver  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="_eval/imgs")
    ap.add_argument("--json", default="")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    src = Path(args.src)
    if not src.is_absolute():
        src = ROOT / src
    files = sorted(src.glob("*.png"))
    if args.limit:
        files = files[: args.limit]

    solver = get_math_solver()
    if not solver.warmup():
        print(f"[FAIL] {solver.last_error}")
        return 1

    rows = []
    t_all = time.time()
    for f in files:
        png = f.read_bytes()
        t0 = time.time()
        cap = solver.solve(png)
        dt = time.time() - t0
        st = solver.last_stats or {}
        rows.append(
            {
                "name": f.stem,
                "answer": cap.answer,
                "expr": cap.expr,
                "votes": cap.votes,
                "parsed": st.get("parsed", 0),
                "calls": st.get("recognized", 0),
                "ms": round(dt * 1000),
            }
        )
        flag = "OK " if cap.ok else "---"
        print(
            f"{f.stem}  {flag}  {cap.expr or '':<8} -> {cap.answer or '?':<4} "
            f"票{cap.votes:<3}/{st.get('parsed', 0):<3} {dt * 1000:>5.0f}ms",
            flush=True,
        )

    ok = sum(1 for r in rows if r["answer"])
    print()
    print(f"[RESULT] {len(rows)} 张，认出 {ok}（{ok / max(len(rows), 1) * 100:.0f}%），"
          f"总耗时 {time.time() - t_all:.1f}s")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1), "utf-8")
        print(f"[SAVED] {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
