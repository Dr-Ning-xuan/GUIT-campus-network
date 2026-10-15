"""从素材图生成应用图标与状态立绘。

用法:
    python tools/make_assets.py [素材目录]      # 默认 D:\Desktop\新建文件夹

素材约定（也支持 .jpg/.jpeg/.png/.webp，脚本按真实格式识别，不看扩展名）:
    1.*  -> assets/icon.ico           应用图标（去白底 + 透明 + 多尺寸）
    2.*  -> assets/state-offline.gif  未联网时窗内右下角播放
    3.*  -> assets/state-online.gif   已联网后换成这张

找不到素材目录时直接跳过（不报错），这样 build.ps1 在别的机器上也能跑。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"
DEFAULT_SRC = Path(r"D:\Desktop\新建文件夹")

ICON_NAMES = ("1.jpg", "1.jpeg", "1.png", "1.webp")
OFFLINE_NAMES = ("2.jpg", "2.jpeg", "2.png", "2.webp")
ONLINE_NAMES = ("3.jpg", "3.jpeg", "3.png", "3.webp")


def find(src: Path, names) -> Path | None:
    for n in names:
        p = src / n
        if p.exists():
            return p
    return None


def make_icon(path: Path) -> None:
    im = Image.open(path).convert("RGBA")
    # 从四角 flood fill 掉白底（比"把所有白像素变透明"安全：不会打穿立绘内部的白色）
    for xy in ((0, 0), (im.width - 1, 0), (0, im.height - 1), (im.width - 1, im.height - 1)):
        ImageDraw.floodfill(im, xy, (0, 0, 0, 0), thresh=42)
    bbox = im.getbbox()
    if bbox:
        im = im.crop(bbox)
    side = max(im.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(im, ((side - im.width) // 2, (side - im.height) // 2), im)
    dst = OUT / "icon.ico"
    canvas.save(dst, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print(f"  icon.ico        <- {path.name}  ({side}x{side} master)")


def _rounded(img: Image.Image, ratio: float = 0.07) -> Image.Image:
    img = img.convert("RGBA")
    w, h = img.size
    r = int(min(w, h) * ratio)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def make_badge(path: Path, dst_name: str, target_h: int = 200) -> None:
    """支持 GIF 动图：逐帧缩放 + 圆角，帧延时原样保留。"""
    im = Image.open(path)
    n = getattr(im, "n_frames", 1)
    frames, durations = [], []
    for i in range(n):
        im.seek(i)
        durations.append(max(40, int(im.info.get("duration", 80) or 80)))
        f = im.convert("RGBA")
        s = target_h / f.height
        f = f.resize((max(1, int(f.width * s)), target_h), Image.LANCZOS)
        frames.append(_rounded(f))
    dst = OUT / dst_name
    if n > 1:
        frames[0].save(
            dst, save_all=True, append_images=frames[1:],
            duration=durations, loop=0, disposal=2, optimize=True,
        )
    else:
        frames[0].save(dst)
    print(f"  {dst_name:<15} <- {path.name}  ({n} 帧, {frames[0].size})")


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    if not src.exists():
        print(f"[skip] 素材目录不存在：{src}（保留现有 assets）")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    print(f"[src] {src}")

    p1 = find(src, ICON_NAMES)
    if p1:
        make_icon(p1)
    p2 = find(src, OFFLINE_NAMES)
    if p2:
        make_badge(p2, "state-offline.gif")
    p3 = find(src, ONLINE_NAMES)
    if p3:
        make_badge(p3, "state-online.gif")

    print(f"[ok] assets: {', '.join(sorted(x.name for x in OUT.iterdir()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
