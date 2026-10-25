"""把一张验证码的若干预处理变体拼成一张图，用来肉眼判断预处理吃掉/留下了什么。

用法：
    python tools/peek_variants.py --src _eval/imgs/004.png --out _eval/peek-004.png
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def variants(png: bytes) -> list[tuple[str, np.ndarray]]:
    """返回 [(说明, BGR 图)]，全部是「白底黑字」。"""
    src = Image.open(io.BytesIO(png)).convert("RGB")
    rgb = np.array(src)
    vmax = rgb.max(axis=2)
    vmin = rgb.min(axis=2)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    sat = hsv[:, :, 1]
    out: list[tuple[str, np.ndarray]] = []

    def as_bgr(mask: np.ndarray) -> np.ndarray:
        return cv2.cvtColor(255 - mask, cv2.COLOR_GRAY2BGR)

    out.append(("origin", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)))

    for t in (120, 150, 175):
        m = (vmax < t).astype(np.uint8) * 255
        out.append((f"vmax<{t}", as_bgr(m)))
        m2 = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        out.append((f"vmax<{t} open2", as_bgr(m2)))

    # 暗且不太饱和：字符深色，干扰多为彩色
    m = (((gray < 130) & (sat < 200)).astype(np.uint8)) * 255
    out.append(("gray<130 sat<200", as_bgr(m)))

    # 只留最暗的一层，尽量避开彩色曲线
    m = (vmin < 90).astype(np.uint8) * 255
    out.append(("vmin<90", as_bgr(m)))

    # 闭运算补断笔
    m = (vmax < 160).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))
    out.append(("vmax<160 close2", as_bgr(m)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--scale", type=int, default=3)
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    src = Path(args.src)
    if not src.is_absolute():
        src = root / src
    out = Path(args.out) if args.out else src.with_name(src.stem + "-variants.png")
    if not out.is_absolute():
        out = root / out

    items = variants(src.read_bytes())
    s = args.scale
    pad = 10
    cw, ch = 200 * s, 60 * s
    cols = 2
    rows = (len(items) + cols - 1) // cols
    sheet = np.full((rows * (ch + 26 + pad) + pad, cols * (cw + pad) + pad, 3), 40, np.uint8)
    for i, (name, im) in enumerate(items):
        r, c = divmod(i, cols)
        y = pad + r * (ch + 26 + pad)
        x = pad + c * (cw + pad)
        sheet[y + 26 : y + 26 + ch, x : x + cw] = cv2.resize(
            im, (cw, ch), interpolation=cv2.INTER_NEAREST
        )
        cv2.putText(sheet, name, (x + 4, y + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (0, 255, 255), 2, cv2.LINE_AA)
    cv2.imwrite(str(out), sheet)
    print(out, sheet.shape)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
