"""把验证码拼成大图，方便人工核对真值。

用法：
    python tools/make_sheets.py --dir _eval/imgs --cols 4 --rows 3
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="_eval/imgs")
    ap.add_argument("--out", default="_eval/sheets")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--rows", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--scale", type=int, default=1)
    args = ap.parse_args()

    src = ROOT / args.dir
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(src.glob("*.png"))
    if args.limit:
        files = files[: args.limit]
    per = args.cols * args.rows
    cw, ch = 200 * args.scale, 60 * args.scale
    pad = 22  # 左侧留给编号

    for si in range(0, len(files), per):
        chunk = files[si : si + per]
        sheet = Image.new("RGB", (pad + args.cols * cw, args.rows * ch), (240, 240, 240))
        d = ImageDraw.Draw(sheet)
        for i, p in enumerate(chunk):
            r, c = divmod(i, args.cols)
            x, y = pad + c * cw, r * ch
            try:
                im = Image.open(p).convert("RGB")
                if args.scale != 1:
                    im = im.resize((cw, ch), Image.LANCZOS)
                sheet.paste(im, (x, y))
            except Exception:
                pass
            d.text((2, y + ch // 2 - 5), p.stem, fill=(0, 0, 0))
        target = out / f"sheet_{si // per}.png"
        sheet.save(target)
        print(f"wrote {target}  ({len(chunk)} 张: {chunk[0].stem}..{chunk[-1].stem})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
