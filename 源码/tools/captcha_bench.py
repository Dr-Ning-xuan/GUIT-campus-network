"""验证码识别率压测：抓真图、识别、存盘，供人工核对 / 自动验收。

两种验证码都能测：

  * --kind math  登录页那套算式题（默认，/ACaptcha.ashx）
  * --kind old   注销/查询页那套四位字母数字（/CheckCode）

加上 --verify 会用假账号真的提交一次，拿服务端的「验证码错误!」判定对错，
这样测出来的是**真正可用的准确率**，不靠人眼估。

用法：
    python tools/captcha_bench.py --count 20
    python tools/captcha_bench.py --count 20 --verify
    python tools/captcha_bench.py --kind old --count 30
"""
from __future__ import annotations

import argparse
import collections
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ocr import get_math_solver, get_solver  # noqa: E402
from src.portal import PortalClient  # noqa: E402

PROBE_GUI_ID = "2024012345"
PROBE_ACCOUNT = "13800138000"
PROBE_PASSWORD = "abcdefg1"


def _submit(cli: PortalClient, form, answer: str) -> str:
    """把答案提交上去，返回服务端提示语。用假账号，不会动到任何真实账号。"""
    out = cli.submit_login(
        form, PROBE_GUI_ID, PROBE_ACCOUNT, PROBE_PASSWORD, answer, "自动选择"
    )
    return out.message


def bench_math(args, out: Path) -> int:
    solver = get_math_solver()
    t0 = time.time()
    if not solver.warmup():
        print(f"[FAIL] 识别引擎加载失败：{solver.last_error}")
        return 1
    print(f"[INFO] 引擎加载耗时 {time.time() - t0:.2f}s")

    cli = PortalClient(args.base)
    cli._get("/login")
    got = 0
    right = 0
    submitted = 0
    times: list[float] = []
    t0 = time.time()
    for i in range(args.count):
        form = cli.prepare_login()
        png = form.captcha_png
        t1 = time.time()
        cap = solver.solve(png)
        dt = time.time() - t1
        times.append(dt)
        label = cap.describe() if cap.ok else "(未识别)"
        line = f"  #{i:02d}  {label:<28} {len(png):>6}B  {dt * 1000:>6.0f}ms"
        if cap.ok:
            got += 1
        low = bool(args.min_votes) and cap.ok and cap.votes < args.min_votes
        if args.verify and cap.ok and not low:
            submitted += 1
            msg = _submit(cli, form, cap.answer)
            ok = "验证码" not in msg
            right += int(ok)
            line += "  " + ("[PASS]" if ok else "[WRONG]")
        elif low:
            line += f"  [低票跳过 <{args.min_votes}]"
        (out / f"{i:03d}_{cap.answer or 'FAIL'}.png").write_bytes(png)
        print(line, flush=True)
        if args.verify and cap.ok and not low:
            time.sleep(0.6)   # 别把门户打急
    total = time.time() - t0
    avg = sum(times) / max(len(times), 1) * 1000
    print()
    print(f"[RESULT] {args.count} 张，用时 {total:.1f}s（识别 {avg:.0f}ms/张）")
    print(f"[RESULT] 认出算式的：{got}/{args.count}（{got / max(args.count, 1) * 100:.0f}%）")
    if args.verify:
        print(
            f"[RESULT] 提交后真的通过的：{right}/{submitted}"
            f"（以「真的提交了」为前提 {right / max(submitted, 1) * 100:.0f}%）"
        )
    print(f"[RESULT] 图片已存到 {out}")
    return 0


def bench_old(args, out: Path) -> int:
    solver = get_solver()
    t0 = time.time()
    if not solver.warmup():
        print(f"[FAIL] 识别引擎加载失败：{solver.last_error}")
        return 1
    print(f"[INFO] 引擎加载耗时 {time.time() - t0:.2f}s")

    cli = PortalClient(args.base)
    cli._get("/login")
    stats = collections.Counter()
    t0 = time.time()
    for i in range(args.count):
        png = cli.fetch_captcha()
        t1 = time.time()
        code = solver.normalize(solver._ensure().classification(png))
        dt = time.time() - t1
        (out / f"{i:03d}_{code or 'FAIL'}.png").write_bytes(png)
        stats[len(code) if code else 0] += 1
        shown = code or "(识别失败)"
        print(f"  #{i:02d}  {shown:<8} {len(png):>5}B  {dt * 1000:>6.0f}ms")
    total = time.time() - t0
    print()
    print(f"[RESULT] {args.count} 张，用时 {total:.1f}s（平均 {total / args.count * 1000:.0f}ms/张）")
    print(f"[RESULT] 识别出 4 位的：{stats[4]} 张；非 4 位：{args.count - stats[4]} 张")
    print(f"[RESULT] 图片已存到 {out}，文件名带识别结果，人工核对后可算出真实准确率")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--kind", choices=("math", "old"), default="math")
    ap.add_argument("--out", default=str(ROOT / "build" / "captcha-bench"))
    ap.add_argument("--base", default="http://10.6.6.6")
    ap.add_argument(
        "--verify",
        action="store_true",
        help="用假账号真的提交一次，按服务端反馈判断对错（会消耗一些请求）",
    )
    ap.add_argument(
        "--min-votes",
        type=int,
        default=0,
        help="票数低于该值的结果不提交（模拟程序里的 min_votes 过滤）",
    )
    args = ap.parse_args()

    out = Path(args.out) / args.kind
    out.mkdir(parents=True, exist_ok=True)
    if args.kind == "old":
        return bench_old(args, out)
    return bench_math(args, out)


if __name__ == "__main__":
    raise SystemExit(main())
