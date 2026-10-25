"""检验「某一列上竖直方向有 3 段前景」能不能区分除号和减号。

除号是「点 / 横线 / 点」上下三段，数字（8/0/6/9）最多两段，
减号只有一段。如果干扰控制得住，这就是个很干净的特征。

用法：
    python tools/test_runs.py
"""
from __future__ import annotations

import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent

DIV = ["004", "006", "010", "011", "021", "024", "030", "041", "045", "049", "051", "055", "059"]
MINUS = ["001", "012", "013", "016", "020", "028", "033", "034", "036", "037", "038",
         "042", "043", "044", "050", "057", "058"]
PLUS = ["000", "009", "015", "017", "026", "027", "029", "031", "046", "054", "056"]
TIMES = ["003", "005", "007", "014", "018", "019", "022", "025", "032", "035", "039",
         "048", "052", "053"]


def mask_of(rgb: np.ndarray, t: int) -> np.ndarray:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    sat = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[:, :, 1]
    return (((gray < t) & (sat < 220)).astype(np.uint8)) * 255


def run_profile(mask: np.ndarray):
    """返回 (最大列游程段数, 出现该段数的列区间)。只看中段 x∈[20,150]。"""
    h, w = mask.shape
    best, span = 0, None
    x0, x1 = 20, min(w, 150)
    for x in range(x0, x1):
        col = mask[:, x] > 0
        runs, prev = 0, False
        for v in col:
            if v and not prev:
                runs += 1
            prev = v
        if runs > best:
            best, span = runs, (x, x)
        elif runs == best and span and x - span[1] <= 6:
            span = (span[0], x)
    return best, span


def main() -> int:
    for t in (120, 135, 150):
        print(f"=== 阈值 gray<{t} ===")
        for names, tag in ((DIV, "除号"), (MINUS, "减号"), (PLUS, "加号"), (TIMES, "乘号")):
            vals = []
            for nm in names:
                rgb = np.array(
                    Image.open(io.BytesIO((ROOT / "_eval/imgs" / f"{nm}.png").read_bytes())).convert("RGB")
                )
                best, _ = run_profile(mask_of(rgb, t))
                vals.append(best)
            from collections import Counter
            c = Counter(vals)
            print(f"  {tag}: {dict(sorted(c.items()))}  n={len(vals)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
