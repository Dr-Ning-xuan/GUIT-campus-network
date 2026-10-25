"""把取样目录里的验证码拼成大图，方便人眼标注 / 核对。

用法：
    python tools/montage.py --src _eval/imgs --cols 3 --rows 5 --out _eval/sheet
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="_eval/imgs")
    ap.add_argument("--out", default="_eval/sheet")
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--rows", type=int, default=5)
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--pad", type=int, default=10)
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    src = (root / args.src) if not Path(args.src).is_absolute() else Path(args.src)
    out = (root / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(src.glob("*.png"))
    per = args.cols * args.rows
    s, pad = args.scale, args.pad
    cw, ch = 200 * s, 60 * s

    for page in range((len(files) + per - 1) // per):
        chunk = files[page * per : (page + 1) * per]
        sheet = np.full(
            (args.rows * (ch + pad) + pad, args.cols * (cw + pad) + pad, 3), 40, np.uint8
        )
        for idx, f in enumerate(chunk):
            im = cv2.imread(str(f), cv2.IMREAD_COLOR)
            if im is None:
                continue
            im = cv2.resize(im, (cw, ch), interpolation=cv2.INTER_LANCZOS4)
            r, c = divmod(idx, args.cols)
            y = pad + r * (ch + pad)
            x = pad + c * (cw + pad)
            sheet[y : y + ch, x : x + cw] = im
            tag = f.stem
            cv2.rectangle(sheet, (x, y), (x + 74, y + 28), (0, 0, 0), -1)
            cv2.putText(sheet, tag, (x + 4, y + 21), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (0, 255, 255), 2, cv2.LINE_AA)
        dst = out.with_name(f"{out.name}-{page + 1}.png")
        cv2.imwrite(str(dst), sheet)
        print(dst, sheet.shape)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
