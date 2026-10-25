"""离线基线：用 _eval/imgs 那批带答案的图跑识别器，不联网、不提交。

用法：
    python tools/bench_offline.py
    python tools/bench_offline.py --dir _eval/live --no-labels   # 只看认不认得出
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
    ap.add_argument("--dir", default="_eval/imgs")
    ap.add_argument("--labels", default="_eval/labels60.json")
    ap.add_argument("--no-labels", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    img_dir = ROOT / args.dir
    if not img_dir.exists():
        print(f"[FAIL] 图片目录不存在: {img_dir}")
        return 1

    labels: dict[str, str] = {}
    if not args.no_labels and (ROOT / args.labels).exists():
        labels = json.loads((ROOT / args.labels).read_text(encoding="utf-8"))

    solver = get_math_solver()
    t0 = time.time()
    if not solver.warmup():
        print(f"[FAIL] 引擎加载失败: {solver.last_error}")
        return 1
    print(f"[INFO] 引擎加载 {time.time()-t0:.1f}s")

    files = sorted(img_dir.glob("*.png"))
    if args.limit:
        files = files[: args.limit]

    got = right = wrong = skipped = 0
    times: list[float] = []
    detail: list[str] = []

    for p in files:
        key = p.stem
        label = labels.get(key)
        png = p.read_bytes()
        t1 = time.time()
        cap = solver.solve(png)
        dt = time.time() - t1
        times.append(dt)

        if cap.ok:
            got += 1
            if label is None:
                detail.append(f"  {key}  {cap.describe():<26} {dt*1000:>6.0f}ms")
            elif cap.answer == label:
                right += 1
            else:
                wrong += 1
                detail.append(f"  {key}  {cap.describe():<26} 期望={label}  {dt*1000:>6.0f}ms  [错]")
        else:
            skipped += 1
            if label is not None:
                detail.append(f"  {key}  未识别               期望={label}  {dt*1000:>6.0f}ms")

    if detail:
        print("[DETAIL]")
        for line in detail[:60]:
            print(line)

    total = len(files)
    avg = sum(times) / max(len(times), 1) * 1000
    print()
    print(f"[RESULT] 共 {total} 张，平均 {avg:.0f}ms/张，总计 {sum(times):.1f}s")
    print(f"[RESULT] 认出了答案：{got}/{total}（{got/max(total,1)*100:.0f}%）")
    if labels and not args.no_labels:
        judged = right + wrong
        print(f"[RESULT] 有答案的 {judged} 张里，答对 {right} 张（{right/max(judged,1)*100:.0f}%）")
        print(f"[RESULT] 未识别（没敢答）：{skipped}")
        print(f"[RESULT] 端到端命中率（答对/全部）：{right/max(total,1)*100:.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
