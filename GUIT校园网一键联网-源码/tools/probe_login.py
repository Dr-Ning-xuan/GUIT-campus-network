"""真机链路验证：用【假账号】跑一次完整登录。

目的是证明「取图 -> 识别 -> 提交」这条链是通的：
  * 若服务端回「验证码错误」 -> 说明识别或 session 关联有问题（FAIL）
  * 若服务端回「账号或密码错误 / 未开通」之类 -> 说明验证码已经过关了（PASS）

安全：默认只用不存在的账号，绝不会踢掉你正在用的在线会话。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ocr import get_solver  # noqa: E402
from src.portal import PortalClient  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://10.6.6.6")
    ap.add_argument("--guiid", default="0000000000")
    ap.add_argument("--account", default="13000000000")
    ap.add_argument("--password", default="Definitely-Not-A-Real-Password")
    ap.add_argument("--rounds", type=int, default=1)
    args = ap.parse_args()

    solver = get_solver()
    print(f"[INFO] 预热识别引擎… {solver.warmup()} ({solver.last_error or 'ok'})")

    failures = 0
    for i in range(args.rounds):
        cli = PortalClient(args.base)
        form = cli.prepare_login()
        code = solver.solve(form.captcha_png)
        print(f"[{i + 1}/{args.rounds}] 识别到验证码 = {code!r}（图 {len(form.captcha_png)}B）")
        if not code:
            print("        × 没认出 4 位，换一张")
            failures += 1
            continue
        if args.guiid == "0000000000":
            print("        → 用假账号提交（不会影响任何真实会话）")
        out = cli.submit_login(form, args.guiid, args.account, args.password, code)
        print(f"        http={out.status_code} ok={out.ok} 服务端原话：{out.message}")
        if "验证码" in out.message:
            failures += 1
            print("        × FAIL：服务端说验证码错误，说明链路有问题")
        else:
            print("        √ PASS：验证码过关了，剩下的只是账号密码的问题")

    print()
    if failures:
        print(f"[RESULT] {failures}/{args.rounds} 次被判验证码错误，需要排查")
        return 1
    print("[RESULT] 全部通过：取图 -> 识别 -> 提交 链路健康")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
