"""探测：这份验证码能不能稳定地按连通域切成「字符块」。

思路是最后的解法：算式只有 A op B = ? 五个字形，逐块识别比整行识别稳得多。
先确认分割本身可行，再谈识别。

用法：
    python tools/segment_probe.py [--dir build/captcha-bench/math]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, deque
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ocr import _dilate2, _erode2  # noqa: E402


def components(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """4 邻域连通域，返回 (x0, y0, x1, y1, 面积)。"""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    for sy in range(h):
        for sx in range(w):
            if not mask[sy, sx] or seen[sy, sx]:
                continue
            q = deque([(sy, sx)])
            seen[sy, sx] = True
            x0 = x1 = sx
            y0 = y1 = sy
            n = 0
            while q:
                y, x = q.popleft()
                n += 1
                if x < x0: x0 = x
                if x > x1: x1 = x
                if y < y0: y0 = y
                if y > y1: y1 = y
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        q.append((ny, nx))
            out.append((x0, y0, x1, y1, n))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="build/captcha-bench/math")
    ap.add_argument("--thresh", type=int, default=150)
    args = ap.parse_args()

    src = ROOT / args.dir
    files = sorted(src.glob("*.png"))
    print(f"[INFO] {len(files)} 张，阈值 gray<{args.thresh}")

    counts = Counter()
    for p in files:
        im = Image.open(p).convert("RGB")
        rgb = np.array(im)
        gray = np.array(im.convert("L"))
        mx = rgb.max(axis=2).astype(np.int32)
        mn = rgb.min(axis=2).astype(np.int32)
        sat = np.where(mx > 0, np.rint((mx - mn) * 255.0 / np.maximum(mx, 1)), 0.0).astype(np.uint8)
        mask = ((gray < args.thresh) & (sat < 220)).astype(np.uint8) * 255
        mask = _dilate2(_erode2(mask))
        comps = components(mask > 0)
        # 只看够大的块（面积 & 高度），滤掉噪点
        big = [c for c in comps if c[4] >= 40 and (c[3] - c[1] + 1) >= 12]
        big.sort()
        counts[len(big)] += 1
        boxes = " ".join(f"{c[0]}-{c[2]}x{c[1]}-{c[3]}({c[4]})" for c in big[:7])
        print(f"  {p.name:<16} 块数={len(big):>2}  {boxes}")

    print()
    print("[RESULT] 块数分布:", dict(sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
