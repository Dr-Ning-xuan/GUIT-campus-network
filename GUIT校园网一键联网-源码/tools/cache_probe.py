"""验证码缓存探测：加随机 query 能不能治『拿到同一张旧图』。

用法: python tools/cache_probe.py [每轮次数]
"""
from __future__ import annotations

import hashlib
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.portal import PortalClient  # noqa: E402


def digest(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:12]


def run(label: str, n: int, bust: bool) -> None:
    cli = PortalClient()
    cli._get("/login")
    seen = []
    for _ in range(n):
        url = "/CheckCode" + (f"?t={random.random()}" if bust else "")
        try:
            r = cli.session.get(cli.base + url, timeout=10)
        except Exception as exc:  # noqa: BLE001
            print(f"  {label}: 请求失败 {exc}")
            return
        seen.append((digest(r.content), r.status_code, len(r.content)))
        time.sleep(0.15)
    uniq = len({s[0] for s in seen})
    print(f"  {label}: {n} 次请求 -> {uniq} 张不同的图  (重复 {n - uniq} 次)")
    for i, (h, st, ln) in enumerate(seen):
        flag = "  <-- 与上一张相同" if i and h == seen[i - 1][0] else ""
        print(f"      #{i:02d} http={st} {ln:>5}B {h}{flag}")


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    print("[INFO] 不带 cache-buster：")
    run("plain ", n, False)
    print("[INFO] 带随机 query（cache-buster）：")
    run("buster", n, True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
