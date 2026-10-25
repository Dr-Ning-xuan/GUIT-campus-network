"""改进版除号检测：只在中段找「上下对称小点 + 中间横线」。

用法：
    python tools/test_div2.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent

DIV = ["004", "006", "010", "011", "021", "024", "030", "041", "045", "049", "051", "055", "059"]
MINUS = ["000", "001", "012", "013", "016", "020", "028", "033", "034", "036", "037",
         "038", "042", "043", "044", "050", "057", "058"]


def detect(rgb: np.ndarray, t: int) -> list[float]:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    sat = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[:, :, 1]
    mask = (((gray < t) & (sat < 220)).astype(np.uint8)) * 255
    n, _lab, stats, cent = cv2.connectedComponentsWithStats(mask)
    dots = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if 4 <= area <= 160 and w <= 14 and h <= 14 and 20 <= cent[i][0] <= 145:
            dots.append((float(cent[i][0]), float(cent[i][1])))
    hits = []
    for i in range(len(dots)):
        ax, ay = dots[i]
        for j in range(i + 1, len(dots)):
            bx, by = dots[j]
            if abs(ax - bx) > 7:
                continue
            dy = abs(ay - by)
            if not (7 <= dy <= 24):
                continue
            xc, yc = int((ax + bx) / 2), int((ay + by) / 2)
            band = mask[max(0, yc - 2) : yc + 3, max(0, xc - 10) : xc + 11]
            if int((band > 0).sum()) >= 10:
                hits.append(round((ax + bx) / 2, 1))
    return hits


def main() -> int:
    for t in (105, 115, 125, 135, 145):
        div_hit = 0
        fp = 0
        detail = []
        for names, tag in ((DIV, "div"), (MINUS, "min")):
            for nm in names:
                rgb = np.array(
                    Image.open(io.BytesIO((ROOT / "_eval/imgs" / f"{nm}.png").read_bytes())).convert("RGB")
                )
                h = detect(rgb, t)
                if tag == "div" and h:
                    div_hit += 1
                if tag == "min" and h:
                    fp += 1
                    detail.append(nm)
        print(f"t={t}: 除号命中 {div_hit}/{len(DIV)}  减号误报 {fp}/{len(MINUS)}  {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
