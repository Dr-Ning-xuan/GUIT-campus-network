"""看看 classification(probability=True) 返回什么，置信度能不能用。"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


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

    eng = ddddocr.DdddOcr(show_ad=False)
    for name in sys.argv[1:]:
        rgb = np.array(
            Image.open(io.BytesIO((ROOT / "_eval/imgs" / f"{name}.png").read_bytes())).convert("RGB")
        )
        data = prep(rgb)
        plain = eng.classification(data)
        withprob = eng.classification(data, probability=True)
        print(f"{name}: plain={plain!r}")
        if isinstance(withprob, dict):
            for k, v in withprob.items():
                s = str(v)
                print(f"   {k}: {s[:160]}")
        else:
            print(f"   prob={withprob!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
