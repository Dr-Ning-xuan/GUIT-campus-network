"""测试 charset_range：把模型输出限制在算式合法字符里，看能不能消掉垃圾字符。

用法：
    python tools/test_charset.py 004 006 011 046 051
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent

LEGAL = "0123456789加减乘除+-*/=?"


def prep(rgb: np.ndarray, t: int = 155, cut: float = 0.60) -> bytes:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    sat = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[:, :, 1]
    mask = (((gray < t) & (sat < 220)).astype(np.uint8)) * 255
    if cut:
        mask = mask[:, : int(rgb.shape[1] * cut)]
    ys, xs = np.nonzero(mask)
    mask = mask[max(0, ys.min() - 3) : ys.max() + 3, max(0, xs.min() - 3) : xs.max() + 3]
    im = Image.fromarray(255 - mask).convert("RGB")
    im = im.resize((im.width * 3, im.height * 3), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def main() -> int:
    import ddddocr

    engines = []
    for kw in ({"show_ad": False}, {"show_ad": False, "old": True}, {"show_ad": False, "beta": True}):
        try:
            engines.append(ddddocr.DdddOcr(**kw))
        except Exception:
            continue
    for i, e in enumerate(engines):
        e.set_ranges(LEGAL)

    for name in sys.argv[1:]:
        rgb = np.array(
            Image.open(io.BytesIO((ROOT / "_eval/imgs" / f"{name}.png").read_bytes())).convert("RGB")
        )
        print(f"=== {name} ===")
        for t in (135, 155):
            for cut in (0, 0.60):
                data = prep(rgb, t, cut)
                outs = []
                for k, e in enumerate(engines):
                    outs.append(f"[{k}]{e.classification(data)!r}")
                print(f"  t={t} cut={cut}: " + "  ".join(outs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
