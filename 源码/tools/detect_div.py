"""检验「除号 = 横线上下一对小圆点」这个几何特征能不能稳定认出来。

用法：
    python tools/detect_div.py --src _eval/imgs
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def mask_of(rgb: np.ndarray, t: int = 145, satmax: int = 220) -> np.ndarray:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    sat = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[:, :, 1]
    return (((gray < t) & (sat < satmax)).astype(np.uint8)) * 255


def find_div(mask: np.ndarray) -> list[tuple[float, float]]:
    n, _lab, stats, cent = cv2.connectedComponentsWithStats(mask)
    dots = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if 5 <= area <= 140 and w <= 13 and h <= 13:
            dots.append((float(cent[i][0]), float(cent[i][1])))
    hits = []
    for i in range(len(dots)):
        ax, ay = dots[i]
        for j in range(i + 1, len(dots)):
            bx, by = dots[j]
            if abs(ax - bx) > 6:
                continue
            dy = abs(ay - by)
            if not (7 <= dy <= 22):
                continue
            xc, yc = (ax + bx) / 2, (ay + by) / 2
            band = mask[max(0, int(yc) - 2) : int(yc) + 3, max(0, int(xc) - 9) : int(xc) + 10]
            if int((band > 0).sum()) >= 14:          # 中间得有一条横线撑着
                hits.append((xc, yc))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="_eval/imgs")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    src = ROOT / args.src
    out = {}
    for f in sorted(src.glob("*.png")):
        rgb = np.array(Image.open(io.BytesIO(f.read_bytes())).convert("RGB"))
        hits = find_div(mask_of(rgb))
        out[f.stem] = [[round(x, 1), round(y, 1)] for x, y in hits]
        flag = "DIV" if hits else "   "
        print(f"{f.stem} {flag} {[[round(x) for x, _ in hits]]}", flush=True)
    if args.json:
        (ROOT / args.json).write_text(json.dumps(out, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
