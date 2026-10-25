"""试试 ddddocr 的 det 模式能不能在干净预处理后逐字切分。

用法：
    python tools/test_det.py 004 006 011 046
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def variants(rgb: np.ndarray, t: int) -> list[Image.Image]:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    sat = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[:, :, 1]
    mask = (((gray < t) & (sat < 220)).astype(np.uint8)) * 255
    out = []
    for cut in (0, int(rgb.shape[1] * 0.60)):
        sub = mask[:, :cut] if cut else mask
        ys, xs = np.nonzero(sub)
        if not len(xs):
            continue
        sub = sub[max(0, ys.min() - 4) : ys.max() + 4, max(0, xs.min() - 4) : xs.max() + 4]
        im = Image.fromarray(255 - sub).convert("RGB")
        out.append(im.resize((im.width * 3, im.height * 3), Image.LANCZOS))
    return out


def main() -> int:
    import ddddocr

    det = ddddocr.DdddOcr(show_ad=False, det=True)
    rec = ddddocr.DdddOcr(show_ad=False)
    for name in sys.argv[1:]:
        rgb = np.array(
            Image.open(io.BytesIO((ROOT / "_eval/imgs" / f"{name}.png").read_bytes())).convert("RGB")
        )
        print(f"=== {name} ===")
        for t in (130, 150):
            for i, im in enumerate(variants(rgb, t)):
                buf = io.BytesIO()
                im.save(buf, "PNG")
                data = buf.getvalue()
                try:
                    res = det.detection(data)
                except Exception as exc:  # noqa: BLE001
                    print(f"  t={t} cut#{i} det 失败 {exc}")
                    continue
                chars = []
                for box, _score in res:
                    x0 = min(p[0] for p in box)
                    x1 = max(p[0] for p in box)
                    y0 = min(p[1] for p in box)
                    y1 = max(p[1] for p in box)
                    crop = im.crop((x0, y0, x1, y1)).resize(((x1 - x0) * 2, (y1 - y0) * 2))
                    b2 = io.BytesIO()
                    crop.save(b2, "PNG")
                    try:
                        chars.append(rec.classification(b2.getvalue()))
                    except Exception:
                        chars.append("?")
                print(f"  t={t} cut#{i}: 框 {len(res)} 个 -> {chars}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
