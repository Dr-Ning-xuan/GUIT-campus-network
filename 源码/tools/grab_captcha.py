"""离线取样：只抓登录页算式验证码存盘，不识别、不提交。

用于给识别算法做离线对比实验（同一批图跑不同算法，看认出率 / 票数分布）。

用法：
    python tools/grab_captcha.py --count 60
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.portal import PortalClient  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=60)
    ap.add_argument("--out", default=str(ROOT / "_eval" / "imgs"))
    ap.add_argument("--base", default="http://10.6.6.6")
    ap.add_argument("--gap", type=float, default=0.12, help="两张之间的间隔秒数")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cli = PortalClient(args.base)
    cli._get("/login")
    got = 0
    for i in range(args.count):
        try:
            form = cli.prepare_login()
        except Exception as exc:  # noqa: BLE001
            print(f"#{i:03d} 取图失败：{exc}", flush=True)
            time.sleep(1.0)
            continue
        png = form.captcha_png
        (out / f"{i:03d}.png").write_bytes(png)
        got += 1
        print(f"#{i:03d} {len(png):>6}B", flush=True)
        time.sleep(args.gap)
    print(f"[DONE] {got}/{args.count} -> {out}")
    return 0 if got else 1


if __name__ == "__main__":
    raise SystemExit(main())
