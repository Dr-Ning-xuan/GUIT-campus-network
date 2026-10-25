"""真机链路验证：用【假账号】跑一次完整登录。

目的是证明「取图 -> 识别算式 -> 算结果 -> 提交」这条链是通的：
  * 若服务端回「验证码错误」 -> 说明识别或 session 关联有问题（FAIL）
  * 若服务端回「账号或密码错误 / 未开通」之类 -> 说明验证码已经过关了（PASS）

登录页的验证码是 200x60 的算式题（`61加5=?` 这种），提交的是**计算结果**。

安全：默认只用不存在的账号，绝不会踢掉你正在用的在线会话。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ocr import get_math_solver  # noqa: E402
from src.portal import PortalClient  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://10.6.6.6")
    ap.add_argument("--guiid", default="0000000000")
    ap.add_argument("--account", default="13000000000")
    ap.add_argument("--password", default="Definitely-Not-A-Real-Password")
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()

    solver = get_math_solver()
    print(f"[INFO] 预热识别引擎… {solver.warmup()} ({solver.last_error or 'ok'})")

    cli = PortalClient(args.base)
    failures = 0
    passed = 0
    skipped = 0
    for i in range(args.rounds):
        form = cli.prepare_login()
        cap = solver.solve(form.captcha_png)
        print(f"[{i + 1}/{args.rounds}] 识别 = {cap.describe()}（图 {len(form.captcha_png)}B）")
        if not cap.ok:
            print("        × 这张没认出算式（真实流程会自动换图重来，这里先跳过）")
            skipped += 1
            continue
        if args.guiid == "0000000000":
            print("        → 用假账号提交（不会影响任何真实会话）")
        out = cli.submit_login(form, args.guiid, args.account, args.password, cap.answer)
        print(f"        http={out.status_code} ok={out.ok} 服务端原话：{out.message}")
        if "验证码" in out.message:
            failures += 1
            print("        × FAIL：服务端说验证码错误，识别或 session 关联有问题")
        else:
            passed += 1
            print("        √ PASS：验证码过关了，剩下的只是账号密码的问题")

    print()
    print(f"[RESULT] 识别成功 {args.rounds - skipped}/{args.rounds} 张，"
          f"其中验证码被服务端认可 {passed} 次，被判错 {failures} 次")
    if failures:
        print("[RESULT] 有提交被判验证码错误 —— 识别准确率不够，靠换图重试兜")
        return 1
    if passed:
        print("[RESULT] 链路健康：取图 -> 识别算式 -> 算结果 -> 提交 全通")
        return 0
    print("[RESULT] 没拿到有效样本，多跑几轮看看")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
