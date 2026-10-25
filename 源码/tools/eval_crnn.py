"""评估 CRNN 的端到端准确率，含多阈值投票。

单阈值一次推理只给一次表决机会。这里量化投票到底值不值：
既看每个阈值各自的整题准确率，也看四个阈值投票之后是多少。

用法：
    python tools/eval_crnn.py build/crnn_v3.npz
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.crnn import CrnnSolver  # noqa: E402
from src.ocr import compute, normalize_expr  # noqa: E402


def main() -> int:
    npz = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "models/crnn.npz"
    if not npz.is_absolute():
        npz = ROOT / npz
    solver = CrnnSolver(npz)
    if not solver.warmup():
        print(f"[FAIL] {solver.last_error}")
        return 1

    data = ROOT / "_train"
    rows = [
        json.loads(line)
        for line in (data / "labels.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(f"[INFO] 样本 {len(rows)} 条")

    single_ok = Counter()
    vote_ok = 0
    n = 0
    vote_conf: Counter = Counter()
    for rec in rows:
        img = data / "images" / rec["file"]
        if not img.exists():
            continue
        want = rec["answer"]
        variants = solver.solve_variants(img.read_bytes())
        if not variants:
            continue
        n += 1
        answers = []
        for idx, (expr, _t) in enumerate(variants):
            e = normalize_expr(expr)
            a = compute(e) if e else ""
            answers.append(a)
            if a == want:
                single_ok[idx] += 1
        c = Counter(a for a in answers if a)
        if c:
            best, cnt = c.most_common(1)[0]
            vote_conf[cnt] += 1
            if best == want:
                vote_ok += 1

    print()
    print(f"[RESULT] 参与评估 {n} 条")
    for idx in sorted(single_ok):
        print(f"[RESULT] 单阈值 #{idx}: {single_ok[idx]:>4}/{n} = {single_ok[idx] / max(n, 1) * 100:.1f}%")
    print(f"[RESULT] 投票后: {vote_ok:>4}/{n} = {vote_ok / max(n, 1) * 100:.1f}%")
    print(f"[RESULT] 投票票数分布 {dict(sorted(vote_conf.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
