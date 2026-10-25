"""多阈值投票版除号检测：单个阈值不可靠，就让多个阈值一起投票。

每个阈值独立找「上下两个小点 + 中间一条横线」，同一位置（x 差 <=8）被
至少 3 个阈值认定，才算真的有除号。

用法：
    python tools/test_div3.py
"""
from __future__ import annotations

import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
THRESHOLDS = (105, 115, 125, 135, 145, 155)

DIV = ["004", "006", "010", "011", "021", "024", "030", "041", "045", "049", "051", "055", "059"]
MINUS = ["001", "012", "013", "016", "020", "028", "033", "034", "036", "037", "038",
         "042", "043", "044", "050", "057", "058"]
PLUS = ["000", "009", "015", "017", "026", "027", "029", "031", "046", "054", "056"]
TIMES = ["003", "005", "007", "014", "018", "019", "022", "025", "032", "035", "039",
         "048", "052", "053"]


def dots_at(rgb: np.ndarray, t: int) -> list[float]:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    sat = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[:, :, 1]
    mask = (((gray < t) & (sat < 220)).astype(np.uint8)) * 255
    n, _lab, stats, cent = cv2.connectedComponentsWithStats(mask)
    dots = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if 4 <= area <= 160 and w <= 14 and h <= 14 and 20 <= cent[i][0] <= 150:
            dots.append((float(cent[i][0]), float(cent[i][1])))
    hits = []
    for i in range(len(dots)):
        ax, ay = dots[i]
        for j in range(i + 1, len(dots)):
            bx, by = dots[j]
            if abs(ax - bx) > 7:
                continue
            if not (7 <= abs(ay - by) <= 24):
                continue
            xc, yc = int((ax + bx) / 2), int((ay + by) / 2)
            band = mask[max(0, yc - 2) : yc + 3, max(0, xc - 10) : xc + 11]
            if int((band > 0).sum()) >= 10:
                hits.append(round((ax + bx) / 2))
    return hits


def vote(rgb: np.ndarray, need: int = 3) -> list[int]:
    buckets: list[list[int]] = []
    for t in THRESHOLDS:
        for x in dots_at(rgb, t):
            for b in buckets:
                if abs(b[0] - x) <= 8:
                    b[1] += 1
                    break
            else:
                buckets.append([x, 1])
    return [b[0] for b in buckets if b[1] >= need]


def main() -> int:
    for need in (2, 3, 4):
        res = {}
        for names, tag in ((DIV, "div"), (MINUS, "min"), (PLUS, "plus"), (TIMES, "times")):
            hit = 0
            for nm in names:
                rgb = np.array(
                    Image.open(io.BytesIO((ROOT / "_eval/imgs" / f"{nm}.png").read_bytes())).convert("RGB")
                )
                if vote(rgb, need):
                    hit += 1
            res[tag] = (hit, len(names))
        print(f"need>={need}:  除号 {res['div'][0]}/{res['div'][1]}  "
              f"减号误报 {res['min'][0]}/{res['min'][1]}  "
              f"加号误报 {res['plus'][0]}/{res['plus'][1]}  "
              f"乘号误报 {res['times'][0]}/{res['times'][1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
