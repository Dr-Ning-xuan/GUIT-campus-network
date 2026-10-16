"""验证码识别率压测：抓 N 张真图、识别、存盘，供人工核对。

用法：
    python tools/captcha_bench.py --count 30 --out ./build/captcha-bench
"""
from __future__ import annotations

import argparse
import collections
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ocr import get_solver  # noqa: E402
from src.portal import PortalClient  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=30)
    ap.add_argument("--out", default=str(ROOT / "build" / "captcha-bench"))
    ap.add_argument("--base", default="http://10.6.6.6")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    solver = get_solver()
    t0 = time.time()
    if not solver.warmup():
        print(f"[FAIL] 识别引擎加载失败：{solver.last_error}")
        return 1
    print(f"[INFO] 引擎加载耗时 {time.time() - t0:.2f}s")

    cli = PortalClient(args.base)
    cli._get("/login")  # 建 session
    stats = collections.Counter()
    t0 = time.time()
    for i in range(args.count):
        png = cli.fetch_captcha()
        t1 = time.time()
        code = solver.normalize(solver._ensure().classification(png))
        dt = time.time() - t1
        name = f"{i:03d}_{code or 'FAIL'}.png"
        (out / name).write_bytes(png)
        stats[len(code) if code else 0] += 1
        print(f"  #{i:02d}  {code or '(识别失败)':<6} {len(png):>5}B  {dt * 1000:>6.0f}ms")
    total = time.time() - t0
    print()
    print(f"[RESULT] {args.count} 张，用时 {total:.1f}s（平均 {total / args.count * 1000:.0f}ms/张）")
    print(f"[RESULT] 识别出 4 位的：{stats[4]} 张；非 4 位：{args.count - stats[4]} 张")
    print(f"[RESULT] 图片已存到 {out}，文件名带识别结果，人工核对后可算出真实准确率")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
