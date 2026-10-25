"""把 eval_offline 的 json 结果和人工标签对照，算真正的准确率。

用法：
    python tools/score.py --pred _eval/v131.json --labels _eval/labels60.json
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
    ap.add_argument("--show-errors", action="store_true")
    args = ap.parse_args()

    pred = json.loads((ROOT / args.pred).read_text("utf-8"))
    labels = json.loads((ROOT / args.labels).read_text("utf-8"))

    n = ok = right = 0
    errors = []
    for row in pred:
        name = row["name"]
        truth = labels.get(name)
        if not truth:
            continue
        n += 1
        got = row.get("answer") or ""
        ok += bool(got)
        if got and got == truth:
            right += 1
        elif got:
            errors.append((name, truth, got, row.get("expr"), row.get("votes"), row.get("parsed")))
    print(f"样本 {n}  认出 {ok} ({ok/n*100:.0f}%)  答对 {right} "
          f"(认出后 {right/max(ok,1)*100:.0f}%，单图 {right/n*100:.0f}%)")
    if args.show_errors:
        print("\n-- 认错明细 --")
        for name, truth, got, expr, votes, parsed in errors:
            print(f"  {name} 真={truth:<4} 识={got:<4} 式={expr or '':<8} 票{votes}/{parsed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
