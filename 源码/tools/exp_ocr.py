"""识别算法离线实验台：拿带真标签的样本集，比较不同预处理 / 后处理配置。

样本集在 _eval/imgs 与 _eval/labels60.json（人工逐张核对过的算式答案）。

用法：
    python tools/exp_ocr.py --config v2 --limit 20
    python tools/exp_ocr.py --config all
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_OP_WORDS = {"加": "+", "减": "-", "乘": "*", "除": "/"}
_OP_SYMBOLS = {
    "×": "*", "✕": "*", "✖": "*", "ｘ": "*", "Ｘ": "*", "x": "*", "X": "*",
    "乖": "*", "÷": "/", "／": "/", "−": "-", "–": "-", "—": "-",
    "一": "-", "ー": "-", "＋": "+", "十": "+", "士": "+",
}


def normalize(raw: str, max_digits: int = 3, allow_neg: bool = False) -> str:
    if not raw:
        return ""
    text = raw
    for w, s in _OP_WORDS.items():
        text = text.replace(w, s)
    for s, r in _OP_SYMBOLS.items():
        text = text.replace(s, r)
    text = re.sub(r"[^0-9+\-*/]", "", text)
    if sum(1 for ch in text if ch in "+-*/") != 1:
        return ""
    m = re.search(rf"(\d){{1,{max_digits}}}([+\-*/])(\d){{1,{max_digits}}}", text)
    return f"{m.group(0)[0:0]}{m.group(0)}" if m else ""


def compute(expr: str):
    m = re.fullmatch(r"(\d{1,3})([+\-*/])(\d{1,3})", expr or "")
    if not m:
        return None
    a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
    if op == "+":
        r = a + b
    elif op == "-":
        r = a - b
    elif op == "*":
        r = a * b
    else:
        if b == 0 or a % b:
            return None
        r = a // b
    if r < 0 or r > 9999:
        return None
    return str(r)


# --------------------------------------------------------------------------- #
# 预处理
# --------------------------------------------------------------------------- #
def _render(mask: np.ndarray, scale: int, crop: bool, stretch: float = 1.0,
            padx: int = 0) -> bytes:
    """mask: 前景=255。转成白底黑字、裁掉外边、放大、编码 PNG。"""
    if crop:
        ys, xs = np.nonzero(mask)
        if len(xs) == 0:
            return b""
        pad = 3
        x0, x1 = max(0, xs.min() - pad), min(mask.shape[1], xs.max() + pad)
        y0, y1 = max(0, ys.min() - pad), min(mask.shape[0], ys.max() + pad)
        mask = mask[y0:y1, x0:x1]
    im = Image.fromarray(255 - mask).convert("RGB")
    im = im.resize((int(im.width * scale * stretch), int(im.height * scale)), Image.LANCZOS)
    if padx:
        canvas = Image.new("RGB", (im.width + padx * 2, im.height), (255, 255, 255))
        canvas.paste(im, (padx, 0))
        im = canvas
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def build_variants(rgb: np.ndarray, cfg: dict) -> list[bytes]:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    sat = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)[:, :, 1]
    vmax = rgb.max(axis=2)
    out: list[bytes] = []
    kind = cfg["kind"]
    if kind == "gray":
        chan = gray
    elif kind == "vmax":
        chan = vmax
    elif kind == "b":
        chan = rgb[:, :, 2]
    elif kind == "g":
        chan = rgb[:, :, 1]
    elif kind == "r":
        chan = rgb[:, :, 0]
    elif kind == "vmin":
        chan = rgb.min(axis=2)
    else:
        raise ValueError(kind)
    for t in cfg["threshs"]:
        m = chan < t
        if cfg.get("satmax"):
            m &= sat < cfg["satmax"]
        base = (m.astype(np.uint8)) * 255
        for op in cfg["morph"]:
            mask = base
            if op == "open":
                mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
            elif op == "close":
                mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))
            for xcut in cfg.get("rightcuts", (0,)):
                sub = mask[:, :xcut] if xcut else mask
                for scale in cfg["scales"]:
                    for crop in cfg["crops"]:
                        for stretch in cfg.get("stretch", (1.0,)):
                            for padx in cfg.get("padx", (0,)):
                                buf = _render(sub, scale, crop, stretch, padx)
                                if buf:
                                    out.append(buf)
    return out


CONFIGS: dict[str, dict] = {
    # 现状：最大通道阈值 + 开运算
    "v1": dict(kind="vmax", threshs=(150, 160, 165, 170, 175, 180),
               morph=("open",), scales=(3, 4), crops=(False,), max_digits=3, engines=3),
    # 现状但限制两位数
    "v1b": dict(kind="vmax", threshs=(150, 160, 165, 170, 175, 180),
                morph=("open",), scales=(3, 4), crops=(False,), max_digits=2, engines=3),
    # 灰度阈值（感知亮度），不裁剪
    "g1": dict(kind="gray", threshs=(110, 125, 140, 155, 170),
               morph=("none", "open"), scales=(3, 4), crops=(False,), max_digits=2, engines=3),
    # 灰度 + 裁边
    "g2": dict(kind="gray", threshs=(110, 125, 140, 155, 170),
               morph=("none", "open"), scales=(3,), crops=(True,), max_digits=2, engines=3),
    # 灰度 + 饱和度过滤 + 裁边
    "g3": dict(kind="gray", threshs=(110, 125, 140, 155, 170), satmax=220,
               morph=("none", "open"), scales=(3,), crops=(True,), max_digits=2, engines=3),
    # g2 + 右侧裁掉「= ?」
    "g4": dict(kind="gray", threshs=(125, 140, 155), satmax=220,
               morph=("none", "open"), scales=(3,), crops=(True,),
               rightcuts=(0, 112, 120, 128), max_digits=2, engines=3),
    # 只保留裁切变体（不要完整图）
    "g5": dict(kind="gray", threshs=(125, 140, 155), satmax=220,
               morph=("none", "open"), scales=(3,), crops=(True,),
               rightcuts=(110, 116, 122, 130), max_digits=2, engines=3),
    # 阈值放宽到 170（浅色字符也能抓到）
    "g6": dict(kind="gray", threshs=(125, 140, 155, 170), satmax=220,
               morph=("none", "open"), scales=(3,), crops=(True,),
               rightcuts=(0, 108, 118, 128), max_digits=2, engines=3),
    # 不做开运算（保住除号那两个点）
    "g7": dict(kind="gray", threshs=(125, 140, 155, 170), satmax=220,
               morph=("none",), scales=(3,), crops=(True,),
               rightcuts=(0, 108, 118, 128), max_digits=2, engines=3),
    # 阈值 4 档 + 裁切 3 档（变体数与 g4 相当，想快一点）
    "g9": dict(kind="gray", threshs=(125, 140, 155, 170), satmax=220,
               morph=("none", "open"), scales=(3,), crops=(True,),
               rightcuts=(0, 112, 124), max_digits=2, engines=3),
    # 加一档放大（3/4）
    "h1": dict(kind="gray", threshs=(125, 140, 155, 170), satmax=220,
               morph=("none", "open"), scales=(3, 4), crops=(True,),
               rightcuts=(0, 108, 118, 128), max_digits=2, engines=3),
    # 蓝通道单开（蓝/紫字符在 B 通道上反差最大）
    "h2": dict(kind="b", threshs=(120, 145, 170, 195), satmax=230,
               morph=("none", "open"), scales=(3,), crops=(True,),
               rightcuts=(0, 108, 118, 128), max_digits=2, engines=3),
    # 蓝通道 + 灰度混投
    "h3": dict(kind="b", threshs=(130, 160, 190), satmax=230,
               morph=("none",), scales=(3,), crops=(True,),
               rightcuts=(0, 108, 118, 128), max_digits=2, engines=3),
    # 横向拉伸（给 CTC 更大的字符间距）
    "s1": dict(kind="gray", threshs=(125, 140, 155, 170), satmax=220,
               morph=("none", "open"), scales=(3,), crops=(True,),
               rightcuts=(0, 108, 118, 128), stretch=(1.0, 1.3), max_digits=2, engines=3),
    # 左右留白
    "s2": dict(kind="gray", threshs=(125, 140, 155, 170), satmax=220,
               morph=("none", "open"), scales=(3,), crops=(True,),
               rightcuts=(0, 108, 118, 128), padx=(0, 24), max_digits=2, engines=3),
}


def load_engines(n: int = 3, charset: str = "") -> list:
    import ddddocr

    out = []
    for kw in ({"show_ad": False}, {"show_ad": False, "old": True},
               {"show_ad": False, "beta": True})[:n]:
        try:
            out.append(ddddocr.DdddOcr(**kw))
        except Exception:
            continue
    if charset:
        for e in out:
            try:
                e.set_ranges(charset)   # 只允许这些字符，垃圾字符的 logits 直接被屏蔽
            except Exception:
                pass
    return out


def run(cfg_name: str, files: list[Path], labels: dict, engines: list) -> dict:
    cfg = CONFIGS[cfg_name]
    ok = right = 0
    details = []
    per_engine = [0] * len(engines)
    t0 = time.time()
    for f in files:
        rgb = np.array(Image.open(io.BytesIO(f.read_bytes())).convert("RGB"))
        variants = build_variants(rgb, cfg)
        votes: dict[str, int] = {}
        for i, v in enumerate(variants):
            eng = engines[i % len(engines)]
            try:
                raw = eng.classification(v)
            except Exception:
                continue
            text = normalize(raw, cfg["max_digits"])
            if not text:
                continue
            ans = compute(text)
            if not ans:
                continue
            votes[ans] = votes.get(ans, 0) + 1
        best = ""
        if votes:
            best = max(votes.items(), key=lambda kv: kv[1])[0]
        truth = labels.get(f.stem, "")
        hit = bool(best) and best == truth
        ok += bool(best)
        right += hit
        details.append((f.stem, truth, best, votes.get(best, 0) if best else 0, images := len(variants)))
    dt = time.time() - t0
    n = len(files)
    return dict(name=cfg_name, n=n, ok=ok, right=right, known_rate=ok / n,
                acc=right / max(ok, 1), use=right / n, ms=dt / n * 1000, details=details)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="_eval/imgs")
    ap.add_argument("--labels", default="_eval/labels60.json")
    ap.add_argument("--config", default="v1")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--charset", default="",
                    help="限制模型输出字符集，例如 0123456789加减乘除+-*/=?")
    args = ap.parse_args()

    src = ROOT / args.src
    labels = json.loads((ROOT / args.labels).read_text("utf-8"))
    files = sorted(src.glob("*.png"))
    if args.limit:
        files = files[: args.limit]
    engines = load_engines(3, args.charset)
    if not engines:
        print("[FAIL] 引擎加载失败")
        return 1

    names = list(CONFIGS) if args.config == "all" else args.config.split(",")
    reports = []
    for nm in names:
        r = run(nm, files, labels, engines)
        reports.append(r)
        print(f"[{nm}] n={r['n']} 认出={r['ok']} ({r['known_rate']*100:.0f}%) "
              f"答对={r['right']} 认出后准={r['acc']*100:.0f}% "
              f"单图可用={r['use']*100:.0f}% {r['ms']:.0f}ms/张", flush=True)
        if args.verbose:
            for nm2, truth, best, v, nv in r["details"]:
                mark = "OK " if best == truth else ("MIS" if best else "---")
                print(f"    {nm2} {mark} 真={truth:<4} 识={best or '?':<4} 票{v}")
    print()
    print("配置         认出率   认出后准   单图可用   ms/张")
    for r in reports:
        print(f"{r['name']:<10} {r['known_rate']*100:>6.0f}% {r['acc']*100:>8.0f}% "
              f"{r['use']*100:>9.0f}% {r['ms']:>7.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
