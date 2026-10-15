"""把压测样本拼成一张大图，方便肉眼核对识别准确率。

用法: python tools/make_montage.py <样本目录> <输出png> [scale]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def load_font(size: int = 16):
    for name in ("consola.ttf", "msyh.ttc", "arial.ttf"):
        try:
            return ImageFont.truetype(str(Path("C:/Windows/Fonts") / name), size)
        except Exception:
            continue
    return ImageFont.load_default()


def main() -> int:
    src = Path(sys.argv[1])
    out = Path(sys.argv[2])
    scale = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    files = sorted(src.glob("*.png"))
    if not files:
        print(f"[FAIL] {src} 里没有 png")
        return 1

    font = load_font(18)
    tw = 150
    with Image.open(files[0]) as probe:
        cw, ch = probe.size[0] * scale, probe.size[1] * scale

    canvas = Image.new("RGB", (tw + cw + 8, ch * len(files) + 8), "white")
    d = ImageDraw.Draw(canvas)
    for i, f in enumerate(files):
        im = Image.open(f).convert("RGB").resize((cw, ch), Image.NEAREST)
        y = 4 + i * ch
        canvas.paste(im, (tw, y))
        d.text((6, y + ch // 2 - 10), f.stem, fill="black", font=font)
        d.line([(0, y), (canvas.width, y)], fill=(220, 220, 220))
    canvas.save(out)
    print(f"[OK] {len(files)} 张 -> {out}  ({canvas.width}x{canvas.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
