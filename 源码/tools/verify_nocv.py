"""验证：用 PIL+numpy 重写 _variants，输出与 cv2 版逐字节一致。

用法：
    python tools/verify_nocv.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

THRESHOLDS = (125, 140, 155, 170)
SAT_MAX = 220
RIGHT_CUTS = (0.54, 0.59, 0.64)
SCALES = (3,)


def old_variants(png: bytes) -> list[bytes]:
    import cv2

    src = Image.open(io.BytesIO(png)).convert("RGB")
    rgb = np.array(src)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    sat = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[:, :, 1]
    width = rgb.shape[1]
    cuts = [0] + [int(width * f) for f in RIGHT_CUTS]
    out: list[bytes] = []
    for thresh in THRESHOLDS:
        base = ((gray < thresh) & (sat < SAT_MAX)).astype(np.uint8) * 255
        for use_open in (False, True):
            mask = base
            if use_open:
                mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
            for cut in cuts:
                sub = mask[:, :cut] if cut else mask
                ys, xs = np.nonzero(sub)
                if len(xs) == 0:
                    continue
                pad = 3
                sub = sub[
                    max(0, int(ys.min()) - pad) : int(ys.max()) + pad,
                    max(0, int(xs.min()) - pad) : int(xs.max()) + pad,
                ]
                im = Image.fromarray(255 - sub).convert("RGB")
                for scale in SCALES:
                    big = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
                    buf = io.BytesIO()
                    big.save(buf, "PNG")
                    out.append(buf.getvalue())
    return out


# cv2 对 2x2 核的默认 anchor 是 (1,1)：erode/dilate 都取「左上方」的 2x2 邻域
def _erode2(m: np.ndarray) -> np.ndarray:
    p = np.pad(m, ((1, 0), (1, 0)), mode="constant", constant_values=255)
    return np.minimum(
        np.minimum(p[:-1, :-1], p[:-1, 1:]), np.minimum(p[1:, :-1], p[1:, 1:])
    )


def _dilate2(m: np.ndarray) -> np.ndarray:
    p = np.pad(m, ((1, 0), (1, 0)), mode="constant", constant_values=0)
    return np.maximum(
        np.maximum(p[:-1, :-1], p[:-1, 1:]), np.maximum(p[1:, :-1], p[1:, 1:])
    )


def new_variants(png: bytes) -> list[bytes]:
    src = Image.open(io.BytesIO(png)).convert("RGB")
    rgb = np.array(src)
    # ITU-R 601-2，与 cv2.COLOR_RGB2GRAY 完全一致
    gray = np.array(src.convert("L"))
    # HSV 的 S = 255*(max-min)/max（max>0），与 cv2 一致
    mx = rgb.max(axis=2).astype(np.int32)
    mn = rgb.min(axis=2).astype(np.int32)
    sat = np.where(
        mx > 0, np.rint((mx - mn) * 255.0 / np.maximum(mx, 1)), 0.0
    ).astype(np.uint8)
    width = rgb.shape[1]
    cuts = [0] + [int(width * f) for f in RIGHT_CUTS]
    out: list[bytes] = []
    for thresh in THRESHOLDS:
        base = ((gray < thresh) & (sat < SAT_MAX)).astype(np.uint8) * 255
        for use_open in (False, True):
            mask = base
            if use_open:
                mask = _dilate2(_erode2(mask))
            for cut in cuts:
                sub = mask[:, :cut] if cut else mask
                ys, xs = np.nonzero(sub)
                if len(xs) == 0:
                    continue
                pad = 3
                sub = sub[
                    max(0, int(ys.min()) - pad) : int(ys.max()) + pad,
                    max(0, int(xs.min()) - pad) : int(xs.max()) + pad,
                ]
                im = Image.fromarray(255 - sub).convert("RGB")
                for scale in SCALES:
                    big = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
                    buf = io.BytesIO()
                    big.save(buf, "PNG")
                    out.append(buf.getvalue())
    return out


def main() -> int:
    dirs = [ROOT / "_eval" / "imgs", ROOT / "_eval" / "live"]
    total = same = diff = 0
    for d in dirs:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.png"))[:40]:
            png = p.read_bytes()
            try:
                a = old_variants(png)
                b = new_variants(png)
            except Exception as exc:
                print(f"[ERR] {p.name}: {exc}")
                continue
            total += 1
            if len(a) != len(b):
                diff += 1
                print(f"[DIFF] {p.name}: 数量 {len(a)} vs {len(b)}")
                continue
            bad = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
            if bad:
                diff += 1
                print(f"[DIFF] {p.name}: {len(bad)}/{len(a)} 个变体不同，首个 idx={bad[0]}")
            else:
                same += 1
    print()
    print(f"[RESULT] 对比 {total} 张，完全一致 {same} 张，有差异 {diff} 张")
    return 0 if diff == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
