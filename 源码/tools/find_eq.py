"""分析算式验证码里「=?」的左边界，给裁切策略定阈值。

用法：
    python tools/find_eq.py --src _eval/imgs            # 只打印每张图的裁切点
    python tools/find_eq.py --src _eval/imgs --draw     # 另外画竖线可视化（前 12 张）
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def mask_of(rgb: np.ndarray, t: int = 145, satmax: int = 220) -> np.ndarray:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    sat = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[:, :, 1]
    return (((gray < t) & (sat < satmax)).astype(np.uint8)) * 255


def eq_cut(mask: np.ndarray, px_thr: int = 2) -> int | None:
    """从右往左跳过「=?」，返回建议裁切列（裁到该列左侧，不含）。"""
    col = (mask > 0).sum(axis=0)
    W = mask.shape[1]
    x = W - 1
    while x >= 0 and col[x] <= px_thr:      # 尾部空白
        x -= 1
    if x < 0:
        return None
    start = x
    while x >= 0 and col[x] > px_thr:       # 吃掉「= ?」整体
        x -= 1
    while x >= 0 and col[x] <= px_thr:      # 间隙
        x -= 1
    if x < 0:
        return None
    # 若「= ?」整体窄得不合理，说明只吃掉了尾部噪声，放弃
    if start - x < 12:
        return None
    return x + 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="_eval/imgs")
    ap.add_argument("--draw", action="store_true")
    ap.add_argument("--out", default="_eval/eqcut.png")
    args = ap.parse_args()

    src = ROOT / args.src
    files = sorted(src.glob("*.png"))
    cuts: list[int] = []
    lines = []
    for f in files:
        rgb = np.array(Image.open(io.BytesIO(f.read_bytes())).convert("RGB"))
        c = eq_cut(mask_of(rgb))
        lines.append(f"{f.stem} cut={c}")
        if c:
            cuts.append(c)
    print("\n".join(lines))
    if cuts:
        arr = np.array(cuts)
        print(f"\n[STAT] n={len(cuts)} min={arr.min()} max={arr.max()} "
              f"mean={arr.mean():.1f} p10={np.percentile(arr,10):.0f} "
              f"p90={np.percentile(arr,90):.0f}")
        print("[HIST]", np.histogram(arr, bins=10, range=(100, 200))[0].tolist())

    if args.draw:
        s, cols = 2, 2
        pick = files[:12]
        cw, ch = 200 * s, 60 * s
        rows = (len(pick) + cols - 1) // cols
        sheet = np.full((rows * (ch + 24) + 8, cols * (cw + 8) + 8, 3), 40, np.uint8)
        for i, f in enumerate(pick):
            rgb = np.array(Image.open(io.BytesIO(f.read_bytes())).convert("RGB"))
            c = eq_cut(mask_of(rgb))
            im = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            if c:
                cv2.line(im, (c, 0), (c, 59), (0, 0, 255), 1)
            r, k = divmod(i, cols)
            y = 8 + r * (ch + 24)
            x = 8 + k * (cw + 8)
            sheet[y + 22 : y + 22 + ch, x : x + cw] = cv2.resize(
                im, (cw, ch), interpolation=cv2.INTER_NEAREST
            )
            cv2.putText(sheet, f"{f.stem} cut={c}", (x + 4, y + 17),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
        out = ROOT / args.out
        cv2.imwrite(str(out), sheet)
        print("[DRAW]", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
