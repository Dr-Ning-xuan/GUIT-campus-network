"""诊断 gray / sat / 开运算 三处差异，定位到底哪一步不同。"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    p = ROOT / "_eval" / "imgs" / "000.png"
    src = Image.open(p).convert("RGB")
    rgb = np.array(src)

    g_cv = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    g_pil = np.array(src.convert("L"))
    g_float = np.rint(0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]).astype(np.uint8)
    print("gray: pil-vs-cv 不同像素数 =", int((g_pil != g_cv).sum()), " maxdiff =", int(np.abs(g_pil.astype(int) - g_cv.astype(int)).max()))
    print("gray: float-vs-cv 不同像素数 =", int((g_float != g_cv).sum()))

    s_cv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[:, :, 1]
    mx = rgb.max(axis=2).astype(np.int32)
    mn = rgb.min(axis=2).astype(np.int32)
    s_floor = np.where(mx > 0, (mx - mn) * 255 // np.maximum(mx, 1), 0).astype(np.uint8)
    s_round = np.where(mx > 0, np.rint((mx - mn) * 255.0 / np.maximum(mx, 1)), 0).astype(np.uint8)
    print("sat: floor-vs-cv 不同像素数 =", int((s_floor != s_cv).sum()), " maxdiff =", int(np.abs(s_floor.astype(int) - s_cv.astype(int)).max()))
    print("sat: round-vs-cv 不同像素数 =", int((s_round != s_cv).sum()), " maxdiff =", int(np.abs(s_round.astype(int) - s_cv.astype(int)).max()))

    # 开运算
    mask = ((g_cv < 155) & (s_cv < 220)).astype(np.uint8) * 255

    def erode2(m):
        p2 = np.pad(m, ((0, 1), (0, 1)), mode="constant", constant_values=255)
        return np.minimum(np.minimum(p2[:-1, :-1], p2[:-1, 1:]), np.minimum(p2[1:, :-1], p2[1:, 1:]))

    def dilate2(m):
        p2 = np.pad(m, ((1, 0), (1, 0)), mode="constant", constant_values=0)
        return np.maximum(np.maximum(p2[:-1, :-1], p2[:-1, 1:]), np.maximum(p2[1:, :-1], p2[1:, 1:]))

    cv_open = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    np_open = dilate2(erode2(mask))
    print("open: numpy-vs-cv 不同像素数 =", int((np_open != cv_open).sum()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
