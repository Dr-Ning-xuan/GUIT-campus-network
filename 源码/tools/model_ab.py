"""比较 ddddocr 两个模型的单独表现：common_old(默认) 与 common(beta)。

用法：
    python tools/model_ab.py [--dir build/captcha-bench/math]

对每张图分别用 old-only / beta-only / 两者投票 跑完整 solve，
打印三者答案对照；对已知通过（PASS）的样本还能直接看出谁对。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ocr import MathCaptchaSolver, _install_cv2_stub  # noqa: E402

# bench 里服务端判过 PASS 的编号（识别答案即真值）
PASSED = {"001", "006", "010", "011", "012", "019", "020", "021", "028", "029"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="build/captcha-bench/math")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    _install_cv2_stub()
    import ddddocr

    solver = MathCaptchaSolver()
    solver.warmup()
    print(f"[INFO] 默认引擎数 = {len(solver._engines)}")

    old = ddddocr.DdddOcr(show_ad=False)
    beta = ddddocr.DdddOcr(show_ad=False, beta=True)
    both = [old, beta]

    src = ROOT / args.dir
    files = sorted(src.glob("*.png"))
    if args.limit:
        files = files[: args.limit]
    print(f"[INFO] 样本 {len(files)} 张：{src}")

    rows = []
    for p in files:
        png = p.read_bytes()
        res = {}
        for name, engines in (("old", [old]), ("beta", [beta]), ("both", both)):
            solver._engines = engines
            cap = solver.solve(png)
            res[name] = cap.answer if cap.ok else ""
        rows.append((p.stem, res))

    print()
    print(f"{'图':<14}{'old':<8}{'beta':<8}{'both':<8}{'一致?':<8}{'PASS?'}")
    print("-" * 62)
    n_old = n_beta = n_both = n_same = 0
    for stem, r in rows:
        key = stem.split("_")[0]
        same = "=" if r["old"] == r["beta"] else "≠"
        n_old += bool(r["old"])
        n_beta += bool(r["beta"])
        n_both += bool(r["both"])
        n_same += r["old"] == r["beta"]
        print(f"{stem:<14}{r['old'] or '-':<8}{r['beta'] or '-':<8}{r['both'] or '-':<8}{same:<8}{'PASS' if key in PASSED else ''}")

    tot = len(rows)
    print()
    print(f"[RESULT] 认出答案：old {n_old}/{tot}  beta {n_beta}/{tot}  both {n_both}/{tot}")
    print(f"[RESULT] old 与 beta 答案相同：{n_same}/{tot}")

    # 只看已知真值的样本
    print()
    print("== 已知通过（答案即真值）的样本 ==")
    for stem, r in rows:
        key = stem.split("_")[0]
        if key not in PASSED:
            continue
        truth = stem.split("_")[1]
        ok_old = "√" if r["old"] == truth else ("空" if not r["old"] else "×")
        ok_beta = "√" if r["beta"] == truth else ("空" if not r["beta"] else "×")
        print(f"  {stem:<14} 真值={truth:<6} old={r['old'] or '-':<6}[{ok_old}]  beta={r['beta'] or '-':<6}[{ok_beta}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
