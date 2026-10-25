"""逐变体打印某个模型的原始输出，定位到底哪一步把字符认错了。

用法：
    python tools/debug_one.py 004 006 051 --config g4
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from exp_ocr import CONFIGS, build_variants, compute, load_engines, normalize  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+")
    ap.add_argument("--config", default="g4")
    ap.add_argument("--src", default="_eval/imgs")
    args = ap.parse_args()

    cfg = CONFIGS[args.config]
    engines = load_engines(3)
    for name in args.names:
        rgb = np.array(
            Image.open(io.BytesIO((ROOT / args.src / f"{name}.png").read_bytes())).convert("RGB")
        )
        variants = build_variants(rgb, cfg)
        print(f"=== {name} 变体 {len(variants)} 个 ===")
        for i, v in enumerate(variants):
            row = []
            for k, eng in enumerate(engines):
                try:
                    raw = eng.classification(v)
                except Exception as exc:  # noqa: BLE001
                    row.append(f"ERR({exc})")
                    continue
                norm = normalize(raw, cfg["max_digits"])
                row.append(f"[{k}]{raw!r}->{norm or '-'}"
                           + (f"={compute(norm)}" if norm else ""))
            print(f"  {i:02d} " + "  ".join(row))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
