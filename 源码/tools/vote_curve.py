"""看「票数阈值」和「准确率」之间的关系，判断 75% 精度需要多大的票数门槛。

用法：
    python tools/vote_curve.py --pred _eval/v131.json --labels _eval/labels60.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default="_eval/v131.json")
    ap.add_argument("--labels", default="_eval/labels60.json")
    args = ap.parse_args()

    pred = json.loads((ROOT / args.pred).read_text("utf-8"))
    labels = json.loads((ROOT / args.labels).read_text("utf-8"))

    rows = []
    for r in pred:
        truth = labels.get(r["name"])
        if not truth:
            continue
        rows.append((r["name"], truth, r.get("answer") or "", r.get("votes") or 0))

    n = len(rows)
    print(f"样本 {n}\n")
    print("票数门槛  覆盖张数  占样本   其中答对  覆盖内准确率  整体可用率")
    for thr in (0, 1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20):
        sel = [x for x in rows if x[2] and x[3] >= thr]
        right = sum(1 for x in sel if x[2] == x[1])
        if not sel:
            print(f"{thr:>6}     0")
            continue
        print(f"{thr:>6}   {len(sel):>7}  {len(sel)/n*100:>6.0f}%  {right:>7}  "
              f"{right/len(sel)*100:>10.0f}%  {right/n*100:>10.0f}%")

    print("\n-- 按票数分桶的准确率 --")
    buckets = [(1, 1), (2, 2), (3, 4), (5, 7), (8, 12), (13, 100)]
    for lo, hi in buckets:
        sel = [x for x in rows if x[2] and lo <= x[3] <= hi]
        if not sel:
            continue
        right = sum(1 for x in sel if x[2] == x[1])
        print(f"票 {lo:>2}-{hi if hi < 100 else '+':<3}: {len(sel):>2} 张, 对 {right:>2}, "
              f"准确率 {right/len(sel)*100:>3.0f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
