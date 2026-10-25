"""批量扒登录页算式验证码，只存图不提交。

用法：
    python tools/fetch_captchas.py 300
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.portal import PortalClient  # noqa: E402

CAPTCHA_LOGIN = "/ACaptcha.ashx"


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    out = ROOT / "_eval" / "live"
    out.mkdir(parents=True, exist_ok=True)
    cli = PortalClient()
    cli._get("/login")
    ok = 0
    t0 = time.time()
    for i in range(n):
        try:
            png = cli.fetch_captcha(CAPTCHA_LOGIN)
            (out / f"{i:04d}.png").write_bytes(png)
            ok += 1
        except Exception as exc:
            print(f"ERR #{i}: {exc}", flush=True)
            time.sleep(1.0)
        time.sleep(0.3)
        if i % 25 == 0:
            print(f"{i}/{n} ok={ok} {time.time()-t0:.0f}s", flush=True)
    print(f"DONE {ok}/{n} in {time.time()-t0:.0f}s -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
