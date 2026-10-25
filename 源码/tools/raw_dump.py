"""打印某张验证码在每个变体/模型下的原始识别输出，用来定位「认出来了却被规则拒掉」。

用法：
    python tools/raw_dump.py build/captcha-bench/math/022_FAIL.png
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ocr import MathCaptchaSolver, _install_cv2_stub, normalize_expr, compute  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python tools/raw_dump.py <图片路径> [...]")
        return 2
    _install_cv2_stub()
    solver = MathCaptchaSolver()
    engines = solver._ensure()
    print(f"[INFO] 引擎数 = {len(engines)}")

    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.is_absolute():
            p = ROOT / p
        png = p.read_bytes()
        variants = solver._variants(png)
        print()
        print("=" * 70)
        print(f"图: {p.name}   变体数 = {len(variants)}")
        raws: list[str] = []
        for i, v in enumerate(variants):
            eng = engines[i % len(engines)]
            tag = "beta" if i % len(engines) == 1 else "old "
            try:
                raw = eng.classification(v)
            except Exception as exc:
                raw = f"<ERR {exc}>"
            raws.append(raw)
            print(f"  v{i:02d} [{tag}] {raw!r:<24} -> norm={normalize_expr(raw)!r}")

        print()
        print("  --- 归一化后统计（每张变体一票）---")
        c = Counter(normalize_expr(r) for r in raws)
        for expr, n in c.most_common():
            ans = compute(expr) if expr else None
            print(f"    {expr or '(空)':<10} {n:>3} 票   答案={ans}")
        print()
        print("  --- 原始串统计 ---")
        for raw, n in Counter(raws).most_common(8):
            print(f"    {raw!r:<26} {n} 次")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
