"""用「等号是两条平行横线」这个特征定位裁切点，比列投影稳。

用法：
    python tools/find_eq2.py --src _eval/imgs --draw
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


def eq_bars(mask: np.ndarray, min_w: int = 18, xmin_frac: float = 0.55) -> tuple[int | None, list]:
    """返回 (等号左端 x, 候选横线列表)。等号一定在右侧，x 小于 xmin_frac 的横线一律不算。"""
    W = mask.shape[1]
    hor = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((1, min_w), np.uint8))
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(hor)
    bars = []
    for i in range(1, n):
        x, y, w, h, _a = stats[i]
        if w >= min_w and h <= 14 and x >= W * xmin_frac:
            bars.append((int(x), int(y), int(w), int(h)))
    best = None
    for i in range(len(bars)):
        ax, ay, aw, ah = bars[i]
        for j in range(i + 1, len(bars)):
            bx, by, bw, bh = bars[j]
            dy = abs(by - ay)
            overlap = min(ax + aw, bx + bw) - max(ax, bx)
            if 4 <= dy <= 13 and overlap >= 12 and overlap >= 0.5 * min(aw, bw):
                x0 = min(ax, bx)
                if best is None or x0 > best:
                    best = x0
    return best, bars


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="_eval/imgs")
    ap.add_argument("--draw", action="store_true")
    ap.add_argument("--out", default="_eval/eqcut2.png")
    ap.add_argument("--t", type=int, default=145)
    args = ap.parse_args()

    src = ROOT / args.src
    files = sorted(src.glob("*.png"))
    cuts, misses = [], []
    for f in files:
        rgb = np.array(Image.open(io.BytesIO(f.read_bytes())).convert("RGB"))
        c, bars = eq_bars(mask_of(rgb, args.t))
        if c is None:
            misses.append(f.stem)
        else:
            cuts.append(c)
    arr = np.array(cuts) if cuts else np.array([0])
    print(f"[STAT] 命中 {len(cuts)}/{len(files)}  min={arr.min()} max={arr.max()} "
          f"mean={arr.mean():.1f} p10={np.percentile(arr,10):.0f} p90={np.percentile(arr,90):.0f}")
    print("[HIST 100..200] ", np.histogram(arr, bins=10, range=(100, 200))[0].tolist())
    print("[MISS]", misses)

    if args.draw:
        s, cols, pick = 2, 2, files[:12]
        cw, ch = 200 * s, 60 * s
        rows = (len(pick) + cols - 1) // cols
        sheet = np.full((rows * (ch + 24) + 8, cols * (cw + 8) + 8, 3), 40, np.uint8)
        for i, f in enumerate(pick):
            rgb = np.array(Image.open(io.BytesIO(f.read_bytes())).convert("RGB"))
            c, _ = eq_bars(mask_of(rgb, args.t))
            im = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            if c:
                cv2.line(im, (c, 0), (c, 59), (0, 0, 255), 1)
            r, k = divmod(i, cols)
            y = 8 + r * (ch + 24)
            x = 8 + k * (cw + 8)
            sheet[y + 22 : y + 22 + ch, x : x + cw] = cv2.resize(im, (cw, ch),
                                                                 interpolation=cv2.INTER_NEAREST)
            cv2.putText(sheet, f"{f.stem} cut={c}", (x + 4, y + 17),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
        out = ROOT / args.out
        cv2.imwrite(str(out), sheet)
        print("[DRAW]", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
