"""对若干张验证码一次性输出「干净变体」对照图（gray<t & sat<s），放大 4 倍。

用法：
    python tools/peek_clean.py 000 001 002 --src _eval/imgs --out _eval/peek-clean.png
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+")
    ap.add_argument("--src", default="_eval/imgs")
    ap.add_argument("--out", default="_eval/peek-clean.png")
    ap.add_argument("--scale", type=int, default=4)
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    src = Path(args.src)
    if not src.is_absolute():
        src = root / src
    out = root / args.out

    s = args.scale
    pad = 8
    cw, ch = 200 * s, 60 * s
    grid_t = (115, 130, 145)
    rows = []
    for name in args.names:
        f = src / f"{name}.png"
        rgb = np.array(Image.open(io.BytesIO(f.read_bytes())).convert("RGB"))
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        sat = hsv[:, :, 1]
        cells = [cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)]
        for t in grid_t:
            m = (((gray < t) & (sat < 200)).astype(np.uint8)) * 255
            cells.append(cv2.cvtColor(255 - m, cv2.COLOR_GRAY2BGR))
        rows.append((name, cells))

    cols = len(rows[0][1])
    sheet = np.full(
        (len(rows) * (ch + 26 + pad) + pad, cols * (cw + pad) + pad, 3), 40, np.uint8
    )
    for r, (name, cells) in enumerate(rows):
        y = pad + r * (ch + 26 + pad)
        for c, im in enumerate(cells):
            x = pad + c * (cw + pad)
            sheet[y + 26 : y + 26 + ch, x : x + cw] = cv2.resize(
                im, (cw, ch), interpolation=cv2.INTER_NEAREST
            )
            label = "origin" if c == 0 else f"g<{grid_t[c - 1]}"
            cv2.putText(sheet, f"{name} {label}", (x + 4, y + 19),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
    cv2.imwrite(str(out), sheet)
    print(out, sheet.shape)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
